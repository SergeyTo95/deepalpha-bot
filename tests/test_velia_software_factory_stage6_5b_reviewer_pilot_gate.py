from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services import velia_software_factory_live_pilot_reviewer_gate_patch as gate
from services.velia_software_factory_core_service import SoftwareFactoryError


def _reviewer_modules(*, enabled: bool, installed: bool):
    reviewer = SimpleNamespace(reviewer_enabled=lambda: enabled)
    runtime = SimpleNamespace(_INSTALLED=installed)
    return reviewer, runtime


def _baseline_preflight():
    return {
        "available": True,
        "read_only": True,
        "grant_read": False,
        "grant_issue": False,
        "dispatch": False,
        "environment_mutation": False,
        "candidate": {"run_id": "run-1"},
        "runtime": {"control_enabled": True, "guard_enabled": True},
        "candidate_blockers": [],
        "runtime_blockers": [],
        "candidate_safe_to_arm_when_runtime_ready": True,
        "runtime_ready_now": True,
        "pilot_candidate_ready_now": True,
    }


def _modules():
    preflight = SimpleNamespace(
        preflight_candidate=lambda user_id, run_id, repository_full_name: _baseline_preflight()
    )
    control = SimpleNamespace(
        _require_live_control=lambda user_id: {"actor_user_id": int(user_id)},
        public_status=lambda user_id: {"available": True, "enabled": True},
    )
    calls = {"issue": 0, "claim": 0}

    def issue(user_id, run, project, **kwargs):
        calls["issue"] += 1
        return {"grant_id": "grant-1", "status": "pending"}

    def claim(user_id, run, project, **kwargs):
        calls["claim"] += 1
        return {"grant_id": "grant-1", "status": "claimed"}

    guard = SimpleNamespace(
        live_pilot_guard_enabled=lambda: True,
        issue_grant=issue,
        claim_dispatch=claim,
    )
    return preflight, control, guard, calls


def test_readiness_requires_flag_and_runtime_hook(monkeypatch):
    monkeypatch.setattr(gate, "_reviewer_modules", lambda: _reviewer_modules(enabled=False, installed=False))
    status = gate.reviewer_readiness()
    assert status["ready"] is False
    assert status["enabled"] is False
    assert status["runtime_installed"] is False
    assert status["blockers"] == ["reviewer_disabled", "reviewer_runtime_not_installed"]
    assert status["required_for_live_pilot"] is True
    assert status["required_for_dry_run"] is False

    monkeypatch.setattr(gate, "_reviewer_modules", lambda: _reviewer_modules(enabled=True, installed=False))
    status = gate.reviewer_readiness()
    assert status["ready"] is False
    assert status["blockers"] == ["reviewer_runtime_not_installed"]

    monkeypatch.setattr(gate, "_reviewer_modules", lambda: _reviewer_modules(enabled=True, installed=True))
    status = gate.reviewer_readiness()
    assert status["ready"] is True
    assert status["blockers"] == []


def test_preflight_remains_read_only_but_live_readiness_fails_closed(monkeypatch):
    preflight, control, guard, calls = _modules()
    monkeypatch.setattr(gate, "reviewer_readiness", lambda: {
        "available": True,
        "ready": False,
        "enabled": False,
        "runtime_installed": True,
        "required_for_live_pilot": True,
        "required_for_dry_run": False,
        "blockers": ["reviewer_disabled"],
    })
    monkeypatch.setattr(gate, "_admin_pilot_for_user", lambda user_id: True)
    gate.install(preflight, control, guard)

    result = preflight.preflight_candidate(7, "run-1", "SergeyTo95/deepalpha-bot")

    assert result["read_only"] is True
    assert result["grant_read"] is False
    assert result["grant_issue"] is False
    assert result["dispatch"] is False
    assert result["environment_mutation"] is False
    assert result["candidate_safe_to_arm_when_runtime_ready"] is True
    assert result["runtime"]["reviewer_ready"] is False
    assert result["runtime"]["reviewer"]["blockers"] == ["reviewer_disabled"]
    assert result["runtime_blockers"] == ["reviewer_not_ready"]
    assert result["runtime_ready_now"] is False
    assert result["pilot_candidate_ready_now"] is False
    assert calls == {"issue": 0, "claim": 0}


