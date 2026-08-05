from pathlib import Path


def test_agent_planner_uses_the_bounded_gateway_adapter():
    planner = Path("services/velia_agent_chat_planner_service.py").read_text(
        encoding="utf-8"
    )
    adapter = Path("services/kimi_gateway_service.py").read_text(encoding="utf-8")

    assert "from services import kimi_gateway_service as kimi_gateway" in planner
    assert "_install_agent_plan_completion_cap()" in adapter
    assert '_AGENT_PLAN_FEATURE = "velia_agent_chat_plan"' in adapter
    assert "_AGENT_PLAN_MIN_TOKENS = 400" in adapter
    assert "_AGENT_PLAN_MAX_TOKENS = 1400" in adapter
