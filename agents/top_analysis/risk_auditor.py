from typing import Dict

from .base import TopAnalysisSpecialistBase
from .schemas import RiskAuditResult


class RiskAuditor(TopAnalysisSpecialistBase):
    name = "risk_auditor"
    role = "forecast_challenge"
    provider_key = "audit_llm"

    def run(self, input_data: Dict[str, object]) -> Dict[str, object]:
        try:
            result = RiskAuditResult(
                specialist_name=self.name,
                status="placeholder",
                provider_key=self.provider_key,
                audit_verdict="not_connected",
                confidence_adjustment="none",
                critical_risks=[],
                missing_checks=[],
                hallucination_risk="unknown",
            )
            return self.safe_result(result, fallback={"status": "placeholder", "specialist_name": self.name})
        except Exception:
            return {"status": "placeholder", "specialist_name": self.name}
