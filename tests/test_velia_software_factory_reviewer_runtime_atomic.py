from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_reviewer_runtime_patch as runtime
from services import velia_software_factory_reviewer_service as reviewer


_FINAL_HEAD = "b" * 40


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
        return {
            **dict(run),
            "status": "ready_for_review",
            "result": result,
        }

    fake._execute_claimed = execute_claimed
    return fake, transitions, events


def _fake_ci(*, enabled: bool, final_result=None):
    transitions = []
    fake = SimpleNamespace()
    fake.ci_watch_enabled = lambda: enabled

    def set_run_state(
        run,
        status,
        *,
        result=None,
        error_code="",
        finished=False,
    ):
        transitions.append(
            (
                str(status),
                {
                    "result": result,
                    "error_code": error_code,
                    "finished": finished,
                },
            )
        )

    fake._set_run_state = set_run_state

    def process_ci_once():
        if final_result is None:
            return None
        run = _run()
        fake._set_run_state(
            run,
            "ready_for_review",
            result=final_result,
            finished=True,
        )
        return {
            **run,
            "status": "ready_for_review",
            "result": final_result,
        }

    fake.process_ci_once = process_ci_once
    return fake, transitions


def _run():
    return {
        "user_id": 1,
        "run_id": "run-1",
        "task_id": "task-1",
        "mission_id": "mission-1",
    }


def _passed_report(head: str = ""):
    return {
        "status": "passed",
        "summary": "Bounded change matches acceptance intent.",
        "findings": [],
        "acceptance": [],
        "evidence": {
            "changed_files": 1,
            "reviewed_head_sha": head,
        },
    }


def _failed_report(head: str = ""):
    return {
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
        "evidence": {
            "changed_files": 1,
            "reviewed_head_sha": head,
        },
    }


def test_failed_reviewer_never_persists_transient_ready_for_review(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot()
    fake_ci, _ci_transitions = _fake_ci(enabled=False)
    monkeypatch.setattr(
        reviewer,
        "review_execution",
        lambda **_kwargs: _failed_report(),
    )

    assert runtime.install(fake, fake_ci) is True
    result = fake._execute_claimed(_run())

    assert result["status"] == "blocked"
    assert result["error_code"] == "velia_factory_reviewer_failed"
    assert [status for status, _kwargs in transitions] == ["blocked"]
    assert transitions[0][1]["result"]["reviewer"]["status"] == "failed"
    assert "draft_pr_ready" not in [event for event, _payload in events]
    assert "draft_pr_created_review_blocked" in [
        event for event, _payload in events
    ]
    assert "reviewer.failed" in [event for event, _payload in events]


def test_passed_reviewer_atomically_persists_ready_with_evidence(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot()
    fake_ci, _ci_transitions = _fake_ci(enabled=False)
    monkeypatch.setattr(
        reviewer,
        "review_execution",
        lambda **_kwargs: _passed_report(),
    )

    assert runtime.install(fake, fake_ci) is True
    result = fake._execute_claimed(_run())

    assert result["status"] == "ready_for_review"
    assert [status for status, _kwargs in transitions] == [
        "ready_for_review"
    ]
    assert transitions[0][1]["result"]["reviewer"]["status"] == "passed"
    assert result["result"]["reviewer"]["status"] == "passed"
    assert "draft_pr_ready" in [event for event, _payload in events]
    assert "reviewer.passed" in [event for event, _payload in events]


def test_non_factory_run_keeps_legacy_transition(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot(
        "ordinary-autopilot-request"
    )
    fake_ci, _ci_transitions = _fake_ci(enabled=False)
    monkeypatch.setattr(
        reviewer,
        "review_execution",
        lambda **_kwargs: pytest.fail(
            "reviewer must not run for ordinary Coding Autopilot"
        ),
    )

    assert runtime.install(fake, fake_ci) is True
    result = fake._execute_claimed(_run())

    assert result["status"] == "ready_for_review"
    assert [status for status, _kwargs in transitions] == [
        "ready_for_review"
    ]
    assert [event for event, _payload in events] == ["draft_pr_ready"]
    assert "reviewer" not in result["result"]


def test_ci_enabled_defers_review_until_final_ci_head(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot()
    final_result = _execution_result("success")
    final_result["ci"] = {
        "status": "success",
        "head_sha": _FINAL_HEAD,
        "attempt_number": 1,
    }
    final_result["repair_commit_sha"] = _FINAL_HEAD
    fake_ci, ci_transitions = _fake_ci(
        enabled=True,
        final_result=final_result,
    )
    reviewed = []

    def review_execution(**kwargs):
        reviewed.append(dict(kwargs["execution_result"]))
        return _passed_report(_FINAL_HEAD)

    monkeypatch.setattr(reviewer, "review_execution", review_execution)

    assert runtime.install(fake, fake_ci) is True

    initial = fake._execute_claimed(_run())
    assert initial["status"] == "ready_for_review"
    assert reviewed == []
    assert [status for status, _kwargs in transitions] == [
        "ready_for_review"
    ]

    final = fake_ci.process_ci_once()
    assert len(reviewed) == 1
    assert reviewed[0]["ci"]["head_sha"] == _FINAL_HEAD
    assert final["status"] == "ready_for_review"
    assert final["result"]["reviewer"]["status"] == "passed"
    assert [status for status, _kwargs in ci_transitions] == [
        "ready_for_review"
    ]
    assert ci_transitions[0][1]["result"]["reviewer"]["evidence"][
        "reviewed_head_sha"
    ] == _FINAL_HEAD
    assert "reviewer.passed" in [event for event, _payload in events]


def test_ci_final_reviewer_block_is_atomic_on_repaired_head(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED", "true")
    fake, transitions, events = _fake_autopilot()
    final_result = _execution_result("success")
    final_result["ci"] = {
        "status": "success",
        "head_sha": _FINAL_HEAD,
        "attempt_number": 2,
    }
    fake_ci, ci_transitions = _fake_ci(
        enabled=True,
        final_result=final_result,
    )
    reviewed = []

    def review_execution(**kwargs):
        reviewed.append(dict(kwargs["execution_result"]))
        return _failed_report(_FINAL_HEAD)

    monkeypatch.setattr(reviewer, "review_execution", review_execution)

    assert runtime.install(fake, fake_ci) is True
    initial = fake._execute_claimed(_run())
    assert initial["status"] == "ready_for_review"
    assert reviewed == []

    final = fake_ci.process_ci_once()
    assert len(reviewed) == 1
    assert reviewed[0]["ci"]["head_sha"] == _FINAL_HEAD
    assert final["status"] == "blocked"
    assert final["error_code"] == "velia_factory_reviewer_failed"
    assert [status for status, _kwargs in ci_transitions] == ["blocked"]
    assert ci_transitions[0][1]["result"]["reviewer"]["status"] == "failed"
    assert "ready_for_review" not in [
        status for status, _kwargs in ci_transitions
    ]
    assert "reviewer.failed" in [event for event, _payload in events]


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
        generator=lambda _prompt: pytest.fail(
            "model must not run after deterministic CI error"
        ),
        diff_loader=lambda *_args: {
            "base": "main",
            "head": "a" * 40,
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
        pull_request_loader=lambda *_args: {
            "number": 17,
            "draft": True,
            "state": "open",
            "merged": False,
            "head_sha": "a" * 40,
            "url": "https://example.invalid/pr/17",
        },
    )

    assert "error" in reviewer._FAILING_CHECK_CONCLUSIONS
    assert report["status"] == "failed"
    assert any(
        item["code"] == "reviewer_ci_failure_observed"
        for item in report["findings"]
    )
