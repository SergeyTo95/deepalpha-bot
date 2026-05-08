from typing import Any, Dict, List, TypedDict


class ForecastCard(TypedDict):
    version: str
    market: Dict[str, Any]
    event_profile: Dict[str, Any]
    drivers: Dict[str, List[Any]]
    data_requirements: List[Any]
    evidence: Dict[str, List[Any]]
    model: Dict[str, Any]
    value: Dict[str, Any]
    what_would_change: List[Any]
    risks: List[Any]
    next_queries: List[Any]


def empty_forecast_card() -> ForecastCard:
    return {
        "version": "1.0",
        "market": {
            "question": "",
            "category_type": "",
            "subcategory": "",
            "market_type": "",
            "market_price": {},
            "deadline": "",
            "resolution_summary": "",
        },
        "event_profile": {
            "event_type": "",
            "yes_condition": "",
            "no_condition": "",
            "target_entity": "",
            "target_group": "",
            "competition": "",
            "event_target": "",
        },
        "drivers": {"yes": [], "no": [], "neutral": []},
        "data_requirements": [],
        "evidence": {"for_yes": [], "for_no": [], "missing_data": [], "contradictions": []},
        "model": {
            "model_level": 0,
            "probability_range": {},
            "point_estimate": {},
            "confidence": "none",
            "why": [],
        },
        "value": {
            "market_price": {},
            "edge": {},
            "decision": "NO TRADE",
            "best_side": "NONE",
            "entry_price": {},
        },
        "what_would_change": [],
        "risks": [],
        "next_queries": [],
    }
