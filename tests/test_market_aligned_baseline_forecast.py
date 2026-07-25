from agents.forecast_aware_trading_plan_agent import ForecastAwareTradingPlanAgent
from agents.independent_forecast_decision_agent import IndependentForecastDecisionAgent
from agents.probability_estimator_agent import ProbabilityEstimatorAgent
from agents.special_market_news_queries import (
    build_social_post_count_queries,
    is_social_post_count_market,
    wrap_targeted_news_queries,
)
from agents.trading_plan_agent import TradingPlanAgent
from agents.value_decision_agent import ValueDecisionAgent


def _empty_evidence():
    return {
        "facts": [],
        "missing_driver_data": [],
        "source_quality": {
            "coverage_score": 0.0,
            "usable_sources_count": 0,
        },
    }


def _no_sources():
    return {
        "relevant_sources": [],
        "sources": [],
        "relevant_sources_count": 0,
        "raw_sources_count": 0,
        "news_quality": "low",
        "source_summary": {},
        "sources_found_but_filtered": False,
    }


def _trump_market(probability: str = ""):
    question = "Will Donald Trump post 120-139 Truth Social posts from July 28 to August 4, 2026?"
    return {
        "question": question,
        "category": "Politics",
        "market_probability": "Yes: 22.5% | No: 77.5%",
        "probability": probability,
    }


def _flatten_strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_flatten_strings(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_flatten_strings(item))
        return out
    return []


def test_valid_priced_market_gets_numeric_baseline_without_sources():
    estimate = ProbabilityEstimatorAgent().estimate(
        event_profile={"event_type": "generic_binary_event"},
        driver_map={},
        data_plan={},
        structured_evidence=_empty_evidence(),
        market_options={"YES": 22.5, "NO": 77.5},
        model_options=None,
    )

    assert estimate["model_level"] == 1
    assert estimate["confidence"] == "low"
    assert estimate["point_estimate"] == {"YES": 22.5, "NO": 77.5}
    assert estimate["probability_range"] == {}
    assert estimate["estimate_source"] == "market_aligned_baseline"
    assert estimate["independent_probability"] is False
    assert max(estimate["point_estimate"], key=estimate["point_estimate"].get) == "NO"


def test_market_aligned_baseline_never_creates_value_edge():
    estimate = ProbabilityEstimatorAgent().estimate(
        event_profile={"event_type": "generic_binary_event"},
        driver_map={},
        data_plan={},
        structured_evidence=_empty_evidence(),
        market_options={"YES": 22.5, "NO": 77.5},
        model_options=None,
    )

    value = ValueDecisionAgent().decide(
        probability_estimate=estimate,
        market_options={"YES": 22.5, "NO": 77.5},
        event_profile={"event_type": "generic_binary_event"},
        structured_evidence=_empty_evidence(),
    )

    assert value["edge"] == {"YES": 0.0, "NO": 0.0}
    assert value["best_side"] == "NONE"
    assert value["decision"] == "NO_TRADE"


def test_trump_market_without_upstream_ai_uses_clean_numeric_baseline():
    base_result = _trump_market()

    result = TradingPlanAgent().run(
        result=base_result,
        market_data=base_result,
        news_data=_no_sources(),
        lang="ru",
    )

    card = result["forecast_card"]
    model = card["model"]
    value = card["value"]

    assert model["model_level"] == 1
    assert model["confidence"] == "low"
    assert model["point_estimate"] == {"YES": 22.5, "NO": 77.5}
    assert model["probability_range"] == {}
    assert model["estimate_source"] == "market_aligned_baseline"
    assert model["independent_probability"] is False
    assert value["edge"] == {}
    assert value["decision"] == "NO_TRADE"


def test_runtime_market_fallback_is_not_disguised_as_independent_ai_model():
    base_result = _trump_market("No — 77.5%")
    base_result["decision_runtime_guard"] = "market_aligned_fallback"
    base_result["decision_fallback_reason"] = "unusable_result"

    result = TradingPlanAgent().run(
        result=base_result,
        market_data=base_result,
        news_data=_no_sources(),
        lang="ru",
    )

    model = result["forecast_card"]["model"]
    value = result["forecast_card"]["value"]

    assert result["upstream_probability_used"] is False
    assert result["upstream_probability_fallback"] is True
    assert model["point_estimate"] == {"YES": 22.5, "NO": 77.5}
    assert model["probability_range"] == {}
    assert model["estimate_source"] == "market_aligned_baseline"
    assert model["independent_probability"] is False
    assert value["edge"] == {}
    assert any("отдельная ai-оценка" in item.lower() for item in model["limitations"])


def test_upstream_kimi_probability_is_exact_point_not_artificial_range():
    base_result = _trump_market("No — 69.0%")
    base_result["decision_runtime_guard"] = "ok"

    result = TradingPlanAgent().run(
        result=base_result,
        market_data=base_result,
        news_data=_no_sources(),
        lang="ru",
    )

    model = result["forecast_card"]["model"]
    value = result["forecast_card"]["value"]

    assert result["upstream_probability_used"] is True
    assert model["point_estimate"] == {"NO": 69.0, "YES": 31.0}
    assert model["probability_range"] == {}
    assert model["estimate_source"] == "upstream_decision_forecast"
    assert model["independent_probability"] is True
    assert max(model["point_estimate"], key=model["point_estimate"].get) == "NO"
    assert value["edge"]["YES"] == 8.5
    assert value["edge"]["NO"] == -8.5


