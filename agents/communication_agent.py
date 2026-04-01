from typing import Any, Dict, List


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> str:
        question = decision_data.get("question", "Unknown question")
        category = decision_data.get("category", "Unknown")
        market_probability = decision_data.get("market_probability", "Unknown")
        system_probability = decision_data.get("probability", "Unknown")
        confidence = decision_data.get("confidence", "Unknown")
        reasoning = decision_data.get("reasoning", "No reasoning available.")
        main_scenario = decision_data.get("main_scenario", "No main scenario available.")
        alt_scenario = decision_data.get("alt_scenario", "No alternative scenario available.")
        conclusion = decision_data.get("conclusion", "No conclusion available.")
        raw_decision_text = str(decision_data.get("raw_decision_text", "")).strip()

        related_markets = decision_data.get("related_markets", [])
        news_sources = decision_data.get("news_sources", [])
        trend_summary = decision_data.get("trend_summary", "")
        crowd_behavior = decision_data.get("crowd_behavior", "")
        mode = decision_data.get("mode", "analysis")
        opportunity_score = decision_data.get("opportunity_score", None)
        url = decision_data.get("url", "")

        if raw_decision_text:
            return self._format_llm_response(
                question=question,
                category=category,
                market_probability=market_probability,
                confidence=confidence,
                raw_decision_text=raw_decision_text,
                related_markets=related_markets,
                news_sources=news_sources,
                trend_summary=trend_summary,
                crowd_behavior=crowd_behavior,
                mode=mode,
                opportunity_score=opportunity_score,
                url=url,
            )

        return self._format_structured_response(
            question=question,
            category=category,
            market_probability=market_probability,
            system_probability=system_probability,
            confidence=confidence,
            reasoning=reasoning,
            main_scenario=main_scenario,
            alt_scenario=alt_scenario,
            conclusion=conclusion,
            related_markets=related_markets,
            news_sources=news_sources,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            mode=mode,
            opportunity_score=opportunity_score,
            url=url,
        )

    def _format_structured_response(
        self,
        question: str,
        category: str,
        market_probability: Any,
        system_probability: str,
        confidence: str,
        reasoning: str,
        main_scenario: str,
        alt_scenario: str,
        conclusion: str,
        related_markets: List[Dict[str, Any]],
        news_sources: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        mode: str,
        opportunity_score: Any,
        url: str,
    ) -> str:
        title = "## DeepAlpha Opportunity" if mode == "opportunity" else "## DeepAlpha Analysis"

        parts = [
            title,
            "",
            f"**Question:** {question}  ",
            f"**Category:** {category}  ",
            f"**Market Probability:** {market_probability}  ",
            "",
            "### System Probability",
            f"**{system_probability}**",
            "",
            "### Confidence",
            f"**{confidence}**",
        ]

        if opportunity_score is not None and mode == "opportunity":
            parts.extend([
                "",
                "### Opportunity Score",
                f"**{opportunity_score}**",
            ])

        if url:
            parts.extend([
                "",
                "### Market URL",
                str(url),
            ])

        if trend_summary:
            parts.extend([
                "",
                "### Trend Summary",
                str(trend_summary),
            ])

        if crowd_behavior:
            parts.extend([
                "",
                "### Crowd Behavior",
                str(crowd_behavior),
            ])

        parts.extend([
            "",
            "### Reasoning",
            reasoning,
            "",
            "### Main Scenario",
            main_scenario,
            "",
            "### Alternative Scenario",
            alt_scenario,
        ])

        related_block = self._format_related_markets(related_markets)
        if related_block:
            parts.extend([
                "",
                "### Related Markets Considered",
                related_block,
            ])

        news_block = self._format_news_sources(news_sources)
        if news_block:
            parts.extend([
                "",
                "### News Signals Considered",
                news_block,
            ])

        parts.extend([
            "",
            "### Conclusion",
            conclusion,
        ])

        return "\n".join(parts).strip()

    def _format_llm_response(
        self,
        question: str,
        category: str,
        market_probability: Any,
        confidence: str,
        raw_decision_text: str,
        related_markets: List[Dict[str, Any]],
        news_sources: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        mode: str,
        opportunity_score: Any,
        url: str,
    ) -> str:
        title = "## DeepAlpha Opportunity" if mode == "opportunity" else "## DeepAlpha Analysis"
        cleaned = self._cleanup_text(raw_decision_text)

        parts = [
            title,
            "",
            f"**Question:** {question}  ",
            f"**Category:** {category}  ",
            f"**Market Probability:** {market_probability}  ",
            "",
            "### Confidence",
            f"**{confidence}**",
        ]

        if opportunity_score is not None and mode == "opportunity":
            parts.extend([
                "",
                "### Opportunity Score",
                f"**{opportunity_score}**",
            ])

        if url:
            parts.extend([
                "",
                "### Market URL",
                str(url),
            ])

        if trend_summary:
            parts.extend([
                "",
                "### Trend Summary",
                str(trend_summary),
            ])

        if crowd_behavior:
            parts.extend([
                "",
                "### Crowd Behavior",
                str(crowd_behavior),
            ])

        parts.extend([
            "",
            "### Forecast Output",
            cleaned,
        ])

        related_block = self._format_related_markets(related_markets)
        if related_block:
            parts.extend([
                "",
                "### Related Markets Considered",
                related_block,
            ])

        news_block = self._format_news_sources(news_sources)
        if news_block:
            parts.extend([
                "",
                "### News Signals Considered",
                news_block,
            ])

        return "\n".join(parts).strip()

    def _format_related_markets(self, related_markets: List[Dict[str, Any]]) -> str:
        if not related_markets:
            return ""

        lines = []
        for item in related_markets[:5]:
            title = item.get("title", "Unknown related market")
            probability = item.get("probability", "Unknown")
            change_24h = item.get("change_24h", "Unknown")
            relation_type = item.get("relation_type", "Unknown")

            line = (
                f"- **{title}** | "
                f"Probability: {probability} | "
                f"24h: {change_24h} | "
                f"Relation: {relation_type}"
            )
            lines.append(line)

        return "\n".join(lines)

    def _format_news_sources(self, news_sources: List[Dict[str, Any]]) -> str:
        if not news_sources:
            return ""

        lines = []
        for item in news_sources[:5]:
            title = item.get("title", "Unknown title")
            source = item.get("source", "Unknown source")
            published = item.get("published", "Unknown time")

            lines.append(
                f"- **{title}** ({source}, {published})"
            )

        return "\n".join(lines)

    def _cleanup_text(self, text: str) -> str:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()

        replacements = {
            "System Probability:": "### System Probability\n",
            "Confidence:": "\n### Confidence\n",
            "Reasoning:": "\n### Reasoning\n",
            "Main Scenario:": "\n### Main Scenario\n",
            "Alternative Scenario:": "\n### Alternative Scenario\n",
            "Conclusion:": "\n### Conclusion\n",
        }

        for old, new in replacements.items():
            cleaned = cleaned.replace(old, new)

        return cleaned
