import asyncio
import random
import concurrent.futures
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
        limit: int = 8,
        category_filter: str = "All",
        strong_only: bool = False,
        min_score: int = 45,
        lang: str = "en",
        exclude_questions: list = None,
    ) -> Dict[str, Any]:
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self._run_async(limit, category_filter, strong_only, min_score, lang, exclude_questions or [])
                )
                return future.result(timeout=120)
        except concurrent.futures.TimeoutError:
            print("OpportunityAgent: timeout after 120 seconds")
            return self._fallback("Analysis timed out")
        except Exception as e:
            print(f"OpportunityAgent error: {e}")
            return self._fallback(str(e))

    async def _run_async(
        self,
        limit: int = 8,
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
            # Если все рынки в blacklist — сбрасываем и берём любые
            raw_markets = self._get_candidate_markets(
                limit=limit,
                category_filter=category_filter,
                exclude_questions=[],
            )

        if not raw_markets:
            return self._fallback("No active candidate markets found.")

        market_contexts = []
        for raw_market in raw_markets:
            market_data = self._build_market_context(raw_market)
            if not market_data:
                continue
            if category_filter != "All" and market_data.get("category") != category_filter:
                continue
            market_contexts.append((raw_market, market_data))

        if not market_contexts:
            return self._fallback("No viable markets after filtering.")

        tasks = [
            self._analyze_market(raw_market, market_data, lang)
            for raw_market, market_data in market_contexts
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates = []
        for result in results:
            if isinstance(result, Exception):
                print(f"Market analysis error: {result}")
                continue
            if result is None:
                continue
            score = result.get("score", 0)
            if strong_only and score < min_score:
                continue
            candidates.append(result)

        if not candidates:
            return self._fallback("No viable opportunities after analysis.")

        best = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]

        market_data = best["market_data"]
        decision_data = best["decision_data"]

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
        }

        save_opportunity(result)
        return result

    async def _analyze_market(
        self,
        raw_market: Dict[str, Any],
        market_data: Dict[str, Any],
        lang: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            news_data = await self._run_news_async(market_data, lang)
            decision_data = await self._run_decision_async(market_data, news_data, lang)
            score = self._score_opportunity(market_data, decision_data)

            return {
                "market_data": market_data,
                "news_data": news_data,
                "decision_data": decision_data,
                "score": score,
            }
        except Exception as e:
            print(f"_analyze_market error: {e}")
            return None

    async def _run_news_async(self, market_data: Dict[str, Any], lang: str) -> Dict[str, Any]:
        try:
            from services.news_service import build_news_query, search_google_news, summarize_news_items
            from services.llm_service import generate_news_text_async

            question = market_data.get("question", "")
            category = market_data.get("category", "")
            date_context = market_data.get("date_context", "")

            news_query = build_news_query(question=question, category=category, date_context=date_context)
            news_items = search_google_news(news_query, limit=5)
            live_news_summary = summarize_news_items(news_items)

            prompt = self.news_agent._build_prompt(
                question=question,
                category=category,
                date_context=date_context,
                related_markets=market_data.get("related_markets", []),
                live_news_summary=live_news_summary,
                lang=lang,
            )

            llm_result = await generate_news_text_async(prompt)

            if llm_result and not llm_result.lower().startswith("llm service is not configured"):
                return self.news_agent._wrap_llm_result(
                    question=question,
                    category=category,
                    llm_result=llm_result,
                    news_query=news_query,
                    news_items=news_items,
                )

            return self.news_agent._fallback_news(
                question=question,
                category=category,
                date_context=date_context,
                related_markets=market_data.get("related_markets", []),
                live_news_summary=live_news_summary,
                news_query=news_query,
                news_items=news_items,
            )
        except Exception as e:
            print(f"_run_news_async error: {e}")
            return {"news_summary": "", "sentiment": "Unclear", "confidence": "Low", "sources": []}

    async def _run_decision_async(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str,
    ) -> Dict[str, Any]:
        try:
            from services.llm_service import generate_decision_text_async

            prompt = self.decision_agent._build_prompt(
                question=market_data.get("question", ""),
                category=market_data.get("category", ""),
                market_probability=market_data.get("market_probability", ""),
                options=market_data.get("options", []),
                related_markets=market_data.get("related_markets", []),
                trend_summary=market_data.get("trend_summary", ""),
                crowd_behavior=market_data.get("crowd_behavior", ""),
                news_summary=news_data.get("news_summary", ""),
                sentiment=news_data.get("sentiment", ""),
                news_confidence=news_data.get("confidence", ""),
                lang=lang,
            )

            raw_response = await generate_decision_text_async(prompt)

            if raw_response and not raw_response.lower().startswith("llm service is not configured"):
                parsed = self.decision_agent._parse_llm_output(raw_response)
                wrapped = self.decision_agent._wrap_llm_result(
                    question=market_data.get("question", ""),
                    category=market_data.get("category", ""),
                    market_probability=market_data.get("market_probability", ""),
                    parsed=parsed,
                    raw_text=raw_response,
                )
                if self.decision_agent._is_valid_result(wrapped):
                    return wrapped

            return self.decision_agent._fallback_decision(
                question=market_data.get("question", ""),
                category=market_data.get("category", ""),
                market_probability=market_data.get("market_probability", ""),
                options=market_data.get("options", []),
                related_markets=market_data.get("related_markets", []),
                trend_summary=market_data.get("trend_summary", ""),
                crowd_behavior=market_data.get("crowd_behavior", ""),
                news_summary=news_data.get("news_summary", ""),
                sentiment=news_data.get("sentiment", ""),
                news_confidence=news_data.get("confidence", ""),
            )
        except Exception as e:
            print(f"_run_decision_async error: {e}")
            return {
                "probability": "Unknown",
                "confidence": "Low",
                "reasoning": "Error during analysis",
                "main_scenario": "Unavailable",
                "alt_scenario": "Unavailable",
                "conclusion": "Unavailable",
            }

    def _get_candidate_markets(
        self,
        limit: int = 8,
        category_filter: str = "All",
        exclude_questions: list = None,
    ) -> List[Dict[str, Any]]:
        exclude = set(q.lower() for q in (exclude_questions or []))

        # Случайный offset чтобы каждый раз разные рынки
        random_offset = random.randint(0, 100)
        markets = list_markets(limit=max(limit * 5, 30), offset=random_offset)

        # Если мало рынков — добавляем без offset
        if len(markets) < limit:
            markets += list_markets(limit=max(limit * 5, 30))

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
            if category_filter != "All":
                detected = self._detect_category(question)
                if detected != category_filter:
                    continue
            filtered.append(market)
            if len(filtered) >= limit:
                break

        return filtered

    def _build_market_context(self, raw_market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized = normalize_market_data(raw_market)
        if not normalized:
            return None

        question = normalized.get("question", "Unknown market")
        options = normalized.get("options", [])
        category = self._detect_category(question)

        return {
            "url": raw_market.get("url", ""),
            "slug": raw_market.get("slug", ""),
            "question": question,
            "category": category,
            "market_type": self._detect_market_type(options),
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

        if "high" in confidence:
            score += 25
        elif "medium" in confidence:
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

        if "independent probability" in reasoning or "market" in reasoning:
            score += 5
        if "related markets" in reasoning or "external signals" in reasoning:
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

        politics_keywords = ["trump", "biden", "election", "senate", "white house",
                             "president", "congress", "vote", "eu", "europe", "summit"]
        crypto_keywords = ["bitcoin", "btc", "eth", "ethereum", "solana", "crypto",
                           "token", "sec", "etf", "ton"]
        sports_keywords = ["nba", "nfl", "mlb", "ufc", "football", "soccer",
                           "tennis", "golf", "match", "cup"]
        economy_keywords = ["inflation", "fed", "rate", "recession", "gdp",
                            "cpi", "jobs", "oil", "economy"]
        tech_keywords = ["openai", "ai", "google", "apple", "tesla", "nvidia",
                         "launch", "chip", "model"]

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
        }
