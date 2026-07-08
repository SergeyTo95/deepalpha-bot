"""Compact AI summaries for paid Watchlist Autopilot alerts."""

from __future__ import annotations

import json
import re
from typing import Any


SAFE_LABELS = {"WATCH", "DATA NEEDED", "NO EDGE", "EDGE CANDIDATE"}
FORBIDDEN_WORDS = ("bet", "buy", "guaranteed", "100%", "ставка", "купить", "гарант")


def _clean_text(value: Any, max_len: int = 420) -> str:
    text = str(value or "").strip()
    for word in FORBIDDEN_WORDS:
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -—:;,.\n")
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].strip() + "."
    return text


def _fallback_summary(
    event_type: str,
    question: str,
    initial_probability: float | None,
    current_probability: float | None,
    probability_change: float | None,
    actual_outcome: str | None,
    closing_hours: int | None,
    lang: str,
) -> dict:
    is_ru = lang == "ru"
    label = "WATCH"
    quality = "limited"
    bullets: list[str]

    if event_type == "resolved_recap":
        label = "NO EDGE"
        result = actual_outcome or ("неизвестен" if is_ru else "unknown")
        summary = (
            f"Рынок закрыт с результатом: {result}. Это фиксирует финальный сценарий и помогает сравнить ожидания с фактом."
            if is_ru else
            f"The market resolved with outcome: {result}. This locks the final scenario and helps compare expectations with the result."
        )
        bullets = [
            "Сравнить исход с исходной вероятностью" if is_ru else "Compare the outcome with the initial probability",
            "Отметить, какие данные оказались решающими" if is_ru else "Note which data points mattered most",
            "Не переносить выводы без нового контекста" if is_ru else "Avoid carrying conclusions forward without fresh context",
        ]
    elif event_type == "closing_soon":
        label = "DATA NEEDED"
        hours = closing_hours if closing_hours is not None else 0
        summary = (
            f"До закрытия осталось около {hours} ч. Поздние изменения вероятности могут отражать свежие данные или снижение ликвидности."
            if is_ru else
            f"About {hours}h remain before close. Late probability moves may reflect fresh data or thinner liquidity."
        )
        bullets = [
            "Проверить свежие новости по событию" if is_ru else "Check fresh event news",
            "Следить за резкими движениями вероятности" if is_ru else "Watch for sharp probability moves",
            "Оценить ликвидность перед выводом" if is_ru else "Review liquidity before drawing conclusions",
        ]
    else:
        change = probability_change
        if change is None and initial_probability is not None and current_probability is not None:
            change = current_probability - initial_probability
        label = "EDGE CANDIDATE" if change is not None and abs(change) >= 15 else "WATCH"
        quality = "medium" if current_probability is not None else "limited"
        summary = (
            f"Вероятность изменилась на {change:+.1f} п.п. Рынок переоценивает сценарий, поэтому важно понять источник движения."
            if is_ru and change is not None else
            f"Probability moved by {change:+.1f} pp. The market is repricing the scenario, so the source of the move matters."
            if change is not None else
            ("Вероятность заметно изменилась. Нужна проверка причины движения перед выводами." if is_ru else "Probability changed meaningfully. The reason for the move should be checked before conclusions.")
        )
        bullets = [
            "Источник изменения: новости, объём или обновление данных" if is_ru else "Source of the move: news, volume, or data update",
            "Сохранится ли новая вероятность после реакции рынка" if is_ru else "Whether the new probability holds after the market reacts",
            "Появятся ли подтверждающие факты" if is_ru else "Whether confirming facts appear",
        ]

    return {
        "summary": _clean_text(summary),
        "label": label,
        "watch_next": [_clean_text(b, 120) for b in bullets[:3]],
        "data_quality": quality,
        "fallback": True,
    }


def _generate_text(prompt: str) -> str:
    from services.llm_service import generate_text
    return generate_text(prompt)


def _build_prompt(**kwargs: Any) -> str:
    return (
        "Return ONLY valid JSON with keys summary, label, watch_next, data_quality. "
        "Make a compact DeepAlpha watchlist alert explanation. No wagering/capper language. "
        "Do not use: bet, buy, guaranteed, 100%. label must be one of WATCH, DATA NEEDED, NO EDGE, EDGE CANDIDATE. "
        "watch_next must contain up to 3 bullets. data_quality one of limited, medium, strong. "
        f"Language: {kwargs.get('lang', 'ru')}. Context: {json.dumps(kwargs, ensure_ascii=False)}"
    )


def build_watchlist_ai_summary(
    event_type: str,
    question: str,
    market_slug: str = "",
    market_url: str = "",
    initial_probability: float | None = None,
    current_probability: float | None = None,
    probability_change: float | None = None,
    actual_outcome: str | None = None,
    closing_hours: int | None = None,
    lang: str = "ru",
) -> dict:
    fallback = _fallback_summary(event_type, question, initial_probability, current_probability, probability_change, actual_outcome, closing_hours, lang)
    try:
        raw = _generate_text(_build_prompt(**locals()))
        if not raw:
            return fallback
        match = re.search(r"\{.*\}", raw, flags=re.S)
        data = json.loads(match.group(0) if match else raw)
        label = str(data.get("label", "")).strip().upper()
        if label not in SAFE_LABELS:
            label = fallback["label"]
        watch_next = data.get("watch_next") or fallback["watch_next"]
        if not isinstance(watch_next, list):
            watch_next = fallback["watch_next"]
        quality = str(data.get("data_quality") or fallback["data_quality"]).strip().lower()
        if quality not in {"limited", "medium", "strong"}:
            quality = fallback["data_quality"]
        summary = _clean_text(data.get("summary") or fallback["summary"])
        if not summary:
            return fallback
        return {
            "summary": summary,
            "label": label,
            "watch_next": [_clean_text(b, 120) for b in watch_next[:3] if _clean_text(b, 120)] or fallback["watch_next"],
            "data_quality": quality,
            "fallback": False,
        }
    except Exception as exc:
        print(f"watchlist_ai_summary fallback: {exc}")
        return fallback


def format_watchlist_ai_summary(summary: dict, lang: str = "ru") -> str:
    title = "🧠 DeepAlpha view:"
    why = "Почему это важно:" if lang == "ru" else "Why it matters:"
    next_title = "Что смотреть дальше:" if lang == "ru" else "What to watch next:"
    bullets = summary.get("watch_next") or []
    bullet_text = "\n".join(f"• {_clean_text(item, 120)}" for item in bullets[:3])
    return (
        f"{title}\n"
        f"Label: {summary.get('label', 'WATCH')}\n\n"
        f"{why}\n{_clean_text(summary.get('summary', ''))}\n\n"
        f"{next_title}\n{bullet_text}\n\n"
        f"Data quality: {summary.get('data_quality', 'limited')}"
    ).strip()
