from __future__ import annotations

from datetime import datetime

import pytest

from services import velia_agent_coding_autopilot_ci_service as ci


def _run(**overrides):
    value = {
        "run_id": "run-1",
        "task_id": "task-1",
        "mission_id": "mission-1",
        "user_id": 7,
        "project_id": "project-1",
        "coding_job_id": "job-1",
        "work_branch": "velia/fix-ci",
        "result": {"pull_request": {"number": 12, "url": "https://github.com/a/b/pull/12"}},
    }
    value.update(overrides)
    return value


def _attempt(**overrides):
    value = {
        "attempt_id": "attempt-1",
        "run_id": "run-1",
        "user_id": 7,
        "attempt_number": 0,
        "head_sha": "a" * 40,
        "status": "waiting",
        "first_seen_at": datetime.utcnow().isoformat() + "Z",
    }
    value.update(overrides)
    return value


def test_checks_state_requires_all_exact_head_checks_to_finish():
    assert ci._checks_state([]) == "missing"
    assert ci._checks_state([
        {"name": "tests", "status": "in_progress", "conclusion": ""}
    ]) == "pending"
    assert ci._checks_state([
        {"name": "tests", "status": "completed", "conclusion": "success"},
        {"name": "railway", "status": "completed", "conclusion": "success"},
    ]) == "success"
    assert ci._checks_state([
        {"name": "tests", "status": "completed", "conclusion": "failure"},
        {"name": "railway", "status": "completed", "conclusion": "success"},
    ]) == "failure"


def test_register_ci_watch_uses_current_work_branch_head(monkeypatch):
    run = _run()
    captured = {}
    monkeypatch.setattr(ci, "_project_and_mission", lambda value: ({"id": "project-1"}, {}))
    monkeypatch.setattr(
        ci.write_service,
        "branch_head",
        lambda project, branch: {"branch": branch, "sha": "b" * 40},
    )
    monkeypatch.setattr(
        ci,
        "_create_attempt",
        lambda value, head_sha, attempt_number: {
            "attempt_id": "attempt-1",
            "head_sha": head_sha,
            "attempt_number": attempt_number,
        },
    )
    monkeypatch.setattr(
        ci,
        "_set_run_state",
        lambda value, status, **kwargs: captured.update(
            {"status": status, "result": kwargs.get("result")}
        ),
    )
    monkeypatch.setattr(ci.autopilot, "_record_event", lambda *args, **kwargs: None)

    result = ci._register_ci_watch(
        run,
        {
            "status": "ready_for_review",
            "work_branch": "velia/fix-ci",
            "result": {"pull_request": {"number": 12}},
        },
    )

    assert result["status"] == "waiting_ci"
    assert result["result"]["ci"]["head_sha"] == "b" * 40
    assert captured["status"] == "waiting_ci"
    assert captured["result"]["ci"]["max_repairs"] == 2


