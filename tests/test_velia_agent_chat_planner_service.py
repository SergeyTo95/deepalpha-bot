import pytest

from services import velia_agent_chat_planner_service as planner
from services import velia_agent_runtime_service as runtime


def test_agent_intent_requires_action_and_supported_scope():
    assert planner.is_agent_request("Создай задачу проверить отчёт") is True
    assert planner.is_agent_request("Покажи мои задачи") is True
    assert planner.is_agent_request("Добавь встречу в календарь") is True
    assert planner.is_agent_request("Как создать встречу в календаре?") is False
    assert planner.is_agent_request("Расскажи про календарь") is False
    assert planner.is_approval("Выполняй план") is True
    assert planner.is_cancel("Отмени план") is True
    assert planner.is_status("Статус плана") is True


def test_normalize_plan_rejects_unknown_tools_and_too_many_actions(monkeypatch):
    with pytest.raises(planner.AgentChatError) as exc:
        planner._normalize_plan(
            {"actions": [{"tool_name": "unknown.tool", "arguments": {}}]},
            {"velia.tasks.list"},
        )
    assert exc.value.code == "velia_agent_chat_tool_unavailable"

    monkeypatch.setenv("VELIA_AGENT_CHAT_MAX_ACTIONS", "1")
    with pytest.raises(planner.AgentChatError) as exc:
        planner._normalize_plan(
            {
                "actions": [
                    {"tool_name": "velia.tasks.list", "arguments": {}},
                    {"tool_name": "velia.tasks.list", "arguments": {}},
                ]
            },
            {"velia.tasks.list"},
        )
    assert exc.value.code == "velia_agent_chat_actions_too_many"


def test_create_chat_plan_persists_only_server_validated_actions(monkeypatch):
    monkeypatch.setattr(planner, "active_chat_job", lambda user_id, conversation_id: None)
    monkeypatch.setattr(
        planner,
        "_model_plan",
        lambda user_id, message: {
            "summary": "Создать черновик задачи",
            "actions": [
                {
                    "tool_name": "velia.tasks.create_draft",
                    "arguments": {"title": "Проверить отчёт"},
                }
            ],
            "suggestions": [],
            "usage": {"total_tokens": 100},
            "estimated_cost_usd": 0.01,
        },
    )
    captured = {}

    def fake_plan(user_id, goal, actions, mode="interactive"):
        captured.update(
            {"user_id": user_id, "goal": goal, "actions": actions, "mode": mode}
        )
        return {
            "job_id": "job-1",
            "status": "awaiting_approval",
            "goal": goal,
            "actions": [
                {
                    "action_id": "action-1",
                    "tool_name": "velia.tasks.create_draft",
                    "arguments": {"title": "Проверить отчёт"},
                    "risk": "write_reversible",
                    "status": "awaiting_approval",
                    "requires_approval": True,
                }
            ],
        }

    monkeypatch.setattr(runtime, "plan_job", fake_plan)
    bindings = []
    monkeypatch.setattr(
        planner,
        "_bind_job",
        lambda user_id, conversation_id, job_id: bindings.append(
            (user_id, conversation_id, job_id)
        ),
    )

    result = planner.create_chat_plan(
        7,
        "conversation-1",
        "Создай задачу проверить отчёт",
    )

    assert captured["mode"] == "interactive"
    assert captured["actions"] == [
        {
            "tool_name": "velia.tasks.create_draft",
            "arguments": {"title": "Проверить отчёт"},
        }
    ]
    assert bindings == [(7, "conversation-1", "job-1")]
    assert result["estimated_cost_usd"] == 0.01


def test_approval_approves_all_pending_actions_then_executes(monkeypatch):
    initial = {
        "job_id": "job-1",
        "status": "awaiting_approval",
        "actions": [
            {"action_id": "a1", "status": "awaiting_approval"},
            {"action_id": "a2", "status": "awaiting_approval"},
            {"action_id": "a3", "status": "proposed"},
        ],
    }
    monkeypatch.setattr(
        planner,
        "active_chat_job",
        lambda user_id, conversation_id: initial,
    )
    approved = []

    def fake_approve(user_id, job_id, action_id):
        approved.append(action_id)
        return initial

    monkeypatch.setattr(runtime, "approve_action", fake_approve)
    monkeypatch.setattr(
        runtime,
        "execute_job",
        lambda user_id, job_id: {
            "job_id": job_id,
            "status": "completed",
            "actions": [],
        },
    )
    cleared = []
    monkeypatch.setattr(
        planner,
        "clear_chat_job",
        lambda user_id, conversation_id: cleared.append((user_id, conversation_id)),
    )

    result = planner.approve_and_execute(7, "conversation-1")

    assert approved == ["a1", "a2"]
    assert result["status"] == "completed"
    assert cleared == [(7, "conversation-1")]


def test_plan_format_explicitly_says_nothing_executes_before_confirmation():
    text = planner.format_plan(
        {
            "goal": "Create task",
            "actions": [
                {
                    "tool_name": "velia.tasks.create_draft",
                    "arguments": {"title": "Review"},
                    "risk": "write_reversible",
                    "requires_approval": True,
                }
            ],
        },
        "Создай задачу",
    )
    assert "Выполняй план" in text
    assert "ничего не выполнит" in text
