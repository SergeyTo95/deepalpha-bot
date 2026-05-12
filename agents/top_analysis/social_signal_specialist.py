from typing import Any, Dict

from .base import TopAnalysisSpecialistBase
from .prompts import SOCIAL_SIGNAL_SPECIALIST_PROMPT
from .provider_router import TopAnalysisProviderRouter


class SocialSignalSpecialist(TopAnalysisSpecialistBase):
    name = "social_signal_specialist"
    role = "narrative_signal_review"
    provider_key = "social_llm"

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        router = TopAnalysisProviderRouter()
        payload = {"prompt": f"{SOCIAL_SIGNAL_SPECIALIST_PROMPT}\nINPUT:\n{input_data}"}
        response = router.route(self.provider_key, payload)
        parsed = response.get("json") or {}
        if response.get("status") != "ok":
            return {"specialist_name": self.name, "status": "error", "provider_key": self.provider_key, "error": response.get("error", "unavailable")}
        return {
            "specialist_name": self.name,
            "status": "ok",
            "provider_key": self.provider_key,
            "social_signal_strength": parsed.get("social_signal_strength", "unknown"),
            "social_confidence": parsed.get("social_confidence", "low"),
            "narratives": self.normalize_list(parsed.get("narratives")),
            "notable_claims": self.normalize_list(parsed.get("notable_claims")),
            "risk_flags": self.normalize_list(parsed.get("risk_flags")) or ["live_social_data_not_connected"],
            "raw_content": response.get("content", ""),
        }
