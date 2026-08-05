from datetime import datetime
from types import SimpleNamespace

from services import velia_agent_chat_presentation_service as presentation


def test_plan_presentation_exposes_safe_task_and_commands():
    payload = presentation.build_presentation(
        reason="velia_agent_chat_plan_ready",
        text="## План Велии\nСоздать задачу",
        job={
            "status": "awaiting_approval",
            "goal": "Создать задачу",
            "planner_summary": "Создать черновик задачи",
            "actions": [
                {
                    "action_id": "action-1",
                    "tool_name": "velia.tasks.create_draft",
                    "risk": "write_reversible",
                    "status": "awaiting_approval",
                    "requires_approval": True,
                    "arguments": {
                        "title": "Проверить отчёт",
                        "notes": "До пятницы",
                        "_velia_idempotency_key": "secret",
                    },
                }
            ],
        },
    )

    assert payload["kind"] == "plan"
    assert payload["title"] == "План Велии"
    assert payload["can_execute"] is True
    assert payload["can_cancel"] is True
    assert payload["execute_command"] == "Выполняй план"
    assert payload["cancel_command"] == "Отмени план"
    assert payload["actions"][0]["task"] == {
        "draft_id": "",
        "title": "Проверить отчёт",
        "notes": "До пятницы",
        "completed": False,
        "created_at": None,
        "updated_at": None,
    }
    assert "_velia_idempotency_key" not in str(payload)


def test_completed_task_list_is_json_safe_and_bounded():
    created_at = datetime(2026, 8, 5, 12, 18, 47, 562666)
    payload = presentation.build_presentation(
        reason="velia_agent_chat_completed",
        text="## План выполнен",
        job={
            "status": "completed",
            "goal": "Показать задачи",
            "actions": [
                {
                    "action_id": "action-2",
                    "tool_name": "velia.tasks.list",
                    "risk": "read",
                    "status": "completed",
                    "requires_approval": False,
                    "arguments": {"limit": 20},
                    "result": {
                        "items": [
                            {
                                "draft_id": "draft-1",
                                "title": "VELIA_AGENT_SMOKE_2026_08_05",
                                "notes": "",
                                "completed": False,
                                "created_at": created_at,
                                "updated_at": created_at,
                                "internal_secret": "must-not-leak",
                            }
                        ]
                    },
                }
            ],
        },
    )

    assert payload["kind"] == "completed"
    assert payload["can_execute"] is False
    action = payload["actions"][0]
    assert action["limit"] == 20
    assert action["tasks"][0]["draft_id"] == "draft-1"
    assert action["tasks"][0]["created_at"] == "2026-08-05T12:18:47.562666"
    assert "internal_secret" not in str(payload)
    presentation._json(payload)


def test_unknown_tool_does_not_expose_arguments_or_result():
    payload = presentation.build_presentation(
        reason="velia_agent_chat_completed",
        text="Plan completed",
        job={
            "status": "completed",
            "actions": [
                {
                    "action_id": "a",
                    "tool_name": "external.private.tool",
                    "risk": "external_write",
                    "status": "completed",
                    "requires_approval": True,
                    "arguments": {"access_token": "secret"},
                    "result": {"private": "secret"},
                }
            ],
        },
    )

    action = payload["actions"][0]
    assert action["tool_name"] == "external.private.tool"
    assert "arguments" not in action
    assert "result" not in action
    assert "secret" not in str(payload)


def test_history_enrichment_applies_only_to_assistant_messages():
    context = {"presentation": {"schema_version": 1, "kind": "plan"}}
    messages = [
        {"id": "u", "role": "user", "request_id": "r1", "content": "create"},
        {"id": "a", "role": "assistant", "request_id": "r1", "content": "plan"},
        {"id": "a2", "role": "assistant", "request_id": "r2", "content": "other"},
    ]

    enriched = presentation.enrich_messages_with_contexts(messages, {"r1": context})

    assert "agent_context" not in enriched[0]
    assert enriched[1]["agent_context"] == context
    assert "agent_context" not in enriched[2]
    assert messages[1].get("agent_context") is None


def test_mobile_route_install_wraps_history_and_send(monkeypatch):
    routes = SimpleNamespace(
        list_messages=lambda user_id, conversation_id, **kwargs: [
            {"role": "assistant", "request_id": "r1"}
        ],
        send_message=lambda user_id, conversation_id, *args, **kwargs: {
            "ok": True,
            "assistant_message": {"role": "assistant", "request_id": "r1"},
        },
    )
    monkeypatch.setattr(
        presentation,
        "load_contexts",
        lambda **kwargs: {"r1": {"presentation": {"kind": "plan"}}},
    )

    presentation.install_mobile_routes(routes)
    presentation.install_mobile_routes(routes)

    history = routes.list_messages(7, "conversation-1", limit=20)
    sent = routes.send_message(7, "conversation-1", "hello", idempotency_key="abcdefgh")
    assert history[0]["agent_context"]["presentation"]["kind"] == "plan"
    assert sent["assistant_message"]["agent_context"]["presentation"]["kind"] == "plan"
