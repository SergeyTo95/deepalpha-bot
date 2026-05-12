from typing import Any, Dict

from .base import TopAnalysisSpecialistBase
from .prompts import RISK_AUDITOR_PROMPT
from .provider_router import TopAnalysisProviderRouter


class RiskAuditor(TopAnalysisSpecialistBase):
    name = "risk_auditor"
    role = "forecast_challenge"
    provider_key = "audit_llm"

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        router = TopAnalysisProviderRouter()
        payload = {"prompt": f"{RISK_AUDITOR_PROMPT}\nINPUT:\n{input_data}"}
        response = router.route(self.provider_key, payload)
        parsed = response.get("json") or {}
        if response.get("status") != "ok":
            return {"specialist_name": self.name, "status": "error", "provider_key": self.provider_key, "error": response.get("error", "unavailable")}
        return {
            "specialist_name": self.name,
            "status": "ok",
            "provider_key": self.provider_key,
            "audit_verdict": parsed.get("audit_verdict", "insufficient_evidence"),
            "critical_risks": self.normalize_list(parsed.get("critical_risks")),
            "missing_checks": self.normalize_list(parsed.get("missing_checks")),
            "overconfidence_flags": self.normalize_list(parsed.get("overconfidence_flags")),
            "risk_flags": self.normalize_list(parsed.get("risk_flags")),
            "raw_content": response.get("content", ""),
        }
