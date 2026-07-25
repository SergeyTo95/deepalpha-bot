import agents.decision_agent as decision_module
from agents.safe_decision_agent import SafeDecisionAgent


def _market():
    return {
        "question": "Will Donald Trump post 120-139 Truth Social posts from July 28 to August 4, 2026?",
        "category": "Politics",
        "market_probability": "Yes: 22.5% | No: 77.5%",
        "market_type": "binary",
        "options": ["Yes", "No"],
        "date_context": "2026-08-04",
        "trend_summary": "Stable",
        "crowd_behavior": "NO leads",
        "market_microstructure": {},
        "market_structure": {},
    }


def _news():
    return {
        "news_summary": "Some background sources were found, but no decisive posting-rate evidence.",
        "sentiment": "Unclear",
        "source_summary": {
            "tier1": 0,
            "tier2": 0,
            "tier3": 2,
            "fresh": 1,
            "usable": 0,
            "stale": 1,
        },
        "key_signals": [],
        "sources": [],
    }


def test_empty_provider_parent_fallback_is_never_marked_ok(monkeypatch):
    monkeypatch.setattr(decision_module, "generate_decision_text", lambda *a, **k: "")

    result = SafeDecisionAgent().run(_market(), _news(), lang="ru")

    assert result["probability"] == "No — 77.5%"
    assert result["decision_runtime_guard"] == "market_aligned_fallback"
    assert result["decision_fallback_reason"] == "provider_empty_or_invalid"


def test_valid_provider_forecast_remains_ok(monkeypatch):
    response = """
System Probability: No — 69.0%
Confidence: Low
Reasoning: Structural estimate with weak source support.
Main Scenario: Posting activity remains below the target interval.
Alternative Scenario: A burst of posts reaches the interval.
Conclusion: NO is more likely, but confidence is low.
"""
    monkeypatch.setattr(decision_module, "generate_decision_text", lambda *a, **k: response)

    result = SafeDecisionAgent().run(_market(), _news(), lang="en")

    assert result["probability"] == "No — 69.0%"
    assert result["decision_runtime_guard"] == "ok"
    assert "decision_fallback_reason" not in result
