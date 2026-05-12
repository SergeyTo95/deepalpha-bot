from typing import Any, Dict

from .base import TopAnalysisSpecialistBase
from .prompts import RESEARCH_SPECIALIST_PROMPT
from .provider_router import TopAnalysisProviderRouter


class ResearchSpecialist(TopAnalysisSpecialistBase):
    name = "research_specialist"
    role = "evidence_planning"
    provider_key = "research_llm"

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        router = TopAnalysisProviderRouter()
        payload = {"prompt": f"{RESEARCH_SPECIALIST_PROMPT}\nINPUT:\n{input_data}"}
        response = router.route(self.provider_key, payload)
        parsed = response.get("json") or {}
        if response.get("status") != "ok":
            return {"specialist_name": self.name, "status": "error", "provider_key": self.provider_key, "error": response.get("error", "unavailable")}
        if not parsed:
            return {"specialist_name": self.name, "status": "error", "provider_key": self.provider_key, "error": "invalid_or_empty_json"}
        return {
            "specialist_name": self.name,
            "status": "ok",
            "provider_key": self.provider_key,
            "evidence_strength": parsed.get("evidence_strength", "unknown"),
            "key_findings": self.normalize_list(parsed.get("key_findings")),
            "primary_evidence": self.normalize_list(parsed.get("primary_evidence")),
            "secondary_evidence": self.normalize_list(parsed.get("secondary_evidence")),
            "missing_data": self.normalize_list(parsed.get("missing_data")),
            "driver_coverage": self.normalize_list(parsed.get("driver_coverage")),
            "risk_flags": self.normalize_list(parsed.get("risk_flags")),
            "raw_content": response.get("content", ""),
        }
