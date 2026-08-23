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


def _enable_gate(monkeypatch, *, actor_source="pilot_allowlist"):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "dry_run")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", actor_source)
    for name in runtime._DANGEROUS_ACCEPTANCE_FLAGS:
        monkeypatch.setenv(name, "false")


def _fake_module(run_acceptance, *, admin_id=99):
    return SimpleNamespace(
        configured_admin_id=lambda: admin_id,
        is_admin_user=lambda user_id: user_id == admin_id,
        autonomy=SimpleNamespace(recommend_write_scope=lambda project: ["services", "tests"]),
        run_acceptance=run_acceptance,
    )


def test_acceptance_gate_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", raising=False)
    fake = SimpleNamespace(run_acceptance=lambda: (_ for _ in ()).throw(AssertionError("must not run")))

    result = runtime._run_dry_run_acceptance_gate(fake)

    assert result == {"enabled": False, "status": "disabled", "passed": False}


def test_acceptance_actor_resolves_exact_repo_from_pilot_allowlist(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", "pilot_allowlist")
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {7, 11})
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


def test_acceptance_actor_fails_closed_when_pilot_repo_owner_missing(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", "pilot_allowlist")
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {7, 11})
    monkeypatch.setattr(runtime.project_service, "list_projects", lambda user_id: [])

    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._resolve_acceptance_actor_id(fake)
    assert exc.value.code == "velia_factory_dry_run_acceptance_actor_not_found"
    assert "actor_source=pilot_allowlist" in exc.value.detail
    assert "candidates=2" in exc.value.detail
    assert "matches=0" in exc.value.detail


def test_acceptance_actor_fails_closed_when_pilot_repo_owner_ambiguous(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", "pilot_allowlist")
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {7, 11})
    monkeypatch.setattr(
        runtime.project_service,
        "list_projects",
        lambda user_id: [{"repository_full_name": "Acme/shop", "archived": False}],
    )

    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._resolve_acceptance_actor_id(fake)
    assert exc.value.code == "velia_factory_dry_run_acceptance_actor_ambiguous"
    assert "matches=2" in exc.value.detail


