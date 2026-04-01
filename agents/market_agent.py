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
            "trump", "biden", "election", "senate", "white house",
            "president", "congress", "vote", "eu", "europe", "summit",
            "government", "minister", "prime minister", "parliament",
        ]
        crypto_keywords = [
            "bitcoin", "btc", "eth", "ethereum", "solana", "crypto",
            "token", "sec", "etf", "ton", "airdrop", "defi", "memecoin",
        ]
        sports_keywords = [
            "nba", "nfl", "mlb", "ufc", "football", "soccer",
            "tennis", "golf", "match", "cup", "championship", "playoffs",
        ]
        economy_keywords = [
            "inflation", "fed", "rate", "recession", "gdp",
            "cpi", "jobs", "oil", "economy", "yield", "unemployment",
        ]
        tech_keywords = [
            "openai", "ai", "google", "apple", "tesla", "nvidia",
            "launch", "chip", "model", "xai", "anthropic",
        ]

        if any(word in s for word in politics_keywords):
            return "Politics"
        if any(word in s for word in crypto_keywords):
            return "Crypto"
        if any(word in s for word in sports_keywords):
            return "Sports"
        if any(word in s for word in economy_keywords):
            return "Economy"
        if any(word in s for word in tech_keywords):
            return "Tech"

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
