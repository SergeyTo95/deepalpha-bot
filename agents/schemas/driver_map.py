from typing import List, Literal, TypedDict


ImpactLevel = Literal["low", "medium", "high", "very_high"]
NeutralImpactLevel = Literal["low", "medium", "high"]


class DriverItem(TypedDict):
    id: str
    label: str
    description: str
    impact: ImpactLevel
    data_needed: List[str]


class NeutralDriverItem(TypedDict):
    id: str
    label: str
    description: str
    impact: NeutralImpactLevel
    data_needed: List[str]


class DriverMap(TypedDict):
    version: str
    event_type: str
    category_type: str
    market_type: str
    yes_drivers: List[DriverItem]
    no_drivers: List[DriverItem]
    neutral_drivers: List[NeutralDriverItem]
    must_find: List[str]
    high_impact_keywords: List[str]
    confidence: str


def empty_driver_map(event_type: str = "generic_binary_event", category_type: str = "other", market_type: str = "binary_event") -> DriverMap:
    return {
        "version": "1.0",
        "event_type": event_type,
        "category_type": category_type,
        "market_type": market_type,
        "yes_drivers": [],
        "no_drivers": [],
        "neutral_drivers": [],
        "must_find": [],
        "high_impact_keywords": [],
        "confidence": "low",
    }
