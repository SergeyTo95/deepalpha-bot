from typing import Any, Dict, List, Literal, Optional, TypedDict


ParserConfidence = Literal["low", "medium", "high"]


class OutcomeItem(TypedDict):
    id: str
    label: str
    condition: str
    market_price: Optional[float]
    normalized_price: Optional[float]
    entity: str
    side: str
    is_binary_yes: bool
    is_binary_no: bool


class OutcomeMarket(TypedDict):
    question: str
    market_type: str
    category_type: str
    subcategory: str
    deadline: str
    resolution_summary: str


class ResolutionLogic(TypedDict):
    type: str
    notes: List[str]


class OutcomeParserMeta(TypedDict):
    name: str
    confidence: ParserConfidence
    warnings: List[str]


class OutcomeMap(TypedDict):
    version: str
    market: OutcomeMarket
    outcomes: List[OutcomeItem]
    primary_entities: List[str]
    target_entity: str
    target_group: str
    event_target: str
    competition: str
    resolution_logic: ResolutionLogic
    parser: OutcomeParserMeta


def empty_outcome_map() -> OutcomeMap:
    return {
        "version": "1.0",
        "market": {
            "question": "",
            "market_type": "unknown",
            "category_type": "other",
            "subcategory": "unknown",
            "deadline": "",
            "resolution_summary": "",
        },
        "outcomes": [],
        "primary_entities": [],
        "target_entity": "",
        "target_group": "",
        "event_target": "",
        "competition": "",
        "resolution_logic": {
            "type": "generic_outcome_resolution",
            "notes": [],
        },
        "parser": {
            "name": "outcome_parser_agent_v1",
            "confidence": "low",
            "warnings": [],
        },
    }


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_market_options(options: Any) -> Dict[str, Optional[float]]:
    if not isinstance(options, dict):
        return {}
    normalized: Dict[str, Optional[float]] = {}
    for key, value in options.items():
        label = str(key or "").strip()
        if not label:
            continue
        normalized[label] = safe_float(value)
    return normalized
