from typing import Any, Dict, List, Literal, TypedDict

AnalysisStatus = Literal["complete", "weak", "incomplete"]
AnalysisConfidence = Literal["none", "low", "medium", "high"]
ParserConfidence = Literal["low", "medium", "high"]


class SourceQuality(TypedDict):
    raw_sources_count: int
    matched_sources_count: int
    claims_count: int
    coverage_score: float
    can_build_forecast: bool


class MarketContext(TypedDict):
    is_equal_market: bool
    market_spread: float
    outcome_count: int
    market_type: str


class AnalysisQualityParser(TypedDict):
    name: str
    confidence: ParserConfidence


class AnalysisQuality(TypedDict):
    version: str
    status: AnalysisStatus
    can_show_forecast: bool
    can_show_likely_outcome: bool
    can_show_value: bool
    should_select_side: bool
    quality_score: float
    confidence: AnalysisConfidence
    reasons: List[str]
    blocking_issues: List[str]
    missing_critical_data: List[Any]
    refund_recommended: bool
    incomplete_paid_analysis: bool
    source_quality: SourceQuality
    market_context: MarketContext
    parser: AnalysisQualityParser


def empty_analysis_quality() -> AnalysisQuality:
    return {
        "version": "1.0",
        "status": "incomplete",
        "can_show_forecast": False,
        "can_show_likely_outcome": False,
        "can_show_value": False,
        "should_select_side": False,
        "quality_score": 0.0,
        "confidence": "none",
        "reasons": [],
        "blocking_issues": [],
        "missing_critical_data": [],
        "refund_recommended": False,
        "incomplete_paid_analysis": False,
        "source_quality": {
            "raw_sources_count": 0,
            "matched_sources_count": 0,
            "claims_count": 0,
            "coverage_score": 0.0,
            "can_build_forecast": False,
        },
        "market_context": {
            "is_equal_market": False,
            "market_spread": 100.0,
            "outcome_count": 0,
            "market_type": "unknown",
        },
        "parser": {
            "name": "analysis_quality_agent_v1",
            "confidence": "low",
        },
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if value is None:
        return default
    return bool(value)


def safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}
