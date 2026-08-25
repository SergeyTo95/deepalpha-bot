from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_reviewer_runtime_patch as runtime
from services import velia_software_factory_reviewer_service as reviewer


def _task(request_id: str = "factory:run-1:task-1"):
    return {
        "task_id": "task-1",
        "client_request_id": request_id,
        "instruction": "Fix auth regression and keep the API contract stable.",
    }


def _mission():
    return {
        "mission_id": "mission-1",
        "project_id": "project-1",
        "base_branch": "main",
        "allowed_paths": ["services", "tests"],
        "blocked_paths": [],
        "max_steps": 4,
        "max_files": 8,
    }


def _execution_result(conclusion: str = ""):
    return {
        "coding_job_id": "coding-1",
        "work_branch": "velia/fix-auth",
        "pull_request": {
            "number": 17,
            "url": "https://example.invalid/pr/17",
            "draft": True,
        },
        "checks": {
            "total": 1,
            "checks": [
                {
                    "name": "tests",
                    "status": "completed" if conclusion else "in_progress",
                    "conclusion": conclusion,
                }
            ],
        },
        "estimated_cost_usd": 0.02,
        "steps": [],
    }


def _fake_autopilot(request_id: str = "factory:run-1:task-1"):
    transitions = []
    events = []
    fake = SimpleNamespace()
    fake.project_service = SimpleNamespace(
        get_project=lambda _user_id, _project_id: {
            "id": "project-1",
            "repository_full_name": "owner/repo",
        }
    )
    fake.get_task = lambda _user_id, _task_id: _task(request_id)
    fake.get_mission = lambda _user_id, _mission_id: _mission()

    def transition(run, status, **kwargs):
        transitions.append((str(status), dict(kwargs)))

    def record_event(run, event_type, payload=None):
        events.append((str(event_type), payload))

    fake._transition = transition
    fake._record_event = record_event

    def execute_claimed(run):
        result = _execution_result()
        fake._transition(
            run,
            "ready_for_review",
            result=result,
            work_branch=result["work_branch"],
            pull_request_number=17,
            pull_request_url=result["pull_request"]["url"],
            estimated_cost_usd=result["estimated_cost_usd"],
            finished=True,
        )
        fake._record_event(run, "draft_pr_ready", result["pull_request"])
        return {**dict(run), "status": "ready_for_review", "result": result}

    fake._execute_claimed = execute_claimed
    return fake, transitions, events


def _run():
    return {
        "user_id": 1,
        "run_id": "run-1",
        "task_id": "task-1",
        "mission_id": "mission-1",
    }


def test_failed_reviewer_never_persists_transient_ready_for_review(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot()
    monkeypatch.setattr(
        reviewer,
        "review_execution",
        lambda **_kwargs: {
            "status": "failed",
            "summary": "Regression found.",
            "findings": [
                {
                    "severity": "high",
                    "code": "regression",
                    "message": "Concrete defect.",
                    "path": "services/auth.py",
                }
            ],
            "acceptance": [],
            "evidence": {"changed_files": 1},
        },
    )

    assert runtime.install(fake) is True
    result = fake._execute_claimed(_run())

    assert result["status"] == "blocked"
    assert result["error_code"] == "velia_factory_reviewer_failed"
    assert [status for status, _kwargs in transitions] == ["blocked"]
    assert transitions[0][1]["result"]["reviewer"]["status"] == "failed"
    assert "draft_pr_ready" not in [event for event, _payload in events]
    assert "draft_pr_created_review_blocked" in [event for event, _payload in events]
    assert "reviewer.failed" in [event for event, _payload in events]


def test_passed_reviewer_atomically_persists_ready_with_evidence(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot()
    monkeypatch.setattr(
        reviewer,
        "review_execution",
        lambda **_kwargs: {
            "status": "passed",
            "summary": "Bounded change matches acceptance intent.",
            "findings": [],
            "acceptance": [],
            "evidence": {"changed_files": 1},
        },
    )

    assert runtime.install(fake) is True
    result = fake._execute_claimed(_run())

    assert result["status"] == "ready_for_review"
    assert [status for status, _kwargs in transitions] == ["ready_for_review"]
    assert transitions[0][1]["result"]["reviewer"]["status"] == "passed"
    assert result["result"]["reviewer"]["status"] == "passed"
    assert "draft_pr_ready" in [event for event, _payload in events]
    assert "reviewer.passed" in [event for event, _payload in events]


def test_non_factory_run_keeps_legacy_transition(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot("ordinary-autopilot-request")
    monkeypatch.setattr(
        reviewer,
        "review_execution",
        lambda **_kwargs: pytest.fail("reviewer must not run for ordinary Coding Autopilot"),
    )

    assert runtime.install(fake) is True
    result = fake._execute_claimed(_run())

    assert result["status"] == "ready_for_review"
    assert [status for status, _kwargs in transitions] == ["ready_for_review"]
    assert [event for event, _payload in events] == ["draft_pr_ready"]
    assert "reviewer" not in result["result"]


def test_commit_status_error_is_a_deterministic_reviewer_failure(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    execution = _execution_result("error")
    report = reviewer.review_execution(
        user_id=1,
        run_id="run-1",
        task=_task(),
        mission=_mission(),
        project={"repository_full_name": "owner/repo"},
        execution_result=execution,
        generator=lambda _prompt: pytest.fail("model must not run after deterministic CI error"),
        diff_loader=lambda *_args: {
            "base": "main",
            "head": "velia/fix-auth",
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
            "total_commits": 1,
            "files": [
                {
                    "path": "services/auth.py",
                    "previous_path": "",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                    "changes": 2,
                    "patch": "@@ -1 +1 @@",
                }
            ],
        },
    )

    assert "error" in reviewer._FAILING_CHECK_CONCLUSIONS
    assert report["status"] == "failed"
    assert any(item["code"] == "reviewer_ci_failure_observed" for item in report["findings"])
