from typing import List, Literal, TypedDict


SourceType = Literal[
    "news", "search", "sports_data", "odds", "crypto_price", "official", "polymarket_related", "generic"
]
PriorityLevel = Literal["low", "medium", "high", "very_high"]


class RequiredDataItem(TypedDict):
    driver_id: str
    description: str
    query: str
    source_type: SourceType
    priority: PriorityLevel


class DataPlan(TypedDict):
    version: str
    event_type: str
    required_data: List[RequiredDataItem]
    missing_data: List[str]
    suggested_queries: List[str]
    priority_sources: List[str]


def empty_data_plan(event_type: str = "generic_binary_event") -> DataPlan:
    return {
        "version": "1.0",
        "event_type": event_type,
        "required_data": [],
        "missing_data": [],
        "suggested_queries": [],
        "priority_sources": [],
    }