def test_control_requires_reviewer_only_after_existing_live_checks(monkeypatch):
    preflight, control, guard, _ = _modules()
    monkeypatch.setattr(gate, "reviewer_readiness", lambda: {
        "ready": False,
        "blockers": ["reviewer_disabled"],
    })
    monkeypatch.setattr(gate, "_admin_pilot_for_user", lambda user_id: True)
    gate.install(preflight, control, guard)

    with pytest.raises(SoftwareFactoryError) as exc:
        control._require_live_control(7)
    assert exc.value.code == "velia_factory_live_pilot_reviewer_not_ready"
    assert "reviewer_disabled" in exc.value.detail

    status = control.public_status(7)
    assert status["reviewer"]["ready"] is False


def test_arm_then_reviewer_change_blocks_exact_dispatch_claim(monkeypatch):
    preflight, control, guard, calls = _modules()
    state = {"ready": True}

    def readiness():
        return {
            "ready": state["ready"],
            "enabled": state["ready"],
            "runtime_installed": True,
            "blockers": [] if state["ready"] else ["reviewer_disabled"],
        }

    monkeypatch.setattr(gate, "reviewer_readiness", readiness)
    monkeypatch.setattr(gate, "_admin_pilot_for_user", lambda user_id: True)
    gate.install(preflight, control, guard)

    # Arm-time boundary passes while reviewer is healthy.
    grant = guard.issue_grant(7, {"run_id": "run-1"}, {"id": "project-1"})
    assert grant["status"] == "pending"
    assert calls["issue"] == 1

    # Reviewer becomes unavailable after arm. The deepest claim boundary must
    # revalidate and reject before the original claim/enqueue path can run.
    state["ready"] = False
    with pytest.raises(SoftwareFactoryError) as exc:
        guard.claim_dispatch(
            7,
            {"run_id": "run-1"},
            {"id": "project-1"},
            factory_task_id="task-1",
            client_request_id="factory:run-1:task-1",
        )
    assert exc.value.code == "velia_factory_live_pilot_reviewer_not_ready"
    assert calls["claim"] == 0


def test_non_admin_pilot_guard_path_is_unchanged(monkeypatch):
    preflight, control, guard, calls = _modules()
    monkeypatch.setattr(gate, "reviewer_readiness", lambda: {
        "ready": False,
        "blockers": ["reviewer_disabled"],
    })
    monkeypatch.setattr(gate, "_admin_pilot_for_user", lambda user_id: False)
    gate.install(preflight, control, guard)

    result = guard.claim_dispatch(11, {}, {}, factory_task_id="task", client_request_id="request")
    assert result["status"] == "claimed"
    assert calls["claim"] == 1


def test_stage3_installs_gate_after_reviewer_runtime_and_before_rollout_capture():
    source = Path("services/velia_software_factory_stage3_hardening_patch.py").read_text(encoding="utf-8")
    reviewer = source.index("reviewer_runtime.install()")
    pilot_gate = source.index("reviewer_pilot_gate.install()")
    rollout = source.index("rollout_runtime.install(factory, module)")
    assert reviewer < pilot_gate < rollout


def test_gate_source_adds_no_merge_deploy_or_environment_mutation_primitives():
    source = Path("services/velia_software_factory_live_pilot_reviewer_gate_patch.py").read_text(encoding="utf-8")
    for forbidden in (
        "merge_pull_request",
        "merge_exact_head",
        "create_deployment",
        "redeploy",
        "set-variables",
        "os.environ[",
        "os.putenv",
        "advance_run(",
        "enqueue_task(",
    ):
        assert forbidden not in source
    assert "required_for_dry_run" in source
    assert "reviewer_not_ready" in source
