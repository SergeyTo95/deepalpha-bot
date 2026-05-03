import re
from typing import Any, Dict, List, Optional, Tuple


class TradingPlanAgent:
    def run(
        self,
        result: dict,
        market_data: dict = None,
        news_data: dict = None,
        lang: str = "ru",
    ) -> dict:
        result = result or {}
        market_data = market_data or {}
        news_data = news_data or {}

        market_type = self._classify_market_type(result, market_data)
        market_probs = self._extract_market_probs(
            str(result.get("market_probability") or market_data.get("market_probability") or ""),
            result.get("options_breakdown") or market_data.get("options_breakdown") or "",
        )

        model_side, model_prob = self._extract_model_prediction(result)
        likely_side, market_prob = self._pick_likely_side(market_probs)

        if model_side not in market_probs:
            model_side = likely_side

        model_prob = float(model_prob if model_prob is not None else market_prob)
        market_prob_same_side = float(market_probs.get(model_side, market_prob or 0.0))

        edge = round(model_prob - market_prob_same_side, 1)
        abs_edge = abs(edge)

        confidence = self._normalize_confidence(str(result.get("confidence") or ""))
        sports_context = result.get("sports_context") if isinstance(result.get("sports_context"), dict) else None
        if sports_context and sports_context.get("data_quality") == "low":
            confidence = "low"

        if model_prob <= 0 or abs(model_prob - market_prob_same_side) < 0.2:
            edge = 0.0
            abs_edge = 0.0

        value_assessment = self._value_assessment(abs_edge, edge)
        recommended_action, bet_side = self._recommend_action(
            model_side=model_side,
            abs_edge=abs_edge,
            confidence=confidence,
            value_assessment=value_assessment,
        )

        entry_zone, avoid_zone = self._entry_zone(model_side, market_prob_same_side, model_prob, lang)

        confirmation_triggers = self._confirmation_triggers(result, market_type, lang)
        invalidation_triggers = self._invalidation_triggers(model_side, lang)

        if confidence == "low" and recommended_action.startswith("CONSIDER"):
            recommended_action = "WAIT"
            bet_side = "NONE"

        if confidence == "low" and abs_edge < 15:
            recommended_action = "NO TRADE"
            bet_side = "NONE"

        missing_data = self._missing_data(result, sports_context)
        key_reasons = self._key_reasons(model_side, model_prob, market_prob_same_side, edge, lang)
        risk_reasons = self._risk_reasons(confidence, missing_data, lang)

        summary = self._summary(model_side, value_assessment, recommended_action, entry_zone, lang)

        return {
            "market_type": market_type,
            "likely_side": likely_side,
            "bet_side": bet_side,
            "model_probability": round(model_prob, 1),
            "market_probability": round(market_prob_same_side, 1),
            "edge": round(edge, 1),
            "edge_side": model_side if abs_edge >= 3 else "NONE",
            "value_assessment": value_assessment,
            "recommended_action": recommended_action,
            "confidence": confidence,
            "entry_zone": entry_zone,
            "avoid_zone": avoid_zone,
            "invalidation_triggers": invalidation_triggers,
            "confirmation_triggers": confirmation_triggers,
            "key_reasons": key_reasons,
            "risk_reasons": risk_reasons,
            "missing_data": missing_data,
            "summary": summary,
            "debug": {
                "market_probs": market_probs,
                "model_side": model_side,
                "sports_data_quality": (sports_context or {}).get("data_quality"),
            },
        }

    def _extract_market_probs(self, text: str, options_breakdown: str = "") -> Dict[str, float]:
        out: Dict[str, float] = {}
        raw = f"{text} | {options_breakdown}".lower()
        for label, key in (("yes", "YES"), ("no", "NO"), ("up", "UP"), ("down", "DOWN"), ("да", "YES"), ("нет", "NO")):
            m = re.search(rf"{re.escape(label)}\s*[:\-]?\s*([\d.]+)%", raw)
            if m:
                out[key] = float(m.group(1))

        for m in re.finditer(r"([^|:,/]+)\s*[:\-]\s*([\d.]+)%", f"{text} | {options_breakdown}"):
            k = m.group(1).strip()
            v = float(m.group(2))
            if k and k.upper() not in out:
                out[k] = v

        return out

    def _extract_model_prediction(self, result: Dict[str, Any]) -> Tuple[str, Optional[float]]:
        primary = str(result.get("probability") or result.get("display_prediction") or "")
        prob_text = " ".join([primary, str(result.get("conclusion") or "")])
        m = re.search(r"([\d.]+)%", prob_text)
        prob = float(m.group(1)) if m else None

        low = prob_text.lower()
        explicit = re.search(r"\b(yes|no|up|down|да|нет|вверх|вниз)\b", low)
        if explicit:
            map_side = {
                "yes": "YES", "да": "YES",
                "no": "NO", "нет": "NO",
                "up": "UP", "вверх": "UP",
                "down": "DOWN", "вниз": "DOWN",
            }
            return map_side.get(explicit.group(1), "UNKNOWN"), prob
        if any(x in low for x in [" no ", "no:", " нет", "не "]):
            return "NO", prob
        if any(x in low for x in [" yes ", "yes:", " да", "будет", "win"]):
            return "YES", prob
        if "down" in low or "вниз" in low:
            return "DOWN", prob
        if "up" in low or "вверх" in low:
            return "UP", prob
        return "UNKNOWN", prob

    def _pick_likely_side(self, market_probs: Dict[str, float]) -> Tuple[str, float]:
        if not market_probs:
            return "UNKNOWN", 50.0
        k = max(market_probs, key=lambda x: market_probs[x])
        return k, float(market_probs[k])

    def _classify_market_type(self, result: Dict[str, Any], market_data: Dict[str, Any]) -> str:
        q = str(result.get("question") or market_data.get("question") or "").lower()
        if result.get("options_breakdown"):
            return "multiple_choice"
        if any(x in q for x in ["bitcoin", "btc", "above $", "below $"]):
            return "crypto_threshold"
        if any(x in q for x in ["win", "vs", "match", "league"]):
            return "sports"
        if any(x in q for x in ["election", "president", "senate"]):
            return "politics"
        if any(x in q for x in ["fed", "inflation", "rate"]):
            return "macro"
        return "binary"

    def _normalize_confidence(self, conf: str) -> str:
        c = conf.lower()
        if "high" in c or "высок" in c:
            return "high"
        if "medium" in c or "сред" in c:
            return "medium"
        return "low"

    def _value_assessment(self, abs_edge: float, edge: float) -> str:
        if abs_edge < 3:
            return "fair_price"
        if abs_edge < 8:
            return "no_edge"
        if abs_edge < 15:
            return "possible_value"
        return "strong_value"

    def _recommend_action(self, model_side: str, abs_edge: float, confidence: str, value_assessment: str) -> Tuple[str, str]:
        if confidence == "low":
            return "NO TRADE", "NONE"
        if abs_edge < 3:
            return "WAIT", "NONE"
        if abs_edge < 8:
            return (f"WATCH {model_side}", "NONE")
        if abs_edge < 15:
            return (f"WATCH {model_side}" if confidence == "medium" else f"CONSIDER {model_side}", model_side if confidence == "high" else "NONE")
        return (f"CONSIDER {model_side}", model_side)

    def _entry_zone(self, side: str, market_prob: float, model_prob: float, lang: str) -> Tuple[str, str]:
        dec = 6 if 50 <= market_prob <= 65 else (10 if market_prob > 65 else 5)
        low = max(1, round(market_prob - dec - 2, 1))
        high = max(1, round(market_prob - dec + 1, 1))
        entry = f"{side} ≤{low}–{high}% or strong confirmation trigger"
        avoid = f"Avoid chasing {side} above {round(market_prob + 3,1)}% without new data"
        if lang == "ru":
            entry = f"{side} интересен только ≤{low}–{high}% или при сильном подтверждении"
            avoid = f"Избегать входа в {side} выше {round(market_prob + 3,1)}% без новых данных"
        return entry, avoid

    def _confirmation_triggers(self, result: Dict[str, Any], market_type: str, lang: str) -> List[str]:
        base = ["new credible source", "sharp market repricing"] if lang == "en" else ["новый надёжный источник", "резкий ценовой сдвиг рынка"]
        return base

    def _invalidation_triggers(self, side: str, lang: str) -> List[str]:
        return [f"Price moves materially against {side}", "Contradicting official evidence"] if lang == "en" else [f"Цена существенно уходит против {side}", "Официальные данные против тезиса"]

    def _missing_data(self, result: Dict[str, Any], sports_context: Optional[Dict[str, Any]]) -> List[str]:
        miss = []
        if sports_context:
            miss.extend(sports_context.get("missing_data") or [])
        if not (result.get("news_sources") or result.get("news_items")):
            miss.append("limited fresh sources")
        return miss[:5]

    def _key_reasons(self, side: str, model: float, market: float, edge: float, lang: str) -> List[str]:
        if lang == "ru":
            return [
                f"{side} вероятнее по модели: {model:.1f}%",
                f"Рынок по этой же стороне: {market:.1f}%",
                "Вероятность и value оцениваются отдельно",
            ]
        return [
            f"{side} is more likely by model: {model:.1f}%",
            f"Market on same side: {market:.1f}%",
            "Probability and value are evaluated separately",
        ]

    def _risk_reasons(self, confidence: str, missing_data: List[str], lang: str) -> List[str]:
        risks = ["low confidence regime" if lang == "en" else "режим пониженной уверенности"] if confidence == "low" else []
        if missing_data:
            risks.append("data gaps remain" if lang == "en" else "есть пробелы в данных")
        return risks

    def _summary(self, side: str, value: str, action: str, entry: str, lang: str) -> str:
        if lang == "ru":
            return f"{side} вероятнее, но статус цены: {value}. Действие: {action}. План входа: {entry}."
        return f"{side} is more likely, but pricing status is {value}. Action: {action}. Entry plan: {entry}."