def test_repository_owner_actor_source_requires_unique_active_owner(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", "repository_owner")
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(runtime, "_repository_owner_actor_ids", lambda repository: [11])

    assert runtime._resolve_acceptance_actor_id(fake) == 11

    monkeypatch.setattr(runtime, "_repository_owner_actor_ids", lambda repository: [11, 12])
    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._resolve_acceptance_actor_id(fake)
    assert exc.value.code == "velia_factory_dry_run_acceptance_actor_ambiguous"


def test_preview_fixture_actor_source_uses_fixture_actor(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", "preview_fixture")
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    monkeypatch.setattr(
        runtime.fixture_service,
        "ensure_fixture",
        lambda repository: {"actor_id": 8100000000000000001, "repository_full_name": repository},
    )
    assert runtime._resolve_acceptance_actor_id(fake) == 8100000000000000001


def test_invalid_acceptance_actor_source_fails_closed(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", "everyone")
    fake = SimpleNamespace(acceptance_repository=lambda: "Acme/shop")
    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._resolve_acceptance_actor_id(fake)
    assert exc.value.code == "velia_factory_dry_run_acceptance_actor_source_invalid"


def test_acceptance_gate_rejects_open_write_or_execution_gate(monkeypatch):
    _enable_gate(monkeypatch)
    monkeypatch.setenv("VELIA_DEVELOPER_WRITE_ENABLED", "true")
    fake = SimpleNamespace(run_acceptance=lambda: _result())

    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._run_dry_run_acceptance_gate(fake, actor_id=7)
    assert exc.value.code == "velia_factory_dry_run_acceptance_execution_gate_open"
    assert "VELIA_DEVELOPER_WRITE_ENABLED" in exc.value.detail


def test_acceptance_gate_passes_only_explicit_pass(monkeypatch):
    _enable_gate(monkeypatch)
    fake = _fake_module(lambda: _result())

    result = runtime._run_dry_run_acceptance_gate(fake, actor_id=7)

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["autopilot_missions_unchanged"] is True
    assert fake.configured_admin_id() == 99
    assert fake.is_admin_user(99) is True


def test_repository_owner_gate_bridges_eligibility_only_during_probe(monkeypatch):
    _enable_gate(monkeypatch, actor_source="repository_owner")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {99})
    monkeypatch.setattr(runtime.rollout, "admin_pilot_id_source", lambda: "mobile_debug")
    observed = {}
    fake = _fake_module(lambda: None)

    def run_acceptance():
        observed["actor_ids"] = runtime.rollout.admin_pilot_user_ids()
        observed["source"] = runtime.rollout.admin_pilot_id_source()
        observed["configured_admin"] = fake.configured_admin_id()
        return _result()

    fake.run_acceptance = run_acceptance
    result = runtime._run_dry_run_acceptance_gate(fake, actor_id=7)

    assert result["passed"] is True
    assert observed == {
        "actor_ids": {7},
        "source": "acceptance_repository_owner",
        "configured_admin": 7,
    }
    assert runtime.rollout.admin_pilot_user_ids() == {99}
    assert runtime.rollout.admin_pilot_id_source() == "mobile_debug"
    assert fake.configured_admin_id() == 99
    assert fake.is_admin_user(99) is True


def test_preview_fixture_bridges_scope_only_during_probe(monkeypatch):
    _enable_gate(monkeypatch, actor_source="preview_fixture")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {99})
    monkeypatch.setattr(runtime.rollout, "admin_pilot_id_source", lambda: "mobile_debug")
    original_scope = lambda project: ["original"]
    observed = {}
    fake = SimpleNamespace(
        configured_admin_id=lambda: 99,
        is_admin_user=lambda user_id: user_id == 99,
        autonomy=SimpleNamespace(recommend_write_scope=original_scope),
    )

    def real_scope(project, *, tree_loader=None):
        observed["tree"] = tree_loader()
        return ["services", "tests", "docs"]

    fake.autonomy.recommend_write_scope = real_scope
    original_scope = fake.autonomy.recommend_write_scope

    def run_acceptance():
        observed["actor_ids"] = runtime.rollout.admin_pilot_user_ids()
        observed["source"] = runtime.rollout.admin_pilot_id_source()
        observed["configured_admin"] = fake.configured_admin_id()
        observed["scope"] = fake.autonomy.recommend_write_scope({"id": "fixture-project"})
        return _result()

    fake.run_acceptance = run_acceptance
    result = runtime._run_dry_run_acceptance_gate(fake, actor_id=8100000000000000001)

    assert result["passed"] is True
    assert observed["actor_ids"] == {8100000000000000001}
    assert observed["source"] == "acceptance_preview_fixture"
    assert observed["configured_admin"] == 8100000000000000001
    assert observed["scope"] == ["services", "tests", "docs"]
    assert [item["path"] for item in observed["tree"]["entries"]] == [
        "services/stage61_acceptance.py",
        "tests/test_stage61_acceptance.py",
        "docs/stage61_acceptance.md",
    ]
    assert runtime.rollout.admin_pilot_user_ids() == {99}
    assert runtime.rollout.admin_pilot_id_source() == "mobile_debug"
    assert fake.autonomy.recommend_write_scope is original_scope
    assert fake.configured_admin_id() == 99


def test_acceptance_gate_rejects_nonpassing_result(monkeypatch):
    _enable_gate(monkeypatch)
    fake = _fake_module(lambda: _result(status="failed", passed=False, failure_reasons=["team_plan_empty"]))

    with pytest.raises(RuntimeError, match="velia_factory_dry_run_acceptance_failed"):
        runtime._run_dry_run_acceptance_gate(fake, actor_id=7)
    assert fake.configured_admin_id() == 99


def test_acceptance_gate_propagates_probe_exception_and_restores(monkeypatch):
    _enable_gate(monkeypatch, actor_source="repository_owner")
    monkeypatch.setattr(runtime.rollout, "admin_pilot_user_ids", lambda: {99})
    monkeypatch.setattr(runtime.rollout, "admin_pilot_id_source", lambda: "mobile_debug")

    class ProbeError(RuntimeError):
        code = "probe_boom"

    fake = _fake_module(lambda: (_ for _ in ()).throw(ProbeError("boom")))

    with pytest.raises(ProbeError, match="boom"):
        runtime._run_dry_run_acceptance_gate(fake, actor_id=7)
    assert runtime.rollout.admin_pilot_user_ids() == {99}
    assert runtime.rollout.admin_pilot_id_source() == "mobile_debug"
    assert fake.configured_admin_id() == 99