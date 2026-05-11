from typing import Any, Dict

from .chief_forecaster import ChiefForecaster
from .research_specialist import ResearchSpecialist
from .risk_auditor import RiskAuditor
from .social_signal_specialist import SocialSignalSpecialist


class TopAnalysisAgent:
    def __init__(self) -> None:
        self.research_specialist = ResearchSpecialist()
        self.social_signal_specialist = SocialSignalSpecialist()
        self.risk_auditor = RiskAuditor()
        self.chief_forecaster = ChiefForecaster()

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            research_result = self.research_specialist.run(input_data)
            social_signal_result = self.social_signal_specialist.run(input_data)
            risk_audit_result = self.risk_auditor.run(input_data)

            chief_input = {
                "input_data": input_data,
                "research_result": research_result,
                "social_signal_result": social_signal_result,
                "risk_audit_result": risk_audit_result,
            }
            chief_forecast_result = self.chief_forecaster.run(chief_input)

            return {
                "status": "completed_placeholder",
                "question": input_data.get("question", ""),
                "research_result": research_result,
                "social_signal_result": social_signal_result,
                "risk_audit_result": risk_audit_result,
                "chief_forecast_result": chief_forecast_result,
                "final_probability_range": chief_forecast_result.get("probability_range", {}),
                "final_recommendation": chief_forecast_result.get("recommendation", "WAIT"),
                "user_facing_summary": chief_forecast_result.get(
                    "summary", "Top Analysis engine skeleton is prepared but not connected."
                ),
            }
        except Exception:
            return {
                "status": "placeholder",
                "question": input_data.get("question", "") if isinstance(input_data, dict) else "",
                "research_result": {"status": "placeholder"},
                "social_signal_result": {"status": "placeholder"},
                "risk_audit_result": {"status": "placeholder"},
                "chief_forecast_result": {"status": "placeholder"},
                "final_probability_range": {},
                "final_recommendation": "WAIT",
                "user_facing_summary": "Top Analysis engine skeleton is prepared but not connected.",
            }
