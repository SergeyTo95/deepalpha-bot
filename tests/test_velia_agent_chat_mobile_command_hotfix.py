from types import SimpleNamespace

from services import velia_agent_chat_conflict_patch as conflict
from services import velia_agent_chat_planner_service as planner


def _chat_module():
    calls = []

    def original(prompt, *, user_id, conversation_id, request_id=None):
        calls.append((prompt, user_id, conversation_id, request_id))
        return {"ok": True, "reason": "ordinary"}

    return SimpleNamespace(generate_velia_chat_result=original), calls


def test_mobile_approval_aliases_are_explicit_and_bounded():
    conflict._install_mobile_approval_aliases()

    for value in (
        "ВЫПОЛНИ",
        "выполни план",
        "выполнить план",
        "запускай план",
        "подтверждаю",
        "Execute",
        "Run the plan",
        "Planı uygula",
    ):
        assert planner.is_approval(value) is True

    for value in ("да", "ок", "продолжай", "выполни домашнюю работу"):
        assert planner.is_approval(value) is False


def test_new_agent_request_reuses_active_plan_instead_of_returning_error(monkeypatch):
    module, calls = _chat_module()
    active = {
        "job_id": "job-1",
        "status": "awaiting_approval",
        "goal": "Создать задачу",
        "actions": [
            {
                "action_id": "action-1",
                "tool_name": "velia.tasks.create_draft",
                "risk": "write_reversible",
                "status": "awaiting_approval",
                "requires_approval": True,
                "arguments": {"title": "UX smoke"},
            }
        ],
    }
    monkeypatch.setattr(conflict.agent_planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        conflict.agent_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Покажи мои задачи",
    )
    monkeypatch.setattr(conflict.agent_planner, "active_chat_job", lambda *args: active)
    monkeypatch.setattr(conflict.coding_service, "active_job", lambda *args: None)
    captured = {}

    def fake_result(text, request_id, **kwargs):
        captured.update({"text": text, "request_id": request_id, **kwargs})
        return {"ok": True, "reason": kwargs["reason"], "job": kwargs.get("job")}

    monkeypatch.setattr(conflict.agent_patch, "_result", fake_result)
    conflict.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "velia_agent_chat_plan_ready"
    assert result["job"]["planner_summary"].startswith("Сначала заверши")
    assert result["job"]["actions"][0]["tool_name"] == "velia.tasks.create_draft"
    assert calls == []


def test_control_command_without_any_active_plan_never_reaches_ordinary_llm(monkeypatch):
    module, calls = _chat_module()
    monkeypatch.setattr(conflict.agent_planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        conflict.agent_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "ВЫПОЛНИ",
    )
    monkeypatch.setattr(conflict.agent_planner, "active_chat_job", lambda *args: None)
    monkeypatch.setattr(conflict.coding_service, "active_job", lambda *args: None)
    captured = {}

    def fake_result(text, request_id, **kwargs):
        captured.update({"text": text, "request_id": request_id, **kwargs})
        return {"ok": True, "reason": kwargs["reason"]}

    monkeypatch.setattr(conflict.agent_patch, "_result", fake_result)
    conflict.install(module)

    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["reason"] == "velia_agent_chat_plan_missing"
    assert "Активного плана" in captured["text"]
    assert calls == []
