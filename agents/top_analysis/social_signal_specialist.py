from typing import Any, Dict
import logging

from .base import TopAnalysisSpecialistBase
from .prompts import SOCIAL_SIGNAL_SPECIALIST_PROMPT
from .provider_router import TopAnalysisProviderRouter


class SocialSignalSpecialist(TopAnalysisSpecialistBase):
    name = "social_signal_specialist"
    role = "narrative_signal_review"
    provider_key = "social_llm"

    def _compact_payload(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        base_analysis = input_data.get("base_analysis") if isinstance(input_data.get("base_analysis"), dict) else {}
        source_summary = input_data.get("source_summary") if isinstance(input_data.get("source_summary"), dict) else {}
        compact = {
            "question": input_data.get("question", ""),
            "market_options": input_data.get("market_options", {}),
            "event_profile": input_data.get("event_profile", {}),
            "base_analysis": {
                "model_probability": base_analysis.get("model_probability"),
                "market_probability": base_analysis.get("market_probability"),
                "probability_gap": base_analysis.get("probability_gap"),
                "horizon": base_analysis.get("horizon"),
                "signal_summary": base_analysis.get("signal_summary"),
                "risk_summary": base_analysis.get("risk_summary"),
            },
            "source_summary": {
                "count": source_summary.get("count"),
                "titles": self.normalize_list(source_summary.get("titles"))[:10],
            },
        }
        return compact

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        logger = logging.getLogger(__name__)
        router = TopAnalysisProviderRouter()
        compact_input = self._compact_payload(input_data)
        logger.info(
            "social_signal_compact_payload question=%s has_options=%s",
            compact_input.get("question", ""),
            bool(compact_input.get("market_options")),
        )
        payload = {"prompt": f"{SOCIAL_SIGNAL_SPECIALIST_PROMPT}\nINPUT:\n{compact_input}"}
        response = router.route(self.provider_key, payload)
        parsed = response.get("json") or {}
        safe_error = str(response.get("error", "unavailable") or "unavailable")[:200]
        if response.get("status") != "ok":
            logger.warning("social_signal_degraded reason=%s", safe_error)
            return {
                "specialist_name": self.name,
                "status": "ok",
                "provider_key": self.provider_key,
                "social_signal_strength": "unknown",
                "social_confidence": "low",
                "narratives": [],
                "notable_claims": [],
                "risk_flags": ["live_social_data_unavailable", "social_signal_degraded"],
                "error": safe_error,
            }
        if not parsed and (response.get("content", "") or "").strip():
            logger.warning("social_signal_non_json_fallback content_len=%s", len(response.get("content", "") or ""))
            return {
                "specialist_name": self.name,
                "status": "ok",
                "provider_key": self.provider_key,
                "social_signal_strength": "unknown",
                "social_confidence": "low",
                "narratives": [],
                "notable_claims": [],
                "risk_flags": ["social_signal_non_json_response"],
            }
        return {
            "specialist_name": self.name,
            "status": "ok",
            "provider_key": self.provider_key,
            "social_signal_strength": parsed.get("social_signal_strength", "unknown"),
            "social_confidence": parsed.get("social_confidence", "low"),
            "narratives": self.normalize_list(parsed.get("narratives")),
            "notable_claims": self.normalize_list(parsed.get("notable_claims")),
            "risk_flags": self.normalize_list(parsed.get("risk_flags")) or ["live_social_data_unavailable"],
        }
