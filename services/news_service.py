
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

def _extract_football_teams_and_date(question: str) -> dict:
    """
    Извлекает команды и дату из football match вопроса.
    Работает не только под конкретный клуб, а универсально.
    """
    import re

    q = (question or "").lower()
    result = {"team1": "", "team2": "", "date": "", "competition": ""}

    vs_patterns = [
        r'will\s+(.+?)\s+(?:beat|defeat)\s+(.+?)(?:\s+(?:on|in|at|by|to)\s|$|\?)',
        r'will\s+(.+?)\s+win\s+(?:against|vs\.?|versus)\s+(.+?)(?:\s+(?:on|in|at|by|to)\s|$|\?)',
        r'(.+?)\s+(?:vs\.?|versus|against|v\.)\s+(.+?)(?:\s+(?:on|in|at|by|to)\s|$|\?)',
    ]

    for pattern in vs_patterns:
        m = re.search(pattern, q, re.IGNORECASE)
        if m:
            result["team1"] = m.group(1).strip().rstrip(" ,")
            result["team2"] = m.group(2).strip().rstrip(" ,?")
            break

    if not result["team1"]:
        win_m = re.search(
            r'will\s+(.+?)\s+(?:win|qualify|advance|progress|score)',
            q,
            re.IGNORECASE,
        )
        if win_m:
            candidate = win_m.group(1).strip().rstrip(" ,")
            if len(candidate) > 3 and candidate not in ("the", "a", "an", "they"):
                result["team1"] = candidate

    date_patterns = [
        r'(\d{4}-\d{2}-\d{2})',
        r'(?:on|by|at)\s+(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*(?:\s+\d{4})?)',
        r'(?:on|by|at)\s+((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}(?:,?\s+\d{4})?)',
    ]
    for dp in date_patterns:
        dm = re.search(dp, q, re.IGNORECASE)
        if dm:
            result["date"] = dm.group(1).strip()
            break

    competitions = {
        "champions league": "Champions League",
        "europa league": "Europa League",
        "conference league": "Conference League",
        "premier league": "Premier League",
        "la liga": "La Liga",
        "serie a": "Serie A",
        "bundesliga": "Bundesliga",
        "ligue 1": "Ligue 1",
        "fa cup": "FA Cup",
        "copa del rey": "Copa del Rey",
        "world cup": "World Cup",
        "euros": "European Championship",
        "euro 2024": "Euro 2024",
        "copa america": "Copa America",
        "nations league": "Nations League",
        "mls": "MLS",
        "eredivisie": "Eredivisie",
        "liga nos": "Liga Portugal",
        "super lig": "Super Lig",
        "süper lig": "Süper Lig",
    }
    for kw, name in competitions.items():
        if kw in q:
            result["competition"] = name
            break

    return result


def _normalize_team_name(raw: str) -> str:
    """
    Нормализует название команды для поиска.
    """
    if not raw:
        return ""

    raw = raw.strip()
    normalized = raw.lower()

    abbrev_map = {
        "atlético madrid": "Atletico Madrid",
        "atletico madrid": "Atletico Madrid",
        "club atlético de madrid": "Atletico Madrid",
        "club atletico de madrid": "Atletico Madrid",
        "paris saint-germain": "PSG",
        "paris saint germain": "PSG",
        "manchester united": "Manchester United",
        "manchester city": "Manchester City",
        "inter milan": "Inter Milan",
        "internazionale": "Inter Milan",
        "ac milan": "AC Milan",
        "bayer leverkusen": "Leverkusen",
        "borussia dortmund": "Dortmund",
        "rb leipzig": "Leipzig",
        "tottenham hotspur": "Tottenham",
        "west ham united": "West Ham",
        "newcastle united": "Newcastle",
        "aston villa": "Aston Villa",
        "galatasaray": "Galatasaray",
        "fenerbahçe": "Fenerbahce",
        "fenerbache": "Fenerbahce",
        "besiktas": "Besiktas",
        "beşiktaş": "Besiktas",
        "ajax amsterdam": "Ajax",
        "club brugge": "Club Brugge",
    }

    for long_name, short_name in abbrev_map.items():
        if long_name in normalized:
            return short_name

    noise_prefixes = ["club ", "fc ", "cf "]
    for prefix in noise_prefixes:
        if normalized.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break

    return raw.title()

