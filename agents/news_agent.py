```python
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

        news_items = search_google_news(news_query, limit=5)
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

        lang_instruction = "Respond in Russian. Use Russian language for all text in your response." if lang == "ru" else "Respond in English."

        return f"""
You are a News Intelligence Agent inside a prediction market AI system.

{lang_instruction}

Your goal:
- analyze real-world context behind an event
- identify signals that affect probability
- distinguish strong vs weak signals
- use the live news feed below as your primary external context layer

Event:
{question}

Category:
{category}

Time context:
{date_context}

Related markets:
{related_block}

Live News Feed:
{live_news_summary}

Instructions:
- do not hallucinate fake news sources
- use the live news feed above
- separate supporting vs opposing signals
- if evidence is weak, say it clearly
- focus on what changes probability

Return format:

News Summary:
...

Key Signals:
- ...
- ...
- ...

Supporting Factors:
- ...
- ...

Opposing Factors:
- ...
- ...

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
            "News Agent fallback mode.",
            f"Question: {question}.",
            f"Category: {category}.",
            f"Search query: {news_query}.",
        ]

        if date_context and date_context != "Unknown":
            summary_parts.append(f"Detected time context: {date_context}.")

        if related_markets:
            summary_parts.append(
                f"There are {len(related_markets)} related market signals to consider."
            )

        if news_items:
            summary_parts.append(
                f"Live news feed found {len(news_items)} relevant recent items."
            )
            summary_parts.append(f"News digest: {live_news_summary}")
        else:
            summary_parts.append("No live news items were found.")

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
        if "sentiment: positive" in t:
            return "Positive"
        if "sentiment: negative" in t:
            return "Negative"
        if "sentiment: mixed" in t:
            return "Mixed"
        if "sentiment: unclear" in t:
            return "Unclear"

        if "positive" in t:
            return "Positive"
        if "negative" in t:
            return "Negative"
        if "mixed" in t:
            return "Mixed"
        return "Unclear"

    def _extract_confidence(self, text: str) -> str:
        t = text.lower()
        if "confidence: high" in t:
            return "High"
        if "confidence: medium" in t:
            return "Medium"
        if "confidence: low" in t:
            return "Low"

        if "high" in t:
            return "High"
        if "medium" in t:
            return "Medium"
        return "Low"
```

Следующий — пришли `decision_agent.py`.
