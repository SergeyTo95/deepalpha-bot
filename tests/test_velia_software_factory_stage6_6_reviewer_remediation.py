from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from services import velia_software_factory_reviewer_remediation_service as remediation
from services import velia_software_factory_reviewer_runtime_patch as runtime
from services import velia_software_factory_reviewer_service as reviewer


_HEAD = "a" * 40
_NEW_HEAD = "b" * 40


def _report(*, code: str = "semantic_regression", path: str = "services/auth.py", status: str = "failed"):
    return {
        "status": status,
        "summary": "Reviewer found a concrete regression.",
        "findings": [
            {
                "severity": "high",
                "code": code,
                "message": "The new branch breaks the expected contract.",
                "path": path,
            }
        ],
        "acceptance": [],
        "evidence": {
            "reviewed_head_sha": _HEAD,
            "current_head_sha": _HEAD,
            "changed_files": 1,
        },
    }


def _run(result=None):
    return {
        "user_id": 7,
        "run_id": "run-1",
        "task_id": "task-1",
        "mission_id": "mission-1",
        "project_id": "project-1",
        "coding_job_id": "coding-1",
        "work_branch": "velia/fix-auth",
        "status": "executing",
        "result": dict(result or {}),
    }


def _ci_module(*, checks_state="pending", head=_NEW_HEAD):
    transitions = []
    project = {"repository_full_name": "owner/repo"}
    write_service = SimpleNamespace(
        branch_head=lambda _project, _branch: {"sha": head},
        commit_status=lambda _project, _sha: {
            "checks": [
                {
                    "name": "tests",
                    "status": "completed" if checks_state != "pending" else "in_progress",
                    "conclusion": "success" if checks_state == "success" else ("failure" if checks_state == "failure" else ""),
                }
            ]
        },
    )
    fake = SimpleNamespace()
    fake.write_service = write_service
    fake.ci_repair_enabled = lambda: True
    fake._project_and_mission = lambda _run: (
        project,
        {
            "allowed_paths": ["services", "tests"],
            "blocked_paths": [],
            "max_files": 8,
        },
    )
    fake._coding_job = lambda _run: {"goal": "Fix auth", "plan": {"steps": []}}
    fake._allowed_repair_files = lambda _job, _mission: ["services/auth.py", "tests/test_auth.py"]
    fake._execute_repair = lambda _run, _attempt, _failure: {
        "summary": "Fix reviewer regression.",
        "files": ["services/auth.py"],
        "commit_sha": _NEW_HEAD,
        "estimated_cost_usd": 0.01,
    }
    fake._utcnow = lambda: datetime(2026, 8, 25, 11, 0, 0)
    fake._env_int = lambda _name, default, _minimum, _maximum: default
    fake._json = lambda value: json.dumps(value, separators=(",", ":"), default=str)
    fake._checks_state = lambda _checks: checks_state

    def set_run_state(run, status, *, result=None, error_code="", finished=False):
        transitions.append(
            {
                "status": status,
                "result": result,
                "error_code": error_code,
                "finished": finished,
            }
        )

    fake._set_run_state = set_run_state
    return fake, transitions


def _autopilot():
    events = []
    fake = SimpleNamespace()
    fake._record_event = lambda run, event_type, payload=None: events.append((event_type, payload))
    return fake, events


def _scheduled_result(attempts=None):
    return {
        "pull_request": {"number": 17, "draft": True},
        "reviewer_remediation": {
            "owner": "reviewer_remediation",
            "phase": "waiting_ci",
            "attempt_number": len(attempts or []) or 1,
            "max_attempts": 2,
            "reviewed_head_sha": _HEAD,
            "head_sha": _NEW_HEAD,
            "started_at": "2026-08-25T10:59:30Z",
            "last_checked_at": None,
            "checks": [],
            "attempts": list(attempts or [{"attempt_number": 1}]),
        },
    }


