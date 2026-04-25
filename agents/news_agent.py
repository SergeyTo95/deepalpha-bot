import re
import time
import random
from typing import Any, Dict, List, Optional

from services.llm_service import generate_news_text
from services.news_service import (
    build_news_query,
    search_google_news,
    summarize_news_items,
)


# ═══════════════════════════════════════════
# UNIFIED CATEGORY DETECTION
# ═══════════════════════════════════════════

POLITICS_KEYWORDS = [
    "trump", "biden", "harris", "vance", "election", "senate", "white house",
    "president", "congress", "vote", "republican", "democrat", "electoral",
    "campaign", "cabinet", "administration", "governor", "mayor", "midterm",
    "putin", "zelensky", "macron", "orban", "modi", "xi jinping",
    "nato", "un ", "united nations", "european union", "parliament",
    "prime minister", "chancellor", "minister", "government", "summit",
    "embassy", "ambassador", "diplomacy", "treaty", "sanctions",
    "iran", "israel", "ukraine", "russia", "china", "war ", "conflict",
    "ceasefire", "military", "missile", "nuclear", "strike", "attack",
    "invasion", "troops", "weapon", "bomb", "drone", "navy",
    "venezuela", "taiwan", "north korea", "pakistan", "syria", "gaza",
    "hezbollah", "hamas", "houthi", "political", "politician",
]

CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "eth ", "ethereum", "solana", "sol ", "crypto",
    "token", "sec ", " etf ", "airdrop", "defi", "memecoin",
    "blockchain", "coinbase", "binance", "altcoin", "nft", "usdc",
    "xrp", "ripple", "cardano", " ada ", "dogecoin", "doge",
    "polygon", "matic", "avalanche", "avax", "chainlink",
    "stablecoin", "halving", "mining", "wallet", "exchange", " dex ",
    "web3", "metaverse", "ton ",
]

SPORTS_KEYWORDS = [
    "nba", "nfl", "mlb", "nhl", "ufc", "mma", "fifa", "nascar",
    "premier league", "champions league", "la liga", "serie a",
    "bundesliga", "ligue 1", "super bowl", "world cup", "stanley cup",
    "world series", "march madness", "masters", "wimbledon", "grand slam",
    "olympics", "formula 1", " f1 ", "grand prix",
    "football", "soccer", "basketball", "baseball", "hockey", "tennis",
    "golf", "boxing", "wrestling", "cricket", "rugby",
    "esports", "league of legends", "valorant", "cs2 ", "dota",
    "celtics", "lakers", "warriors", "heat", "bulls", "knicks",
    "nets", "mavericks", "nuggets", "suns", "clippers", "bucks",
    "76ers", "spurs", "rockets", "pistons", "pacers", "hawks",
    "thunder", "trail blazers", "jazz", "timberwolves", "grizzlies",
    "chiefs", "patriots", "cowboys", "eagles", "49ers", "ravens",
    "bengals", "bills", "dolphins", "steelers", "browns", "broncos",
    "yankees", "dodgers", "red sox", "cubs", "astros", "braves",
    "arsenal", "chelsea", "liverpool", "manchester", "barcelona",
    "real madrid", "psg", "juventus", "bayern", "inter milan", "ac milan",
    "atletico", "borussia", "ajax", "porto", "benfica",
    "djokovic", "nadal", "federer", "alcaraz", "sinner", "swiatek",
    "championship", "playoff", "finals", "tournament",
    " cup ", "trophy", "title", " goal ", "boxing", "fight",
]

ECONOMY_KEYWORDS = [
    "inflation", " fed ", "federal reserve", "recession", " gdp ",
    " cpi ", "unemployment", "interest rate", "wall street",
    "stock market", " s&p ", "nasdaq", "dow jones", "dollar",
    "currency", "trade war", "tariff", "debt", "deficit", "budget",
    "treasury", "bond ", "fomc ", "powell", " ecb ", " imf ",
    "world bank", "brent", " wti ", "gold ", "silver",
    "commodit", "bankruptcy", "merger", " ipo ", "jobless",
    "payrolls", "economic",
]

TECH_KEYWORDS = [
    "openai", "chatgpt", " gpt", "ai ", "artificial intelligence",
    "google", "apple", "tesla", "nvidia", "microsoft", "meta ",
    "amazon", "spacex", "starship", "anthropic", "grok", "xai ",
    "gemini", "claude", " llm ", "launch", " chip ",
    "iphone", "android", "samsung", "intel ", " amd ",
    "robot", "autonomous", "self-driving", "electric vehicle", " ev ",
    "neuralink", "starlink", "satellite",
]

