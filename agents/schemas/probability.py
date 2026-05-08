from typing import Dict, List, Literal, TypedDict


ProbabilityConfidence = Literal["none", "low", "medium", "high"]
Direction = Literal["YES", "NO", "NEUTRAL", "UNKNOWN"]


class ProbabilityAdjustment(TypedDict):
    driver_id: str
    direction: Direction
    impact_points: float
    confidence_weight: float
    reason: str


class ProbabilityPointRange(TypedDict):
    low: float
    high: float


class ProbabilityDataQuality(TypedDict):
    coverage_score: float
    usable_sources_count: int
    missing_high_impact_drivers: int


class ProbabilityEstimate(TypedDict):
    version: str
    model_level: int
    confidence: ProbabilityConfidence
    probability_range: Dict[str, ProbabilityPointRange]
    point_estimate: Dict[str, float]
    base_prior: Dict[str, float]
    adjustments: List[ProbabilityAdjustment]
    why: List[str]
    limitations: List[str]
    data_quality: ProbabilityDataQuality



def empty_probability_estimate() -> ProbabilityEstimate:
    return {
        "version": "1.0",
        "model_level": 0,
        "confidence": "none",
        "probability_range": {},
        "point_estimate": {},
        "base_prior": {},
        "adjustments": [],
        "why": [],
        "limitations": [],
        "data_quality": {
            "coverage_score": 0.0,
            "usable_sources_count": 0,
            "missing_high_impact_drivers": 0,
        },
    }
