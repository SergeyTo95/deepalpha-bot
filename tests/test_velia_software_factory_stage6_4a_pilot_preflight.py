from __future__ import annotations

from pathlib import Path

import pytest

from services import velia_software_factory_live_pilot_preflight_service as preflight
from services.velia_software_factory_core_service import SoftwareFactoryError


def _run(**overrides):
    value = {
        "run_id": "run-1",
        "project_id": "project-1",
        "state": "planning",
        "spec_fingerprint": "f" * 64,
        "spec": {"allowed_paths": ["services", "tests"]},
        "dag": [
            {"task_id": "task-1", "status": "pending", "external_ref": ""},
            {"task_id": "task-2", "status": "pending", "external_ref": ""},
        ],
    }
    value.update(overrides)
    return value


def _project(**overrides):
    value = {
        "id": "project-1",
        "repository_full_name": "SergeyTo95/deepalpha-bot",
    }
    value.update(overrides)
    return value


def _runtime(monkeypatch, *, ready: bool):
    monkeypatch.setattr(preflight, "configured_admin_id", lambda: 7)
    monkeypatch.setattr(preflight.factory, "get_run", lambda user_id, run_id: _run())
    monkeypatch.setattr(preflight.project_service, "get_project", lambda user_id, project_id: _project())
    monkeypatch.setattr(preflight.control, "live_pilot_control_enabled", lambda: ready)
    monkeypatch.setattr(
        preflight.guard,
        "public_status",
        lambda: {
            "available": True,
            "enabled": ready,
            "mode": "one_shot_dispatch_grant",
            "max_dispatches_per_run": 1,
        },
    )
    monkeypatch.setattr(
        preflight.rollout,
        "public_status",
        lambda user_id: {
            "mode": "live" if ready else "off",
            "eligibility_source": "admin_pilot" if ready else "none",
            "pilot_readiness": {
                "build_review": {
                    "ready": ready,
                    "missing_flags": [] if ready else ["VELIA_DEVELOPER_WRITE_ENABLED"],
                }
            },
        },
    )


def test_preflight_requires_configured_owner(monkeypatch):
    monkeypatch.setattr(preflight, "configured_admin_id", lambda: 7)
    with pytest.raises(SoftwareFactoryError) as exc:
        preflight.preflight_candidate(8, "run-1", "SergeyTo95/deepalpha-bot")
    assert exc.value.code == "velia_factory_live_pilot_admin_required"


def test_safe_candidate_is_visible_while_runtime_remains_closed(monkeypatch):
    _runtime(monkeypatch, ready=False)

    result = preflight.preflight_candidate(7, "run-1", "SergeyTo95/deepalpha-bot")

    assert result["read_only"] is True
    assert result["grant_read"] is False
    assert result["grant_issue"] is False
    assert result["dispatch"] is False
    assert result["environment_mutation"] is False
    assert result["candidate_blockers"] == []
    assert result["candidate_safe_to_arm_when_runtime_ready"] is True
    assert result["runtime_ready_now"] is False
    assert result["pilot_candidate_ready_now"] is False
    assert result["runtime_blockers"] == [
        "control_disabled",
        "guard_disabled",
        "admin_eligibility_required",
        "live_rollout_required",
        "build_review_not_ready",
    ]
    assert result["runtime"]["missing_build_review_flags"] == ["VELIA_DEVELOPER_WRITE_ENABLED"]


def test_exact_ready_candidate_reports_ready_without_dispatching(monkeypatch):
    _runtime(monkeypatch, ready=True)
    called = {"advance": 0, "issue": 0, "grant": 0}

    def forbidden_advance(*args, **kwargs):
        called["advance"] += 1
        raise AssertionError("preflight must not advance a Factory run")

    def forbidden_issue(*args, **kwargs):
        called["issue"] += 1
        raise AssertionError("preflight must not issue a grant")

    def forbidden_grant(*args, **kwargs):
        called["grant"] += 1
        raise AssertionError("preflight must not read the grant table")

    monkeypatch.setattr(preflight.factory, "advance_run", forbidden_advance)
    monkeypatch.setattr(preflight.guard, "issue_grant", forbidden_issue)
    monkeypatch.setattr(preflight.guard, "get_grant", forbidden_grant)

    result = preflight.preflight_candidate(7, "run-1", "SergeyTo95/deepalpha-bot")

    assert result["candidate_blockers"] == []
    assert result["runtime_blockers"] == []
    assert result["candidate_safe_to_arm_when_runtime_ready"] is True
    assert result["runtime_ready_now"] is True
    assert result["pilot_candidate_ready_now"] is True
    assert called == {"advance": 0, "issue": 0, "grant": 0}


def test_preflight_reports_intrinsic_candidate_blockers(monkeypatch):
    _runtime(monkeypatch, ready=True)
    bad_run = _run(
        run_id="different-run",
        state="completed",
        spec_fingerprint="",
        spec={"allowed_paths": []},
        dag=[{"task_id": "task-1", "external_ref": "autopilot-123"}],
    )
    monkeypatch.setattr(preflight.factory, "get_run", lambda user_id, run_id: bad_run)

    result = preflight.preflight_candidate(7, "run-1", "Other/repo")

    assert result["candidate_blockers"] == [
        "run_identity_mismatch",
        "repository_confirmation_mismatch",
        "spec_fingerprint_missing",
        "run_state_not_dispatchable",
        "write_scope_missing",
        "work_already_dispatched",
    ]
    assert result["candidate"]["dispatched_external_refs"] == ["autopilot-123"]
    assert result["candidate_safe_to_arm_when_runtime_ready"] is False
    assert result["runtime_ready_now"] is True
    assert result["pilot_candidate_ready_now"] is False


def test_project_identity_mismatch_is_blocking(monkeypatch):
    _runtime(monkeypatch, ready=True)
    monkeypatch.setattr(
        preflight.project_service,
        "get_project",
        lambda user_id, project_id: _project(id="other-project"),
    )

    result = preflight.preflight_candidate(7, "run-1", "SergeyTo95/deepalpha-bot")
    assert result["candidate_blockers"] == ["project_identity_mismatch"]
    assert result["pilot_candidate_ready_now"] is False


def test_preflight_source_has_no_execution_or_storage_side_effect_primitives():
    source = Path("services/velia_software_factory_live_pilot_preflight_service.py").read_text(encoding="utf-8")

    forbidden = (
        "advance_run(",
        "issue_grant(",
        "claim_dispatch(",
        "confirm_dispatch(",
        "get_grant(",
        "get_connection(",
        "set-variables",
        "merge_pull_request",
        "create_pull_request",
    )
    for token in forbidden:
        assert token not in source

    assert "factory.get_run(" in source
    assert "project_service.get_project(" in source
    assert "rollout.public_status(" in source
    assert '"grant_read": False' in source
    assert '"grant_issue": False' in source
    assert '"dispatch": False' in source
    assert '"environment_mutation": False' in source