def test_successful_ai_estimate_equal_to_market_has_no_fake_value():
    base_result = _trump_market("No — 77.5%")
    base_result["decision_runtime_guard"] = "ok"

    result = TradingPlanAgent().run(
        result=base_result,
        market_data=base_result,
        news_data=_no_sources(),
        lang="ru",
    )

    model = result["forecast_card"]["model"]
    value = result["forecast_card"]["value"]

    assert result["upstream_probability_used"] is True
    assert model["point_estimate"] == {"NO": 77.5, "YES": 22.5}
    assert model["probability_range"] == {}
    assert model["independent_probability"] is True
    assert value["edge"] == {}
    assert value["best_side"] == "NONE"
    assert any("совпадает с текущей рыночной линией" in item.lower() for item in model["limitations"])


def test_ru_forecast_card_localizes_known_service_phrases():
    base_result = _trump_market("No — 69.0%")
    base_result["decision_runtime_guard"] = "ok"

    result = TradingPlanAgent().run(
        result=base_result,
        market_data=base_result,
        news_data=_no_sources(),
        lang="ru",
    )

    strings = _flatten_strings(result["forecast_card"])
    assert "Deadline sensitivity" not in strings
    assert "Filtered previews are not valid evidence for this exact match." not in strings
    assert "primary source confirmation" not in strings
    assert "resolution rule mapping" not in strings


def test_upstream_yes_probability_builds_complement():
    parsed = ForecastAwareTradingPlanAgent._parse_binary_probability("YES: 63.5%")

    assert parsed == {"YES": 63.5, "NO": 36.5}


def test_invalid_upstream_probability_falls_back_to_market_baseline():
    base_result = _trump_market("N/A")

    result = TradingPlanAgent().run(
        result=base_result,
        market_data=base_result,
        news_data=_no_sources(),
        lang="ru",
    )

    model = result["forecast_card"]["model"]
    assert result["upstream_probability_used"] is False
    assert model["point_estimate"] == {"YES": 22.5, "NO": 77.5}
    assert model["probability_range"] == {}
    assert model["estimate_source"] == "market_aligned_baseline"


def test_real_directional_evidence_still_replaces_market_baseline():
    evidence = {
        "facts": [
            {
                "driver_id": "posting_rate",
                "direction": "YES",
                "impact": "high",
                "confidence": "high",
                "claim": "Verified posting rate is above the range pace.",
            }
        ],
        "missing_driver_data": [],
        "source_quality": {
            "coverage_score": 0.5,
            "usable_sources_count": 2,
        },
    }

    estimate = ProbabilityEstimatorAgent().estimate(
        event_profile={"event_type": "generic_binary_event"},
        driver_map={},
        data_plan={},
        structured_evidence=evidence,
        market_options={"YES": 22.5, "NO": 77.5},
        model_options=None,
    )

    assert estimate["estimate_source"] == "evidence_adjusted"
    assert estimate["independent_probability"] is True
    assert estimate["point_estimate"]["YES"] > 22.5
    assert estimate["point_estimate"]["NO"] < 77.5
    assert estimate["probability_range"]


def test_neutral_fact_keeps_market_aligned_baseline_not_researched_range():
    neutral_evidence = {
        "facts": [
            {
                "driver_id": "deadline",
                "direction": "NEUTRAL",
                "impact": "high",
                "confidence": "high",
                "claim": "The market has a fixed weekly deadline.",
            }
        ],
        "missing_driver_data": [],
        "source_quality": {
            "coverage_score": 0.6,
            "usable_sources_count": 2,
        },
    }

    estimate = ProbabilityEstimatorAgent().estimate(
        event_profile={"event_type": "generic_binary_event"},
        driver_map={},
        data_plan={},
        structured_evidence=neutral_evidence,
        market_options={"YES": 22.5, "NO": 77.5},
        model_options=None,
    )

    assert estimate["point_estimate"] == {"YES": 22.5, "NO": 77.5}
    assert estimate["probability_range"] == {}
    assert estimate["estimate_source"] == "market_aligned_baseline"
    assert estimate["independent_probability"] is False
    assert any("neutral" in item.lower() for item in estimate["limitations"])


def test_partial_binary_market_does_not_emit_baseline():
    estimate = ProbabilityEstimatorAgent().estimate(
        event_profile={"event_type": "generic_binary_event"},
        driver_map={},
        data_plan={},
        structured_evidence=_empty_evidence(),
        market_options={"YES": 22.5},
        model_options=None,
    )

    assert estimate["model_level"] == 0
    assert estimate["point_estimate"] == {}
    assert estimate["probability_range"] == {}
    assert estimate["estimate_source"] == "unavailable"
    assert any("complete market options" in item.lower() for item in estimate["limitations"])


def test_independent_prompt_requires_point_estimate_without_invented_sources():
    rules = IndependentForecastDecisionAgent.independent_probability_rules("ru")

    assert "ОДНУ точечную оценку" in rules
    assert "не механическое копирование market odds" in rules
    assert "Не выдумывай" in rules
    assert "Низкую уверенность" in rules


def test_truth_social_post_count_market_gets_targeted_activity_queries():
    question = _trump_market()["question"]

    assert is_social_post_count_market(question) is True
    queries = build_social_post_count_queries(question)
    assert any("post count tracker" in query for query in queries)
    assert any("posting frequency" in query for query in queries)

    original = lambda *args, **kwargs: ["generic politics query"]
    enhanced = wrap_targeted_news_queries(original)
    merged = enhanced("politics", "unknown", [], "binary", question, "")

    assert merged[0].startswith("Donald Trump Truth Social")
    assert "generic politics query" in merged
    assert len(merged) <= 7
