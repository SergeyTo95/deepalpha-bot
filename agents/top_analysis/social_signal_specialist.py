from typing import Any, Dict

from .base import TopAnalysisSpecialistBase
from .schemas import SocialSignalResult


class SocialSignalSpecialist(TopAnalysisSpecialistBase):
    name = "social_signal_specialist"
    role = "narrative_signal_review"
    provider_key = "social_llm"

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = SocialSignalResult(
                specialist_name=self.name,
                status="placeholder",
                provider_key=self.provider_key,
                social_signal_strength="unknown",
                narratives=[],
                notable_claims=[],
                risk_flags=["social_signal_not_connected"],
            )
            return self.safe_result(result, fallback={"status": "placeholder", "specialist_name": self.name})
        except Exception:
            return {"status": "placeholder", "specialist_name": self.name}
