from typing import Any, Dict

from .base import TopAnalysisSpecialistBase
from .schemas import ResearchSpecialistResult


class ResearchSpecialist(TopAnalysisSpecialistBase):
    name = "research_specialist"
    role = "evidence_planning"
    provider_key = "research_llm"

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = ResearchSpecialistResult(
                specialist_name=self.name,
                status="placeholder",
                provider_key=self.provider_key,
                evidence_strength="unknown",
                key_findings=[],
                missing_data=[],
                recommended_queries=[],
                risk_flags=[],
            )
            return self.safe_result(result, fallback={"status": "placeholder", "specialist_name": self.name})
        except Exception:
            return {"status": "placeholder", "specialist_name": self.name}
