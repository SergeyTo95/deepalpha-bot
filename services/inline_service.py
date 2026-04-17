
"""
Сервис для Inline Query режима бота.
Позволяет использовать бот в любом чате Telegram через @bot_username.
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from services.polymarket_service import (
    extract_slug_from_url,
    get_primary_market_from_url,
    normalize_market_data,
)
from db.database import get_signal_cache


def is_polymarket_url(text: str) -> bool:
    """Проверяет является ли текст ссылкой Polymarket."""
    if not text:
        return False
    return "polymarket.com" in text.lower()


def extract_url_from_query(query: str) -> Optional[str]:
    """Извлекает URL Polymarket из inline-запроса."""
    if not query:
        return None
    # Ищем URL в запросе
    match = re.search(r'https?://[^\s]+polymarket\.com[^\s]*', query)
    if match:
        return match.group(0)
    return None


def build_quick_market_preview(url: str, lang: str = "ru") -> Optional[Dict[str, Any]]:
    """
    Быстрое превью рынка без AI-анализа — для inline.
    Возвращает только базовую инфу из Polymarket API.
    """
    try:
        raw_market = get_primary_market_from_url(url)
        if not raw_market:
            return None

        normalized = normalize_market_data(raw_market)
        if not normalized:
            return None

        question = normalized.get("question", "")
        if not question:
            return None

        return {
            "url": url,
            "question": question,
            "market_probability": normalized.get("market_probability", "Unknown"),
            "volume": normalized.get("volume", "Unknown"),
            "end_date": normalized.get("end_date", "Unknown"),
        }
    except Exception as e:
        print(f"build_quick_market_preview error: {e}")
        return None


def format_inline_market_text(preview: Dict[str, Any], author_id: int, bot_username: str, lang: str = "ru") -> str:
    """
    Форматирует текст который будет отправлен в чат когда юзер выбрал inline-результат.
    """
    question = preview.get("question", "")
    market_prob = preview.get("market_probability", "Unknown")
    url = preview.get("url", "")

    ref_link = f"https://t.me/{bot_username}?start=ref_{author_id}"

    if lang == "ru":
        text = (
            f"🔍 Polymarket рынок\n\n"
            f"📌 {question}\n\n"
            f"📊 {market_prob}\n\n"
            f"🤖 Узнай прогноз AI:\n"
            f"👉 {ref_link}"
        )
        if url:
            text += f"\n\n🔗 Рынок: {url}"
    else:
        text = (
            f"🔍 Polymarket market\n\n"
            f"📌 {question}\n\n"
            f"📊 {market_prob}\n\n"
            f"🤖 Get AI forecast:\n"
            f"👉 {ref_link}"
        )
        if url:
            text += f"\n\n🔗 Market: {url}"

    return text


def format_inline_signal_text(signal: Dict[str, Any], author_id: int, bot_username: str, lang: str = "ru") -> str:
    """
    Форматирует кешированный сигнал для inline-отправки.
    """
    question = signal.get("question", "")
    market_prob = signal.get("market_probability", "Unknown")
    category = signal.get("category", "")
    score = signal.get("opportunity_score", 0)
    url = signal.get("url", "")

    score_bar = "🟩" * min(int(score / 20), 5) + "⬜" * (5 - min(int(score / 20), 5))
    ref_link = f"https://t.me/{bot_username}?start=ref_{author_id}"

    if lang == "ru":
        text = (
            f"💡 DeepAlpha Сигнал\n\n"
            f"📌 {question}\n\n"
            f"🏷 Категория: {category}\n"
            f"📊 Рынок: {market_prob}\n"
            f"⚡ Скор: {score} {score_bar}\n\n"
            f"🤖 Полный анализ в боте:\n"
            f"👉 {ref_link}"
        )
    else:
        text = (
            f"💡 DeepAlpha Signal\n\n"
            f"📌 {question}\n\n"
            f"🏷 Category: {category}\n"
            f"📊 Market: {market_prob}\n"
            f"⚡ Score: {score} {score_bar}\n\n"
            f"🤖 Full analysis in bot:\n"
            f"👉 {ref_link}"
        )

    if url:
        text += f"\n\n🔗 {url}"

    return text


def get_top_cached_signals(limit: int = 5) -> List[Dict[str, Any]]:
    """Возвращает топ кешированных сигналов для пустого inline-запроса."""
    categories = ["Politics", "Crypto", "Sports", "Economy", "Tech"]
    signals = []

    for cat in categories:
        cached = get_signal_cache(cat, max_age_seconds=7200)  # 2 часа
        if cached and cached.get("question") != "No strong opportunity found":
            signals.append(cached)

    # Сортируем по скору
    signals.sort(key=lambda s: s.get("opportunity_score", 0), reverse=True)
    return signals[:limit]


def format_preview_title(preview: Dict[str, Any], lang: str = "ru") -> str:
    """Короткий заголовок для inline-результата (показывается в списке)."""
    question = preview.get("question", "")[:80]
    return f"📌 {question}"


def format_preview_description(preview: Dict[str, Any], lang: str = "ru") -> str:
    """Описание под заголовком."""
    market_prob = preview.get("market_probability", "Unknown")
    if lang == "ru":
        return f"📊 {market_prob}"
    return f"📊 {market_prob}"


def format_signal_title(signal: Dict[str, Any], lang: str = "ru") -> str:
    """Заголовок для кешированного сигнала."""
    question = signal.get("question", "")[:80]
    score = signal.get("opportunity_score", 0)
    return f"⚡{score} {question}"


def format_signal_description(signal: Dict[str, Any], lang: str = "ru") -> str:
    """Описание для кешированного сигнала."""
    category = signal.get("category", "")
    market_prob = signal.get("market_probability", "Unknown")
    if lang == "ru":
        return f"{category} | {market_prob}"
    return f"{category} | {market_prob}"

    # Регистрируем юзера и инкрементируем счётчик
    try:
        ensure_user(
            user_id=uid,
            username=inline_query.from_user.username or "",
            first_name=inline_query.from_user.first_name or "",
        )
        increment_inline_queries(uid)
    except Exception as e:
        print(f"inline_query user tracking error: {e}")

    results = []

      
