import json
import re
from typing import Any



def get_setting(key: str, default: str | None = None) -> str | None:
    from db.database import get_setting as _get_setting
    return _get_setting(key, default)


def generate_live_analyst_text(*args, **kwargs) -> str:
    from services.llm_service import generate_live_analyst_text as _generate_live_analyst_text
    return _generate_live_analyst_text(*args, **kwargs)

SAFE_LABELS = {"WATCH", "DATA NEEDED", "NO EDGE", "EDGE CANDIDATE"}
FORBIDDEN_WORDS = ("bet", "buy", "guaranteed", "100%", "ставка", "куп", "гарант")


def _clean_text(value: Any, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for word in FORBIDDEN_WORDS:
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    return text[:limit].strip()


def _safe_label(value: Any) -> str:
    label = str(value or "WATCH").strip().upper()
    return label if label in SAFE_LABELS else "WATCH"


def _max_bullets() -> int:
    try:
        return max(1, min(5, int(get_setting("watchlist_ai_summary_max_bullets", "3") or 3)))
    except Exception:
        return 3


def _data_quality(initial_probability=None, current_probability=None, probability_change=None, actual_outcome=None, closing_hours=None) -> str:
    signals = sum(v is not None for v in (initial_probability, current_probability, probability_change, actual_outcome, closing_hours))
    if signals >= 3:
        return "strong"
    if signals >= 2:
        return "medium"
    return "limited"


def _fallback_summary(event_type: str, question: str, initial_probability=None, current_probability=None, probability_change=None, actual_outcome=None, closing_hours=None, lang: str = "ru") -> dict:
    quality = _data_quality(initial_probability, current_probability, probability_change, actual_outcome, closing_hours)
    label = "WATCH"
    if quality == "limited":
        label = "DATA NEEDED"
    if event_type == "resolved_recap":
        label = "NO EDGE"
    elif probability_change is not None and abs(float(probability_change or 0)) >= 15:
        label = "EDGE CANDIDATE"

    if lang == "en":
        if event_type == "closing_soon":
            summary = "The market is near its deadline, so late information and liquidity can move probabilities faster than usual."
            watch_next = ["Final data releases or official updates", "Sharp probability moves into the close", "Whether liquidity stays broad enough"]
        elif event_type == "resolved_recap":
            summary = f"The market resolved as {actual_outcome or 'unknown'}, closing the tracking loop and making the prior probability path reviewable."
            watch_next = ["Compare the final result with the last tracked probability", "Check which signals changed before resolution", "Use the recap to calibrate future watchlist alerts"]
        else:
            summary = "The tracked probability moved enough to merit attention; the change may reflect new information or a shift in market positioning."
            watch_next = ["Confirm whether there is fresh source data", "Watch if the move holds after liquidity improves", "Compare the move with related markets"]
    else:
        if event_type == "closing_soon":
            summary = "Рынок близок к дедлайну, поэтому новые данные и ликвидность могут быстрее двигать вероятность."
            watch_next = ["Финальные данные или официальные обновления", "Резкие движения вероятности перед закрытием", "Достаточна ли ликвидность рынка"]
        elif event_type == "resolved_recap":
            summary = f"Рынок завершён с результатом: {actual_outcome or 'неизвестно'}. Это закрывает цикл наблюдения и помогает оценить прежнюю динамику вероятности."
            watch_next = ["Сравнить итог с последней отслеженной вероятностью", "Проверить, какие сигналы менялись перед закрытием", "Использовать выводы для калибровки будущих alerts"]
        else:
            summary = "Отслеживаемая вероятность изменилась достаточно заметно; это может отражать новые данные или сдвиг позиционирования рынка."
            watch_next = ["Подтвердить наличие свежих исходных данных", "Посмотреть, удержится ли движение при лучшей ликвидности", "Сравнить движение со связанными рынками"]
    return {"summary": _clean_text(summary), "label": label, "watch_next": watch_next[:_max_bullets()], "data_quality": quality, "fallback": True}


def _parse_provider_json(text: str) -> dict | None:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    bullets = data.get("watch_next") or []
    if not isinstance(bullets, list):
        bullets = []
    return {
        "summary": _clean_text(data.get("summary")),
        "label": _safe_label(data.get("label")),
        "watch_next": [_clean_text(x, 120) for x in bullets if _clean_text(x, 120)][:_max_bullets()],
        "data_quality": str(data.get("data_quality") or "limited").strip().lower() if str(data.get("data_quality") or "").strip().lower() in {"limited", "medium", "strong"} else "limited",
        "fallback": False,
    }


def build_watchlist_ai_summary(event_type: str, question: str, market_slug: str = "", market_url: str = "", initial_probability: float | None = None, current_probability: float | None = None, probability_change: float | None = None, actual_outcome: str | None = None, closing_hours: int | None = None, lang: str = "ru") -> dict:
    fallback = _fallback_summary(event_type, question, initial_probability, current_probability, probability_change, actual_outcome, closing_hours, lang)
    try:
        prompt = f"""
Return only compact JSON for a paid Watchlist alert. Language: {lang}.
Allowed label values: WATCH, DATA NEEDED, NO EDGE, EDGE CANDIDATE.
Never use betting/capper wording. Forbidden words: bet, buy, guaranteed, 100%.
Fields: summary (1-2 short sentences: why matters + what changed), label, watch_next (max {_max_bullets()} bullets), data_quality (limited|medium|strong).
Event: {event_type}
Question: {question}
Slug: {market_slug}
URL: {market_url}
Initial probability: {initial_probability}
Current probability: {current_probability}
Probability change: {probability_change}
Actual outcome: {actual_outcome}
Closing hours: {closing_hours}
""".strip()
        parsed = _parse_provider_json(generate_live_analyst_text(prompt, feature="watchlist_ai_summary", is_background=True, budget_checked=False) or "")
        if parsed and parsed["summary"] and parsed["watch_next"]:
            return parsed
    except Exception as exc:
        print(f"watchlist_ai_summary_provider_failed: {exc}")
    return fallback


def format_watchlist_ai_summary(summary: dict, lang: str = "ru") -> str:
    if not summary:
        return ""
    bullets = summary.get("watch_next") or []
    bullet_text = "\n".join(f"• {_clean_text(x, 140)}" for x in bullets[:_max_bullets()] if _clean_text(x, 140))
    if lang == "en":
        return f"\n\n🧠 DeepAlpha view:\nLabel: {_safe_label(summary.get('label'))}\n\nWhy it matters:\n{_clean_text(summary.get('summary'))}\n\nWhat to watch next:\n{bullet_text}\n\nData quality: {summary.get('data_quality') or 'limited'}"
    return f"\n\n🧠 DeepAlpha view:\nLabel: {_safe_label(summary.get('label'))}\n\nПочему это важно:\n{_clean_text(summary.get('summary'))}\n\nЧто смотреть дальше:\n{bullet_text}\n\nData quality: {summary.get('data_quality') or 'limited'}"
