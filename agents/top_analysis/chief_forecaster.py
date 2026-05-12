from typing import Any, Dict

from .base import TopAnalysisSpecialistBase
from .prompts import CHIEF_FORECASTER_PROMPT
from .provider_router import TopAnalysisProviderRouter


class ChiefForecaster(TopAnalysisSpecialistBase):
    name = "chief_forecaster"
    role = "synthesis"
    provider_key = "chief_llm"

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        router = TopAnalysisProviderRouter()
        payload = {"prompt": f"{CHIEF_FORECASTER_PROMPT}\nINPUT:\n{input_data}"}
        response = router.route(self.provider_key, payload)
        parsed = response.get("json") or {}
        if response.get("status") != "ok":
            return {"specialist_name": self.name, "status": "error", "provider_key": self.provider_key, "error": response.get("error", "unavailable")}
        return {
            "specialist_name": self.name,
            "status": "ok",
            "provider_key": self.provider_key,
            "final_forecast_available": bool(parsed.get("final_forecast_available", False)),
            "forecast_summary": parsed.get("forecast_summary", "No strong forecast."),
            "probability_range": parsed.get("probability_range", {}),
            "confidence": parsed.get("confidence", "Low"),
            "value_summary": parsed.get("value_summary", "No clear value."),
            "final_conclusion": parsed.get("final_conclusion", "No clear value."),
            "key_factors": self.normalize_list(parsed.get("key_factors")),
            "risks": self.normalize_list(parsed.get("risks")),
            "raw_content": response.get("content", ""),
        }
