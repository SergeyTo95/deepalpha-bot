from services import velia_agent_chat_runtime_patch as runtime_patch
from services import velia_agent_chat_presentation_service as presentation


def test_runtime_result_contains_and_persists_structured_presentation(monkeypatch):
    persisted = []
    monkeypatch.setattr(
        presentation,
        "persist_context_best_effort",
        lambda **kwargs: persisted.append(kwargs),
    )

    result = runtime_patch._result(
        "## План Велии",
        "request-1",
        reason="velia_agent_chat_plan_ready",
        user_id=7,
        conversation_id="conversation-1",
        job={
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
                    "arguments": {"title": "Проверить UX"},
                }
            ],
        },
    )

    context = result["agent_context"]
    assert context["job_id"] == "job-1"
    assert context["presentation"]["kind"] == "plan"
    assert context["presentation"]["can_execute"] is True
    assert persisted == [
        {
            "request_id": "request-1",
            "user_id": 7,
            "conversation_id": "conversation-1",
            "context": context,
        }
    ]


def test_runtime_result_does_not_persist_without_request_identity(monkeypatch):
    persisted = []
    monkeypatch.setattr(
        presentation,
        "persist_context_best_effort",
        lambda **kwargs: persisted.append(kwargs),
    )

    result = runtime_patch._result(
        "There is no active plan.",
        None,
        reason="velia_agent_chat_plan_missing",
    )

    assert result["agent_context"]["presentation"]["kind"] == "error"
    assert persisted == []
