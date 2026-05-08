from typing import Dict, List, Literal, TypedDict


Direction = Literal["YES", "NO", "NEUTRAL", "UNKNOWN"]
ImpactLevel = Literal["low", "medium", "high", "very_high", "unknown"]
ConfidenceLevel = Literal["low", "medium", "high", "unknown"]
FreshnessLevel = Literal["fresh", "recent", "stale", "unknown"]


class EvidenceFact(TypedDict):
    claim: str
    driver_id: str
    driver_label: str
    direction: Direction
    impact: ImpactLevel
    confidence: ConfidenceLevel
    source_title: str
    source_url: str
    source_type: str
    freshness: FreshnessLevel


class MissingDriverDataItem(TypedDict):
    driver_id: str
    description: str
    priority: Literal["high", "very_high"]
    why_missing: str


class SourceQuality(TypedDict):
    raw_sources_count: int
    usable_sources_count: int
    driver_coverage: Dict[str, int]
    coverage_score: float


class StructuredEvidence(TypedDict):
    version: str
    event_type: str
    facts: List[EvidenceFact]
    for_yes: List[str]
    for_no: List[str]
    neutral: List[str]
    missing_driver_data: List[MissingDriverDataItem]
    contradictions: List[str]
    source_quality: SourceQuality
    notes: List[str]


def empty_structured_evidence(event_type: str = "generic_binary_event") -> StructuredEvidence:
    return {
        "version": "1.0",
        "event_type": event_type,
        "facts": [],
        "for_yes": [],
        "for_no": [],
        "neutral": [],
        "missing_driver_data": [],
        "contradictions": [],
        "source_quality": {
            "raw_sources_count": 0,
            "usable_sources_count": 0,
            "driver_coverage": {},
            "coverage_score": 0.0,
        },
        "notes": [],
    }
