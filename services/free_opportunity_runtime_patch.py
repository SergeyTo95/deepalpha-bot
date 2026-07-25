import html
from typing import Any, Dict, List


def install(telegram_module: Any) -> None:
    """Render zero-LLM opportunity results before the generic signal formatter."""
    original = getattr(telegram_module, "_format_analysis", None)
    if not callable(original) or getattr(original, "_deepalpha_free_opportunity", False):
        return

    def format_analysis(result: dict, uid: int) -> str:
        if str((result or {}).get("mode") or "") == "free_opportunity_prescan":
            lang = (
                (result or {}).get("lang")
                or (result or {}).get("language")
                or getattr(telegram_module, "get_user_lang", lambda _uid: "ru")(uid)
            )
            return format_free_opportunity_card(result or {}, lang=str(lang or "ru"))
        return original(result, uid)

    format_analysis._deepalpha_free_opportunity = True
    telegram_module._format_analysis = format_analysis


def format_free_opportunity_card(result: Dict[str, Any], lang: str = "ru") -> str:
    is_ru = str(lang or "ru").lower() != "en"
    candidates = result.get("free_candidates")
    candidates = candidates if isinstance(candidates, list) else []

    if not candidates:
        if is_ru:
            return (
                "🔍 Бесплатный Opportunity Scan\n\n"
                "Подходящие рынки сейчас не найдены.\n\n"
                "💸 AI-расход: 0\n"
                "Kimi и Gemini не запускались. Попробуй повторить сканирование позже."
            )
        return (
            "🔍 Free Opportunity Scan\n\n"
            "No suitable markets were found right now.\n\n"
            "💸 AI cost: 0\n"
            "Kimi and Gemini were not called. Run the scan again later."
        )

    lines: List[str] = []
    for index, candidate in enumerate(candidates[:7], 1):
        if not isinstance(candidate, dict):
            continue
        question = html.escape(str(candidate.get("question") or "Unknown market"))
        score = int(candidate.get("score") or 0)
        yes = _number(candidate.get("yes_price"))
        no = _number(candidate.get("no_price"))
        volume = _number(candidate.get("volume_24h"))
        liquidity = _number(candidate.get("liquidity"))
        tier = str(candidate.get("tier") or "")
        url = html.escape(str(candidate.get("url") or ""), quote=True)
        reason_items = candidate.get("reasons") if isinstance(candidate.get("reasons"), list) else []
        reason = "; ".join(html.escape(str(item)) for item in reason_items[:3])

        if is_ru:
            tier_label = {
                "DEEP_ANALYSIS_CANDIDATE": "кандидат на глубокий анализ",
                "WATCH_CANDIDATE": "кандидат для наблюдения",
                "LOW_PRIORITY": "низкий приоритет",
            }.get(tier, "предварительный кандидат")
            block = (
                f"{index}. <b>{question}</b>\n"
                f"Score: {score}/100 · {tier_label}\n"
                f"YES {yes:.1f}% / NO {no:.1f}%\n"
                f"Объём 24ч: ${volume:,.0f} · Ликвидность: ${liquidity:,.0f}"
            )
            if reason:
                block += f"\nПочему в топе: {reason}"
            if url:
                block += f'\n<a href="{url}">Открыть рынок</a>'
        else:
            tier_label = {
                "DEEP_ANALYSIS_CANDIDATE": "deep-analysis candidate",
                "WATCH_CANDIDATE": "watch candidate",
                "LOW_PRIORITY": "low priority",
            }.get(tier, "pre-screen candidate")
            block = (
                f"{index}. <b>{question}</b>\n"
                f"Score: {score}/100 · {tier_label}\n"
                f"YES {yes:.1f}% / NO {no:.1f}%\n"
                f"24h volume: ${volume:,.0f} · Liquidity: ${liquidity:,.0f}"
            )
            if reason:
                block += f"\nWhy ranked: {reason}"
            if url:
                block += f'\n<a href="{url}">Open market</a>'
        lines.append(block)

    if is_ru:
        return (
            "🔍 <b>Бесплатный Opportunity Scan</b>\n\n"
            f"Проверено рынков: {int(result.get('markets_received') or len(candidates))}\n"
            f"Подходящих кандидатов: {int(result.get('eligible_markets') or len(candidates))}\n"
            "💸 AI-расход: <b>0</b> — Kimi/Gemini не запускались.\n\n"
            + "\n\n".join(lines)
            + "\n\n⚠️ Это предварительный отбор, а не BUY. "
              "Справедливая вероятность и edge появятся только после обычного анализа выбранного рынка."
        )
    return (
        "🔍 <b>Free Opportunity Scan</b>\n\n"
        f"Markets checked: {int(result.get('markets_received') or len(candidates))}\n"
        f"Eligible candidates: {int(result.get('eligible_markets') or len(candidates))}\n"
        "💸 AI cost: <b>0</b> — Kimi/Gemini were not called.\n\n"
        + "\n\n".join(lines)
        + "\n\n⚠️ This is a pre-screen, not BUY. "
          "Fair probability and edge require a normal analysis of the selected market."
    )


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
