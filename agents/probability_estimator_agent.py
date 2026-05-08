from typing import Any, Dict, List, Tuple

from agents.schemas.probability import ProbabilityEstimate, empty_probability_estimate


class ProbabilityEstimatorAgent:
    IMPACT_BASE = {"low": 0.75, "medium": 1.5, "high": 3.0, "very_high": 5.0, "unknown": 0.0}
    CONFIDENCE_WEIGHT = {"low": 0.4, "medium": 0.7, "high": 1.0, "unknown": 0.2}

    def estimate(self, event_profile: Dict[str, Any], driver_map: Dict[str, Any], data_plan: Dict[str, Any], structured_evidence: Dict[str, Any], market_options: Dict[str, Any], model_options: Dict[str, Any] = None) -> ProbabilityEstimate:
        out = empty_probability_estimate()
        market = self._normalize_options(market_options)
        existing_model = self._normalize_options(model_options)
        out["base_prior"] = market

        source_quality = structured_evidence.get("source_quality") if isinstance(structured_evidence, dict) else {}
        coverage_score = float((source_quality or {}).get("coverage_score") or 0.0)
        usable_sources_count = int((source_quality or {}).get("usable_sources_count") or 0)
        missing_high_impact = len([m for m in (structured_evidence.get("missing_driver_data") or []) if str(m.get("priority") or "").lower() in {"high", "very_high"}])
        out["data_quality"] = {
            "coverage_score": round(coverage_score, 3),
            "usable_sources_count": usable_sources_count,
            "missing_high_impact_drivers": missing_high_impact,
        }

        facts = structured_evidence.get("facts") if isinstance(structured_evidence, dict) else []
        facts = facts if isinstance(facts, list) else []

        if not facts and coverage_score == 0 and not existing_model:
            out["limitations"].append("No usable evidence and zero coverage; independent probability not produced.")
            return out

        model_level = self._infer_model_level(facts, coverage_score, usable_sources_count, missing_high_impact, event_profile, existing_model)
        out["model_level"] = model_level
        out["confidence"] = {0: "none", 1: "low", 2: "medium", 3: "high"}.get(model_level, "low")

        if existing_model:
            point = dict(existing_model)
            out["why"].append("Used existing model_options as primary independent model signal.")
        elif not market:
            out["limitations"].append("Market options unavailable; cannot anchor base prior.")
            return out
        else:
            point = dict(market)

        point, adjustments = self._apply_adjustments(point, facts, model_level)
        out["adjustments"] = adjustments

        if point:
            out["point_estimate"] = point
            out["probability_range"] = self._build_ranges(point, model_level)

        if not facts:
            out["limitations"].append("No extracted directional facts; model relies on prior only.")
        if missing_high_impact > 0:
            out["limitations"].append("Some high-impact drivers are missing.")
        if coverage_score < 0.4:
            out["limitations"].append("Low source coverage limits confidence.")

        if adjustments:
            out["why"].append("Driver-level directional adjustments applied with conservative caps.")
        if out["model_level"] == 1:
            out["why"].append("Evidence is weak; output kept broad and cautious.")
        if out["model_level"] >= 2:
            out["why"].append("Multiple usable facts support an independent estimate.")
        return out

    def _infer_model_level(self, facts: List[Dict[str, Any]], coverage_score: float, usable_sources_count: int, missing_high_impact: int, event_profile: Dict[str, Any], existing_model: Dict[str, float]) -> int:
        if existing_model:
            if coverage_score == 0 and usable_sources_count == 0:
                return 1
            return 2
        if not facts and coverage_score == 0:
            return 0
        if coverage_score >= 0.75 and usable_sources_count >= 5 and missing_high_impact <= 1 and len(facts) >= 4:
            return 3
        if len(facts) >= 2 and (coverage_score >= 0.35 or usable_sources_count >= 2):
            return 2
        event_type = str((event_profile or {}).get("event_type") or "")
        if event_type in {"football_tournament_winner_group", "crypto_price_threshold", "football_team_win", "tennis_head_to_head", "company_legal", "legal_regulatory"}:
            return 1 if facts else 0
        return 1

    def _apply_adjustments(self, point: Dict[str, float], facts: List[Dict[str, Any]], model_level: int) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
        if not point or model_level == 0:
            return {}, []
        adjustments: List[Dict[str, Any]] = []
        total_yes_shift = 0.0
        cap = {1: 5.0, 2: 10.0, 3: 15.0}.get(model_level, 5.0)
        is_binary = set(point.keys()) == {"YES", "NO"}

        for fact in facts:
            direction = str(fact.get("direction") or "UNKNOWN").upper()
            impact = str(fact.get("impact") or "unknown").lower()
            conf = str(fact.get("confidence") or "unknown").lower()
            base = self.IMPACT_BASE.get(impact, 0.0)
            weight = self.CONFIDENCE_WEIGHT.get(conf, 0.2)
            delta = base * weight
            adjustments.append({
                "driver_id": str(fact.get("driver_id") or "unknown"),
                "direction": direction if direction in {"YES", "NO", "NEUTRAL", "UNKNOWN"} else "UNKNOWN",
                "impact_points": round(base, 3),
                "confidence_weight": round(weight, 3),
                "reason": str(fact.get("claim") or fact.get("driver_label") or "Directional fact"),
            })
            if not is_binary:
                continue
            if direction == "YES":
                total_yes_shift += delta
            elif direction == "NO":
                total_yes_shift -= delta

        if is_binary:
            total_yes_shift = max(-cap, min(cap, total_yes_shift))
            yes = float(point.get("YES", 50.0)) + total_yes_shift
            no = float(point.get("NO", 50.0)) - total_yes_shift
            yes = max(1.0, min(99.0, yes))
            no = max(1.0, min(99.0, no))
            s = yes + no
            point = {"YES": round((yes / s) * 100.0, 2), "NO": round((no / s) * 100.0, 2)}
        return point, adjustments

    def _build_ranges(self, point: Dict[str, float], model_level: int) -> Dict[str, Dict[str, float]]:
        if model_level == 0:
            return {}
        width = {1: 5.0, 2: 3.0, 3: 2.0}.get(model_level, 5.0)
        out: Dict[str, Dict[str, float]] = {}
        for side, p in point.items():
            low = max(1.0, min(99.0, float(p) - width))
            high = max(1.0, min(99.0, float(p) + width))
            out[side] = {"low": round(low, 2), "high": round(high, 2)}
        return out

    def _normalize_options(self, options: Dict[str, Any]) -> Dict[str, float]:
        if not isinstance(options, dict):
            return {}
        out: Dict[str, float] = {}
        for k, v in options.items():
            try:
                out[str(k).upper()] = float(v)
            except (TypeError, ValueError):
                continue
        return out
