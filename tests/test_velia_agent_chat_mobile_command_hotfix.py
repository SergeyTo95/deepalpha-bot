from services import velia_agent_chat_planner_service as planner
from services import velia_agent_chat_presentation_service as presentation


def test_mobile_approval_aliases_are_explicit_and_bounded():
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


def test_active_plan_reminder_stays_an_executable_plan_card():
    payload = presentation.build_presentation(
        reason="velia_agent_chat_plan_active",
        text="Сначала завершите активный план.",
        job={
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
        },
    )

    assert payload["kind"] == "plan"
    assert payload["can_execute"] is True
    assert payload["execute_command"] == "Выполняй план"
    assert payload["actions"][0]["tool_name"] == "velia.tasks.create_draft"
