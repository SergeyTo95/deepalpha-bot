from typing import Any, Dict, List

from agents.forecast_aware_trading_plan_agent import ForecastAwareTradingPlanAgent


class DecisionFirstTradingPlanAgent(ForecastAwareTradingPlanAgent):
    """Add an actionable decision summary to the existing forecast card.

    The summary does not invent a new probability or overwrite the established
    forecast/value logic. It translates the already computed fair probability,
    market price, edge, confidence and entry-price margin into a compact product
    contract that can be rendered first in Telegram and the WebApp.
    """

    def run(
        self,
        result: dict,
        market_data: dict = None,
        news_data: dict = None,
        lang: str = "ru",
    ) -> dict:
        output = super().run(
            result=result,
            market_data=market_data,
            news_data=news_data,
            lang=lang,
        )
        if not isinstance(output, dict):
            return output

        forecast_card = output.get("forecast_card")
        if not isinstance(forecast_card, dict):
            return output

        summary = build_decision_summary(
            forecast_card=forecast_card,
            analysis_quality=output.get("analysis_quality"),
            lang=lang,
        )
        forecast_card["decision_summary"] = summary
        output["decision_summary"] = summary
        output["forecast_card"] = forecast_card

        trading_plan = output.get("trading_plan")
        if isinstance(trading_plan, dict):
            trading_plan["decision_summary"] = summary
            trading_plan["forecast_card"] = forecast_card

        deep_analysis = output.get("deep_analysis")
        if isinstance(deep_analysis, dict):
            deep_analysis["decision_summary"] = summary
            deep_analysis["forecast_card"] = forecast_card

        return output


def build_decision_summary(
    *,
    forecast_card: Dict[str, Any],
    analysis_quality: Any = None,
    lang: str = "ru",
) -> Dict[str, Any]:
    card = forecast_card if isinstance(forecast_card, dict) else {}
    model = card.get("model") if isinstance(card.get("model"), dict) else {}
    value = card.get("value") if isinstance(card.get("value"), dict) else {}
    market = card.get("market") if isinstance(card.get("market"), dict) else {}

    point = _numeric_options(model.get("point_estimate"))
    market_price = _numeric_options(value.get("market_price")) or _numeric_options(
        market.get("market_price")
    )
    edge = _numeric_options(value.get("edge"), allow_negative=True)

    confidence = str(model.get("confidence") or "none").strip().lower()
    independent = bool(model.get("independent_probability"))
    model_level = _safe_int(model.get("model_level"), 0)
    raw_decision = str(value.get("decision") or "NO_TRADE").strip().upper().replace(" ", "_")

    likely_side = _highest_side(point)
    best_side = str(value.get("best_side") or "").strip().upper()
    if best_side not in point or float(edge.get(best_side, 0.0)) <= 0.0:
        best_side = likely_side

    fair_probability = point.get(best_side)
    current_market_probability = market_price.get(best_side)
    edge_pp = edge.get(best_side)
    if edge_pp is None and fair_probability is not None and current_market_probability is not None:
        edge_pp = round(fair_probability - current_market_probability, 2)

    required_edge = 5.0 if confidence == "low" else 3.0
    if confidence not in {"low", "medium", "high"}:
        required_edge = 5.0

    has_independent_model = bool(
        independent
        and model_level > 0
        and point
        and best_side in point
        and best_side in market_price
    )

    verdict = "NO_TRADE"
    if has_independent_model:
        if raw_decision == "CONSIDER":
            verdict = "BUY"
        elif raw_decision in {"WATCH", "WAIT"}:
            verdict = "WATCH"

    entry_prices = _numeric_options(value.get("entry_price"))
    entry_price_max = entry_prices.get(best_side)
    if entry_price_max is None and fair_probability is not None:
        entry_price_max = round(max(1.0, fair_probability - required_edge), 2)

    quality_score = _data_quality_score(analysis_quality)
    quality_label = _quality_label(quality_score)
    reason = _decision_reason(
        verdict=verdict,
        independent=has_independent_model,
        side=best_side,
        edge_pp=edge_pp,
        required_edge=required_edge,
        lang=lang,
    )

    return {
        "version": "1.0",
        "verdict": verdict,
        "entry_now": verdict == "BUY",
        "side": best_side or likely_side or "NONE",
        "fair_probability": _round_or_none(fair_probability),
        "market_probability": _round_or_none(current_market_probability),
        "edge_pp": _round_or_none(edge_pp),
        "minimum_edge_required_pp": required_edge,
        "entry_price_max": _round_or_none(entry_price_max),
        "confidence": confidence,
        "independent_probability": has_independent_model,
        "data_quality_score": quality_score,
        "data_quality_label": quality_label,
        "reason": reason,
        "recheck_conditions": _recheck_conditions(card, best_side or likely_side, lang),
    }


