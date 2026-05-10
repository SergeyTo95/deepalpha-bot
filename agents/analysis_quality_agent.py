from typing import Any, Dict, List

from agents.schemas.analysis_quality import (
    AnalysisQuality,
    empty_analysis_quality,
    safe_bool,
    safe_dict,
    safe_float,
    safe_int,
    safe_list,
)


class AnalysisQualityAgent:
    MIN_TOTAL_FACTS = 5

    def evaluate(
        self,
        question: str,
        outcome_map: Dict[str, Any],
        research_plan: Dict[str, Any],
        targeted_research: Dict[str, Any],
        event_profile: Dict[str, Any],
        market_options: Dict[str, float],
        model_options: Dict[str, float],
        probability_estimate: Dict[str, Any],
        value_decision: Dict[str, Any],
        source_summary: Dict[str, Any],
    ) -> AnalysisQuality:
        aq = empty_analysis_quality()

        targeted_research = safe_dict(targeted_research)
        research_plan = safe_dict(research_plan)
        event_profile = safe_dict(event_profile)
        market_options = safe_dict(market_options)
        probability_estimate = safe_dict(probability_estimate)
        value_decision = safe_dict(value_decision)
        source_summary = safe_dict(source_summary)

        sq = safe_dict(targeted_research.get("source_quality"))
        raw_sources_count = safe_int(sq.get("raw_sources_count"), safe_int(source_summary.get("raw_sources_count"), 0))
        matched_sources_count = safe_int(sq.get("matched_sources_count"), 0)
        high_relevance_sources_count = safe_int(sq.get("high_relevance_sources_count"), 0)
        medium_relevance_sources_count = safe_int(sq.get("medium_relevance_sources_count"), 0)
        filtered_sources_count = safe_int(sq.get("filtered_sources_count"), safe_int(safe_dict(targeted_research.get("coverage_attempt")).get("filtered_sources_count"), 0))
        claims_count = safe_int(sq.get("claims_count"), 0)
        coverage_score = max(0.0, min(1.0, safe_float(sq.get("coverage_score"), 0.0)))
        can_build_forecast = safe_bool(sq.get("can_build_forecast"), False)

        aq["source_quality"] = {
            "raw_sources_count": raw_sources_count,
            "matched_sources_count": matched_sources_count,
            "claims_count": claims_count,
            "coverage_score": coverage_score,
            "can_build_forecast": can_build_forecast,
            "high_relevance_sources_count": high_relevance_sources_count,
            "medium_relevance_sources_count": medium_relevance_sources_count,
            "filtered_sources_count": filtered_sources_count,
        }

        market_context = self._market_context(market_options, event_profile)
        aq["market_context"] = market_context

        point_estimate_exists = probability_estimate.get("point_estimate") is not None
        edge = safe_float(value_decision.get("edge"), 0.0)

        status = "incomplete"
        can_show_forecast = False
        can_show_likely_outcome = False
        can_show_value = False
        should_select_side = False
        confidence = "none"

        weak_relevance = (high_relevance_sources_count + medium_relevance_sources_count) < 2 and matched_sources_count > 0
        shared_facts = safe_int(safe_dict(targeted_research.get("shared_coverage")).get("facts_found"), 0)
        is_h2h = str(event_profile.get("market_type") or "").lower() == "head_to_head" or str(event_profile.get("event_type") or "").lower() == "tennis_head_to_head"
        if not can_build_forecast:
            status = "incomplete"
            confidence = "none" if claims_count == 0 else "low"
        elif is_h2h and shared_facts == 0:
            status = "weak"
            can_show_forecast = True
            confidence = "low"
        elif weak_relevance or (filtered_sources_count > matched_sources_count and matched_sources_count > 0):
            status = "weak"
            can_show_forecast = True
            confidence = "low"
        elif coverage_score < 0.7:
            status = "weak"
            can_show_forecast = True
            can_show_likely_outcome = point_estimate_exists
            should_select_side = point_estimate_exists
            confidence = "low"
            can_show_value = point_estimate_exists and edge > 0 and self._value_confidence_ok(value_decision)
        elif claims_count >= self.MIN_TOTAL_FACTS and (high_relevance_sources_count + medium_relevance_sources_count) >= 2 and filtered_sources_count <= max(2, matched_sources_count):
            status = "complete"
            can_show_forecast = True
            can_show_likely_outcome = True
            should_select_side = True
            can_show_value = edge > 0
            confidence = "high" if coverage_score >= 0.85 and claims_count >= (self.MIN_TOTAL_FACTS + 2) else "medium"
        else:
            status = "weak"
            can_show_forecast = True
            can_show_likely_outcome = point_estimate_exists
            should_select_side = point_estimate_exists
            confidence = "low"

        reasons: List[str] = []
        blocking_issues: List[str] = []

        if coverage_score < 0.7:
            reasons.append("Targeted research coverage is below required threshold.")
        if claims_count < self.MIN_TOTAL_FACTS:
            reasons.append("Not enough claims were extracted from existing sources.")
        if claims_count == 0:
            blocking_issues.append("No claims were extracted from available sources.")
        if matched_sources_count == 0:
            blocking_issues.append("No matched sources were found for critical drivers.")
        if not can_build_forecast:
            blocking_issues.append("Independent forecast cannot be built from targeted research.")
        if point_estimate_exists is False and (status == "weak" or status == "complete"):
            blocking_issues.append("Model point_estimate is missing.")

        if market_context["is_equal_market"] and status == "incomplete":
            can_show_likely_outcome = False
            should_select_side = False
            blocking_issues.append("Market is close to 50/50 and independent forecast data is insufficient.")
            reasons.append("Market is close to 50/50 and no independent edge is confirmed.")

        missing_critical_data = self._collect_missing_critical_data(research_plan, targeted_research)
        if missing_critical_data:
            reasons.append("Per-outcome minimum facts were not met.")

        aq["status"] = status
        aq["can_show_forecast"] = can_show_forecast
        aq["can_show_likely_outcome"] = can_show_likely_outcome
        aq["can_show_value"] = can_show_value
        aq["should_select_side"] = should_select_side
        aq["confidence"] = confidence
        aq["quality_score"] = self._quality_score(status, coverage_score, claims_count, can_build_forecast)
        aq["reasons"] = reasons[:12]
        aq["blocking_issues"] = blocking_issues[:12]
        aq["missing_critical_data"] = missing_critical_data

        incomplete_paid_analysis = (
            status == "incomplete"
            and not can_show_forecast
            and (claims_count == 0 or matched_sources_count == 0)
        )
        refund_recommended = incomplete_paid_analysis and (
            raw_sources_count == 0 or matched_sources_count == 0 or claims_count == 0
        )

        aq["incomplete_paid_analysis"] = incomplete_paid_analysis
        aq["refund_recommended"] = refund_recommended
        aq["parser"] = {
            "name": "analysis_quality_agent_v1",
            "confidence": "high" if status == "complete" else "medium" if status == "weak" else "low",
        }
        return aq

    def _market_context(self, market_options: Dict[str, Any], event_profile: Dict[str, Any]) -> Dict[str, Any]:
        values = [safe_float(v, -1.0) for v in (market_options or {}).values()]
        valid_values = [v for v in values if v >= 0.0]
        spread = 100.0
        if len(valid_values) >= 2:
            spread = max(valid_values) - min(valid_values)
        is_equal_market = len(valid_values) == 2 and spread <= 2.0
        market_type = str(event_profile.get("market_type") or "unknown")
        return {
            "is_equal_market": is_equal_market,
            "market_spread": round(spread, 4),
            "outcome_count": len(market_options or {}),
            "market_type": market_type,
        }

    def _collect_missing_critical_data(self, research_plan: Dict[str, Any], targeted_research: Dict[str, Any]) -> List[Any]:
        out: List[Any] = []

        for item in safe_list(targeted_research.get("missing_research")):
            if not isinstance(item, dict):
                continue
            priority = str(item.get("priority") or "").lower()
            if priority in {"high", "critical"}:
                out.append(item.get("driver") or item.get("query") or item)

        mdp = safe_dict(research_plan.get("minimum_data_policy"))
        for driver in safe_list(mdp.get("critical_drivers")):
            out.append(driver)

        for item in safe_list(targeted_research.get("outcome_coverage")):
            if not isinstance(item, dict):
                continue
            for driver in safe_list(item.get("missing_drivers")):
                out.append(driver)

        deduped: List[Any] = []
        seen = set()
        for item in out:
            key = str(item).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= 12:
                break
        return deduped

    def _quality_score(self, status: str, coverage_score: float, claims_count: int, can_build_forecast: bool) -> float:
        if not can_build_forecast:
            return round(min(0.35, coverage_score * 0.4 + min(claims_count, 3) * 0.03), 4)
        base = 0.45 if status == "weak" else 0.7
        return round(min(1.0, base + coverage_score * 0.2 + min(claims_count, 8) * 0.015), 4)

    def _value_confidence_ok(self, value_decision: Dict[str, Any]) -> bool:
        confidence = str(value_decision.get("confidence") or "").lower()
        return confidence in {"medium", "high"}
