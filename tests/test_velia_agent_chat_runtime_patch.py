from types import SimpleNamespace

from services import velia_agent_chat_runtime_patch as patch
from services import velia_agent_chat_planner_service as planner


def _chat_module():
    calls = []

    def original(prompt, *, user_id, conversation_id, request_id=None):
        calls.append((prompt, user_id, conversation_id, request_id))
        return {"ok": True, "text": "ordinary", "reason": "ordinary"}

    return SimpleNamespace(generate_velia_chat_result=original), calls


def test_repository_request_falls_through_to_developer_layer(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "В репозитории GitHub создай файл README.md",
    )
    monkeypatch.setattr(planner, "active_chat_job", lambda *args: None)
    patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "ordinary"
    assert len(calls) == 1


def test_personal_action_request_returns_plan_without_execution(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Создай задачу проверить отчёт",
    )
    monkeypatch.setattr(planner, "active_chat_job", lambda *args: None)
    monkeypatch.setattr(
        planner,
        "create_chat_plan",
        lambda user_id, conversation_id, message: {
            "job_id": "job-1",
            "status": "awaiting_approval",
            "goal": message,
            "actions": [
                {
                    "action_id": "a1",
                    "tool_name": "velia.tasks.create_draft",
                    "arguments": {"title": "Проверить отчёт"},
                    "risk": "write_reversible",
                    "status": "awaiting_approval",
                    "requires_approval": True,
                }
            ],
            "usage": {"total_tokens": 100},
            "estimated_cost_usd": 0.01,
        },
    )
    patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "velia_agent_chat_plan_ready"
    assert result["agent_context"]["job_id"] == "job-1"
    assert "Выполняй план" in result["text"]
    assert calls == []


def test_active_agent_approval_executes_bound_job(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Выполняй план",
    )
    monkeypatch.setattr(
        planner,
        "active_chat_job",
        lambda *args: {"job_id": "job-1", "status": "awaiting_approval", "actions": []},
    )
    executed = []

    def fake_execute(user_id, conversation_id):
        executed.append((user_id, conversation_id))
        return {"job_id": "job-1", "status": "completed", "actions": []}

    monkeypatch.setattr(planner, "approve_and_execute", fake_execute)
    patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "velia_agent_chat_completed"
    assert executed == [(7, "conversation-1")]
    assert calls == []


def test_agent_disabled_preserves_ordinary_chat(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: False)
    patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "ordinary"
    assert len(calls) == 1
