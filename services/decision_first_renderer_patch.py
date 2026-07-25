from typing import Any, Dict


def install(telegram_module: Any) -> None:
    """Wrap the canonical forecast-card renderer with a decision-first header."""
    original = getattr(telegram_module, "_format_forecast_card_signal", None)
    if not callable(original) or getattr(original, "_deepalpha_decision_first", False):
        return

    def decision_first_renderer(result: dict, uid: int) -> str:
        text = original(result, uid)
        if not isinstance(text, str) or not text.strip():
            return text

        forecast_card = result.get("forecast_card") if isinstance(result, dict) else {}
        summary = (
            forecast_card.get("decision_summary")
            if isinstance(forecast_card, dict)
            and isinstance(forecast_card.get("decision_summary"), dict)
            else {}
        )
        if not summary or "🎯 РЕШЕНИЕ:" in text[:700] or "🎯 DECISION:" in text[:700]:
            return _clean_generic_outcome_labels(text)

        lang = (
            result.get("lang")
            or result.get("language")
            or getattr(telegram_module, "get_user_lang", lambda _uid: "ru")(uid)
        )
        block = build_decision_first_block(summary, lang=str(lang or "ru"))
        if not block:
            return _clean_generic_outcome_labels(text)

        marker = "🔎 DeepAlpha Signal"
        if text.startswith(marker):
            remainder = text[len(marker):].lstrip("\n")
            text = f"{marker}\n\n{block}\n\n{remainder}"
        else:
            text = f"{block}\n\n{text}"
        return _clean_generic_outcome_labels(text)

    decision_first_renderer._deepalpha_decision_first = True
    telegram_module._format_forecast_card_signal = decision_first_renderer


def build_decision_first_block(summary: Dict[str, Any], lang: str = "ru") -> str:
    if not isinstance(summary, dict):
        return ""

    is_ru = lang != "en"
    verdict = str(summary.get("verdict") or "NO_TRADE").upper().strip()
    side = str(summary.get("side") or "NONE").upper().strip()
    fair = _number(summary.get("fair_probability"))
    market = _number(summary.get("market_probability"))
    edge = _number(summary.get("edge_pp"))
    required = _number(summary.get("minimum_edge_required_pp"))
    entry = _number(summary.get("entry_price_max"))
    quality = _number(summary.get("data_quality_score"))
    confidence = str(summary.get("confidence") or "none").lower().strip()
    reason = str(summary.get("reason") or "").strip()

    if is_ru:
        verdict_text = {
            "BUY": "BUY — РАССМОТРЕТЬ ВХОД",
            "WATCH": "WATCH — НАБЛЮДАТЬ, НЕ ВХОДИТЬ",
            "NO_TRADE": "NO TRADE — НЕ ВХОДИТЬ",
        }.get(verdict, "NO TRADE — НЕ ВХОДИТЬ")
        confidence_text = {
            "high": "высокая",
            "medium": "средняя",
            "low": "низкая",
            "none": "нет независимой оценки",
        }.get(confidence, confidence)
        quality_text = _quality_text(summary.get("data_quality_label"), True)
        lines = [f"🎯 РЕШЕНИЕ: {verdict_text}"]
        if side != "NONE":
            lines.append(f"Сторона: {side}")
        if fair is not None:
            lines.append(f"Справедливая вероятность: {_fmt(fair)}%")
        if market is not None:
            lines.append(f"Цена рынка: {_fmt(market)}%")
        if edge is not None:
            lines.append(f"Edge: {edge:+.1f} п.п.")
        if required is not None:
            lines.append(f"Минимум для входа: +{required:.1f} п.п.")
        if entry is not None and side != "NONE":
            lines.append(f"📍 Интересная цена {side}: {_fmt(entry)}% или ниже")
        lines.append(f"🧠 Уверенность: {confidence_text}")
        if quality is not None:
            lines.append(f"🧾 Качество данных: {int(round(quality))}/10 — {quality_text}")
        if reason:
            lines.append(f"Почему: {reason}")
        return "\n".join(lines)

    verdict_text = {
        "BUY": "BUY — CONSIDER ENTRY",
        "WATCH": "WATCH — DO NOT ENTER YET",
        "NO_TRADE": "NO TRADE",
    }.get(verdict, "NO TRADE")
    quality_text = _quality_text(summary.get("data_quality_label"), False)
    lines = [f"🎯 DECISION: {verdict_text}"]
    if side != "NONE":
        lines.append(f"Side: {side}")
    if fair is not None:
        lines.append(f"Fair probability: {_fmt(fair)}%")
    if market is not None:
        lines.append(f"Market price: {_fmt(market)}%")
    if edge is not None:
        lines.append(f"Edge: {edge:+.1f} pp")
    if required is not None:
        lines.append(f"Minimum required edge: +{required:.1f} pp")
    if entry is not None and side != "NONE":
        lines.append(f"📍 Interesting {side} price: {_fmt(entry)}% or lower")
    lines.append(f"🧠 Confidence: {confidence}")
    if quality is not None:
        lines.append(f"🧾 Data quality: {int(round(quality))}/10 — {quality_text}")
    if reason:
        lines.append(f"Why: {reason}")
    return "\n".join(lines)


def _clean_generic_outcome_labels(text: str) -> str:
    replacements = {
        "👉 Наиболее вероятный исход: исход YES": "👉 Наиболее вероятный исход: YES",
        "👉 Наиболее вероятный исход: исход NO": "👉 Наиболее вероятный исход: NO",
        "— исход YES:": "— YES:",
        "— исход NO:": "— NO:",
        "DeepAlpha оценивает исход YES": "DeepAlpha оценивает YES",
        "DeepAlpha оценивает исход NO": "DeepAlpha оценивает NO",
        "выше вероятность сценария: исход YES": "выше вероятность сценария: YES",
        "выше вероятность сценария: исход NO": "выше вероятность сценария: NO",
    }
    clean = text
    for old, new in replacements.items():
        clean = clean.replace(old, new)
    return clean


def _quality_text(label: Any, is_ru: bool) -> str:
    value = str(label or "weak").lower().strip()
    if is_ru:
        return {
            "high": "высокое",
            "medium": "среднее",
            "limited": "ограниченное",
            "weak": "слабое",
        }.get(value, value)
    return {
        "high": "high",
        "medium": "medium",
        "limited": "limited",
        "weak": "weak",
    }.get(value, value)


def _number(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.1f}"
