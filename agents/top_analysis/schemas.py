from dataclasses import dataclass, field
from typing import Any, Dict, List, TypedDict


class TopAnalysisInput(TypedDict, total=False):
    question: str
    market_options: Dict[str, float]
    event_profile: Dict[str, Any]
    base_analysis: Dict[str, Any]
    source_summary: List[str]


@dataclass
class SpecialistResult:
    specialist_name: str
    status: str = "placeholder"
    provider_key: str = ""
    model_metadata: Dict[str, Any] = field(default_factory=dict)
    evidence_summary: List[str] = field(default_factory=list)
    confidence: str = "unknown"
    risk_flags: List[str] = field(default_factory=list)
    missing_data: List[str] = field(default_factory=list)


@dataclass
class ResearchSpecialistResult(SpecialistResult):
    evidence_strength: str = "unknown"
    key_findings: List[str] = field(default_factory=list)
    recommended_queries: List[str] = field(default_factory=list)


@dataclass
class SocialSignalResult(SpecialistResult):
    social_signal_strength: str = "unknown"
    narratives: List[str] = field(default_factory=list)
    notable_claims: List[str] = field(default_factory=list)


@dataclass
class RiskAuditResult(SpecialistResult):
    audit_verdict: str = "not_connected"
    confidence_adjustment: str = "none"
    critical_risks: List[str] = field(default_factory=list)
    missing_checks: List[str] = field(default_factory=list)
    hallucination_risk: str = "unknown"


@dataclass
class ChiefForecastResult(SpecialistResult):
    final_forecast_available: bool = False
    probability_range: Dict[str, float] = field(default_factory=dict)
    recommendation: str = "WAIT"
    summary: str = "Top Analysis engine skeleton is prepared but not connected."


class TopAnalysisResult(TypedDict, total=False):
    status: str
    question: str
    research_result: Dict[str, Any]
    social_signal_result: Dict[str, Any]
    risk_audit_result: Dict[str, Any]
    chief_forecast_result: Dict[str, Any]
    final_probability_range: Dict[str, float]
    final_recommendation: str
    user_facing_summary: str
