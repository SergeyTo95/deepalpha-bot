
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

        # Дополнительный поиск Twitter/X упоминаний
        twitter_query = f"{news_query} site:twitter.com OR site:x.com"
        twitter_items = search_google_news(twitter_query, limit=3)

        all_items = news_items + [i for i in twitter_items if i not in news_items]
        live_news_summary = summarize_news_items(all_items[:7])

        prompt = self._build_prompt(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            news_items=all_items[:5],
            lang=lang,
        )

        llm_result = generate_news_text(prompt)

        if llm_result and not llm_result.lower().startswith("llm service is not configured"):
            return self._wrap_llm_result(
                question=question,
                category=category,
                llm_result=llm_result,
                news_query=news_query,
                news_items=all_items[:5],
            )

        return self._fallback_news(
            question=question,
            category=category,
            date_context=date_context,
            related_markets=related_markets,
            live_news_summary=live_news_summary,
            news_query=news_query,
            news_items=all_items[:5],
        )

    def _build_prompt(
        self,
        question: str,
        category: str,
        date_context: str,
        related_markets: List[Dict[str, Any]],
        live_news_summary: str,
        news_items: List[Dict[str, str]] = None,
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
            "Respond ONLY in Russian. Every single word must be in Russian language. "
            "Translate all terms, sources, and analysis into Russian."
            if lang == "ru"
            else "Respond in English."
        )

        has_news = live_news_summary and "No relevant" not in live_news_summary

        # Форматируем топ новости со ссылками
        top_news_block = ""
        if news_items:
            lines = []
            for i, item in enumerate(news_items[:5], 1):
                title = item.get("title", "")
                source = item.get("source", "")
                published = item.get("published", "")
                link = item.get("link", "")
                if title:
                    line = f"{i}. {title} ({source}, {published})"
                    if link:
                        line += f" — {link}"
                    lines.append(line)
            top_news_block = "\n".join(lines)

        return f"""
You are DeepAlpha News Intelligence — an expert analyst for prediction markets.

{lang_instruction}

TASK: Analyze real-world news context for this prediction market and identify signals that affect probability. Base your analysis STRICTLY on the provided news — do not invent facts.

MARKET QUESTION: {question}
CATEGORY: {category}
DEADLINE: {date_context}

RELATED MARKETS:
{related_block}

TOP NEWS SOURCES:
{top_news_block if top_news_block else "No news sources found."}

FULL NEWS FEED:
{live_news_summary if has_news else "No recent news found for this topic."}

ANALYSIS RULES:
1. Base your analysis ONLY on the provided news above — do not hallucinate
2. If news feed is empty — explicitly state "No recent news found" and use only market data
3. Clearly separate SUPPORTING signals from OPPOSING signals
4. Rate signal strength: Strong / Moderate / Weak
5. Be specific — mention dates, names, numbers from the news
6. Focus on what CHANGES the probability
7. Include Twitter/X sentiment if social media sources are present

REQUIRED OUTPUT FORMAT:

News Summary:
[2-3 sentence overview based strictly on provided news]

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

Social Sentiment:
[Twitter/X and social media sentiment if available, otherwise "No social data"]

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
            summary_parts.append(f"Found {len(news_items)} relevant recent news items.")
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
