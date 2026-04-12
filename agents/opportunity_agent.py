import random
from typing import Any, Dict, List, Optional

from agents.news_agent import NewsAgent
from agents.decision_agent import DecisionAgent
from db.database import save_opportunity
from services.polymarket_service import list_markets, normalize_market_data


class OpportunityAgent:
    def __init__(self) -> None:
        self.news_agent = NewsAgent()
        self.decision_agent = DecisionAgent()

    def run(
        self,
        limit: int = 3,
        category_filter: str = "All",
        strong_only: bool = False,
        min_score: int = 45,
        lang: str = "en",
        exclude_questions: list = None,
    ) -> Dict[str, Any]:
        raw_markets = self._get_candidate_markets(
            limit=limit,
            category_filter=category_filter,
            exclude_questions=exclude_questions or [],
        )

        if not raw_markets:
            raw_markets = self._get_candidate_markets(
                limit=limit,
                category_filter=category_filter,
                exclude_questions=[],
            )

        if not raw_markets:
            return self._fallback("No active candidate markets found.")

        candidates = []

        for raw_market in raw_markets:
            try:
                market_data = self._build_market_context(raw_market)
                if not market_data:
                    continue

                if category_filter != "All" and market_data.get("category") != category_filter:
                    continue

                news_data = self.news_agent.run(market_data, lang=lang)
                decision_data = self.decision_agent.run(market_data, news_data, lang=lang)
                score = self._score_opportunity(market_data, decision_data)

                if strong_only and score < min_score:
                    continue

                candidates.append({
                    "market_data": market_data,
                    "news_data": news_data,
                    "decision_data": decision_data,
                    "score": score,
                })
            except Exception as e:
                print(f"Market analysis error: {e}")
                continue

        if not candidates:
            return self._fallback("No viable opportunities after analysis.")

        best = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]

        market_data = best["market_data"]
        decision_data = best["decision_data"]
        news_data = best.get("news_data", {})

        result = {
            "mode": "opportunity",
            "question": market_data.get("question", "Unknown market"),
            "category": market_data.get("category", "Unknown"),
            "market_probability": market_data.get("market_probability", "Unknown"),
            "probability": decision_data.get("probability", "Unknown"),
            "confidence": decision_data.get("confidence", "Unknown"),
            "reasoning": decision_data.get("reasoning", "No reasoning available."),
            "main_scenario": decision_data.get("main_scenario", "No main scenario."),
            "alt_scenario": decision_data.get("alt_scenario", "No alternative scenario."),
            "conclusion": decision_data.get("conclusion", "No conclusion."),
            "opportunity_score": best["score"],
            "url": market_data.get("url", ""),
            "news_items": news_data.get("sources", []),
            "news_sources": news_data.get("sources", []),
        }

        save_opportunity(result)
        return result

    def _get_candidate_markets(
        self,
        limit: int = 3,
        category_filter: str = "All",
        exclude_questions: list = None,
    ) -> List[Dict[str, Any]]:
        exclude = set(q.lower() for q in (exclude_questions or []))

        random_offset = random.randint(0, 100)
        markets = list_markets(limit=max(limit * 5, 20), offset=random_offset)

        if len(markets) < limit:
            markets += list_markets(limit=max(limit * 5, 20))

        random.shuffle(markets)

        filtered = []
        for market in markets:
            question = str(market.get("question") or market.get("title") or "").strip()
            if not question:
                continue
            if self._looks_like_noise(question):
                continue
            if question.lower() in exclude:
                continue
            if self._is_too_one_sided(market):
                continue
            if category_filter != "All":
                detected = self._detect_category(question)
                if detected != category_filter:
                    continue
            filtered.append(market)
            if len(filtered) >= limit:
                break

        return filtered

    def _is_too_one_sided(self, market: Dict[str, Any]) -> bool:
        try:
            outcome_prices = market.get("outcomePrices", "")
            if isinstance(outcome_prices, str):
                cleaned = outcome_prices.strip("[]")
                prices = [float(p.strip().strip('"')) for p in cleaned.split(",") if p.strip()]
            elif isinstance(outcome_prices, list):
                prices = [float(p) for p in outcome_prices]
            else:
                return False

            if not prices:
                return False

            return max(prices) >= 0.85
        except Exception:
            return False

    def _build_market_context(self, raw_market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized = normalize_market_data(raw_market)
        if not normalized:
            return None

        question = normalized.get("question", "Unknown market")
        options = normalized.get("options", [])
        category = self._detect_category(question)
        market_type = self._detect_market_type(options)

        return {
            "url": raw_market.get("url", ""),
            "slug": raw_market.get("slug", ""),
            "question": question,
            "category": category,
            "market_type": market_type,
            "market_probability": normalized.get("market_probability", "Unknown"),
            "options": options,
            "related_markets": [],
            "volume": normalized.get("volume", "Unknown"),
            "liquidity": normalized.get("liquidity", "Unknown"),
            "trend_summary": normalized.get("trend_summary", "Unknown"),
            "crowd_behavior": normalized.get("crowd_behavior", "Unknown"),
            "date_context": normalized.get("end_date", "Unknown"),
            "raw_market_data": normalized.get("raw_market_data", {}),
            "price_history": normalized.get("price_history", {"24h": [], "7d": []}),
        }

    def _score_opportunity(self, market_data: Dict[str, Any], decision_data: Dict[str, Any]) -> int:
        score = 0

        confidence = str(decision_data.get("confidence", "")).lower()
        probability_text = str(decision_data.get("probability", "")).lower()
        category = str(market_data.get("category", "")).lower()
        liquidity = str(market_data.get("liquidity", "0"))
        volume = str(market_data.get("volume", "0"))
        trend_summary = str(market_data.get("trend_summary", "")).lower()
        crowd_behavior = str(market_data.get("crowd_behavior", "")).lower()
        reasoning = str(decision_data.get("reasoning", "")).lower()

        if "high" in confidence or "высок" in confidence:
            score += 25
        elif "medium" in confidence or "средн" in confidence:
            score += 15
        else:
            score += 5

        if "%" in probability_text:
            score += 15

        if category in {"politics", "crypto", "economy"}:
            score += 10
        elif category in {"sports", "tech"}:
            score += 6

        if "accelerated" in trend_summary:
            score += 10
        if "24h move" in trend_summary:
            score += 5
        if "strengthened sharply" in crowd_behavior:
            score += 8
        elif "moderately" in crowd_behavior:
            score += 4

        score += self._parse_numeric_bonus(liquidity, max_bonus=20)
        score += self._parse_numeric_bonus(volume, max_bonus=20)

        if "market" in reasoning:
            score += 5
        if "related" in reasoning or "external" in reasoning:
            score += 5

        return score

    def _parse_numeric_bonus(self, value: str, max_bonus: int = 20) -> int:
        try:
            cleaned = value.replace(",", "").replace("$", "").replace("%", "").strip()
            number = float(cleaned)
            if number <= 0:
                return 0
            if number < 1000:
                return min(5, max_bonus)
            if number < 10000:
                return min(10, max_bonus)
            if number < 100000:
                return min(15, max_bonus)
            return min(20, max_bonus)
        except Exception:
            return 0

    def _detect_market_type(self, options: List[str]) -> str:
        if len(options) == 2 and {"Yes", "No"} == set(options):
            return "binary"
        if len(options) > 2:
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
        ]

        tech_keywords = [
            "openai", "chatgpt", "gpt", "ai ", "artificial intelligence",
            "google", "apple", "tesla", "nvidia", "microsoft", "meta",
            "amazon", "spacex", "starship", "anthropic", "grok", "xai",
            "gemini", "claude", "llm", "model", "launch", "chip",
            "iphone", "android", "samsung", "intel", "amd",
            "robot", "autonomous", "self-driving", "electric vehicle", "ev",
            "neuralink", "starlink", "satellite",
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

    def _looks_like_noise(self, question: str) -> bool:
        q = question.lower()
        return any(pattern in q for pattern in ["test", "demo", "sample", "mock"])

    def _fallback(self, reason: str) -> Dict[str, Any]:
        return {
            "mode": "opportunity",
            "question": "No strong opportunity found",
            "category": "Unknown",
            "market_probability": "Unknown",
            "probability": "Unavailable",
            "confidence": "Low",
            "reasoning": reason,
            "main_scenario": "Not available",
            "alt_scenario": "Not available",
            "conclusion": "Opportunity engine could not identify a valid signal yet.",
            "opportunity_score": 0,
            "url": "",
            "news_items": [],
            "news_sources": [],
        }
