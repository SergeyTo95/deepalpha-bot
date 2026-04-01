from typing import Any, Dict

from db.database import save_analysis


class ChiefAgent:
    def __init__(self) -> None:
        pass

    def run(self, url: str) -> Dict[str, Any]:
        market_data = self._run_market_agent(url)
        news_data = self._run_news_agent(market_data)
        decision_data = self._run_decision_agent(market_data, news_data)

        enriched_decision_data = self._enrich_decision_data(
            url=url,
            market_data=market_data,
            news_data=news_data,
            decision_data=decision_data,
        )

        final_response = self._run_communication_agent(enriched_decision_data)

        final_result = {
            "question": enriched_decision_data.get(
                "question",
                market_data.get("question", "Unknown market")
            ),
            "category": enriched_decision_data.get(
                "category",
                market_data.get("category", "Unknown")
            ),
            "market_probability": enriched_decision_data.get(
                "market_probability",
                market_data.get("market_probability", "Unknown")
            ),
            "probability": enriched_decision_data.get("probability", "N/A"),
            "confidence": enriched_decision_data.get("confidence", "Unknown"),
            "reasoning": enriched_decision_data.get("reasoning", "No reasoning available."),
            "main_scenario": enriched_decision_data.get("main_scenario", "No main scenario."),
            "alt_scenario": enriched_decision_data.get("alt_scenario", "No alternative scenario."),
            "conclusion": final_response,
            "url": url,
            "mode": "analysis",
            "related_markets": enriched_decision_data.get("related_markets", []),
            "news_sources": enriched_decision_data.get("news_sources", []),
            "trend_summary": enriched_decision_data.get("trend_summary", ""),
            "crowd_behavior": enriched_decision_data.get("crowd_behavior", ""),
            "market_data": market_data,
            "news_data": news_data,
            "decision_data": enriched_decision_data,
        }

        save_analysis(url, final_result)
        return final_result

    def _enrich_decision_data(
        self,
        url: str,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        decision_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        enriched = dict(decision_data)

        enriched["url"] = url
        enriched["mode"] = "analysis"
        enriched["related_markets"] = market_data.get("related_markets", [])
        enriched["trend_summary"] = market_data.get("trend_summary", "")
        enriched["crowd_behavior"] = market_data.get("crowd_behavior", "")
        enriched["news_sources"] = news_data.get("sources", [])
        enriched["news_query"] = news_data.get("news_query", "")

        if "question" not in enriched or not enriched.get("question"):
            enriched["question"] = market_data.get("question", "Unknown market")

        if "category" not in enriched or not enriched.get("category"):
            enriched["category"] = market_data.get("category", "Unknown")

        if "market_probability" not in enriched or not enriched.get("market_probability"):
            enriched["market_probability"] = market_data.get("market_probability", "Unknown")

        return enriched

    def _run_market_agent(self, url: str) -> Dict[str, Any]:
        try:
            from agents.market_agent import MarketAgent  # type: ignore
            agent = MarketAgent()
            result = agent.run(url)

            if isinstance(result, dict):
                return result

            return self._market_fallback(url)
        except Exception:
            return self._market_fallback(url)

    def _run_news_agent(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from agents.news_agent import NewsAgent  # type: ignore
            agent = NewsAgent()
            result = agent.run(market_data)

            if isinstance(result, dict):
                return result

            return self._news_fallback(market_data)
        except Exception:
            return self._news_fallback(market_data)

    def _run_decision_agent(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            from agents.decision_agent import DecisionAgent  # type: ignore
            agent = DecisionAgent()
            result = agent.run(market_data, news_data)

            if isinstance(result, dict):
                return result

            return self._decision_fallback(market_data, news_data)
        except Exception:
            return self._decision_fallback(market_data, news_data)

    def _run_communication_agent(self, decision_data: Dict[str, Any]) -> str:
        try:
            from agents.communication_agent import CommunicationAgent  # type: ignore
            agent = CommunicationAgent()
            result = agent.run(decision_data)

            if isinstance(result, str):
                return result

            return self._communication_fallback(decision_data)
        except Exception:
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
            "trend_summary": "Market Agent fallback mode. Real market parsing not connected yet.",
            "crowd_behavior": "Market Agent fallback mode. Crowd behavior not connected yet.",
            "date_context": "Unknown",
            "raw_market_data": {},
            "price_history": {"24h": [], "7d": []},
        }

    def _news_fallback(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        question = market_data.get("question", "Unknown market")
        return {
            "question": question,
            "news_query": "",
            "news_summary": (
                "News Agent fallback mode. External news, Twitter/X signals, "
                "and official statements are not connected yet."
            ),
            "sources": [],
            "sentiment": "Unknown",
            "confidence": "Low",
            "raw_news_text": "",
        }

    def _decision_fallback(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        question = market_data.get("question", "Unknown market")
        category = market_data.get("category", "Unknown")

        return {
            "question": question,
            "category": category,
            "market_probability": market_data.get("market_probability", "Unknown"),
            "probability": "System probability not available yet",
            "confidence": "Low",
            "reasoning": (
                "Decision Agent fallback mode. The system received market and news layers, "
                "but the final reasoning engine is not connected yet."
            ),
            "main_scenario": (
                "Main scenario cannot be calculated yet because Decision Agent "
                "still uses fallback mode."
            ),
            "alt_scenario": (
                "Alternative scenario cannot be calculated yet because Decision Agent "
                "still uses fallback mode."
            ),
            "conclusion": (
                "Chief Agent orchestration works, but final decision logic still needs "
                "to be connected."
            ),
            "raw_decision_text": "",
        }

    def _communication_fallback(self, decision_data: Dict[str, Any]) -> str:
        return decision_data.get("conclusion") or "Communication Agent fallback mode."
