from typing import Dict

from .base import TopAnalysisSpecialistBase
from .schemas import ChiefForecastResult


class ChiefForecaster(TopAnalysisSpecialistBase):
    name = "chief_forecaster"
    role = "synthesis"
    provider_key = "chief_llm"

    def run(self, input_data: Dict[str, object]) -> Dict[str, object]:
        try:
            result = ChiefForecastResult(
                specialist_name=self.name,
                status="placeholder",
                provider_key=self.provider_key,
                final_forecast_available=False,
                probability_range={},
                confidence="unknown",
                recommendation="WAIT",
                summary="Top Analysis engine skeleton is prepared but not connected.",
            )
            return self.safe_result(result, fallback={"status": "placeholder", "specialist_name": self.name})
        except Exception:
            return {"status": "placeholder", "specialist_name": self.name}
