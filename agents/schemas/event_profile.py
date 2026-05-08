from typing import List, TypedDict


class EventProfile(TypedDict):
    version: str
    event_type: str
    category_type: str
    subcategory: str
    market_type: str
    yes_condition: str
    no_condition: str
    target_entity: str
    target_group: str
    competition: str
    event_target: str
    deadline: str
    resolution_notes: List[str]
    confidence: str
    parser: str


def empty_event_profile() -> EventProfile:
    return {
        "version": "1.0",
        "event_type": "generic_binary_event",
        "category_type": "other",
        "subcategory": "unknown",
        "market_type": "binary_event",
        "yes_condition": "YES resolves true under market rules.",
        "no_condition": "NO resolves true under market rules.",
        "target_entity": "",
        "target_group": "",
        "competition": "",
        "event_target": "",
        "deadline": "",
        "resolution_notes": [],
        "confidence": "low",
        "parser": "event_parser_agent_v1",
    }
