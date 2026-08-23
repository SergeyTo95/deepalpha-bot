from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_rollout_runtime_patch as runtime
from services.velia_software_factory_core_service import SoftwareFactoryError


def _result(**overrides):
    value = {
        "status": "passed",
        "passed": True,
        "repository_full_name": "SergeyTo95/deepalpha-bot",
        "code_ref": "a" * 40,
        "run_id": "run-1",
        "dry_run": True,
        "execution_blocked": True,
        "autopilot_missions_unchanged": True,
        "repository_write_performed": False,
        "autopilot_task_dispatched": False,
        "merge_performed": False,
        "deployment_triggered": False,
    }
    value.update(overrides)
    return value


def test_acceptance_gate_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", raising=False)
    fake = SimpleNamespace(run_acceptance=lambda: (_ for _ in ()).throw(AssertionError("must not run")))

    result = runtime._run_dry_run_acceptance_gate(fake)

    assert result == {"enabled": False, "status": "disabled", "passed": False}


def test_acceptance_actor_resolves_exact_repo_without_exposing_id(monkeypatch):
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {7, 11})
    monkeypatch.setattr(runtime.rollout, "admin_pilot_id_source", lambda: "live_owner")
    monkeypatch.setattr(
        runtime.project_service,
        "list_projects",
        lambda user_id: (
            [{"repository_full_name": "Acme/shop", "archived": False}]
            if user_id == 11
            else [{"repository_full_name": "Acme/other", "archived": False}]
        ),
    )

    assert runtime._resolve_acceptance_actor_id(fake) == 11


def test_acceptance_actor_fails_closed_when_repo_has_no_pilot_owner(monkeypatch):
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {7, 11})
    monkeypatch.setattr(runtime.rollout, "admin_pilot_id_source", lambda: "chat_beta")
    monkeypatch.setattr(runtime.project_service, "list_projects", lambda user_id: [])

    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._resolve_acceptance_actor_id(fake)
    assert exc.value.code == "velia_factory_dry_run_acceptance_actor_not_found"
    assert "candidates=2" in exc.value.detail
    assert "matches=0" in exc.value.detail
    assert "source=chat_beta" in exc.value.detail


def test_acceptance_actor_fails_closed_when_repo_owner_is_ambiguous(monkeypatch):
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {7, 11})
    monkeypatch.setattr(runtime.rollout, "admin_pilot_id_source", lambda: "jarvis_founder")
    monkeypatch.setattr(
        runtime.project_service,
        "list_projects",
        lambda user_id: [{"repository_full_name": "Acme/shop", "archived": False}],
    )

    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._resolve_acceptance_actor_id(fake)
    assert exc.value.code == "velia_factory_dry_run_acceptance_actor_ambiguous"
    assert "matches=2" in exc.value.detail


def test_acceptance_gate_passes_only_explicit_pass(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")
    fake = SimpleNamespace(
        configured_admin_id=lambda: 99,
        is_admin_user=lambda user_id: user_id == 99,
        run_acceptance=lambda: _result(),
    )

    result = runtime._run_dry_run_acceptance_gate(fake, actor_id=7)

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["autopilot_missions_unchanged"] is True
    assert fake.configured_admin_id() == 99
    assert fake.is_admin_user(99) is True


def test_acceptance_gate_rejects_nonpassing_result(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")
    fake = SimpleNamespace(
        configured_admin_id=lambda: 99,
        is_admin_user=lambda user_id: user_id == 99,
        run_acceptance=lambda: _result(status="failed", passed=False, failure_reasons=["team_plan_empty"]),
    )

    with pytest.raises(RuntimeError, match="velia_factory_dry_run_acceptance_failed"):
        runtime._run_dry_run_acceptance_gate(fake, actor_id=7)
    assert fake.configured_admin_id() == 99


def test_acceptance_gate_propagates_probe_exception(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")

    class ProbeError(RuntimeError):
        code = "probe_boom"

    fake = SimpleNamespace(
        configured_admin_id=lambda: 99,
        is_admin_user=lambda user_id: user_id == 99,
        run_acceptance=lambda: (_ for _ in ()).throw(ProbeError("boom")),
    )

    with pytest.raises(ProbeError, match="boom"):
        runtime._run_dry_run_acceptance_gate(fake, actor_id=7)
    assert fake.configured_admin_id() == 99