def build_news_queries(
    question: str,
    category: str = "",
    date_context: str = "",
    user_context: str = "",
) -> list:
    cat = (category or "").lower()
    q = (question or "").lower()
    core_words = extract_keywords(question)
    core = " ".join(core_words[:5])

    queries = []

    if user_context and user_context.strip():
        uc_short = user_context.strip()[:100]
        queries.append(f"{core} {uc_short}")

    # ── Central Bank / Economy / Rates ──
    if "economy" in cat:
        bank_name = ""

        if "banxico" in q or "bank of mexico" in q:
            bank_name = "Banxico Bank of Mexico"
        elif "bank of england" in q or "boe" in q:
            bank_name = "Bank of England"
        elif "bank of japan" in q or "boj" in q:
            bank_name = "Bank of Japan"
        elif "ecb" in q or "european central bank" in q:
            bank_name = "ECB European Central Bank"
        elif "federal reserve" in q or "fed" in q or "fomc" in q:
            bank_name = "Federal Reserve Fed FOMC"
        elif "central bank" in q:
            bank_name = core

        if bank_name:
            queries += [
                f"{bank_name} rate decision meeting",
                f"{bank_name} monetary policy statement",
                f"{bank_name} interest rate cut hold hike",
                f"{bank_name} inflation CPI data",
                f"{bank_name} Reuters Bloomberg economist poll",
            ]

            if "bank of mexico" in q or "banxico" in q:
                queries += [
                    "Banxico May meeting rate decision",
                    "Bank of Mexico monetary policy statement",
                    "Mexico inflation CPI Banxico",
                    "Reuters poll Bank of Mexico rate cut",
                    "Bloomberg survey Banxico rate decision",
                ]
        else:
            queries += [
                f"{core} central bank rate decision",
                f"{core} monetary policy statement",
                f"{core} Reuters Bloomberg",
                f"{core} CPI inflation jobs",
                f"{core} official data release",
            ]

    # ── Gaming / Esports ──
    elif "gaming" in cat or "esports" in cat:
        if "cache" in q or "map pool" in q or "active duty" in q:
            queries += [
                "CS2 Cache map pool Active Duty update",
                "Valve CS2 patch notes map pool change",
                "FMPONE Cache CS2 update workshop",
                "Counter-Strike Active Duty map pool 2026",
                "CS2 official blog Steam announcement Cache",
            ]
        elif "cs2" in q or "counter-strike" in q or "counter strike" in q:
            queries += [
                f"{core} CS2 official update patch notes",
                f"{core} Counter-Strike Steam announcement",
                f"{core} CS2 tournament insider report",
            ]
        elif "valve" in q or "steam" in q:
            queries += [
                f"{core} Valve official update",
                f"{core} Steam announcement",
                f"{core} official blog",
            ]
        elif "dota" in q:
            queries += [
                f"{core} Dota 2 Valve official update",
                f"{core} Dota 2 tournament announcement",
            ]
        elif "valorant" in q:
            queries += [
                f"{core} Valorant Riot Games official update",
                f"{core} Valorant esports tournament announcement",
            ]
        else:
            queries += [
                f"{core} official update patch notes",
                f"{core} esports tournament announcement",
                f"{core} gaming official announcement",
            ]

    # ── Sports / Football ──
    elif "sports" in cat or "football" in cat or "soccer" in cat:
        match_info = _extract_football_teams_and_date(question)
        team1_raw = match_info["team1"]
        team2_raw = match_info["team2"]
        date_str = match_info["date"]
        competition = match_info["competition"]

        team1 = _normalize_team_name(team1_raw)
        team2 = _normalize_team_name(team2_raw)

        if team1 and team2:
            comp_suffix = f" {competition}" if competition else ""
            queries += [
                f"{team1} vs {team2} match preview{comp_suffix}",
                f"{team1} vs {team2} lineups injuries",
                f"{team1} vs {team2} odds prediction",
                f"{team1} vs {team2} result",
            ]
            if competition:
                queries.append(f"{team1} {team2} {competition}")
            if date_str:
                queries.append(f"{team1} vs {team2} {date_str}")

        elif team1:
            comp_suffix = f" {competition}" if competition else ""
            queries += [
                f"{team1} match preview{comp_suffix}",
                f"{team1} lineups injuries form",
            ]
            if date_str:
                queries += [
                    f"{team1} match {date_str}",
                    f"{team1} fixture {date_str}",
                ]
            queries += [
                f"{team1} odds result",
                f"{team1} {competition}".strip(),
            ]

        else:
            queries += [
                f"{core} match preview",
                f"{core} lineups injuries",
                f"{core} recent form",
                f"{core} odds",
            ]

    # ── Crypto ──
    elif "crypto" in cat:
        queries += [
            f"{core} SEC ETF exchange",
            f"{core} official announcement",
            f"{core} CoinDesk The Block",
            f"{core} on-chain whale listing",
        ]

    # ── Politics / Geopolitics ──
    elif "politics" in cat or "geopolit" in cat:
        queries += [
            f"{core} official statement",
            f"{core} Reuters AP Bloomberg",
            f"{core} negotiations deadline",
            f"{core} sanctions ceasefire diplomatic",
        ]

    # ── Tech ──
    elif "tech" in cat:
        queries += [
            f"{core} official announcement",
            f"{core} earnings guidance Reuters CNBC",
            f"{core} product launch regulation",
        ]

    # ── Other / fallback ──
    else:
        queries += [
            core,
            f"{core} latest",
            f"{core} official",
            f"{core} Reuters",
        ]

    if core and core not in queries:
        queries.insert(1 if queries else 0, core)

    seen = set()
    result = []
    for q_item in queries:
        q_clean = q_item.strip()
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
