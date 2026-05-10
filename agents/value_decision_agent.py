from typing import Any, Dict

from agents.schemas.value_decision import ValueDecision, empty_value_decision


class ValueDecisionAgent:
    def decide(self, probability_estimate: Dict[str, Any], market_options: Dict[str, Any], event_profile: Dict[str, Any], structured_evidence: Dict[str, Any]) -> ValueDecision:
        out = empty_value_decision()
        market = self._normalize(market_options)
        out["market_price"] = market

        model_level = int((probability_estimate or {}).get("model_level") or 0)
        confidence = str((probability_estimate or {}).get("confidence") or "none")
        point = self._normalize((probability_estimate or {}).get("point_estimate") or {})

        if model_level == 0 or not point:
            out["reason"] = ["No independent probability was produced because high-impact drivers are missing or evidence is insufficient."]
            out["risk_flags"] = ["no_independent_model"]
            return out

        edge: Dict[str, float] = {}
        for side, m_price in market.items():
            if side in point:
                edge[side] = round(point[side] - m_price, 2)
        out["edge"] = edge
        best_side = "NONE"
        best_edge = 0.0
        for side, e in edge.items():
            if e > best_edge:
                best_side, best_edge = side, e
        out["best_side"] = best_side

        required_margin = 5 if confidence == "low" else 3
        for side, p in point.items():
            out["entry_price"][side] = round(max(1.0, p - required_margin), 2)
            if side in market and market[side] > p:
                out["avoid_price"][side] = round(market[side], 2)

        out["decision"] = self._decide(best_edge, confidence)
        out["reason"] = self._reasons(best_edge, confidence, best_side)
        out["risk_flags"] = self._risk_flags(confidence, structured_evidence, model_level)
        return out

    def _decide(self, best_edge: float, confidence: str) -> str:
        if best_edge < 2:
            return "NO_TRADE"
        if best_edge < 5:
            return "WAIT"
        if best_edge <= 8:
            return "WATCH"
        if confidence in {"medium", "high"}:
            return "CONSIDER"
        return "WATCH"

    def _reasons(self, best_edge: float, confidence: str, best_side: str):
        if best_side == "NONE":
            return ["Market price is above independent estimate; no value confirmed."]
        if best_edge > 8 and confidence == "low":
            return ["Positive edge exists, but confidence is low; keep as WATCH, not CONSIDER."]
        return ["Entry only makes sense if price is below required margin."]

    def _risk_flags(self, confidence: str, structured_evidence: Dict[str, Any], model_level: int):
        flags = []
        if confidence == "low":
            flags.append("low_confidence")
        missing = structured_evidence.get("missing_driver_data") if isinstance(structured_evidence, dict) else []
        if any(str(x.get("priority") or "").lower() in {"high", "very_high"} for x in (missing or [])):
            flags.append("missing_high_impact_data")
        src = (structured_evidence.get("source_quality") or {}) if isinstance(structured_evidence, dict) else {}
        if float(src.get("coverage_score") or 0) < 0.4:
            flags.append("low_source_coverage")
        if int(src.get("usable_sources_count") or 0) <= 1:
            flags.append("stale_or_weak_evidence")
        if model_level == 0:
            flags.append("no_independent_model")
        return flags

    def _normalize(self, options: Dict[str, Any]) -> Dict[str, float]:
        if not isinstance(options, dict):
            return {}
        out = {}
        for k, v in options.items():
            try:
                out[str(k).upper()] = float(v)
            except (TypeError, ValueError):
                pass
        return out