CULTURE_KEYWORDS = [
    "oscar", "grammy", "emmy", "golden globe", "academy award",
    "box office", "album", "song ", "artist", "celebrity",
    "movie", "film ", " show ", "series", "netflix", "disney",
    "taylor swift", "beyonce", "drake", "kanye", "rihanna",
    "billboard", "spotify", "halftime",
]

WEATHER_KEYWORDS = [
    "hurricane", "tornado", "earthquake", "flood", "wildfire",
    "temperature", "celsius", "fahrenheit", "snowfall", "rainfall",
    "climate", "el nino", "storm ", "typhoon", "cyclone",
]


def detect_category_from_text(text: str) -> str:
    """
    Единая функция определения категории для всего бота.
    Используй её вместо локальных _detect_category.
    """
    if not text:
        return "Other"

    s = " " + text.lower() + " "

    if any(kw in s for kw in POLITICS_KEYWORDS):
        return "Politics"
    if any(kw in s for kw in SPORTS_KEYWORDS):
        return "Sports"
    if any(kw in s for kw in CRYPTO_KEYWORDS):
        return "Crypto"
    if any(kw in s for kw in ECONOMY_KEYWORDS):
        return "Economy"
    if any(kw in s for kw in TECH_KEYWORDS):
        return "Tech"
    if any(kw in s for kw in CULTURE_KEYWORDS):
        return "Culture"
    if any(kw in s for kw in WEATHER_KEYWORDS):
        return "Weather"

    return "Other"


# ═══════════════════════════════════════════
# TWITTER / X SCRAPER
# ═══════════════════════════════════════════