def test_pending_ci_keeps_run_waiting_and_does_not_claim_new_work(monkeypatch):
    run = _run()
    attempt = _attempt()
    states = []
    monkeypatch.setattr(ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(ci, "_claim_ci_run", lambda: run)
    monkeypatch.setattr(ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(ci, "_project_and_mission", lambda value: ({"id": "project-1"}, {}))
    monkeypatch.setattr(ci.write_service, "branch_head", lambda *args: {"sha": "a" * 40})
    monkeypatch.setattr(
        ci.write_service,
        "commit_status",
        lambda *args: {
            "checks": [{"name": "tests", "status": "in_progress", "conclusion": ""}]
        },
    )
    monkeypatch.setattr(ci, "_set_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ci,
        "_set_run_state",
        lambda value, status, **kwargs: states.append((status, kwargs)),
    )

    result = ci.process_ci_once()

    assert result["status"] == "waiting_ci"
    assert states[-1][0] == "waiting_ci"
    assert states[-1][1]["result"]["ci"]["status"] == "pending"


def test_successful_ci_moves_run_to_ready_for_review(monkeypatch):
    run = _run()
    attempt = _attempt()
    states = []
    monkeypatch.setattr(ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(ci, "_claim_ci_run", lambda: run)
    monkeypatch.setattr(ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(ci, "_project_and_mission", lambda value: ({"id": "project-1"}, {}))
    monkeypatch.setattr(ci.write_service, "branch_head", lambda *args: {"sha": "a" * 40})
    monkeypatch.setattr(
        ci.write_service,
        "commit_status",
        lambda *args: {
            "checks": [
                {"name": "tests", "status": "completed", "conclusion": "success"},
                {"name": "railway", "status": "completed", "conclusion": "success"},
            ]
        },
    )
    monkeypatch.setattr(ci, "_set_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ci,
        "_set_run_state",
        lambda value, status, **kwargs: states.append((status, kwargs)),
    )
    monkeypatch.setattr(ci.autopilot, "_record_event", lambda *args, **kwargs: None)

    result = ci.process_ci_once()

    assert result["status"] == "ready_for_review"
    assert states[-1][0] == "ready_for_review"
    assert states[-1][1]["finished"] is True
    assert states[-1][1]["result"]["ci"]["status"] == "success"


def test_infrastructure_failure_never_changes_product_code(monkeypatch):
    run = _run()
    attempt = _attempt()
    states = []
    repair_calls = []
    monkeypatch.setattr(ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(ci, "ci_repair_enabled", lambda: True)
    monkeypatch.setattr(ci, "_claim_ci_run", lambda: run)
    monkeypatch.setattr(ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(ci, "_project_and_mission", lambda value: ({"id": "project-1"}, {}))
    monkeypatch.setattr(ci.write_service, "branch_head", lambda *args: {"sha": "a" * 40})
    monkeypatch.setattr(
        ci.write_service,
        "commit_status",
        lambda *args: {
            "checks": [{"name": "tests", "status": "completed", "conclusion": "failure"}]
        },
    )
    monkeypatch.setattr(
        ci,
        "_failure_details",
        lambda *args: {
            "head_sha": "a" * 40,
            "checks": [],
            "failures": [{"name": "tests", "summary": "runner network timeout"}],
            "repairable": False,
            "infrastructure": True,
        },
    )
    monkeypatch.setattr(ci, "_execute_repair", lambda *args: repair_calls.append(True))
    monkeypatch.setattr(ci, "_set_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ci,
        "_set_run_state",
        lambda value, status, **kwargs: states.append((status, kwargs)),
    )
    monkeypatch.setattr(ci.autopilot, "_record_event", lambda *args, **kwargs: None)

    result = ci.process_ci_once()

    assert result["status"] == "blocked"
    assert result["error_code"] == "velia_coding_autopilot_ci_infrastructure_failure"
    assert repair_calls == []
    assert states[-1][0] == "blocked"


def test_repair_commit_stays_on_existing_branch_and_creates_next_attempt(monkeypatch):
    run = _run()
    attempt = _attempt()
    states = []
    attempts = []
    monkeypatch.setattr(ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(ci, "ci_repair_enabled", lambda: True)
    monkeypatch.setattr(ci, "_claim_ci_run", lambda: run)
    monkeypatch.setattr(ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(ci, "_project_and_mission", lambda value: ({"id": "project-1"}, {}))
    monkeypatch.setattr(ci.write_service, "branch_head", lambda *args: {"sha": "a" * 40})
    monkeypatch.setattr(
        ci.write_service,
        "commit_status",
        lambda *args: {
            "checks": [{"name": "tests", "status": "completed", "conclusion": "failure"}]
        },
    )
    failure = {
        "head_sha": "a" * 40,
        "checks": [],
        "failures": [{"source": "check_run", "name": "tests", "summary": "assertion failed"}],
        "repairable": True,
        "infrastructure": False,
    }
    monkeypatch.setattr(ci, "_failure_details", lambda *args: failure)
    monkeypatch.setattr(
        ci,
        "_execute_repair",
        lambda *args: {
            "summary": "Fix assertion",
            "files": ["services/example.py"],
            "commit_sha": "b" * 40,
            "estimated_cost_usd": 0.02,
        },
    )
    monkeypatch.setattr(
        ci,
        "_create_attempt",
        lambda value, head_sha, attempt_number: attempts.append(
            (head_sha, attempt_number)
        ) or {"attempt_number": attempt_number, "head_sha": head_sha},
    )
    monkeypatch.setattr(ci, "_set_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ci,
        "_set_run_state",
        lambda value, status, **kwargs: states.append((status, kwargs)),
    )
    monkeypatch.setattr(ci.autopilot, "_record_event", lambda *args, **kwargs: None)

    result = ci.process_ci_once()

    assert result["status"] == "waiting_ci"
    assert attempts == [("b" * 40, 1)]
    assert states[-1][0] == "waiting_ci"
    assert states[-1][1]["result"]["repairs"][0]["commit_sha"] == "b" * 40


def test_repair_exhaustion_blocks_third_code_change(monkeypatch):
    run = _run()
    attempt = _attempt(attempt_number=2)
    repair_calls = []
    monkeypatch.setattr(ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(ci, "ci_repair_enabled", lambda: True)
    monkeypatch.setattr(ci, "_claim_ci_run", lambda: run)
    monkeypatch.setattr(ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(ci, "_project_and_mission", lambda value: ({"id": "project-1"}, {}))
    monkeypatch.setattr(ci.write_service, "branch_head", lambda *args: {"sha": "a" * 40})
    monkeypatch.setattr(
        ci.write_service,
        "commit_status",
        lambda *args: {
            "checks": [{"name": "tests", "status": "completed", "conclusion": "failure"}]
        },
    )
    monkeypatch.setattr(
        ci,
        "_failure_details",
        lambda *args: {
            "head_sha": "a" * 40,
            "checks": [],
            "failures": [{"source": "check_run", "name": "tests", "summary": "failed"}],
            "repairable": True,
            "infrastructure": False,
        },
    )
    monkeypatch.setattr(ci, "_execute_repair", lambda *args: repair_calls.append(True))
    monkeypatch.setattr(ci, "_set_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(ci, "_set_run_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(ci.autopilot, "_record_event", lambda *args, **kwargs: None)

    result = ci.process_ci_once()

    assert result["status"] == "blocked"
    assert result["error_code"] == "velia_coding_autopilot_ci_repairs_exhausted"
    assert repair_calls == []


def test_allowed_repair_files_are_only_original_approved_plan(monkeypatch):
    mission = {
        "allowed_paths": ["services/"],
        "blocked_paths": [],
        "max_steps": 2,
        "max_files": 3,
    }
    job = {
        "plan": {
            "steps": [
                {"files": ["services/a.py", "services/b.py"]},
                {"files": ["services/b.py", "services/c.py"]},
            ]
        }
    }
    monkeypatch.setattr(ci.policy_service, "validate_plan", lambda *args, **kwargs: {})

    assert ci._allowed_repair_files(job, mission) == [
        "services/a.py",
        "services/b.py",
        "services/c.py",
    ]


def test_branch_head_drift_blocks_repair_before_model_or_commit(monkeypatch):
    run = _run()
    attempt = _attempt()
    monkeypatch.setattr(ci, "_project_and_mission", lambda value: ({"id": "project-1"}, {"max_files": 1}))
    monkeypatch.setattr(ci, "_coding_job", lambda value: {"goal": "fix", "plan": {"steps": [{"files": ["services/a.py"]}]}})
    monkeypatch.setattr(ci, "_allowed_repair_files", lambda *args: ["services/a.py"])
    monkeypatch.setattr(ci.write_service, "branch_head", lambda *args: {"sha": "c" * 40})

    with pytest.raises(ci.CodingAutopilotCIError) as exc:
        ci._execute_repair(run, attempt, {"failures": []})

    assert exc.value.code == "velia_coding_autopilot_branch_head_changed"
