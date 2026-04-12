
from typing import Any, Dict

from db.database import save_analysis


class ChiefAgent:
    def __init__(self) -> None:
        pass

    def run(self, url: str, lang: str = "en") -> Dict[str, Any]:
        print(f"ChiefAgent: starting analysis for {url[:50]}")
        market_data = self._run_market_agent(url)
        print(f"ChiefAgent: market_data done, question={market_data.get('question', '')[:50]}")
        news_data = self._run_news_agent(market_data, lang=lang)
        print(f"ChiefAgent: news_data done, sentiment={news_data.get('sentiment', 'N/A')}")
        decision_data = self._run_decision_agent(market_data, news_data, lang=lang)
        print(f"ChiefAgent: decision_data done, probability={decision_data.get('probability', 'N/A')}")
        conclusion = self._run_communication_agent(decision_data)

        news_sources = news_data.get("sources", [])

        final_result = {
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
            "conclusion": conclusion,
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
            save_analysis(url, final_result)
        except Exception as e:
            print(f"ChiefAgent save_analysis error: {e}")

        return final_result

    def _run_market_agent(self, url: str) -> Dict[str, Any]:
        try:
            print(f"MarketAgent: starting")
            from agents.market_agent import MarketAgent
            agent = MarketAgent()
            result = agent.run(url)
            print(f"MarketAgent: done")
            if isinstance(result, dict):
                return result
            return self._market_fallback(url)
        except Exception as e:
            print(f"MarketAgent ERROR: {e}")
            return self._market_fallback(url)

    def _run_news_agent(self, market_data: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
        try:
            print(f"NewsAgent: starting")
            from agents.news_agent import NewsAgent
            agent = NewsAgent()
            result = agent.run(market_data, lang=lang)
            print(f"NewsAgent: done, sources={len(result.get('sources', []))}")
            if isinstance(result, dict):
                return result
            return self._news_fallback(market_data)
        except Exception as e:
            print(f"NewsAgent ERROR: {e}")
            return self._news_fallback(market_data)

    def _run_decision_agent(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str = "en",
    ) -> Dict[str, Any]:
        try:
            print(f"DecisionAgent: starting")
            from agents.decision_agent import DecisionAgent
            agent = DecisionAgent()
            result = agent.run(market_data, news_data, lang=lang)
            print(f"DecisionAgent: done, probability={result.get('probability', 'N/A')}")
            if isinstance(result, dict):
                return result
            return self._decision_fallback(market_data, news_data)
        except Exception as e:
            print(f"DecisionAgent ERROR: {e}")
            return self._decision_fallback(market_data, news_data)

    def _run_communication_agent(self, decision_data: Dict[str, Any]) -> str:
        try:
            print(f"CommunicationAgent: starting")
            from agents.communication_agent import CommunicationAgent
            agent = CommunicationAgent()
            result = agent.run(decision_data)
            print(f"CommunicationAgent: done")
            if isinstance(result, str):
                return result
            return self._communication_fallback(decision_data)
        except Exception as e:
            print(f"CommunicationAgent ERROR: {e}")
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
            "probability": "Система не смогла рассчитать вероятность",
            "confidence": "Low",
            "reasoning": "Агент анализа работает в резервном режиме.",
            "main_scenario": "Основной сценарий недоступен.",
            "alt_scenario": "Альтернативный сценарий недоступен.",
            "conclusion": "Анализ недоступен.",
            "raw_decision_text": "",
        }

    def _communication_fallback(self, decision_data: Dict[str, Any]) -> str:
        return decision_data.get("conclusion") or "Анализ завершён."