def _fetch_twitter_signals(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    Ищет сигналы из Twitter/X через Nitter (публичный frontend без API).
    Возвращает список: [{"title": str, "source": "Twitter/X", "link": str}]
    """
    results = []

    nitter_instances = [
        "https://nitter.net",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
    ]

    clean_query = re.sub(r'[^\w\s]', '', query)[:80].strip()
    encoded = clean_query.replace(" ", "+")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    for instance in nitter_instances:
        if len(results) >= limit:
            break
        try:
            import requests
            url = f"{instance}/search?q={encoded}&f=tweets"
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code != 200:
                continue

            html = resp.text

            tweet_blocks = re.findall(
                r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>',
                html,
                re.DOTALL,
            )

            for block in tweet_blocks[:limit]:
                text = re.sub(r'<[^>]+>', '', block).strip()
                text = re.sub(r'\s+', ' ', text).strip()

                if len(text) < 20:
                    continue
                if any(
                    spam in text.lower()
                    for spam in ["follow me", "click here", "buy now", "promo"]
                ):
                    continue

                results.append({
                    "title": text[:200],
                    "source": "Twitter/X",
                    "published": "recent",
                    "link": f"{instance}/search?q={encoded}",
                })

                if len(results) >= limit:
                    break

            if results:
                break

        except Exception as e:
            print(f"Nitter {instance} error: {e}")
            continue

    return results


def _fetch_twitter_via_google(query: str, limit: int = 4) -> List[Dict[str, str]]:
    """
    Fallback: ищет Twitter через Google News (site:twitter.com OR x.com).
    """
    try:
        twitter_query = f"{query} site:twitter.com OR site:x.com"
        items = search_google_news(twitter_query, limit=limit)
        for item in items:
            item["source"] = "Twitter/X (via Google)"
        return items
    except Exception:
        return []


# ═══════════════════════════════════════════
# KEY SIGNALS EXTRACTOR
# ═══════════════════════════════════════════

def _extract_key_signals(llm_text: str, news_items: List[Dict]) -> List[str]:
    """
    Извлекает ключевые сигналы из LLM-ответа и новостных заголовков.
    Возвращает список строк — конкретных фактов/сигналов.
    """
    signals = []

    if llm_text:
        signal_section = re.search(
            r'Key Signals?:(.*?)(?:Supporting|Opposing|Social|Sentiment|$)',
            llm_text,
            re.DOTALL | re.IGNORECASE,
        )
        if signal_section:
            raw = signal_section.group(1).strip()
            lines = [
                re.sub(r'^[-•*\d\.\s]+', '', line).strip()
                for line in raw.splitlines()
                if line.strip() and len(line.strip()) > 15
            ]
            signals.extend(lines[:4])

    if len(signals) < 2 and news_items:
        for item in news_items[:5]:
            title = item.get("title", "").strip()
            if title and len(title) > 20:
                clean = re.sub(r'\s+', ' ', title).strip()
                if clean not in signals:
                    signals.append(clean)
            if len(signals) >= 4:
                break

    return signals[:5]


# ═══════════════════════════════════════════
# NEWS AGENT
# ═══════════════════════════════════════════

class NewsAgent:
    def __init__(self) -> None:
        pass

    def _detect_category(self, text: str) -> str:
        """Обёртка над единой функцией — для обратной совместимости."""
        return detect_category_from_text(text)

    def run(self, market_data: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
        question = market_data.get("question", "Unknown market")
        category = market_data.get("category", "Unknown")
        date_context = market_data.get("date_context", "Unknown")
        related_markets = market_data.get("related_markets", [])

        news_query = build_news_query(
            question=question,
            category=category,
            date_context=date_context,
        )

        # Основные новости
        news_items = search_google_news(news_query, limit=7)

        # Twitter/X — сначала пробуем Nitter, потом Google fallback
        twitter_items = _fetch_twitter_signals(news_query, limit=4)
        if not twitter_items:
            twitter_items = _fetch_twitter_via_google(news_query, limit=3)

        # Дедуплицируем
        seen_titles = {item.get("title", "")[:50] for item in news_items}
        unique_twitter = [
            item for item in twitter_items
            if item.get("title", "")[:50] not in seen_titles
        ]

        all_items = news_items + unique_twitter
        live_news_summary = summarize_news_items(all_items[:8])

        prompt = self._build_prompt(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            news_items=all_items[:6],
            lang=lang,
        )

        llm_result = generate_news_text(prompt)

        has_twitter = bool(unique_twitter)

        if llm_result and not llm_result.lower().startswith("llm service is not configured"):
            key_signals = _extract_key_signals(llm_result, all_items)
            return self._wrap_llm_result(
                question=question,
                category=category,
                llm_result=llm_result,
                news_query=news_query,
                news_items=all_items[:6],
                key_signals=key_signals,
                has_twitter=has_twitter,
            )

        key_signals = _extract_key_signals("", all_items)
        return self._fallback_news(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            news_query=news_query,
            news_items=all_items[:6],
            key_signals=key_signals,
        )

    def _build_prompt(
        self,
        question: str,
        category: str,
        date_context: str,
        related_markets: List[Dict[str, Any]],
        live_news_summary: str,
        news_items: List[Dict[str, str]] = None,
        lang: str = "en",
    ) -> str:
        related_lines = []
        for item in related_markets[:6]:
            title = item.get("title", "Unknown related market")
            relation_type = item.get("relation_type", "unknown")
            probability = item.get("probability", "Unknown")
            related_lines.append(
                f"- {title} | relation: {relation_type} | probability: {probability}"
            )

        related_block = (
            "\n".join(related_lines) if related_lines else "- No related markets"
        )

        lang_instruction = (
            "Respond ONLY in Russian. Every single word must be in Russian language. "
            "Translate all terms, sources, and analysis into Russian."
            if lang == "ru"
            else "Respond in English."
        )

        has_news = bool(
            live_news_summary and "No relevant" not in live_news_summary
        )

        top_news_block = ""
        if news_items:
            lines = []
            for i, item in enumerate(news_items[:6], 1):
                title = item.get("title", "")
                source = item.get("source", "")
                published = item.get("published", "")
                link = item.get("link", "")
                if title:
                    line = f"{i}. [{source}] {title} ({published})"
                    if link:
                        line += f" — {link}"
                    lines.append(line)
            top_news_block = "\n".join(lines)

        twitter_count = sum(
            1 for item in (news_items or [])
            if "twitter" in item.get("source", "").lower()
            or "x.com" in item.get("source", "").lower()
        )
        twitter_note = (
            f"\nNote: {twitter_count} sources are from Twitter/X social media."
            if twitter_count > 0
            else ""
        )

        return f"""
You are DeepAlpha — a senior analyst for prediction markets with hedge fund expertise.

{lang_instruction}

TASK: Provide DEEP analysis of news context for this prediction market.
Go beyond summarizing — identify what DRIVES the probability and what could CHANGE it.

MARKET QUESTION: {question}
CATEGORY: {category}
DEADLINE: {date_context}

RELATED MARKETS:
{related_block}

TOP NEWS SOURCES:{twitter_note}
{top_news_block if top_news_block else "No news sources found."}

FULL NEWS FEED:
{live_news_summary if has_news else "No recent news found for this topic."}

ANALYSIS RULES:
1. Base analysis ONLY on provided news — do not hallucinate facts
2. Explain WHY the market is priced as it is — causal reasoning
3. Identify STRUCTURAL factors (not just surface-level news)
4. Distinguish between signal (changes probability) and noise (irrelevant)
5. Twitter/X sources = social sentiment signal, not hard facts
6. Be specific: mention names, dates, numbers from sources

REQUIRED OUTPUT FORMAT:

News Summary:
[2-3 sentences: what is happening RIGHT NOW that affects this market]

Key Signals:
- [Signal 1 — specific fact + strength: Strong/Moderate/Weak]
- [Signal 2 — specific fact + strength: Strong/Moderate/Weak]
- [Signal 3 — specific fact + strength: Strong/Moderate/Weak]

Supporting Factors:
- [Concrete reason why YES outcome becomes more likely]
- [Concrete reason why YES outcome becomes more likely]

Opposing Factors:
- [Concrete reason why NO outcome becomes more likely]
- [Concrete reason why NO outcome becomes more likely]

Structural Context:
[1-2 sentences about underlying structural forces — historical patterns, institutional constraints, political dynamics]

Social Sentiment:
[Twitter/X and social media sentiment if available, otherwise "No social data"]

Sentiment: Positive / Negative / Mixed / Unclear
Confidence: Low / Medium / High
""".strip()

    def _wrap_llm_result(
        self,
        question: str,
        category: str,
        llm_result: str,
        news_query: str,
        news_items: List[Dict[str, str]],
        key_signals: List[str] = None,
        has_twitter: bool = False,
    ) -> Dict[str, Any]:
        return {
            "question": question,
            "category": category,
            "news_query": news_query,
            "news_summary": llm_result,
            "sources": news_items,
            "sentiment": self._extract_sentiment(llm_result),
            "confidence": self._extract_confidence(llm_result),
            "key_signals": key_signals or [],
            "has_twitter": has_twitter,
            "raw_news_text": llm_result,
        }

    def _fallback_news(
        self,
        question: str,
        category: str,
        date_context: str,
        related_markets: List[Dict[str, Any]],
        live_news_summary: str,
        news_query: str,
        news_items: List[Dict[str, str]],
        key_signals: List[str] = None,
    ) -> Dict[str, Any]:
        summary_parts = [
            f"News analysis for: {question}.",
            f"Category: {category}.",
        ]

        if date_context and date_context != "Unknown":
            summary_parts.append(f"Time context: {date_context}.")

        if related_markets:
            summary_parts.append(
                f"There are {len(related_markets)} related market signals to consider."
            )

        if news_items:
            summary_parts.append(f"Found {len(news_items)} relevant recent news items.")
            summary_parts.append(f"News digest: {live_news_summary}")
        else:
            summary_parts.append("No live news items were found for this topic.")

        return {
            "question": question,
            "category": category,
            "news_query": news_query,
            "news_summary": " ".join(summary_parts),
            "sources": news_items,
            "sentiment": "Mixed" if news_items else "Unclear",
            "confidence": "Medium" if news_items else "Low",
            "key_signals": key_signals or [],
            "has_twitter": False,
            "raw_news_text": "",
        }

    def _extract_sentiment(self, text: str) -> str:
        t = text.lower()
        for phrase, result in [
            ("sentiment: positive", "Positive"),
            ("настроение: позитивное", "Positive"),
            ("sentiment: negative", "Negative"),
            ("настроение: негативное", "Negative"),
            ("sentiment: mixed", "Mixed"),
            ("настроение: смешанное", "Mixed"),
            ("sentiment: unclear", "Unclear"),
            ("настроение: неясное", "Unclear"),
        ]:
            if phrase in t:
                return result
        return "Unclear"

    def _extract_confidence(self, text: str) -> str:
        t = text.lower()
        for phrase, result in [
            ("confidence: high", "High"),
            ("уверенность: высокая", "High"),
            ("confidence: medium", "Medium"),
            ("уверенность: средняя", "Medium"),
            ("confidence: low", "Low"),
            ("уверенность: низкая", "Low"),
        ]:
            if phrase in t:
                return result
        return "Low"
