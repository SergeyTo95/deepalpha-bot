from typing import Any, Dict, List

from agents.schemas.forecast_card import empty_forecast_card


class ForecastCardAgent:
    def build(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        card = empty_forecast_card()

        market_options = self._as_dict(data.get("market_options"))
        model_options = self._as_dict(data.get("model_options"))
        option_differences = self._as_dict(data.get("option_differences"))
        analyst_view = self._as_dict(data.get("analyst_view"))
        no_model = self._as_dict(data.get("no_model_analysis"))
        forecast_evidence = self._as_dict(data.get("forecast_evidence"))
        source_summary = self._as_dict(data.get("source_summary"))
        event_drivers = self._as_dict(data.get("event_drivers"))
        category_context = self._as_dict(data.get("category_context"))

        card["market"].update({
            "question": str(data.get("title") or data.get("question") or ""),
            "category_type": str(data.get("category_type") or ""),
            "subcategory": str(data.get("subcategory") or ""),
            "market_type": str(data.get("market_type") or ""),
            "market_price": market_options,
            "deadline": str(data.get("event_deadline") or ""),
            "resolution_summary": str(data.get("resolution_summary") or ""),
        })

        entities = data.get("entities") if isinstance(data.get("entities"), list) else []
        side_meanings = self._as_dict(data.get("side_meanings"))
        card["event_profile"].update({
            "event_type": str(data.get("market_type") or ""),
            "yes_condition": str(side_meanings.get("YES") or ""),
            "no_condition": str(side_meanings.get("NO") or ""),
            "target_entity": str(data.get("primary_entity") or (entities[0] if entities else "")),
            "target_group": str(data.get("subcategory") or ""),
            "competition": str(data.get("subcategory") or ""),
            "event_target": str(data.get("event_target") or ""),
        })

        card["drivers"] = {
            "yes": self._as_list(event_drivers.get("yes")),
            "no": self._as_list(event_drivers.get("no")),
            "neutral": self._as_list(event_drivers.get("neutral")),
        }

        card["data_requirements"] = (
            self._as_list(no_model.get("next_check_checklist"))
            or self._as_list(event_drivers.get("must_find"))
            or self._category_fallback(str(data.get("category_type") or ""))
        )

        card["evidence"] = {
            "for_yes": self._as_list(forecast_evidence.get("for_yes")),
            "for_no": self._as_list(forecast_evidence.get("for_no")),
            "missing_data": self._as_list(forecast_evidence.get("missing_critical_data"))
            or self._as_list(no_model.get("why_no_model"))
            or self._as_list(category_context.get("uncertainties")),
            "contradictions": self._as_list(forecast_evidence.get("contradictions")),
        }

        if model_options:
            card["model"] = {
                "model_level": 1,
                "probability_range": {},
                "point_estimate": model_options,
                "confidence": str(analyst_view.get("confidence") or data.get("confidence") or "none"),
                "why": self._as_list(analyst_view.get("why") or data.get("reasoning")),
            }
        else:
            card["model"] = {
                "model_level": 0,
                "probability_range": {},
                "point_estimate": {},
                "confidence": "none",
                "why": self._as_list(analyst_view.get("why") or data.get("reasoning")),
            }

        decision = (
            analyst_view.get("recommended_action")
            or data.get("recommended_action")
            or "NO TRADE"
        )
        best_side = (
            analyst_view.get("best_priced_option")
            or data.get("edge_side")
            or data.get("bet_side")
            or "NONE"
        )

        card["value"] = {
            "market_price": market_options,
            "edge": option_differences,
            "decision": str(decision),
            "best_side": str(best_side),
            "entry_price": {},
        }

        card["what_would_change"] = self._as_list(no_model.get("what_changes_for_entry"))
        card["risks"] = self._as_list(analyst_view.get("risk_factors")) + self._as_list(forecast_evidence.get("counterarguments"))
        card["next_queries"] = self._as_list(data.get("news_queries_used")) or self._as_list(source_summary.get("news_queries_used"))
        return card

    def _category_fallback(self, category: str) -> List[str]:
        if category == "sports":
            return ["Lineups", "Injuries", "Motivation", "Form"]
        if category == "crypto":
            return ["Spot distance to target", "Volatility", "Catalyst confirmation"]
        return ["Primary sources", "Resolution clarity", "Deadline proximity"]

    def _as_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def _as_dict(self, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}
