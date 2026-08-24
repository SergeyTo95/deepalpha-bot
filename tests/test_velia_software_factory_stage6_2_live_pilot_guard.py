from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_live_pilot_guard_service as guard
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError


def _clear_rollout(monkeypatch):
    for name in (
        "VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE",
        "VELIA_SOFTWARE_FACTORY_USER_IDS",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE",
        "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED",
        "LIVE_OWNER_USER_IDS",
        "JARVIS_FOUNDER_IDS",
        "VELIA_CHAT_BETA_USER_IDS",
        "VELIA_MOBILE_DEBUG_USER_IDS",
        *rollout._PLAN_FLAGS,
        *rollout._BUILD_REVIEW_FLAGS,
        *rollout._RELEASE_FLAGS,
    ):
        monkeypatch.delenv(name, raising=False)


def _admin_live(monkeypatch, *, guard_enabled: bool):
    _clear_rollout(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "admin_id")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true" if guard_enabled else "false")
    monkeypatch.setattr(rollout, "configured_admin_id", lambda: 7)


def _run(*, allowed=True, external=False):
    return {
        "run_id": "run-1",
        "user_id": 7,
        "project_id": "project-1",
        "spec_fingerprint": "f" * 64,
        "state": "planning",
        "spec": {"allowed_paths": ["services"] if allowed else []},
        "dag": [
            {
                "task_id": "task-1",
                "status": "dispatched" if external else "pending",
                "external_ref": "autopilot-1" if external else "",
            }
        ],
    }


def _project():
    return {
        "id": "project-1",
        "repository_full_name": "SergeyTo95/deepalpha-bot",
    }


def test_guard_defaults_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", raising=False)
    status = guard.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "one_shot_dispatch_grant"
    assert status["max_dispatches_per_run"] == 1
    assert status["grant_required_before_autopilot_enqueue"] is True
    assert status["repository_write_supported_by_guard"] is False
    assert status["merge_supported_by_guard"] is False
    assert status["deployment_supported_by_guard"] is False


def test_admin_live_requires_guard(monkeypatch):
    _admin_live(monkeypatch, guard_enabled=False)
    assert rollout.user_allowed(7) is True
    assert rollout.intake_allowed(7) is True
    assert rollout.live_execution_allowed(7) is False
    assert rollout.supervisor_allowed() is False
    assert rollout.public_status(7)["live_execution"] is False

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    assert rollout.live_execution_allowed(7) is True
    assert rollout.supervisor_allowed() is True
    assert rollout.public_status(7)["live_execution"] is True


def test_explicit_allowlist_live_keeps_existing_behavior(monkeypatch):
    _clear_rollout(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "false")
    assert rollout.eligibility_source(7) == "explicit_allowlist"
    assert rollout.live_execution_allowed(7) is True
    assert rollout.supervisor_allowed() is True


def test_admin_live_readiness_names_missing_guard(monkeypatch):
    _admin_live(monkeypatch, guard_enabled=False)
    for name in rollout._RELEASE_FLAGS:
        monkeypatch.setenv(name, "true")
    readiness = rollout.pilot_readiness(7)
    assert "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED" in readiness["build_review"]["missing_flags"]
    assert "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED" in readiness["release"]["missing_flags"]
    assert readiness["build_review"]["ready"] is False

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    readiness = rollout.pilot_readiness(7)
    assert "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED" not in readiness["build_review"]["missing_flags"]


def test_issue_grant_fails_before_db_when_guard_disabled(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "false")
    with pytest.raises(SoftwareFactoryError) as exc:
        guard.issue_grant(7, _run(), _project())
    assert exc.value.code == "velia_factory_live_pilot_guard_disabled"


def test_issue_grant_requires_approved_scope(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    with pytest.raises(SoftwareFactoryError) as exc:
        guard.issue_grant(7, _run(allowed=False), _project())
    assert exc.value.code == "velia_factory_live_pilot_write_scope_required"


def test_issue_grant_refuses_existing_external_work(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    with pytest.raises(SoftwareFactoryError) as exc:
        guard.issue_grant(7, _run(external=True), _project())
    assert exc.value.code == "velia_factory_live_pilot_work_already_dispatched"


def test_identity_is_bound_to_exact_project_and_repo():
    identity = guard._run_identity(_run(), _project())
    assert identity == {
        "run_id": "run-1",
        "project_id": "project-1",
        "repository_full_name": "SergeyTo95/deepalpha-bot",
        "spec_fingerprint": "f" * 64,
    }
    with pytest.raises(SoftwareFactoryError) as exc:
        guard._run_identity(_run(), {"id": "other", "repository_full_name": "SergeyTo95/deepalpha-bot"})
    assert exc.value.code == "velia_factory_live_pilot_project_mismatch"


def test_tuple_serializer_preserves_one_shot_fields():
    row = (
        "grant-1", 7, "run-1", "project-1", "SergeyTo95/deepalpha-bot", "f" * 64,
        "consumed", "task-1", "factory:run-1:task-1", "autopilot-1", "explicit_admin",
        None, None, None, None, None,
    )
    item = guard._row(row)
    assert item["grant_id"] == "grant-1"
    assert item["status"] == "consumed"
    assert item["factory_task_id"] == "task-1"
    assert item["autopilot_task_id"] == "autopilot-1"
    assert item["max_dispatches"] == 1
