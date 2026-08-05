from pathlib import Path


def test_agent_chat_cost_guard_documentation_matches_runtime_bounds():
    documentation = Path("docs/VELIA_AGENT_CHAT_COST_GUARD.md").read_text(
        encoding="utf-8"
    )
    adapter = Path("services/kimi_gateway_service.py").read_text(encoding="utf-8")

    assert "400–1400" in documentation
    assert "one foreground attempt" in documentation
    assert "_AGENT_PLAN_MIN_TOKENS = 400" in adapter
    assert "_AGENT_PLAN_MAX_TOKENS = 1400" in adapter
