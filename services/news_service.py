
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


# ═══════════════════════════════════════════
# SOURCE QUALITY
# ═══════════════════════════════════════════

_TIER1_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "bbc.com", "bbc.co.uk", "cnbc.com", "sec.gov", "federalreserve.gov",
    "whitehouse.gov", "congress.gov", "un.org", "nato.int", "europa.eu",
    "espn.com", "theathletic.com", "coindesk.com", "theblock.co",
    "nytimes.com", "washingtonpost.com", "economist.com",
}
_TIER1_KEYWORDS = {
    "reuters", "associated press", "ap news", "bloomberg", "financial times",
    "wall street journal", "bbc", "cnbc", "sec.gov", "federal reserve",
    "white house", "coindesk", "the block", "espn", "the athletic",
}
_TIER2_DOMAINS = {
    "politico.com", "axios.com", "theguardian.com", "skysports.com",
    "aljazeera.com", "cointelegraph.com", "decrypt.co", "forbes.com",
    "businessinsider.com", "techcrunch.com", "theverge.com", "wired.com",
}
_TIER2_KEYWORDS = {
    "politico", "axios", "the guardian", "sky sports", "al jazeera",
    "cointelegraph", "decrypt", "forbes", "business insider",
}
_TIER3_KEYWORDS = {
    "twitter", "x.com", "nitter", "reddit", "medium", "substack",
    "telegram", "youtube", "tiktok",
}


def classify_source_quality(source: str, link: str = "") -> str:
    s = (source or "").lower()
    l = (link or "").lower()
    combined = s + " " + l
    for domain in _TIER1_DOMAINS:
        if domain in combined:
            return "tier1"
    for kw in _TIER1_KEYWORDS:
        if kw in combined:
            return "tier1"
    for domain in _TIER2_DOMAINS:
        if domain in combined:
            return "tier2"
    for kw in _TIER2_KEYWORDS:
        if kw in combined:
            return "tier2"
    for kw in _TIER3_KEYWORDS:
        if kw in combined:
            return "tier3"
    return "unknown"


def source_score(quality: str) -> int:
    return {"tier1": 3, "tier2": 2, "tier3": 1, "unknown": 1}.get(quality, 1)


# ═══════════════════════════════════════════
# FRESHNESS
# ═══════════════════════════════════════════

def classify_freshness(published: str) -> str:
    if not published:
        return "unknown"
    import re
    p = (published or "").lower().strip()

    m = re.search(r'(\d+)\s*(min|minute|hour|hr|day|week)', p)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if "min" in unit:
            return "very_fresh"
        if "hour" in unit or "hr" in unit:
            return "very_fresh" if num < 6 else "fresh"
        if "day" in unit:
            if num <= 1:
                return "fresh"
            elif num <= 3:
                return "usable"
            return "stale"
        if "week" in unit:
            return "stale"

    m2 = re.search(r'^(\d+)d\b', p)
    if m2:
        d = int(m2.group(1))
        if d <= 1:
            return "fresh"
        elif d <= 3:
            return "usable"
        return "stale"

    if any(w in p for w in ["just now", "recently", "moments ago"]):
        return "very_fresh"

    return "unknown"


def freshness_score(freshness: str) -> int:
    return {
        "very_fresh": 4, "fresh": 3, "usable": 2,
        "stale": 1, "unknown": 2,
    }.get(freshness, 2)


# ═══════════════════════════════════════════
# RELEVANCE
# ═══════════════════════════════════════════

def score_news_relevance(
    item: dict,
    question: str,
    user_context: str = "",
) -> int:
    import re
    title = (item.get("title") or "").lower()
    q_words = set(re.findall(r'\w{4,}', (question or "").lower()))
    uc_words = set(re.findall(r'\w{4,}', (user_context or "").lower()))

    score = 0
    q_matches = sum(1 for w in q_words if w in title)
    score += min(q_matches, 3)
    if uc_words:
        uc_matches = sum(1 for w in uc_words if w in title)
        score += min(uc_matches * 2, 4)

    quality = classify_source_quality(item.get("source", ""), item.get("link", ""))
    score += source_score(quality)
    score += freshness_score(classify_freshness(item.get("published", "")))
    return score


def enrich_news_item(
    item: dict,
    question: str = "",
    user_context: str = "",
) -> dict:
    quality = classify_source_quality(item.get("source", ""), item.get("link", ""))
    freshness = classify_freshness(item.get("published", ""))
    item["source_quality"] = quality
    item["source_score"] = source_score(quality)
    item["freshness"] = freshness
    item["freshness_score"] = freshness_score(freshness)
    item["relevance_score"] = score_news_relevance(item, question, user_context)
    return item


# ═══════════════════════════════════════════
# MULTI-QUERY BUILDER
# ═══════════════════════════════════════════

def build_news_queries(
    question: str,
    category: str = "",
    date_context: str = "",
    user_context: str = "",
) -> list:
    cat = (category or "").lower()
    core_words = extract_keywords(question)
    core = " ".join(core_words[:5])

    queries = []

    if user_context and user_context.strip():
        uc_short = user_context.strip()[:100]
        queries.append(f"{core} {uc_short}")

    queries.append(core)

    if "politics" in cat:
        queries += [
            f"{core} official statement",
            f"{core} Reuters AP Bloomberg",
            f"{core} latest talks negotiations",
            f"{core} deadline",
        ]
    elif "sports" in cat:
        queries += [
            f"{core} injuries lineup",
            f"{core} match preview",
            f"{core} recent form",
            f"{core} odds",
        ]
    elif "crypto" in cat:
        queries += [
            f"{core} SEC ETF exchange",
            f"{core} official announcement",
            f"{core} CoinDesk The Block",
        ]
    elif "economy" in cat:
        queries += [
            f"{core} Reuters Bloomberg",
            f"{core} Fed CPI inflation jobs",
            f"{core} official data release",
        ]
    elif "tech" in cat:
        queries += [
            f"{core} official announcement",
            f"{core} earnings Reuters CNBC",
        ]
    else:
        queries += [
            f"{core} latest",
            f"{core} official",
            f"{core} Reuters",
        ]

    seen = set()
    result = []
    for q in queries:
        q_clean = q.strip()
        if q_clean and q_clean not in seen:
            seen.add(q_clean)
            result.append(q_clean)

    return result[:7]


def search_google_news_multi(
    queries: list,
    limit: int = 10,
) -> list:
    seen_titles: set = set()
    results: list = []

    for query in queries:
        if len(results) >= limit:
            break
        try:
            items = search_google_news(query, limit=limit)
            for item in items:
                title_key = (item.get("title") or "")[:60].lower()
                if title_key and title_key not in seen_titles:
                    seen_titles.add(title_key)
                    results.append(item)
                    if len(results) >= limit:
                        break
        except Exception as e:
            print(f"search_google_news_multi error for '{query}': {e}")
            continue

    return results
