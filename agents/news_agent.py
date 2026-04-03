
from typing import Any, Dict, List

from services.llm_service import generate_news_text
from services.news_service import (
    build_news_query,
    search_google_news,
    summarize_news_items,
)


class NewsAgent:
    def __init__(self) -> None:
        pass

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
        live_news_summary = summarize_news_items(news_items)

        prompt = self._build_prompt(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            lang=lang,
        )

        llm_result = generate_news_text(prompt)

        if llm_result and not llm_result.lower().startswith("llm service is not configured"):
            return self._wrap_llm_result(
                question=question,
                category=category,
                llm_result=llm_result,
                news_query=news_query,
                news_items=news_items,
            )

        return self._fallback_news(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            news_query=news_query,
            news_items=news_items,
        )

    def _build_prompt(
        self,
        question: str,
        category: str,
        date_context: str,
        related_markets: List[Dict[str, Any]],
        live_news_summary: str,
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
            "Respond ONLY in Russian. Every single word must be in Russian language."
            if lang == "ru"
            else "Respond in English."
        )

        has_news = live_news_summary and "No relevant" not in live_news_summary

        return f"""
You are DeepAlpha News Intelligence — an expert analyst for prediction markets.

{lang_instruction}

TASK: Analyze real-world news context for this prediction market event and identify signals that affect probability.

MARKET QUESTION: {question}
CATEGORY: {category}
DEADLINE: {date_context}

RELATED MARKETS:
{related_block}

LIVE NEWS FEED ({len(live_news_summary.split(chr(10)))} items found):
{live_news_summary if has_news else "No recent news found for this topic."}

ANALYSIS RULES:
1. If news feed is empty — use your knowledge of this topic up to your training cutoff
2. Clearly separate SUPPORTING signals (increase probability) from OPPOSING signals (decrease probability)
3. Rate signal strength: Strong / Moderate / Weak
4. Be specific — mention dates, names, numbers when available
5. Do NOT hallucinate news sources
6. Focus on what CHANGES the probability, not just what confirms current odds

REQUIRED OUTPUT FORMAT:

News Summary:
[2-3 sentence overview of the current situation]

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
            summary_parts.append(
                f"Found {len(news_items)} relevant recent news items."
            )
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
