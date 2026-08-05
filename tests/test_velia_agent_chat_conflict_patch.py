from types import SimpleNamespace

from services import velia_agent_chat_conflict_patch as patch
from services import velia_agent_chat_planner_service as planner
from services import velia_developer_coding_service as coding


def _chat_module():
    calls = []

    def original(prompt, *, user_id, conversation_id, request_id=None):
        calls.append((prompt, user_id, conversation_id, request_id))
        return {"ok": True, "reason": "inner"}

    return SimpleNamespace(generate_velia_chat_result=original), calls


def test_coding_plan_blocks_new_personal_agent_plan(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        patch.agent_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Создай задачу проверить отчёт",
    )
    monkeypatch.setattr(planner, "active_chat_job", lambda *args: None)
    monkeypatch.setattr(coding, "active_job", lambda *args: {"job_id": "coding-1"})
    patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "velia_agent_chat_coding_job_active"
    assert result["agent_context"]["conflict_blocked"] is True
    assert calls == []


def test_personal_agent_plan_blocks_repository_work(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        patch.agent_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "В репозитории GitHub исправь endpoint",
    )
    monkeypatch.setattr(planner, "active_chat_job", lambda *args: {"job_id": "agent-1"})
    monkeypatch.setattr(coding, "active_job", lambda *args: None)
    patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "velia_agent_chat_job_active"
    assert calls == []


def test_coding_approval_falls_through_when_no_personal_plan(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        patch.agent_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Выполняй план",
    )
    monkeypatch.setattr(planner, "active_chat_job", lambda *args: None)
    monkeypatch.setattr(coding, "active_job", lambda *args: {"job_id": "coding-1"})
    patch.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "inner"
    assert len(calls) == 1
