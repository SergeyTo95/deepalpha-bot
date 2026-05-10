from typing import Any, Dict, List, Literal, TypedDict

ParserConfidence = Literal["low", "medium", "high"]
Priority = Literal["low", "medium", "high", "critical"]


class ResearchQuery(TypedDict):
    query: str
    driver: str
    priority: Priority
    source_type: str
    why: str


class OutcomeResearch(TypedDict):
    outcome_id: str
    outcome_label: str
    queries: List[ResearchQuery]
    required_facts: List[str]
    minimum_facts_needed: int


class ResearchMarket(TypedDict):
    question: str
    market_type: str
    category_type: str
    subcategory: str
    event_type: str


class MinimumDataPolicy(TypedDict):
    minimum_total_facts: int
    minimum_per_outcome: int
    critical_drivers: List[str]
    can_forecast_without_sources: bool


class ResearchParserMeta(TypedDict):
    name: str
    confidence: ParserConfidence


class ResearchPlan(TypedDict):
    version: str
    market: ResearchMarket
    outcome_research: List[OutcomeResearch]
    shared_research: List[ResearchQuery]
    minimum_data_policy: MinimumDataPolicy
    warnings: List[str]
    parser: ResearchParserMeta


def empty_research_plan() -> ResearchPlan:
    return {
        "version": "1.0",
        "market": {
            "question": "",
            "market_type": "unknown",
            "category_type": "other",
            "subcategory": "unknown",
            "event_type": "unknown",
        },
        "outcome_research": [],
        "shared_research": [],
        "minimum_data_policy": {
            "minimum_total_facts": 2,
            "minimum_per_outcome": 1,
            "critical_drivers": ["primary_sources", "latest_developments"],
            "can_forecast_without_sources": False,
        },
        "warnings": [],
        "parser": {
            "name": "research_plan_agent_v1",
            "confidence": "low",
        },
    }


def safe_outcomes(outcome_map: Any) -> List[Dict[str, Any]]:
    if not isinstance(outcome_map, dict):
        return []
    outcomes = outcome_map.get("outcomes")
    if not isinstance(outcomes, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in outcomes:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "id": str(item.get("id") or "option"),
            "label": str(item.get("label") or item.get("entity") or "Unknown"),
        })
    return normalized
