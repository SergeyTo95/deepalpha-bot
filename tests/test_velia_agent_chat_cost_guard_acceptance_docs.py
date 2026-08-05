from pathlib import Path


def test_agent_cost_guard_acceptance_keeps_external_features_disabled():
    text = Path("docs/VELIA_AGENT_CHAT_COST_GUARD_ACCEPTANCE.md").read_text(
        encoding="utf-8"
    )
    assert "Calendar and Scheduler flags disabled" in text
    assert "exact-head Agent test suite" in text
    assert "configured USD budget" in text
