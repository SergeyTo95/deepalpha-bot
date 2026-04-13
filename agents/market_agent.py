
from typing import Dict, List, Any, Optional

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
        market_type = self._detect_market_type(options)

        market_probability = self._build_market_probability(
            raw_market=raw_market,
            options=options,
            market_type=market_type,
            normalized_prob=normalized.get("market_probability", "Unknown"),
        )

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
            "market_type": market_type,
            "market_probability": market_probability,
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

    def _build_market_probability(
        self,
        raw_market: Dict[str, Any],
        options: List[str],
        market_type: str,
        normalized_prob: str,
    ) -> str:
        try:
            outcome_prices = raw_market.get("outcomePrices", "")

            prices = []
            if isinstance(outcome_prices, list):
                prices = [float(p) for p in outcome_prices]
            elif isinstance(outcome_prices, str):
                cleaned = outcome_prices.strip("[]")
                prices = [
                    float(p.strip().strip('"'))
                    for p in cleaned.split(",")
                    if p.strip()
                ]

            if not prices or not options:
                return normalized_prob

            if market_type == "binary":
                if len(prices) >= 2 and len(options) >= 2:
                    yes_idx = next(
                        (i for i, o in enumerate(options) if o.strip().lower() == "yes"), 0
                    )
                    no_idx = next(
                        (i for i, o in enumerate(options) if o.strip().lower() == "no"), 1
                    )
                    yes_pct = round(prices[yes_idx] * 100, 2) if yes_idx < len(prices) else 0
                    no_pct = round(prices[no_idx] * 100, 2) if no_idx < len(prices) else 0
                    return f"Yes: {yes_pct}% | No: {no_pct}%"
                elif prices:
                    yes_pct = round(prices[0] * 100, 2)
                    return f"Yes: {yes_pct}% | No: {round(100 - yes_pct, 2)}%"

            elif market_type == "multiple_choice":
                paired = sorted(
                    zip(options, prices),
                    key=lambda x: x[1],
                    reverse=True,
                )
                parts = [f"{opt}: {round(price * 100, 2)}%" for opt, price in paired]
                return " | ".join(parts)

        except Exception as e:
            print(f"MarketAgent._build_market_probability error: {e}")

        return normalized_prob

    def _detect_market_type(self, options: List[str]) -> str:
        normalized = {str(x).strip().lower() for x in options}

        if len(normalized) == 2 and normalized == {"yes", "no"}:
            return "binary"

        if len(normalized) > 2:
            return "multiple_choice"

        if len(normalized) == 1:
            return "binary"

        return "unknown"

    def _detect_category(self, text: str) -> str:
        s = (text or "").lower()

        # ===== ECONOMY — ПЕРВЫМ (макро, банки, ставки) =====
        economy_priority = [
            "bank of japan", "boj", "federal reserve", "fed rate",
            "fed cut", "fed hike", "fed hold", "fed pause",
            "interest rate", "rate cut", "rate hike", "rate hold",
            "basis point", "bps", "ecb rate", "bank of england",
            "boe rate", "rba rate", "inflation rate", "cpi report",
            "pce report", "gdp growth", "recession risk",
            "unemployment rate", "opec", "oil price", "crude oil",
            "brent", "wti", "yield curve", "treasury yield",
            "10-year yield", "imf forecast", "world bank",
            "debt ceiling", "fiscal", "monetary policy",
            "quantitative easing", "qe", "tapering",
        ]
        if any(word in s for word in economy_priority):
            return "Economy"

        # ===== POLITICS =====
        politics_keywords = [
            "trump", "biden", "harris", "vance", "election", "senate",
            "white house", "president", "congress", "vote", "republican",
            "democrat", "electoral", "campaign", "cabinet", "administration",
            "governor", "mayor", "midterm", "putin", "zelensky", "macron",
            "orban", "modi", "xi jinping", "nato", "un ", "united nations",
            "eu ", "european union", "parliament", "prime minister",
            "chancellor", "minister", "government", "summit", "embassy",
            "ambassador", "diplomacy", "treaty", "sanctions",
            "iran deal", "israel", "ukraine", "russia", "war", "conflict",
            "ceasefire", "military", "missile", "nuclear", "strike",
            "attack", "invasion", "troops", "weapon", "bomb", "drone",
            "navy", "venezuela", "taiwan", "north korea", "pakistan",
            "peace deal", "peace talks", "negotiations", "geopolitical",
            "coup", "protest", "revolution", "polling", "approval rating",
            "impeach", "resign",
        ]
        if any(word in s for word in politics_keywords):
            return "Politics"

        # ===== CRYPTO =====
        crypto_keywords = [
            "bitcoin", "btc", "eth", "ethereum", "solana", "sol",
            "crypto", "token", "defi", "memecoin", "blockchain",
            "coinbase", "binance", "altcoin", "nft", "usdc", "xrp",
            "ripple", "cardano", "ada", "dogecoin", "doge", "polygon",
            "matic", "avalanche", "avax", "chainlink", "stablecoin",
            "halving", "mining", "wallet", "exchange", "dex", "web3",
            "dao", "smart contract", "crypto etf", "bitcoin etf",
            "sec crypto",
        ]
        if any(word in s for word in crypto_keywords):
            return "Crypto"

        # ===== SPORTS — только явный спорт =====
        sports_keywords = [
            "nba", "nfl", "mlb", "nhl", "ufc", "mma", "fifa", "nascar",
            "premier league", "champions league", "la liga", "serie a",
            "bundesliga", "ligue 1", "super bowl", "world cup",
            "stanley cup", "world series", "march madness", "masters",
            "wimbledon", "grand slam", "olympics", "formula 1",
            " f1 ", "grand prix",
            "celtics", "lakers", "warriors", "heat", "bulls", "knicks",
            "nets", "mavericks", "nuggets", "suns", "clippers", "bucks",
            "76ers", "spurs", "rockets", "pistons", "pacers", "hawks",
            "thunder", "trail blazers", "jazz", "timberwolves", "grizzlies",
            "chiefs", "patriots", "cowboys", "eagles", "49ers", "ravens",
            "bengals", "bills", "dolphins", "steelers", "browns", "broncos",
            "yankees", "dodgers", "red sox", "cubs", "astros", "braves",
            "arsenal", "chelsea", "liverpool", "manchester united",
            "manchester city", "barcelona", "real madrid", "psg",
            "juventus", "bayern munich", "inter milan", "ac milan",
            "djokovic", "nadal", "federer", "alcaraz", "sinner", "swiatek",
            "win the nba", "win the nfl", "win the mlb", "win the nhl",
            "win the championship", "win the finals", "win the cup",
            "win the league", "win the tournament", "win the title",
            "win the world cup", "win the super bowl",
            "playoffs", "postseason", "draft pick",
            "basketball game", "football game", "soccer match",
            "tennis match", "golf tournament", "boxing match",
        ]
        if any(word in s for word in sports_keywords):
            return "Sports"

        # ===== ECONOMY — общие =====
        economy_general = [
            "inflation", "interest rates", "central bank",
            "stock market", "s&p 500", "nasdaq", "dow jones",
            "dollar index", "currency", "trade war", "tariff",
            "debt", "deficit", "budget", "fomc", "powell",
            "gold price", "silver price", "commodities",
            "bankruptcy", "merger", "acquisition", "ipo",
            "earnings", "revenue", "profit", "market cap",
            "largest company", "biggest company",
            "economic growth", "economic crisis", "gdp",
        ]
        if any(word in s for word in economy_general):
            return "Economy"

        # ===== TECH =====
        tech_keywords = [
            "openai", "chatgpt", "gpt-", "ai model",
            "artificial intelligence", "google", "apple", "tesla",
            "nvidia", "microsoft", "meta", "amazon", "spacex",
            "starship", "anthropic", "grok", "xai", "gemini",
            "claude", "llm", "language model", "chip", "iphone",
            "android", "samsung", "intel", "amd", "robot",
            "autonomous", "self-driving", "electric vehicle",
            "neuralink", "starlink", "satellite", "cybertruck",
            "best ai", "which company has the best",
            "software", "hardware", "data center", "cloud",
        ]
        if any(word in s for word in tech_keywords):
            return "Tech"

        # ===== CULTURE =====
        culture_keywords = [
            "oscar", "grammy", "emmy", "golden globe", "academy award",
            "box office", "album", "song", "artist", "celebrity",
            "movie", "film", "show", "series", "netflix", "disney",
            "taylor swift", "beyonce", "drake", "kanye", "rihanna",
            "billboard", "spotify",
        ]
        if any(word in s for word in culture_keywords):
            return "Culture"

        # ===== WEATHER =====
        weather_keywords = [
            "hurricane", "tornado", "earthquake", "flood", "wildfire",
            "temperature record", "climate", "el nino", "typhoon",
            "cyclone",
        ]
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
            "trend_summary": "Could not fetch market history.",
            "crowd_behavior": "Crowd behavior unavailable.",
            "date_context": "Unknown",
            "primary_token_id": "",
            "price_history": {"24h": [], "7d": []},
            "raw_market_data": {},
        }
