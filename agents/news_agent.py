
from typing import Any, Dict, List

from services.llm_service import generate_news_text
from services.news_service import (
    build_news_query,
    search_google_news,
    summarize_news_items,
)


# ═══════════════════════════════════════════
# UNIFIED CATEGORY DETECTION
# Используется везде: OpportunityAgent, post_to_channel, ChiefAgent
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
    "mets", "cardinals", "giants", "phillies",
    "arsenal", "chelsea", "liverpool", "manchester", "barcelona",
    "real madrid", "psg", "juventus", "bayern", "inter milan", "ac milan",
    "atletico", "borussia", "ajax", "porto", "benfica",
    "djokovic", "nadal", "federer", "alcaraz", "sinner", "swiatek",
    "championship", "playoff", "finals", "tournament",
    " cup ", "trophy", "title", "will win the", "champion",
    "season", "transfer", "roster", " goal ",
    "boxing", "fight", "knockout", "ko ",
]

ECONOMY_KEYWORDS = [
    "inflation", " fed ", "federal reserve", "recession", " gdp ",
    " cpi ", "unemployment",
    "interest rate", "wall street", "stock market", " s&p ", "nasdaq",
    "dow jones", "dollar", "currency", "trade war", "tariff",
    "debt", "deficit", "budget", "treasury", "bond ", "fomc ",
    "powell", " ecb ", " imf ", "world bank", "brent", " wti ",
    "gold ", "silver", "commodit", "bankruptcy", "merger", " ipo ",
    "jobless", "payrolls", "economic",
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
    Порядок важен — сначала политика, потом спорт, крипта, экономика.
    """
    if not text:
        return "Other"

    # Добавляем пробелы по краям чтобы правильно работать с "word boundary"
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

        news_items = search_google_news(news_query, limit=7)

        twitter_query = f"{news_query} site:twitter.com OR site:x.com"
        twitter_items = search_google_news(twitter_query, limit=3)

        all_items = news_items + [i for i in twitter_items if i not in news_items]
        live_news_summary = summarize_news_items(all_items[:7])

        prompt = self._build_prompt(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            news_items=all_items[:5],
            lang=lang,
        )

        llm_result = generate_news_text(prompt)

        if llm_result and not llm_result.lower().startswith("llm service is not configured"):
            return self._wrap_llm_result(
                question=question,
                category=category,
                llm_result=llm_result,
                news_query=news_query,
                news_items=all_items[:5],
            )

        return self._fallback_news(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            news_query=news_query,
            news_items=all_items[:5],
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

        related_block = "\n".join(related_lines) if related_lines else "- No related markets"

        lang_instruction = (
            "Respond ONLY in Russian. Every single word must be in Russian language. "
            "Translate all terms, sources, and analysis into Russian."
            if lang == "ru"
            else "Respond in English."
        )

        has_news = live_news_summary and "No relevant" not in live_news_summary

        top_news_block = ""
        if news_items:
            lines = []
            for i, item in enumerate(news_items[:5], 1):
                title = item.get("title", "")
                source = item.get("source", "")
                published = item.get("published", "")
                link = item.get("link", "")
                if title:
                    line = f"{i}. {title} ({source}, {published})"
                    if link:
                        line += f" — {link}"
                    lines.append(line)
            top_news_block = "\n".join(lines)

        return f"""
You are DeepAlpha News Intelligence — an expert analyst for prediction markets.

{lang_instruction}

TASK: Analyze real-world news context for this prediction market and identify signals that affect probability. Base your analysis STRICTLY on the provided news — do not invent facts.

MARKET QUESTION: {question}
CATEGORY: {category}
DEADLINE: {date_context}

RELATED MARKETS:
{related_block}

TOP NEWS SOURCES:
{top_news_block if top_news_block else "No news sources found."}

FULL NEWS FEED:
{live_news_summary if has_news else "No recent news found for this topic."}

ANALYSIS RULES:
1. Base your analysis ONLY on the provided news above — do not hallucinate
2. If news feed is empty — explicitly state "No recent news found" and use only market data
3. Clearly separate SUPPORTING signals from OPPOSING signals
4. Rate signal strength: Strong / Moderate / Weak
5. Be specific — mention dates, names, numbers from the news
6. Focus on what CHANGES the probability
7. Include Twitter/X sentiment if social media sources are present

REQUIRED OUTPUT FORMAT:

News Summary:
[2-3 sentence overview based strictly on provided news]

Key Signals:
- [Signal 1 with strength rating]
- [Signal 2 with strength rating]
- [Signal 3 with strength rating]

Supporting Factors:
- [Factor that increases YES probability]
- [Factor that increases YES probability]

Opposing Factors:
- [Factor that decreases YES probability]
- [Factor that decreases YES probability]

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
    ) -> Dict[str, Any]:
        return {
            "question": question,
            "category": category,
            "news_query": news_query,
            "news_summary": llm_result,
            "sources": news_items,
            "sentiment": self._extract_sentiment(llm_result),
            "confidence": self._extract_confidence(llm_result),
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
