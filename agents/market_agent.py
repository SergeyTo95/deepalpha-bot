from typing import Dict, List, Any

from services.polymarket_service import (
    extract_slug_from_url,
    find_related_markets,
    get_primary_market_from_url,
    normalize_market_data,
    normalize_related_markets,
)


class MarketAgent:
    def __init__(self) -> None:
        pass

    def run(self, url: str) -> Dict[str, Any]:
        slug = extract_slug_from_url(url)
        raw_market = get_primary_market_from_url(url)

        if not raw_market:
            return self._fallback(url, slug)

        normalized = normalize_market_data(raw_market)
        question = normalized.get("question", "Unknown market")
        category = self._detect_category(question)
        options = normalized.get("options", [])

        raw_related = find_related_markets(
            question=question,
            category_hint=category,
            limit=5,
        )
        related_markets = normalize_related_markets(
            raw_related,
            main_question=question,
        )

        return {
            "url": url,
            "slug": slug,
            "question": question,
            "category": category,
            "market_type": self._detect_market_type(options),
            "market_probability": normalized.get("market_probability", "Unknown"),
            "options": options,
            "related_markets": related_markets,
            "volume": normalized.get("volume", "Unknown"),
            "liquidity": normalized.get("liquidity", "Unknown"),
            "trend_summary": normalized.get("trend_summary", "Unknown"),
            "crowd_behavior": normalized.get("crowd_behavior", "Unknown"),
            "date_context": normalized.get("end_date", "Unknown"),
            "primary_token_id": normalized.get("primary_token_id", ""),
            "price_history": normalized.get("price_history", {"24h": [], "7d": []}),
            "raw_market_data": normalized.get("raw_market_data", {}),
        }

    def _detect_market_type(self, options: List[str]) -> str:
        normalized = {str(x).strip() for x in options}

        if len(normalized) == 2 and normalized == {"Yes", "No"}:
            return "binary"

        if len(normalized) > 2:
            return "multiple_choice"

        return "unknown"

    def _detect_category(self, text: str) -> str:
        s = (text or "").lower()

        politics_keywords = [
            "trump", "biden", "harris", "vance", "election", "senate", "white house",
            "president", "congress", "vote", "republican", "democrat", "electoral",
            "campaign", "cabinet", "administration", "governor", "mayor", "midterm",
            "putin", "zelensky", "macron", "orban", "modi", "xi jinping",
            "nato", "un ", "united nations", "eu ", "european union", "parliament",
            "prime minister", "chancellor", "minister", "government", "summit",
            "embassy", "ambassador", "diplomacy", "treaty", "sanctions",
            "iran", "israel", "ukraine", "russia", "china", "war", "conflict",
            "ceasefire", "military", "missile", "nuclear", "strike", "attack",
            "invasion", "troops", "weapon", "bomb", "drone", "navy",
            "venezuela", "taiwan", "north korea", "pakistan",
            "peace deal", "peace talks", "negotiations",
            "geopolitical", "coup", "protest", "revolution",
        ]

        crypto_keywords = [
            "bitcoin", "btc", "eth", "ethereum", "solana", "sol", "crypto",
            "token", "sec", "etf", "ton", "airdrop", "defi", "memecoin",
            "blockchain", "coinbase", "binance", "altcoin", "nft", "usdc",
            "xrp", "ripple", "cardano", "ada", "dogecoin", "doge",
            "polygon", "matic", "avalanche", "avax", "chainlink",
            "stablecoin", "halving", "mining", "wallet", "exchange", "dex",
        ]

        sports_keywords = [
            "nba", "nfl", "mlb", "nhl", "ufc", "mma", "fifa", "nascar",
            "premier league", "champions league", "la liga", "serie a",
            "bundesliga", "ligue 1", "super bowl", "world cup", "stanley cup",
            "world series", "march madness", "masters", "wimbledon", "grand slam",
            "olympics", "formula 1", "f1", "grand prix",
            "football", "soccer", "basketball", "baseball", "hockey", "tennis",
            "golf", "boxing", "wrestling", "cricket", "rugby",
            "esports", "league of legends", "valorant", "cs2", "dota",
            "celtics", "lakers", "warriors", "heat", "bulls", "knicks",
            "nets", "mavericks", "nuggets", "suns", "clippers", "bucks",
            "76ers", "spurs", "rockets", "pistons", "pacers", "hawks",
            "thunder", "trail blazers", "jazz", "timberwolves", "grizzlies",
            "chiefs", "patriots", "cowboys", "eagles", "49ers", "ravens",
            "bengals", "bills", "dolphins", "steelers", "browns", "broncos",
            "yankees", "dodgers", "red sox", "cubs", "astros", "braves",
            "mets", "cardinals", "giants", "phillies",
            "arsenal", "chelsea", "liverpool", "manchester", "barcelona",
            "real madrid", "psg", "juventus", "bayern", "inter", "ac milan",
            "atletico", "borussia", "ajax", "porto", "benfica",
            "djokovic", "nadal", "federer", "alcaraz", "sinner", "swiatek",
            "championship", "playoffs", "finals", "match", "tournament",
            "cup", "trophy", "title", "win the", "will win", "champion",
            "season", "transfer", "roster", "score", "goal",
        ]

        economy_keywords = [
            "inflation", "fed", "federal reserve", "rate", "recession", "gdp",
            "cpi", "jobs", "oil", "economy", "yield", "unemployment",
            "interest rate", "wall street", "stock market", "s&p", "nasdaq",
            "dow jones", "dollar", "currency", "trade war", "tariff",
            "debt", "deficit", "budget", "treasury", "bond", "fomc",
            "powell", "ecb", "imf", "world bank", "brent", "wti",
            "gold", "silver", "commodities", "bankruptcy", "merger", "ipo",
            "earnings", "revenue", "profit", "gdp growth",
        ]

        tech_keywords = [
            "openai", "chatgpt", "gpt", "ai ", "artificial intelligence",
            "google", "apple", "tesla", "nvidia", "microsoft", "meta",
            "amazon", "spacex", "starship", "anthropic", "grok", "xai",
            "gemini", "claude", "llm", "model", "launch", "chip",
            "iphone", "android", "samsung", "intel", "amd",
            "robot", "autonomous", "self-driving", "electric vehicle", "ev",
            "neuralink", "starlink", "satellite", "cybertruck",
        ]

        culture_keywords = [
            "oscar", "grammy", "emmy", "golden globe", "academy award",
            "box office", "album", "song", "artist", "celebrity",
            "movie", "film", "show", "series", "netflix", "disney",
            "taylor swift", "beyonce", "drake", "kanye", "rihanna",
            "billboard", "spotify", "superbowl halftime",
        ]

        weather_keywords = [
            "hurricane", "tornado", "earthquake", "flood", "wildfire",
            "temperature", "celsius", "fahrenheit", "snowfall", "rainfall",
            "climate", "el nino", "storm", "typhoon", "cyclone",
        ]

        # Порядок важен — Politics перед Sports
        if any(word in s for word in politics_keywords):
            return "Politics"
        if any(word in s for word in sports_keywords):
            return "Sports"
        if any(word in s for word in crypto_keywords):
            return "Crypto"
        if any(word in s for word in economy_keywords):
            return "Economy"
        if any(word in s for word in tech_keywords):
            return "Tech"
        if any(word in s for word in culture_keywords):
            return "Culture"
        if any(word in s for word in weather_keywords):
            return "Weather"
        return "Other"

    def _fallback(self, url: str, slug: str) -> Dict[str, Any]:
        return {
            "url": url,
            "slug": slug,
            "question": "Unable to resolve market from Polymarket URL",
            "category": "Unknown",
            "market_type": "unknown",
            "market_probability": "Unknown",
            "options": [],
            "related_markets": [],
            "volume": "Unknown",
            "liquidity": "Unknown",
            "trend_summary": "Could not fetch market history in current request.",
            "crowd_behavior": "Crowd behavior unavailable because primary market data could not be resolved.",
            "date_context": "Unknown",
            "primary_token_id": "",
            "price_history": {"24h": [], "7d": []},
            "raw_market_data": {},
        }
