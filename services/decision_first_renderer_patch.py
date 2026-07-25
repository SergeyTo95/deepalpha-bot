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

        summary = extract_decision_summary(result)
        lang = (
            result.get("lang")
            or result.get("language")
            or getattr(telegram_module, "get_user_lang", lambda _uid: "ru")(uid)
        )
        return prepend_decision_first_block(text, summary, lang=str(lang or "ru"))

    decision_first_renderer._deepalpha_decision_first = True
    telegram_module._format_forecast_card_signal = decision_first_renderer


def extract_decision_summary(result: Any) -> Dict[str, Any]:
    payload = result if isinstance(result, dict) else {}
    direct = payload.get("decision_summary")
    if isinstance(direct, dict) and direct:
        return direct
    forecast_card = payload.get("forecast_card") if isinstance(payload.get("forecast_card"), dict) else {}
    nested = forecast_card.get("decision_summary")
    return nested if isinstance(nested, dict) else {}


def prepend_decision_first_block(text: str, summary: Dict[str, Any], lang: str = "ru") -> str:
    clean_text = _clean_generic_outcome_labels(str(text or ""))
    if not clean_text.strip() or not isinstance(summary, dict) or not summary:
        return clean_text
    if "🎯 РЕШЕНИЕ:" in clean_text[:700] or "🎯 DECISION:" in clean_text[:700]:
        return clean_text

    block = build_decision_first_block(summary, lang=lang)
    if not block:
        return clean_text

    marker = "🔎 DeepAlpha Signal"
    if clean_text.startswith(marker):
        remainder = clean_text[len(marker):].lstrip("\n")
        return f"{marker}\n\n{block}\n\n{remainder}"
    return f"{block}\n\n{clean_text}"


def build_decision_first_block(summary: Dict[str, Any], lang: str = "ru") -> str:
    if not isinstance(summary, dict):
        return ""

    is_ru = lang != "en"
    verdict = str(summary.get("verdict") or "NO_TRADE").upper().strip()
    side = str(summary.get("side") or "NONE").upper().strip()
    fair = _number(summary.get("fair_probability"))
    market = _number(summary.get("market_probability"))
    edge = _number(summary.get("edge_pp"))
    watch_required = _number(summary.get("watch_edge_required_pp"))
    watch_price = _number(summary.get("watch_price_max"))
    buy_required = _number(summary.get("minimum_edge_required_pp"))
    buy_price = _number(summary.get("entry_price_max"))
    buy_available = bool(summary.get("buy_available"))
    quality = _number(summary.get("data_quality_score"))
    confidence = str(summary.get("confidence") or "none").lower().strip()
    reason = str(summary.get("reason") or "").strip()

    if is_ru:
        verdict_text = {
            "BUY": "BUY — РАССМОТРЕТЬ ВХОД",
            "WATCH": "WATCH — НАБЛЮДАТЬ, НЕ ВХОДИТЬ",
            "WAIT": "ОЖИДАТЬ — НЕ ВХОДИТЬ",
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

        if verdict == "WATCH":
            if watch_required is not None:
                lines.append(f"Порог усиления WATCH: +{watch_required:.1f} п.п.")
            if watch_price is not None and side != "NONE":
                lines.append(f"📍 Цена для усиления WATCH {side}: {_fmt(watch_price)}% или ниже")
            if buy_available and buy_required is not None:
                lines.append(f"Порог BUY: +{buy_required:.1f} п.п.")
            elif summary.get("buy_blocked_reason") == "confidence_below_medium":
                lines.append("Порог BUY: сначала нужна уверенность не ниже средней")
            if buy_price is not None and side != "NONE":
                lines.append(f"📍 Цена для BUY {side}: {_fmt(buy_price)}% или ниже")
        elif verdict == "BUY":
            if buy_required is not None:
                lines.append(f"Порог BUY: +{buy_required:.1f} п.п.")
            if buy_price is not None and side != "NONE":
                lines.append(f"📍 Максимальная цена BUY {side}: {_fmt(buy_price)}%")

        lines.append(f"🧠 Уверенность: {confidence_text}")
        if quality is not None:
            lines.append(f"🧾 Качество данных: {int(round(quality))}/10 — {quality_text}")
        if reason:
            lines.append(f"Почему: {reason}")
        tracking_offer = _tracking_offer(verdict, is_ru=True)
        if tracking_offer:
            lines.extend(["", tracking_offer])
        return "\n".join(lines)

    verdict_text = {
        "BUY": "BUY — CONSIDER ENTRY",
        "WATCH": "WATCH — DO NOT ENTER YET",
        "WAIT": "WAIT — DO NOT ENTER YET",
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

    if verdict == "WATCH":
        if watch_required is not None:
            lines.append(f"WATCH strengthening threshold: +{watch_required:.1f} pp")
        if watch_price is not None and side != "NONE":
            lines.append(f"📍 Stronger WATCH price for {side}: {_fmt(watch_price)}% or lower")
        if buy_available and buy_required is not None:
            lines.append(f"BUY threshold: +{buy_required:.1f} pp")
        elif summary.get("buy_blocked_reason") == "confidence_below_medium":
            lines.append("BUY threshold: confidence must first reach at least medium")
        if buy_price is not None and side != "NONE":
            lines.append(f"📍 BUY price for {side}: {_fmt(buy_price)}% or lower")
    elif verdict == "BUY":
        if buy_required is not None:
            lines.append(f"BUY threshold: +{buy_required:.1f} pp")
        if buy_price is not None and side != "NONE":
            lines.append(f"📍 Maximum BUY price for {side}: {_fmt(buy_price)}%")

    lines.append(f"🧠 Confidence: {confidence}")
    if quality is not None:
        lines.append(f"🧾 Data quality: {int(round(quality))}/10 — {quality_text}")
    if reason:
        lines.append(f"Why: {reason}")
    tracking_offer = _tracking_offer(verdict, is_ru=False)
    if tracking_offer:
        lines.extend(["", tracking_offer])
    return "\n".join(lines)


def _tracking_offer(verdict: str, is_ru: bool) -> str:
    normalized = str(verdict or "").upper().strip()
    if normalized not in {"WAIT", "WATCH", "NO_TRADE"}:
        return ""
    if is_ru:
        if normalized == "WATCH":
            return (
                "🔔 Отслеживать рынок: добавь его в Watchlist кнопкой ниже — "
                "DeepAlpha сообщит, когда появится BUY или сигнал ослабнет."
            )
        return (
            "🔔 Предлагаю отслеживать рынок: добавь его в Watchlist кнопкой ниже — "
            "DeepAlpha сообщит, когда появится WATCH или BUY."
        )
    if normalized == "WATCH":
        return (
            "🔔 Track this market: add it to Watchlist with the button below — "
            "DeepAlpha will alert you when BUY appears or the signal weakens."
        )
    return (
        "🔔 Track this market: add it to Watchlist with the button below — "
        "DeepAlpha will alert you when the decision changes to WATCH or BUY."
    )


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
