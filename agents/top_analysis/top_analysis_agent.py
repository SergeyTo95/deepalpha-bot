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
        research_result = self.research_specialist.run(input_data)
        social_signal_result = self.social_signal_specialist.run(input_data)
        risk_audit_result = self.risk_auditor.run(input_data)
        for result in (research_result, social_signal_result, risk_audit_result):
            if result.get("status") != "ok":
                return {"status": "maintenance", "final_available": False, "error": "required_component_unavailable", "user_message_key": "top_analysis_maintenance"}

        chief_forecast_result = self.chief_forecaster.run(
            {"input_data": input_data, "research_result": research_result, "social_signal_result": social_signal_result, "risk_audit_result": risk_audit_result}
        )
        if chief_forecast_result.get("status") != "ok":
            return {"status": "maintenance", "final_available": False, "error": "required_component_unavailable", "user_message_key": "top_analysis_maintenance"}
        if not bool(chief_forecast_result.get("final_forecast_available")):
            return {"status": "maintenance", "final_available": False, "error": "final_forecast_unavailable", "user_message_key": "top_analysis_maintenance"}

        return {
            "status": "ok",
            "final_available": True,
            "question": input_data.get("question", ""),
            "research_result": research_result,
            "social_signal_result": social_signal_result,
            "risk_audit_result": risk_audit_result,
            "chief_forecast_result": chief_forecast_result,
            "final_probability_range": chief_forecast_result.get("probability_range", {}),
            "final_recommendation": chief_forecast_result.get("final_conclusion", "No clear value."),
            "user_facing_summary": chief_forecast_result.get("forecast_summary", "No strong forecast."),
        }
