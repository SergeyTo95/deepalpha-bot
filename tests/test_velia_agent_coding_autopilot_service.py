from pathlib import Path

from services import velia_agent_coding_autopilot_service as autopilot


def _run():
    return {
        "run_id": "run-1",
        "task_id": "task-1",
        "mission_id": "mission-1",
        "user_id": 7,
        "project_id": "project-1",
        "conversation_id": "autopilot:run-1",
    }


def _mission():
    return {
        "mission_id": "mission-1",
        "project_id": "project-1",
        "status": "active",
        "base_branch": "develop",
        "allowed_paths": ["app/src/main", "app/src/test"],
        "blocked_paths": [".github", ".env", "auth", "billing", "migrations"],
        "max_steps": 4,
        "max_files": 8,
    }


def test_worker_is_disabled_until_both_flags_are_enabled(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_AUTOPILOT_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED", raising=False)
    monkeypatch.setattr(
        autopilot,
        "_claim_next_task",
        lambda worker_id: (_ for _ in ()).throw(AssertionError("must not claim")),
    )
    assert autopilot.run_autopilot_once() == []


def test_worker_claims_bounded_tasks_and_runs_existing_coding_agent(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED", "true")
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_MAX_RUNS_PER_TICK", "1")
    monkeypatch.setattr(autopilot.coding_service, "coding_enabled", lambda: True)
    claimed = [_run(), None]
    monkeypatch.setattr(autopilot, "_claim_next_task", lambda worker_id: claimed.pop(0))
    executed = []
    monkeypatch.setattr(
        autopilot,
        "_execute_claimed",
        lambda run: executed.append(run["run_id"]) or {"status": "ready_for_review"},
    )

    assert autopilot.run_autopilot_once() == [{"status": "ready_for_review"}]
    assert executed == ["run-1"]


def test_claimed_run_creates_only_a_draft_pr_and_stops_for_review(monkeypatch):
    run = _run()
    monkeypatch.setattr(
        autopilot,
        "get_task",
        lambda user_id, task_id: {
            "task_id": task_id,
            "instruction": "Improve the Agent card",
        },
    )
    monkeypatch.setattr(autopilot, "get_mission", lambda user_id, mission_id: _mission())
    monkeypatch.setattr(
        autopilot.project_service,
        "get_project",
        lambda user_id, project_id: {
            "id": project_id,
            "repository_full_name": "SergeyTo95/deepalpha-android",
            "selected_branch": "develop",
        },
    )
    transitions = []
    monkeypatch.setattr(
        autopilot,
        "_transition",
        lambda run, status, **kwargs: transitions.append((status, kwargs)),
    )
    monkeypatch.setattr(autopilot, "_record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autopilot.coding_service,
        "plan_job",
        lambda **kwargs: {
            "job_id": "coding-1",
            "plan": {
                "steps": [
                    {
                        "files": [
                            "app/src/main/java/com/velia/Card.kt",
                            "app/src/test/java/com/velia/CardTest.kt",
                        ]
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        autopilot.coding_service,
        "execute_job",
        lambda **kwargs: {
            "status": "completed",
            "work_branch": "velia/agent-card",
            "pull_request": {
                "number": 42,
                "url": "https://github.com/SergeyTo95/deepalpha-android/pull/42",
                "draft": True,
            },
            "checks": {"total": 1, "checks": [{"name": "Android CI", "status": "queued"}]},
            "estimated_cost_usd": 0.12,
            "steps": [{"commit_sha": "abc123"}],
        },
    )

    result = autopilot._execute_claimed(run)

    assert result["status"] == "ready_for_review"
    assert [item[0] for item in transitions] == ["planning", "planning", "executing", "ready_for_review"]
    final = transitions[-1][1]
    assert final["pull_request_number"] == 42
    assert final["pull_request_url"].endswith("/pull/42")
    assert final["result"]["pull_request"]["draft"] is True


def test_denied_plan_is_cancelled_before_any_write(monkeypatch):
    run = _run()
    monkeypatch.setattr(
        autopilot,
        "get_task",
        lambda user_id, task_id: {"task_id": task_id, "instruction": "Change workflow"},
    )
    monkeypatch.setattr(autopilot, "get_mission", lambda user_id, mission_id: _mission())
    monkeypatch.setattr(
        autopilot.project_service,
        "get_project",
        lambda user_id, project_id: {"id": project_id, "selected_branch": "develop"},
    )
    transitions = []
    monkeypatch.setattr(
        autopilot,
        "_transition",
        lambda run, status, **kwargs: transitions.append((status, kwargs)),
    )
    monkeypatch.setattr(autopilot, "_record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        autopilot.coding_service,
        "plan_job",
        lambda **kwargs: {
            "job_id": "coding-1",
            "plan": {"steps": [{"files": [".github/workflows/android-ci.yml"]}]},
        },
    )
    cancelled = []
    monkeypatch.setattr(
        autopilot.coding_service,
        "cancel_active_job",
        lambda user_id, conversation_id: cancelled.append((user_id, conversation_id)) or True,
    )
    monkeypatch.setattr(
        autopilot.coding_service,
        "execute_job",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("write must not start")),
    )

    result = autopilot._execute_claimed(run)

    assert result["status"] == "blocked"
    assert result["error_code"] == "velia_coding_autopilot_plan_path_denied"
    assert cancelled == [(7, "autopilot:run-1")]
    assert transitions[-1][0] == "blocked"


def test_worker_source_has_lock_lease_and_no_merge_or_deploy_capability():
    source = Path("services/velia_agent_coding_autopilot_service.py").read_text(encoding="utf-8")

    assert "FOR UPDATE OF t SKIP LOCKED" in source
    assert "ux_velia_autopilot_run_project_active" in source
    assert "claimed_until" in source
    assert "velia_coding_autopilot_lease_expired_after_write_started" in source
    assert "coding_service.plan_job" in source
    assert "coding_service.execute_job" in source
    assert "create_draft_pull_request" not in source
    assert "merge_pull_request" not in source
    assert "deploy" not in source.casefold()