def _decision_reason(
    *,
    verdict: str,
    independent: bool,
    side: str,
    edge_pp: Any,
    required_edge: float,
    lang: str,
) -> str:
    edge_value = _safe_float(edge_pp)
    if lang == "ru":
        if not independent:
            return "Отдельная AI-оценка не подтверждена, поэтому вход по рыночной линии не рекомендуется."
        if verdict == "BUY":
            return f"Перевес {edge_value:+.1f} п.п. по {side} превышает требуемые {required_edge:.1f} п.п."
        if verdict == "WATCH":
            return f"Перевес {edge_value:+.1f} п.п. по {side} есть, но для входа требуется минимум {required_edge:.1f} п.п."
        return "Цена почти совпадает со справедливой оценкой; отдельного преимущества для входа нет."

    if not independent:
        return "A separate AI estimate is not confirmed, so entry at the market line is not recommended."
    if verdict == "BUY":
        return f"The {side} edge of {edge_value:+.1f} pp exceeds the required {required_edge:.1f} pp."
    if verdict == "WATCH":
        return f"A {side} edge of {edge_value:+.1f} pp exists, but entry requires at least {required_edge:.1f} pp."
    return "Market price is close to fair value; no separate entry advantage is confirmed."


def _recheck_conditions(card: Dict[str, Any], side: str, lang: str) -> List[str]:
    candidates: List[str] = []
    for key in ("what_would_change", "data_requirements"):
        items = card.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            text = _item_text(item)
            if text:
                candidates.append(text)

    evidence = card.get("evidence") if isinstance(card.get("evidence"), dict) else {}
    for item in evidence.get("missing_data") or []:
        text = _item_text(item)
        if text:
            candidates.append(text)

    if lang == "ru":
        candidates.append("Появятся подтверждённые данные по отсутствующим ключевым драйверам.")
        if side and side != "NONE":
            candidates.append(f"Цена {side} изменится более чем на 4 п.п.")
    else:
        candidates.append("Verified data appears for currently missing key drivers.")
        if side and side != "NONE":
            candidates.append(f"The {side} price moves by more than 4 pp.")

    out: List[str] = []
    seen = set()
    for item in candidates:
        clean = " ".join(str(item or "").split()).strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= 4:
            break
    return out


def _data_quality_score(value: Any) -> int:
    quality = value if isinstance(value, dict) else {}
    raw_score = _safe_float(quality.get("quality_score"))
    if raw_score is not None:
        if raw_score <= 1.0:
            return max(0, min(10, int(round(raw_score * 10.0))))
        return max(0, min(10, int(round(raw_score))))

    source = quality.get("source_quality") if isinstance(quality.get("source_quality"), dict) else {}
    coverage = max(0.0, min(1.0, _safe_float(source.get("coverage_score")) or 0.0))
    matched = max(0, _safe_int(source.get("matched_sources_count"), 0))
    claims = max(0, _safe_int(source.get("claims_count"), 0))
    score = coverage * 6.0 + min(matched, 2) * 1.0 + min(claims, 4) * 0.5
    return max(0, min(10, int(round(score))))


def _quality_label(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 6:
        return "medium"
    if score >= 3:
        return "limited"
    return "weak"


def _numeric_options(value: Any, allow_negative: bool = False) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, float] = {}
    for key, raw in value.items():
        number = _safe_float(raw)
        if number is None:
            continue
        if not allow_negative and number < 0:
            continue
        out[str(key or "").upper().strip()] = number
    return out


def _highest_side(options: Dict[str, float]) -> str:
    if not options:
        return "NONE"
    return max(options.items(), key=lambda item: item[1])[0]


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("description")
        or item.get("query")
        or item.get("driver_label")
        or item.get("claim")
        or item.get("driver")
        or ""
    ).strip()


def _safe_float(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _round_or_none(value: Any) -> Any:
    number = _safe_float(value)
    return round(number, 2) if number is not None else None
