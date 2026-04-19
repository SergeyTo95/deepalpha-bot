
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
        """Единая функция из news_agent — единый источник правды."""
        from agents.news_agent import detect_category_from_text
        return detect_category_from_text(text)

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
