
from typing import Any, Dict, List

from services.llm_service import generate_decision_text


class DecisionAgent:
    def __init__(self) -> None:
        pass

    def run(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str = "en",
    ) -> Dict[str, Any]:

        question = market_data.get("question", "Unknown market")
        category = market_data.get("category", "Unknown")
        market_probability = market_data.get("market_probability", "Unknown")
        options = market_data.get("options", [])
        related_markets = market_data.get("related_markets", [])
        trend_summary = market_data.get("trend_summary", "Unknown")
        crowd_behavior = market_data.get("crowd_behavior", "Unknown")

        news_summary = news_data.get("news_summary", "Unknown")
        sentiment = news_data.get("sentiment", "Unknown")
        news_confidence = news_data.get("confidence", "Unknown")

        prompt = self._build_prompt(
            question=question,
            category=category,
            market_probability=market_probability,
            options=options,
            related_markets=related_markets,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            news_summary=news_summary,
            sentiment=sentiment,
            news_confidence=news_confidence,
            lang=lang,
        )

        raw_response = generate_decision_text(prompt)

        print("========== RAW LLM RESPONSE ==========")
        print(raw_response)
        print("======================================")

        if raw_response and not raw_response.lower().startswith("llm service is not configured"):
            parsed = self._parse_llm_output(raw_response)

            print("========== PARSED LLM RESULT ==========")
            print(parsed)
            print("=======================================")

            wrapped = self._wrap_llm_result(
                question=question,
                category=category,
                market_probability=market_probability,
                parsed=parsed,
                raw_text=raw_response,
            )

            if self._is_valid_result(wrapped):
                return wrapped

        return self._fallback_decision(
            question=question,
            category=category,
            market_probability=market_probability,
            options=options,
            related_markets=related_markets,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            news_summary=news_summary,
            sentiment=sentiment,
            news_confidence=news_confidence,
        )

    def _build_prompt(
        self,
        question: str,
        category: str,
        market_probability: Any,
        options: List[str],
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        news_summary: str,
        sentiment: str,
        news_confidence: str,
        lang: str = "en",
    ) -> str:

        options_text = ", ".join(options) if options else "Unknown"

        related_lines = []
        for item in related_markets[:8]:
            related_lines.append(
                f"- {item.get('title', 'Unknown')} | "
                f"probability: {item.get('probability', 'Unknown')} | "
                f"change_24h: {item.get('change_24h', 'Unknown')} | "
                f"relation: {item.get('relation_type', 'Unknown')} | "
                f"trend: {item.get('trend_summary', 'Unknown')}"
            )
        related_text = "\n".join(related_lines) if related_lines else "- No related markets"

        lang_instruction = "Respond in Russian. Use Russian language for all text in your response." if lang == "ru" else "Respond in English."

        return f"""
You are DeepAlpha Decision Engine.

{lang_instruction}

Your job:
- infer an independent market view
- do NOT simply repeat market odds
- combine market structure, related markets, trend behavior, crowd behavior and news context
- provide a clear main scenario and an alternative scenario
- be concise but concrete

Question:
{question}

Category:
{category}

Market probability:
{market_probability}

Options:
{options_text}

Trend summary:
{trend_summary}

Crowd behavior:
{crowd_behavior}

News summary:
{news_summary}

News sentiment:
{sentiment}

News confidence:
{news_confidence}

Related markets:
{related_text}

IMPORTANT:
- Return ALL fields
- Do not leave fields blank
- If evidence is weak, say so
- If probability is uncertain, still provide a best estimate
- Probability must be in a human-readable form, for example:
  Yes — 62%
  No — 71%
  Most likely outcome — 58%

Return EXACTLY in this format:

System Probability: ...
Confidence: ...
Reasoning: ...
Main Scenario: ...
Alternative Scenario: ...
Conclusion: ...
""".strip()

    def _parse_llm_output(self, text: str) -> Dict[str, str]:
        fields = {
            "System Probability": "",
            "Confidence": "",
            "Reasoning": "",
            "Main Scenario": "",
            "Alternative Scenario": "",
            "Conclusion": "",
        }

        current_key = None
        lines = text.splitlines()

        for line in lines:
            stripped = line.strip()
            matched = False

            for key in fields.keys():
                prefix = f"{key}:"
                if stripped.startswith(prefix):
                    value = stripped[len(prefix):].strip()
                    fields[key] = value
                    current_key = key
                    matched = True
                    break

            if not matched and current_key and stripped:
                if fields[current_key]:
                    fields[current_key] += " " + stripped
                else:
                    fields[current_key] = stripped

        return fields

    def _wrap_llm_result(
        self,
        question: str,
        category: str,
        market_probability: Any,
        parsed: Dict[str, str],
        raw_text: str,
    ) -> Dict[str, Any]:
        probability = parsed.get("System Probability", "").strip() or "N/A"
        confidence = parsed.get("Confidence", "").strip() or "Medium"
        reasoning = parsed.get("Reasoning", "").strip() or "Model failed to generate reasoning."
        main_scenario = parsed.get("Main Scenario", "").strip() or "No main scenario generated."
        alt_scenario = parsed.get("Alternative Scenario", "").strip() or "No alternative scenario generated."
        conclusion = parsed.get("Conclusion", "").strip() or "No conclusion generated."

        return {
            "question": question,
            "category": category,
            "market_probability": market_probability,
            "probability": probability,
            "confidence": confidence,
            "reasoning": reasoning,
            "main_scenario": main_scenario,
            "alt_scenario": alt_scenario,
            "conclusion": conclusion,
            "raw_decision_text": raw_text,
        }

    def _is_valid_result(self, result: Dict[str, Any]) -> bool:
        required_fields = [
            "probability",
            "confidence",
            "reasoning",
            "main_scenario",
            "alt_scenario",
            "conclusion",
        ]

        for field in required_fields:
            value = str(result.get(field, "")).strip()
            if not value:
                return False
            if value in {"N/A", "Unknown"} and field in {"reasoning", "main_scenario", "alt_scenario", "conclusion"}:
                return False

        return True

    def _fallback_decision(
        self,
        question: str,
        category: str,
        market_probability: Any,
        options: List[str],
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        news_summary: str,
        sentiment: str,
        news_confidence: str,
    ) -> Dict[str, Any]:

        probability_value = self._estimate_probability_value(
            related_markets=related_markets,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            sentiment=sentiment,
            news_confidence=news_confidence,
            category=category,
        )

        main_option = self._choose_main_option(options)
        confidence = self._estimate_confidence(
            related_markets=related_markets,
            trend_summary=trend_summary,
            news_confidence=news_confidence,
        )

        reasoning_parts = [
            f"Fallback reasoning for category {category}.",
            f"Observed market probability: {market_probability}.",
            f"Trend layer: {trend_summary}.",
            f"Crowd layer: {crowd_behavior}.",
            f"External sentiment: {sentiment}.",
            f"News context: {news_summary}.",
        ]

        if related_markets:
            reasoning_parts.append(
                f"Detected {len(related_markets)} related markets that may influence the main outcome."
            )

        reasoning = " ".join(reasoning_parts)

        main_scenario = (
            f"The current base case favors '{main_option}' because trend, crowd behavior "
            f"and the available external context lean in that direction."
        )

        alt_scenario = (
            "The alternative case remains viable if the current trend weakens, "
            "if crowd positioning reverses, or if news flow changes materially."
        )

        conclusion = (
            f"System fallback estimate favors '{main_option}' at around {probability_value} "
            f"with {confidence.lower()} confidence."
        )

        return {
            "question": question,
            "category": category,
            "market_probability": market_probability,
            "probability": f"{main_option} — {probability_value}",
            "confidence": confidence,
            "reasoning": reasoning,
            "main_scenario": main_scenario,
            "alt_scenario": alt_scenario,
            "conclusion": conclusion,
            "raw_decision_text": "",
        }

    def _estimate_probability_value(
        self,
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        sentiment: str,
        news_confidence: str,
        category: str,
    ) -> str:
        score = 50

        if category in {"Politics", "Crypto", "Economy"}:
            score += 5

        if related_markets:
            score += min(len(related_markets) * 4, 16)

        t = str(trend_summary).lower()
        c = str(crowd_behavior).lower()
        s = str(sentiment).lower()
        nc = str(news_confidence).lower()

        if "accelerated" in t:
            score += 6
        if "24h move" in t:
            score += 4
        if "strengthened sharply" in c:
            score += 7
        elif "moderately" in c:
            score += 4
        elif "reversed sharply" in c:
            score -= 7
        elif "softened" in c:
            score -= 4

        if "positive" in s:
            score += 6
        elif "negative" in s:
            score -= 6

        if "high" in nc:
            score += 5
        elif "medium" in nc:
            score += 2

        score = max(5, min(95, score))
        return f"{score}%"

    def _estimate_confidence(
        self,
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        news_confidence: str,
    ) -> str:
        score = 0

        if related_markets:
            score += 1
        if trend_summary and "no price history" not in str(trend_summary).lower():
            score += 1
        if str(news_confidence).lower() == "high":
            score += 2
        elif str(news_confidence).lower() == "medium":
            score += 1

        if score >= 4:
            return "High"
        if score >= 2:
            return "Medium"
        return "Low"

    def _choose_main_option(self, options: List[str]) -> str:
        if not options:
            return "Most likely outcome"

        cleaned = [str(x).strip() for x in options if str(x).strip()]

        if "Yes" in cleaned:
            return "Yes"
        if "No" in cleaned:
            return "No"

        return cleaned[0] if cleaned else "Most likely outcome"
