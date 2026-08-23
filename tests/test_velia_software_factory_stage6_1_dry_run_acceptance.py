from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_dry_run_acceptance_service as acceptance
from services.velia_software_factory_core_service import SoftwareFactoryError


def _wire_happy(monkeypatch, *, mission_snapshots=None, clarification_reasons=None):
    monkeypatch.setattr(
        acceptance,
        "_require_runtime",
        lambda: (7, "Acme/shop", "Хочу интернет-магазин цветов", "a" * 40),
    )
    monkeypatch.setattr(acceptance, "_existing", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        acceptance,
        "_project",
        lambda user_id, repository: {
            "id": "project-1",
            "repository_full_name": repository,
            "selected_branch": "main",
            "archived": False,
        },
    )
    snapshots = list(mission_snapshots or [["mission-old"], ["mission-old"]])
    monkeypatch.setattr(acceptance, "_mission_ids", lambda user_id: snapshots.pop(0))
    monkeypatch.setattr(acceptance.autonomy, "recommend_write_scope", lambda project: ["src", "tests"])
    monkeypatch.setattr(
        acceptance.autonomy,
        "build_project_spec_from_message",
        lambda *args, **kwargs: {
            "project_id": "project-1",
            "title": "Flower shop",
            "objective": "Build a flower shop",
            "allowed_paths": [],
            "acceptance_criteria": [],
        },
    )
    reasons = list(clarification_reasons if clarification_reasons is not None else ["write_scope_required"])
    monkeypatch.setattr(
        acceptance.factory,
        "create_run",
        lambda user_id, spec: {
            "run_id": "run-1",
            "state": "clarifying" if reasons else "ready",
            "clarification": {
                "blocking": bool(reasons),
                "questions": [
                    {"key": "allowed_paths", "reason": reason, "question": "q"}
                    for reason in reasons
                ],
            },
        },
    )
    monkeypatch.setattr(
        acceptance.factory,
        "answer_clarifications",
        lambda user_id, run_id, answers: {
            "run_id": run_id,
            "state": "ready",
            "clarification": {"blocking": False, "questions": []},
        },
    )
    monkeypatch.setattr(
        acceptance.factory,
        "advance_run",
        lambda user_id, run_id: {
            "run_id": run_id,
            "state": "planning",
            "dry_run": True,
            "execution_blocked": True,
            "architecture": {"mode": "fallback", "components": [{"name": "web"}]},
            "team_plan": {"tasks": [{"id": "frontend", "role": "frontend"}]},
            "team_manifest": {"execution_roles": ["architect", "planner", "frontend", "qa"]},
        },
    )
    monkeypatch.setattr(
        acceptance,
        "_persist",
        lambda user_id, repository, commit_sha, identity, result: {
            **dict(result),
            "repository_full_name": repository,
            "code_ref": commit_sha,
            "probe_fingerprint": identity["probe_fingerprint"],
        },
    )


def test_acceptance_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", raising=False)
    status = acceptance.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "startup_dry_run_probe"
    assert status["dry_run_required"] is True
    assert status["repository_write_supported"] is False
    assert status["autopilot_execution_supported"] is False
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False


def test_code_ref_requires_exact_railway_commit_sha(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "bad")
    assert acceptance.code_ref() == ""
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "A" * 40)
    assert acceptance.code_ref() == "a" * 40


def test_runtime_requires_dry_run_and_admin_pilot(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_REPOSITORY", "Acme/shop")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "a" * 40)
    monkeypatch.setattr(acceptance, "configured_admin_id", lambda: 7)
    monkeypatch.setattr(acceptance, "is_admin_user", lambda user_id: user_id == 7)
    monkeypatch.setattr(acceptance.rollout, "admin_pilot_enabled", lambda: True)
    monkeypatch.setattr(acceptance.rollout, "rollout_mode", lambda: "live")
    monkeypatch.setattr(acceptance.rollout, "dry_run_enabled", lambda user_id: False)
    monkeypatch.setattr(acceptance.rollout, "live_execution_allowed", lambda user_id: True)
    monkeypatch.setattr(acceptance.rollout, "supervisor_allowed", lambda: True)
    with pytest.raises(SoftwareFactoryError) as exc:
        acceptance._require_runtime()
    assert exc.value.code == "velia_factory_dry_run_acceptance_mode_required"


def test_happy_path_auto_approves_only_safe_scope_and_never_changes_missions(monkeypatch):
    _wire_happy(monkeypatch)
    result = acceptance.run_acceptance()
    assert result["status"] == "passed"
    assert result["passed"] is True
    assert result["initial_clarification_reasons"] == ["write_scope_required"]
    assert result["safe_scope_auto_approved"] is True
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert result["final_state"] == "planning"
    assert result["team_plan_task_count"] == 1
    assert "architect" in result["team_roles"]
    assert result["autopilot_missions_unchanged"] is True
    assert result["repository_write_performed"] is False
    assert result["autopilot_task_dispatched"] is False
    assert result["merge_performed"] is False
    assert result["deployment_triggered"] is False


def test_unexpected_blocking_clarification_stops_without_advance(monkeypatch):
    _wire_happy(monkeypatch, clarification_reasons=["objective_required"])
    called = {"advance": False}

    def forbidden_advance(*args, **kwargs):
        called["advance"] = True
        raise AssertionError("advance must not run")

    monkeypatch.setattr(acceptance.factory, "advance_run", forbidden_advance)
    result = acceptance.run_acceptance()
    assert result["status"] == "blocked"
    assert result["blocker_code"] == "velia_factory_dry_run_acceptance_unexpected_clarification"
    assert called["advance"] is False


def test_missing_safe_scope_blocks_before_factory_run(monkeypatch):
    _wire_happy(monkeypatch)
    monkeypatch.setattr(acceptance.autonomy, "recommend_write_scope", lambda project: [])
    called = {"create": False}

    def forbidden_create(*args, **kwargs):
        called["create"] = True
        raise AssertionError("factory run must not be created")

    monkeypatch.setattr(acceptance.factory, "create_run", forbidden_create)
    result = acceptance.run_acceptance()
    assert result["status"] == "blocked"
    assert result["blocker_code"] == "velia_factory_dry_run_acceptance_safe_scope_missing"
    assert called["create"] is False


def test_changed_autopilot_missions_fail_acceptance(monkeypatch):
    _wire_happy(monkeypatch, mission_snapshots=[["mission-old"], ["mission-new", "mission-old"]])
    result = acceptance.run_acceptance()
    assert result["status"] == "failed"
    assert result["passed"] is False
    assert result["autopilot_missions_unchanged"] is False
    assert "autopilot_missions_changed" in result["failure_reasons"]


def test_existing_probe_is_reused_without_new_factory_run(monkeypatch):
    monkeypatch.setattr(
        acceptance,
        "_require_runtime",
        lambda: (7, "Acme/shop", "Хочу интернет-магазин цветов", "a" * 40),
    )
    monkeypatch.setattr(
        acceptance,
        "_existing",
        lambda user_id, fingerprint: {"status": "passed", "passed": True, "run_id": "old-run"},
    )
    monkeypatch.setattr(
        acceptance.factory,
        "create_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not create a second run")),
    )
    result = acceptance.run_acceptance()
    assert result["status"] == "passed"
    assert result["reused"] is True
    assert result["run_id"] == "old-run"
