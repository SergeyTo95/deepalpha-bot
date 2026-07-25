from agents.forecast_aware_trading_plan_agent import ForecastAwareTradingPlanAgent
from agents.probability_estimator_agent import ProbabilityEstimatorAgent
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


def test_trump_market_without_upstream_ai_uses_numeric_market_baseline():
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
    assert value["edge"] == {"YES": 0.0, "NO": 0.0}
    assert value["decision"] == "NO_TRADE"


def test_upstream_kimi_probability_is_used_instead_of_copying_market():
    base_result = _trump_market("No — 69.0%")

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
    assert model["estimate_source"] == "upstream_decision_forecast"
    assert model["independent_probability"] is True
    assert max(model["point_estimate"], key=model["point_estimate"].get) == "NO"
    assert value["edge"]["YES"] == 8.5
    assert value["edge"]["NO"] == -8.5


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
