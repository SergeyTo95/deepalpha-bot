from services import kimi_gateway
from services import kimi_gateway_service as adapter


def test_agent_plan_cap_installation_is_idempotent():
    before = kimi_gateway._initial_completion_limit
    adapter._install_agent_plan_completion_cap()
    adapter._install_agent_plan_completion_cap()
    assert kimi_gateway._initial_completion_limit is before
    assert kimi_gateway._initial_completion_limit("velia_agent_chat_plan", 900) == 900
