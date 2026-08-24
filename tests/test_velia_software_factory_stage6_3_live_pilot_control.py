from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_live_pilot_control_service as control
from services.velia_software_factory_core_service import SoftwareFactoryError


def _run():
    return {
        "run_id": "run-1",
        "project_id": "project-1",
        "state": "planning",
        "spec_fingerprint": "f" * 64,
        "spec": {"allowed_paths": ["services"]},
        "dag": [{"task_id": "task-1", "status": "pending", "external_ref": ""}],
    }


def _project():
    return {"id": "project-1", "repository_full_name": "SergeyTo95/deepalpha-bot"}


def _ready(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_CONTROL_ENABLED", "true")
    monkeypatch.setattr(control, "configured_admin_id", lambda: 7)
    monkeypatch.setattr(control.guard, "live_pilot_guard_enabled", lambda: True)
    monkeypatch.setattr(control.rollout, "eligibility_source", lambda user_id: "admin_pilot")
    monkeypatch.setattr(control.rollout, "live_execution_allowed", lambda user_id: True)
    monkeypatch.setattr(
        control.rollout,
        "pilot_readiness",
        lambda user_id: {"build_review": {"ready": True, "missing_flags": []}},
    )
    monkeypatch.setattr(control.factory, "get_run", lambda user_id, run_id: _run())
    monkeypatch.setattr(control.project_service, "get_project", lambda user_id, project_id: _project())


def test_control_defaults_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_CONTROL_ENABLED", raising=False)
    assert control.live_pilot_control_enabled() is False


def test_non_admin_cannot_use_control(monkeypatch):
    monkeypatch.setattr(control, "configured_admin_id", lambda: 7)
    with pytest.raises(SoftwareFactoryError) as exc:
        control.public_status(8)
    assert exc.value.code == "velia_factory_live_pilot_admin_required"


def test_live_control_requires_independent_control_flag(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_CONTROL_ENABLED", "false")
    with pytest.raises(SoftwareFactoryError) as exc:
        control.arm_grant(7, "run-1", "SergeyTo95/deepalpha-bot", "arm:run-1:SergeyTo95/deepalpha-bot")
    assert exc.value.code == "velia_factory_live_pilot_control_disabled"


def test_live_control_requires_one_shot_guard(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(control.guard, "live_pilot_guard_enabled", lambda: False)
    with pytest.raises(SoftwareFactoryError) as exc:
        control.arm_grant(7, "run-1", "SergeyTo95/deepalpha-bot", "arm:run-1:SergeyTo95/deepalpha-bot")
    assert exc.value.code == "velia_factory_live_pilot_guard_disabled"


def test_live_control_requires_admin_pilot_source(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(control.rollout, "eligibility_source", lambda user_id: "explicit_allowlist")
    with pytest.raises(SoftwareFactoryError) as exc:
        control.arm_grant(7, "run-1", "SergeyTo95/deepalpha-bot", "arm:run-1:SergeyTo95/deepalpha-bot")
    assert exc.value.code == "velia_factory_live_pilot_admin_eligibility_required"


def test_live_control_requires_build_review_readiness(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        control.rollout,
        "pilot_readiness",
        lambda user_id: {"build_review": {"ready": False, "missing_flags": ["VELIA_DEVELOPER_WRITE_ENABLED"]}},
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        control.arm_grant(7, "run-1", "SergeyTo95/deepalpha-bot", "arm:run-1:SergeyTo95/deepalpha-bot")
    assert exc.value.code == "velia_factory_live_pilot_build_review_not_ready"
    assert "VELIA_DEVELOPER_WRITE_ENABLED" in exc.value.detail


def test_arm_requires_exact_repository_and_confirmation(monkeypatch):
    _ready(monkeypatch)
    with pytest.raises(SoftwareFactoryError) as exc:
        control.arm_grant(7, "run-1", "Other/repo", "arm:run-1:Other/repo")
    assert exc.value.code == "velia_factory_live_pilot_repository_confirmation_mismatch"

    with pytest.raises(SoftwareFactoryError) as exc:
        control.arm_grant(7, "run-1", "SergeyTo95/deepalpha-bot", "yes")
    assert exc.value.code == "velia_factory_live_pilot_explicit_confirmation_required"


def test_arm_issues_only_bound_guard_grant(monkeypatch):
    _ready(monkeypatch)
    seen = {}

    def issue(user_id, run, project, *, approval_source, ttl_seconds):
        seen.update(
            user_id=user_id,
            run=run,
            project=project,
            approval_source=approval_source,
            ttl_seconds=ttl_seconds,
        )
        return {
            "grant_id": "grant-1",
            "run_id": "run-1",
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "status": "pending",
        }

    monkeypatch.setattr(control.guard, "issue_grant", issue)
    result = control.arm_grant(
        7,
        "run-1",
        "SergeyTo95/deepalpha-bot",
        "arm:run-1:SergeyTo95/deepalpha-bot",
        ttl_seconds=300,
    )
    assert result["grant"]["grant_id"] == "grant-1"
    assert seen["user_id"] == 7
    assert seen["approval_source"] == "control_center_stage6_3"
    assert seen["ttl_seconds"] == 300
    assert result["expected_dispatch_confirmation"] == (
        "dispatch:run-1:SergeyTo95/deepalpha-bot:grant-1"
    )


def test_revoke_remains_available_after_control_is_closed(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_CONTROL_ENABLED", "false")
    monkeypatch.setattr(
        control.guard,
        "get_grant",
        lambda user_id, run_id: {
            "grant_id": "grant-1",
            "run_id": "run-1",
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "status": "pending",
        },
    )
    monkeypatch.setattr(
        control.guard,
        "revoke_pending_grant",
        lambda user_id, run_id: {
            "grant_id": "grant-1",
            "run_id": "run-1",
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "status": "revoked",
        },
    )
    result = control.revoke_grant(7, "run-1", "SergeyTo95/deepalpha-bot")
    assert result["grant"]["status"] == "revoked"


def test_dispatch_requires_exact_grant_and_explicit_confirmation(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        control.guard,
        "get_grant",
        lambda user_id, run_id: {
            "grant_id": "grant-1",
            "run_id": "run-1",
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "status": "pending",
        },
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        control.dispatch_once(
            7,
            "run-1",
            "SergeyTo95/deepalpha-bot",
            "other-grant",
            "dispatch:run-1:SergeyTo95/deepalpha-bot:other-grant",
        )
    assert exc.value.code == "velia_factory_live_pilot_grant_confirmation_mismatch"

    with pytest.raises(SoftwareFactoryError) as exc:
        control.dispatch_once(7, "run-1", "SergeyTo95/deepalpha-bot", "grant-1", "yes")
    assert exc.value.code == "velia_factory_live_pilot_explicit_confirmation_required"


def test_dispatch_calls_factory_once_and_returns_consumed_grant(monkeypatch):
    _ready(monkeypatch)
    calls = {"advance": 0, "grant": 0}

    def grant(user_id, run_id):
        calls["grant"] += 1
        status = "pending" if calls["grant"] == 1 else "consumed"
        return {
            "grant_id": "grant-1",
            "run_id": "run-1",
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "status": status,
            "autopilot_task_id": "autopilot-1" if status == "consumed" else "",
        }

    def advance(user_id, run_id):
        calls["advance"] += 1
        return {**_run(), "state": "executing"}

    monkeypatch.setattr(control.guard, "get_grant", grant)
    monkeypatch.setattr(control.factory, "advance_run", advance)
    result = control.dispatch_once(
        7,
        "run-1",
        "SergeyTo95/deepalpha-bot",
        "grant-1",
        "dispatch:run-1:SergeyTo95/deepalpha-bot:grant-1",
    )
    assert calls["advance"] == 1
    assert result["grant"]["status"] == "consumed"
    assert result["max_dispatches"] == 1


def test_consumed_grant_never_dispatches_again(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        control.guard,
        "get_grant",
        lambda user_id, run_id: {
            "grant_id": "grant-1",
            "run_id": "run-1",
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "status": "consumed",
        },
    )
    called = {"advance": False}
    monkeypatch.setattr(control.factory, "advance_run", lambda *args: called.update(advance=True))
    with pytest.raises(SoftwareFactoryError) as exc:
        control.dispatch_once(
            7,
            "run-1",
            "SergeyTo95/deepalpha-bot",
            "grant-1",
            "dispatch:run-1:SergeyTo95/deepalpha-bot:grant-1",
        )
    assert exc.value.code == "velia_factory_live_pilot_dispatch_budget_exhausted"
    assert called["advance"] is False
