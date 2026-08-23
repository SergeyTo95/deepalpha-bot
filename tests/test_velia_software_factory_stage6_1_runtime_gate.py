from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_rollout_runtime_patch as runtime


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


def test_acceptance_gate_passes_only_explicit_pass(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")
    fake = SimpleNamespace(run_acceptance=lambda: _result())

    result = runtime._run_dry_run_acceptance_gate(fake)

    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["autopilot_missions_unchanged"] is True


def test_acceptance_gate_rejects_nonpassing_result(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")
    fake = SimpleNamespace(run_acceptance=lambda: _result(status="failed", passed=False, failure_reasons=["team_plan_empty"]))

    with pytest.raises(RuntimeError, match="velia_factory_dry_run_acceptance_failed"):
        runtime._run_dry_run_acceptance_gate(fake)


def test_acceptance_gate_propagates_probe_exception(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")

    class ProbeError(RuntimeError):
        code = "probe_boom"

    fake = SimpleNamespace(run_acceptance=lambda: (_ for _ in ()).throw(ProbeError("boom")))

    with pytest.raises(ProbeError, match="boom"):
        runtime._run_dry_run_acceptance_gate(fake)
