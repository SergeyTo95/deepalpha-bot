from pathlib import Path


def test_agent_plan_cap_does_not_modify_the_global_gateway_source():
    adapter = Path("services/kimi_gateway_service.py").read_text(encoding="utf-8")
    assert "from services import kimi_gateway" in adapter
    assert "str(feature or \"\") == _AGENT_PLAN_FEATURE" in adapter
