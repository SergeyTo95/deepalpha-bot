import pytest

from services import velia_agent_job_service as jobs
from services import velia_agent_runtime_service as runtime
from services import velia_agent_tool_registry_service as registry


def _reset_registry():
    registry.clear_registry_for_tests()
    runtime._BUILTINS_READY = False


def test_plan_marks_write_action_for_approval(monkeypatch):
    _reset_registry()
    captured = {}
    monkeypatch.setattr(jobs, "create_job", lambda user_id, goal, mode, actions: captured.update({"actions": actions}) or {"job_id": "job-1"})
    result = runtime.plan_job(7, "Create a task", [{"tool_name": "velia.tasks.create_draft", "arguments": {"title": "Review PR"}}])
    assert result["job_id"] == "job-1"
    assert captured["actions"][0].requires_approval is True
    assert captured["actions"][0].status.value == "awaiting_approval"


def test_plan_mode_denies_writes():
    _reset_registry()
    with pytest.raises(runtime.AgentRuntimeError) as exc:
        runtime.plan_job(7, "Create a task", [{"tool_name": "velia.tasks.create_draft", "arguments": {"title": "Review PR"}}], mode="plan")
    assert exc.value.code == "velia_agent_action_denied"


def test_execute_claims_job_then_runs_actions_in_order(monkeypatch):
    _reset_registry()
    job = {
        "job_id": "job-1",
        "actions": [
            {"action_id": "a1", "tool_name": "velia.echo", "arguments": {"text": "hi"}, "status": "proposed"},
            {"action_id": "a2", "tool_name": "velia.tasks.create_draft", "arguments": {"title": "Ship"}, "status": "approved"},
        ],
    }
    claims = []
    monkeypatch.setattr(jobs, "claim_job_for_execution", lambda user_id, job_id: claims.append((user_id, job_id)))
    monkeypatch.setattr(jobs, "get_job", lambda user_id, job_id: job if not all(a["status"] == "completed" for a in job["actions"]) else {**job, "status": "completed"})
    monkeypatch.setattr(jobs, "set_job_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobs, "audit", lambda *args, **kwargs: None)
    updates = []

    def update_action(user_id, job_id, action_id, status, **kwargs):
        updates.append((action_id, status.value, kwargs.get("result"), kwargs.get("error_code")))
        if status.value == "completed":
            next(item for item in job["actions"] if item["action_id"] == action_id)["status"] = "completed"

    monkeypatch.setattr(jobs, "update_action", update_action)
    monkeypatch.setattr(jobs, "create_task_draft", lambda user_id, title, notes="": {"draft_id": "d1", "title": title})
    result = runtime.execute_job(7, "job-1")
    assert claims == [(7, "job-1")]
    assert result["status"] == "completed"
    assert [item[0] for item in updates if item[1] == "completed"] == ["a1", "a2"]


def test_duplicate_run_is_rejected_before_handlers(monkeypatch):
    _reset_registry()
    called = []

    def reject_claim(user_id, job_id):
        raise jobs.AgentJobError("velia_agent_job_not_executable", status=409, detail="running")

    monkeypatch.setattr(jobs, "claim_job_for_execution", reject_claim)
    monkeypatch.setattr(jobs, "get_job", lambda *args, **kwargs: called.append("get") or {})
    with pytest.raises(jobs.AgentJobError) as exc:
        runtime.execute_job(7, "job-1")
    assert exc.value.code == "velia_agent_job_not_executable"
    assert called == []


def test_execution_failure_persists_failed_action_and_job(monkeypatch):
    _reset_registry()
    job = {
        "job_id": "job-1",
        "actions": [
            {"action_id": "a1", "tool_name": "velia.tasks.create_draft", "arguments": {"title": "Ship"}, "status": "approved"},
        ],
    }
    monkeypatch.setattr(jobs, "claim_job_for_execution", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobs, "get_job", lambda *args, **kwargs: job)
    monkeypatch.setattr(jobs, "audit", lambda *args, **kwargs: None)
    statuses = []
    monkeypatch.setattr(jobs, "set_job_status", lambda user_id, job_id, status: statuses.append(status.value))
    updates = []
    monkeypatch.setattr(jobs, "update_action", lambda user_id, job_id, action_id, status, **kwargs: updates.append((status.value, kwargs.get("error_code"))))
    monkeypatch.setattr(jobs, "create_task_draft", lambda *args, **kwargs: (_ for _ in ()).throw(jobs.AgentJobError("draft_failed")))

    with pytest.raises(jobs.AgentJobError):
        runtime.execute_job(7, "job-1")
    assert updates[-1] == ("failed", "draft_failed")
    assert statuses[-1] == "failed"
