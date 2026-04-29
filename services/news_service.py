
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List
from urllib.parse import quote_plus

import requests


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
REQUEST_TIMEOUT = 20


def search_google_news(query: str, limit: int = 7) -> List[Dict[str, str]]:
    if not query or not query.strip():
        return []

    # Пробуем несколько вариантов запроса
    queries = _build_query_variants(query)

    for q in queries:
        results = _fetch_google_news(q, limit)
        if results:
            return results

    return []


def _fetch_google_news(query: str, limit: int = 7) -> List[Dict[str, str]]:
    url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        results = _parse_google_news_rss(response.text, limit=limit)
        return results
    except Exception:
        return []


def _build_query_variants(query: str) -> List[str]:
    """Строит несколько вариантов запроса от конкретного к общему."""
    query = query.strip()
    keywords = extract_keywords(query)

    variants = []

    # 1. Оригинальный запрос
    variants.append(query)

    # 2. Только ключевые слова без даты
    core = " ".join(keywords[:5])
    if core and core != query:
        variants.append(core)

    # 3. Первые 3 ключевых слова
    short = " ".join(keywords[:3])
    if short and short != core:
        variants.append(short)

    # 4. Первые 2 слова оригинального запроса
    words = query.split()
    if len(words) >= 2:
        two_words = " ".join(words[:2])
        if two_words not in variants:
            variants.append(two_words)

    return variants


def build_news_query(question: str, category: str = "", date_context: str = "") -> str:
    question = (question or "").strip()
    category = (category or "").strip()

    keywords = extract_keywords(question)
    core = " ".join(keywords[:6]).strip()

    parts = [core]
    if category and category not in ("Unknown", ""):
        parts.append(category)

    result = " ".join(p for p in parts if p).strip()
    return result or question


def summarize_news_items(items: List[Dict[str, str]]) -> str:
    if not items:
        return "No relevant live news items were found."

    lines = []
    for item in items[:7]:
        title = item.get("title", "Unknown title")
        source = item.get("source", "Unknown source")
        published = item.get("published", "Unknown time")
        lines.append(f"- {title} ({source}, {published})")

    return "\n".join(lines)


def extract_keywords(text: str) -> List[str]:
    text = (text or "").lower()
    words = re.findall(r"[a-zA-Z0-9]+", text)

    stop = {
        "will", "the", "a", "an", "to", "of", "and", "or", "in", "on", "for",
        "be", "is", "are", "today", "this", "that", "what", "when", "with",
        "from", "into", "after", "before", "does", "did", "can", "has", "have",
        "been", "by", "at", "its", "it", "as", "not", "but", "if", "so",
        "win", "lose", "get", "hit", "reach", "end", "new", "first", "last",
        "more", "less", "than", "over", "under", "per", "vs", "2024", "2025", "2026"
    }

    keywords = [w for w in words if len(w) > 2 and w not in stop]

    # Приоритет более длинным словам
    keywords.sort(key=lambda x: -len(x))
    return keywords[:10]


def _parse_google_news_rss(xml_text: str, limit: int = 7) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return []

        items = channel.findall("item")
        if not items:
            return []

        for item in items[:limit]:
            title = _safe_text(item.find("title"))
            link = _safe_text(item.find("link"))
            pub_date = _safe_text(item.find("pubDate"))

            source = "Unknown source"
            source_el = item.find("{http://search.yahoo.com/mrss/}source")
            if source_el is not None and source_el.text:
                source = source_el.text.strip()

            if not title:
                continue

            results.append({
                "title": _cleanup_title(title),
                "link": link,
                "published": _format_pub_date(pub_date),
                "source": source,
            })

        return results

    except Exception:
        return []


def _safe_text(element) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _cleanup_title(title: str) -> str:
    if " - " in title:
        return title.rsplit(" - ", 1)[0].strip()
    return title.strip()


def _format_pub_date(pub_date: str) -> str:
    if not pub_date:
        return "Unknown time"

    try:
        dt = parsedate_to_datetime(pub_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        diff = now - dt

        hours = int(diff.total_seconds() // 3600)
        if hours < 1:
            return "less than 1h ago"
        if hours < 24:
            return f"{hours}h ago"

        days = diff.days
        return f"{days}d ago"
    except Exception:
        return pub_date