def test_remediation_is_default_off_and_requires_existing_ci_repair(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_ENABLED", raising=False)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    ci, _ = _ci_module()
    assert remediation.remediation_enabled(ci) is False

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_ENABLED", "true")
    ci.ci_repair_enabled = lambda: False
    assert remediation.remediation_enabled(ci) is False

    ci.ci_repair_enabled = lambda: True
    assert remediation.remediation_enabled(ci) is True


def test_repairable_findings_are_exactly_inside_original_plan_scope():
    allowed = ["services/auth.py", "tests/test_auth.py"]
    assert remediation._repairable_findings(_report(), allowed)[0]["path"] == "services/auth.py"
    assert remediation._repairable_findings(_report(path="services/outside.py"), allowed) == []
    assert remediation._repairable_findings(
        _report(code="reviewer_path_outside_scope"), allowed
    ) == []
    assert remediation._repairable_findings(
        _report(code="reviewer_pr_not_draft"), allowed
    ) == []


def test_failed_review_schedules_bounded_exact_head_repair(monkeypatch):
    monkeypatch.setattr(remediation, "remediation_enabled", lambda _ci: True)
    monkeypatch.setattr(remediation, "remediation_max_attempts", lambda: 2)
    persisted = []
    monkeypatch.setattr(
        remediation,
        "_persist_active",
        lambda _ci, _run, result: persisted.append(dict(result)),
    )
    ci, _ = _ci_module(head=_HEAD)
    autopilot, events = _autopilot()
    execution = {
        "pull_request": {"number": 17, "draft": True},
        "estimated_cost_usd": 0.02,
        "reviewer": _report(),
    }
    decision = {"status": "failed", "report": _report(), "result": execution}

    scheduled = remediation.schedule_after_failed_review(
        autopilot,
        ci,
        _run(execution),
        execution,
        decision,
    )

    assert scheduled["status"] == "executing"
    assert scheduled["head_sha"] == _NEW_HEAD
    state = scheduled["result"]["reviewer_remediation"]
    assert state["owner"] == "reviewer_remediation"
    assert state["phase"] == "waiting_ci"
    assert state["attempt_number"] == 1
    assert state["reviewed_head_sha"] == _HEAD
    assert state["head_sha"] == _NEW_HEAD
    assert state["attempts"][0]["from_head_sha"] == _HEAD
    assert state["attempts"][0]["to_head_sha"] == _NEW_HEAD
    assert persisted
    assert "reviewer.remediation_committed" in [name for name, _ in events]


def test_blocked_review_and_safety_findings_never_schedule(monkeypatch):
    monkeypatch.setattr(remediation, "remediation_enabled", lambda _ci: True)
    ci, _ = _ci_module(head=_HEAD)
    autopilot, _ = _autopilot()
    execution = {"reviewer": _report()}

    assert remediation.schedule_after_failed_review(
        autopilot,
        ci,
        _run(execution),
        execution,
        {"status": "blocked", "report": _report(status="blocked"), "result": execution},
    ) is None

    unsafe = _report(code="reviewer_pr_not_open")
    assert remediation.schedule_after_failed_review(
        autopilot,
        ci,
        _run(execution),
        execution,
        {"status": "failed", "report": unsafe, "result": execution},
    ) is None


def test_attempt_limit_is_fail_closed(monkeypatch):
    monkeypatch.setattr(remediation, "remediation_enabled", lambda _ci: True)
    monkeypatch.setattr(remediation, "remediation_max_attempts", lambda: 2)
    ci, _ = _ci_module(head=_HEAD)
    autopilot, _ = _autopilot()
    existing = {
        "reviewer_remediation": {
            "owner": "reviewer_remediation",
            "attempts": [{"attempt_number": 1}, {"attempt_number": 2}],
        }
    }
    decision = {"status": "failed", "report": _report(), "result": existing}
    assert remediation.schedule_after_failed_review(
        autopilot,
        ci,
        _run(existing),
        existing,
        decision,
    ) is None


def test_pending_remediation_ci_keeps_active_without_reviewer(monkeypatch):
    monkeypatch.setattr(remediation, "remediation_enabled", lambda _ci: True)
    persisted = []
    monkeypatch.setattr(
        remediation,
        "_persist_active",
        lambda _ci, _run, result: persisted.append(dict(result)),
    )
    ci, transitions = _ci_module(checks_state="pending")
    autopilot, events = _autopilot()
    run = _run(_scheduled_result())

    result = remediation._process_run(autopilot, ci, run)

    assert result["status"] == "executing"
    assert persisted
    assert transitions == []
    assert "reviewer.remediation_ci_success" not in [name for name, _ in events]


def test_remediation_ci_failure_blocks_without_nested_auto_repair(monkeypatch):
    monkeypatch.setattr(remediation, "remediation_enabled", lambda _ci: True)
    ci, transitions = _ci_module(checks_state="failure")
    autopilot, events = _autopilot()
    run = _run(_scheduled_result())

    result = remediation._process_run(autopilot, ci, run)

    assert result["status"] == "blocked"
    assert result["error_code"] == "velia_factory_reviewer_remediation_ci_failed"
    assert [item["status"] for item in transitions] == ["blocked"]
    assert "reviewer.remediation_blocked" in [name for name, _ in events]


def test_remediation_ci_success_routes_back_through_reviewer_state_setter(monkeypatch):
    monkeypatch.setattr(remediation, "remediation_enabled", lambda _ci: True)
    ci, transitions = _ci_module(checks_state="success")
    autopilot, events = _autopilot()
    run = _run(_scheduled_result())

    result = remediation._process_run(autopilot, ci, run)

    assert result["status"] == "ready_for_review"
    assert [item["status"] for item in transitions] == ["ready_for_review"]
    assert transitions[0]["result"]["reviewer_remediation"]["phase"] == "reviewing"
    assert transitions[0]["result"]["checks"]["head_sha"] == _NEW_HEAD
    assert "reviewer.remediation_ci_success" in [name for name, _ in events]


def test_reviewer_runtime_rewrites_failed_final_ci_to_remediating_without_block(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    ci, raw_transitions = _ci_module(checks_state="success", head=_HEAD)
    ci.ci_watch_enabled = lambda: True
    autopilot = SimpleNamespace(
        get_task=lambda _uid, _tid: {
            "task_id": "task-1",
            "client_request_id": "factory:run-1:task-1",
        },
        get_mission=lambda _uid, _mid: {"project_id": "project-1"},
        project_service=SimpleNamespace(get_project=lambda _uid, _pid: {}),
    )
    decision = {
        "status": "failed",
        "report": _report(),
        "result": {"pull_request": {"number": 17}, "reviewer": _report()},
        "error_code": "velia_factory_reviewer_failed",
    }
    monkeypatch.setattr(runtime, "_review_decision", lambda *_args, **_kwargs: decision)
    scheduled = {
        "status": "executing",
        "result": _scheduled_result(),
        "attempt_number": 1,
        "head_sha": _NEW_HEAD,
    }
    monkeypatch.setattr(
        remediation,
        "schedule_after_failed_review",
        lambda *_args, **_kwargs: scheduled,
    )
    runtime._context().clear()

    runtime._review_ci_transition(
        autopilot,
        ci,
        ci._set_run_state,
        _run(),
        "ready_for_review",
        result={"pull_request": {"number": 17}},
        finished=True,
    )

    assert raw_transitions == []
    stored = runtime._context()["run-1"]
    assert stored["status"] == "remediating"
    rewritten = runtime._rewrite_result(
        {"run_id": "run-1", "status": "ready_for_review"},
        stored,
    )
    assert rewritten["status"] == "executing"
    assert rewritten["result"]["reviewer_remediation"]["head_sha"] == _NEW_HEAD
    runtime._context().clear()


def test_reviewer_pass_marks_remediation_completed():
    ci, _ = _ci_module()
    result = _scheduled_result()
    completed = remediation.mark_review_passed(
        ci,
        result,
        {
            "evidence": {
                "reviewed_head_sha": _NEW_HEAD,
                "current_head_sha": _NEW_HEAD,
            }
        },
    )
    assert completed["reviewer_remediation"]["phase"] == "completed"
    assert completed["reviewer_remediation"]["completed_head_sha"] == _NEW_HEAD


def test_stage6_6_adds_no_new_github_merge_deploy_or_direct_commit_primitives():
    from pathlib import Path

    source = Path(
        "services/velia_software_factory_reviewer_remediation_service.py"
    ).read_text(encoding="utf-8")
    assert "ci_module._execute_repair" in source
    for forbidden in (
        "merge_pull_request",
        "merge_exact_head",
        "create_deployment",
        "create_pull_request",
        "commit_operations(",
        "github_service._request(",
        "os.environ[",
        "os.putenv",
        "enqueue_task(",
    ):
        assert forbidden not in source, forbidden
