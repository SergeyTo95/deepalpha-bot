from typing import Any, Dict, List, Literal, TypedDict

ParserConfidence = Literal["low", "medium", "high"]
Priority = Literal["low", "medium", "high", "critical"]
MatchStatus = Literal["matched_existing_source", "missing", "skipped"]
Freshness = Literal["fresh", "recent", "stale", "unknown"]
Relevance = Literal["low", "medium", "high"]


class TargetedResearchMarket(TypedDict):
    question: str
    market_type: str
    category_type: str
    subcategory: str
    event_type: str


class MatchedSource(TypedDict):
    title: str
    snippet: str
    url: str
    source: str
    freshness: Freshness
    relevance: Relevance


class ExtractedClaim(TypedDict):
    claim: str
    supports_outcome: str
    driver: str
    confidence: Literal["low", "medium", "high", "unknown"]
    source_title: str
    source_url: str


class QueryResult(TypedDict):
    query: str
    driver: str
    outcome_id: str
    outcome_label: str
    priority: Priority
    source_type: str
    status: MatchStatus
    matched_sources: List[MatchedSource]
    extracted_claims: List[ExtractedClaim]


class OutcomeCoverage(TypedDict):
    outcome_id: str
    outcome_label: str
    covered_drivers: List[str]
    missing_drivers: List[str]
    facts_found: int
    coverage_score: float


class SharedCoverage(TypedDict):
    covered_drivers: List[str]
    missing_drivers: List[str]
    facts_found: int
    coverage_score: float


class MissingResearchItem(TypedDict):
    query: str
    driver: str
    outcome_label: str
    priority: str
    why_needed: str


class SourceQuality(TypedDict):
    raw_sources_count: int
    matched_sources_count: int
    claims_count: int
    coverage_score: float
    can_build_forecast: bool


class TargetedResearchParser(TypedDict):
    name: str
    confidence: ParserConfidence


class TargetedResearch(TypedDict):
    version: str
    market: TargetedResearchMarket
    query_results: List[QueryResult]
    outcome_coverage: List[OutcomeCoverage]
    shared_coverage: SharedCoverage
    missing_research: List[MissingResearchItem]
    source_quality: SourceQuality
    notes: List[str]
    parser: TargetedResearchParser


def empty_targeted_research() -> TargetedResearch:
    return {
        "version": "1.0",
        "market": {
            "question": "",
            "market_type": "unknown",
            "category_type": "other",
            "subcategory": "unknown",
            "event_type": "unknown",
        },
        "query_results": [],
        "outcome_coverage": [],
        "shared_coverage": {
            "covered_drivers": [],
            "missing_drivers": [],
            "facts_found": 0,
            "coverage_score": 0.0,
        },
        "missing_research": [],
        "source_quality": {
            "raw_sources_count": 0,
            "matched_sources_count": 0,
            "claims_count": 0,
            "coverage_score": 0.0,
            "can_build_forecast": False,
        },
        "notes": [],
        "parser": {
            "name": "targeted_research_agent_v1",
            "confidence": "low",
        },
    }


def safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
