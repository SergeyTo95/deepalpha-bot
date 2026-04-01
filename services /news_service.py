import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List
from urllib.parse import quote_plus

import requests


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
REQUEST_TIMEOUT = 20


def search_google_news(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    Simple Google News RSS search.
    Returns recent news items for a query.
    """
    if not query or not query.strip():
        return []

    url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return _parse_google_news_rss(response.text, limit=limit)
    except Exception:
        return []


def build_news_query(question: str, category: str = "", date_context: str = "") -> str:
    """
    Builds a better search query from market question.
    """
    question = (question or "").strip()
    category = (category or "").strip()
    date_context = (date_context or "").strip()

    keywords = extract_keywords(question)
    core = " ".join(keywords[:6]).strip()

    parts = [core]
    if category and category != "Unknown":
        parts.append(category)
    if date_context and date_context != "Unknown":
        parts.append(date_context)

    result = " ".join(p for p in parts if p).strip()
    return result or question


def summarize_news_items(items: List[Dict[str, str]]) -> str:
    if not items:
        return "No relevant live news items were found."

    lines = []
    for item in items[:5]:
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
        "from", "into", "after", "before", "does", "did", "can"
    }

    keywords = [w for w in words if len(w) > 2 and w not in stop]
    return keywords[:10]


def _parse_google_news_rss(xml_text: str, limit: int = 5) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return []

        for item in channel.findall("item")[:limit]:
            title = _safe_text(item.find("title"))
            link = _safe_text(item.find("link"))
            pub_date = _safe_text(item.find("pubDate"))

            source = "Unknown source"
            source_el = item.find("{http://search.yahoo.com/mrss/}source")
            if source_el is not None and source_el.text:
                source = source_el.text.strip()

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
    # Google News titles often look like: "Headline - Source"
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
