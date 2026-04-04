import asyncio
import concurrent.futures
from typing import Any, Dict

from db.database import save_analysis


class ChiefAgent:
    def __init__(self) -> None:
        pass

    def run(self, url: str, lang: str = "en") -> Dict[str, Any]:
        """Синхронная обёртка — запускает параллельный анализ."""
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self._run_async(url, lang)
                )
                return future.result(timeout=90)
        except concurrent.futures.TimeoutError:
            print("ChiefAgent: timeout after 90 seconds")
            return self._build_result(url, {}, {}, {})
        except Exception as e:
            print(f"ChiefAgent error: {e}")
            return self._build_result(url, {}, {}, {})

    async def _run_async(self, url: str, lang: str = "en") -> Dict[str, Any]:
        # Шаг 1 — получаем данные рынка (синхронно, нужен slug)
        market_data = await asyncio.get_event_loop().run_in_executor(
            None, self._run_market_agent, url
        )

        # Шаг 2 — параллельно запускаем news и decision агентов
        news_task = asyncio.get_event_loop().run_in_executor(
            None, self._run_news_agent, market_data, lang
        )
        decision_placeholder_task = asyncio.get_event_loop().run_in_executor(
            None, self._run_trend_summary, market_data
        )

        news_data, trend_summary = await asyncio.gather(
            news_task, decision_placeholder_task
        )

        # Шаг 3 — decision agent с данными от news и trend
        decision_data = await asyncio.get_event_loop().run_in_executor(
            None, self._run_decision_agent, market_data, news_data, lang
        )

        # Шаг 4 — communication agent
        conclusion = await asyncio.get_event_loop().run_in_executor(
            None, self._run_communication_agent, decision_data
        )

        return self._build_result(url, market_data, news_data, decision_data, conclusion)

    def _build_result(
        self,
        url: str,
        market_data: Dict,
        news_data: Dict,
        decision_data: Dict,
        conclusion: str = "",
    ) -> Dict[str, Any]:
        news_sources = news_data.get("sources", []) if news_data else []

        result = {
            "question": (
                decision_data.get("question") or
                market_data.get("question", "Unknown market")
            ),
            "category": (
                decision_data.get("category") or
                market_data.get("category", "Unknown")
            ),
            "market_probability": (
                decision_data.get("market_probability") or
                market_data.get("market_probability", "Unknown")
            ),
            "probability": decision_data.get("probability", "N/A"),
            "confidence": decision_data.get("confidence", "Unknown"),
            "reasoning": decision_data.get("reasoning", "No reasoning available."),
            "main_scenario": decision_data.get("main_scenario", "No main scenario."),
            "alt_scenario": decision_data.get("alt_scenario", "No alternative scenario."),
            "conclusion": conclusion or decision_data.get("conclusion", ""),
            "url": url,
            "mode": "analysis",
            "related_markets": market_data.get("related_markets", []),
            "news_sources": news_sources,
            "news_items": news_sources,
            "trend_summary": market_data.get("trend_summary", ""),
            "crowd_behavior": market_data.get("crowd_behavior", ""),
            "market_data": market_data,
            "news_data": news_data,
            "decision_data": decision_data,
        }

        try:
            save_analysis(url, result)
        except Exception as e:
            print(f"ChiefAgent save_analysis error: {e}")

        return result

    def _run_trend_summary(self, market_data: Dict) -> str:
        """Быстро возвращает trend summary — уже есть в market_data."""
        return market_data.get("trend_summary", "")

    def _run_market_agent(self, url: str) -> Dict[str, Any]:
        try:
            from agents.market_agent import MarketAgent
            agent = MarketAgent()
            result = agent.run(url)
            if isinstance(result, dict):
                return result
            return self._market_fallback(url)
        except Exception as e:
            print(f"MarketAgent error: {e}")
            return self._market_fallback(url)

    def _run_news_agent(self, market_data: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
        try:
            from agents.news_agent import NewsAgent
            agent = NewsAgent()
            result = agent.run(market_data, lang=lang)
            if isinstance(result, dict):
                return result
            return self._news_fallback(market_data)
        except Exception as e:
            print(f"NewsAgent error: {e}")
            return self._news_fallback(market_data)

    def _run_decision_agent(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str = "en",
    ) -> Dict[str, Any]:
        try:
            from agents.decision_agent import DecisionAgent
            agent = DecisionAgent()
            result = agent.run(market_data, news_data, lang=lang)
            if isinstance(result, dict):
                return result
            return self._decision_fallback(market_data, news_data)
        except Exception as e:
            print(f"DecisionAgent error: {e}")
            return self._decision_fallback(market_data, news_data)

    def _run_communication_agent(self, decision_data: Dict[str, Any]) -> str:
        try:
            from agents.communication_agent import CommunicationAgent
            agent = CommunicationAgent()
            result = agent.run(decision_data)
            if isinstance(result, str):
                return result
            return self._communication_fallback(decision_data)
        except Exception as e:
            print(f"CommunicationAgent error: {e}")
            return self._communication_fallback(decision_data)

    def _market_fallback(self, url: str) -> Dict[str, Any]:
        return {
            "url": url,
            "question": "Fallback market question from Polymarket link",
            "category": "Unknown",
            "market_probability": "Unknown",
            "options": [],
            "related_markets": [],
            "volume": "Unknown",
            "liquidity": "Unknown",
            "trend_summary": "Market Agent fallback mode.",
            "crowd_behavior": "Market Agent fallback mode.",
            "date_context": "Unknown",
            "raw_market_data": {},
            "price_history": {"24h": [], "7d": []},
        }

    def _news_fallback(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "question": market_data.get("question", "Unknown market"),
            "news_query": "",
            "news_summary": "News Agent fallback mode.",
            "sources": [],
            "sentiment": "Unknown",
            "confidence": "Low",
            "raw_news_text": "",
        }

    def _decision_fallback(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "question": market_data.get("question", "Unknown market"),
            "category": market_data.get("category", "Unknown"),
            "market_probability": market_data.get("market_probability", "Unknown"),
            "probability": "System probability not available yet",
            "confidence": "Low",
            "reasoning": "Decision Agent fallback mode.",
            "main_scenario": "Main scenario unavailable.",
            "alt_scenario": "Alternative scenario unavailable.",
            "conclusion": "Analysis unavailable.",
            "raw_decision_text": "",
        }

    def _communication_fallback(self, decision_data: Dict[str, Any]) -> str:
        return decision_data.get("conclusion") or "Communication Agent fallback mode."
