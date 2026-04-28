import json
import re
from typing import Any, Dict, Optional


ALLOWED_DECISIONS = {"TRADE", "CONDITIONAL TRADE", "WAIT", "NO TRADE"}

REQUIRED_FIELDS = {
    "decision", "market_logic", "entry_logic", "risk", "conclusion", "confidence"
}


class CryptoLLMAgent:
    def __init__(self) -> None:
        pass

    def refine_decision(
        self,
        market_data: Dict[str, Any],
        ta_data: Dict[str, Any],
        news_data: Dict[str, Any],
        decision_data: Dict[str, Any],
        lang: str = "ru",
    ) -> Dict[str, Any]:
        """
        Refinement layer: отправляет собранные данные в Gemini,
        получает финальный decision. Если Gemini недоступен — fallback.
        """
        try:
            context = self._build_context(
                market_data, ta_data, news_data, decision_data
            )
            prompt = self._build_prompt(context, lang)

            raw_response = self._call_llm(prompt)
            if not raw_response:
                return self._fallback(decision_data, reason="empty_response")

            parsed = self._parse_response(raw_response)
            if not parsed:
                return self._fallback(decision_data, reason="parse_error")

            validated = self._validate(parsed, decision_data)
            if not validated:
                return self._fallback(decision_data, reason="validation_error")

            return self._merge(decision_data, validated)

        except Exception as e:
            print(f"CryptoLLMAgent error: {e}")
            return self._fallback(decision_data, reason=str(e))

    # ═══════════════════════════════════════════
    # CONTEXT BUILDER
    # ═══════════════════════════════════════════

    def _build_context(
        self,
        market_data: Dict,
        ta_data: Dict,
        news_data: Dict,
        decision_data: Dict,
    ) -> Dict:
        macd = ta_data.get("macd") or {}
        ma = ta_data.get("ma") or {}
        sr = ta_data.get("support_resistance") or {}
        fib = ta_data.get("fibonacci") or {}
        fib_levels = fib.get("levels") or {}

        news_items_raw = news_data.get("news_items") or []
        news_titles = [
            (item.get("title") or "")[:100]
            for item in news_items_raw[:5]
            if item.get("title")
        ]

        key_events = news_data.get("key_events") or []

        return {
            "symbol": market_data.get("symbol", ""),
            "base": market_data.get("base", ""),
            "quote": market_data.get("quote", ""),
            "timeframe": market_data.get("timeframe", "4h"),
            "price": market_data.get("price", 0),
            "change_24h": market_data.get("change_24h", 0),
            "trend": market_data.get("trend", "Unknown"),
            "volatility": market_data.get("volatility"),
            "volume_24h": market_data.get("volume_24h", 0),
            "source": market_data.get("source", ""),
            "rsi": ta_data.get("rsi"),
            "macd": {
                "trend": macd.get("trend"),
                "crossover": macd.get("crossover"),
                "histogram": macd.get("histogram"),
            },
            "ma": {
                "trend": ma.get("trend"),
                "ma20": ma.get("ma20"),
                "ma50": ma.get("ma50"),
                "ma200": ma.get("ma200"),
                "ma20_signal": ma.get("ma20_signal"),
                "ma50_signal": ma.get("ma50_signal"),
                "ma_summary_ru": ma.get("ma_summary_ru"),
                "ma_summary_en": ma.get("ma_summary_en"),
            },
            "support_resistance": {
                "nearest_support": sr.get("nearest_support"),
                "nearest_resistance": sr.get("nearest_resistance"),
            },
            "fibonacci": {
                "levels": {
                    "0.382": fib_levels.get("0.382"),
                    "0.5": fib_levels.get("0.5"),
                    "0.618": fib_levels.get("0.618"),
                }
            },
            "elliott_hypothesis": ta_data.get("elliott_hypothesis", ""),
            "volume_signal": ta_data.get("volume_signal", "unknown"),
            "news_sentiment": news_data.get("sentiment", "neutral"),
            "news_quality": news_data.get("news_quality", "none"),
            "key_events": key_events[:3],
            "news_items": news_titles,
            "rule_based_decision": decision_data.get("decision", "WAIT"),
            "rule_based_market_logic": decision_data.get("market_logic", ""),
            "rule_based_entry_logic": decision_data.get("entry_logic", ""),
            "rule_based_risk": decision_data.get("risk", ""),
            "rule_based_conclusion": decision_data.get("conclusion", ""),
        }

    # ═══════════════════════════════════════════
    # PROMPT BUILDER
    # ═══════════════════════════════════════════

    def _build_prompt(self, context: Dict, lang: str) -> str:
        context_json = json.dumps(context, ensure_ascii=False, indent=2)

        lang_instruction = (
            "Respond ONLY in Russian language. Every field must be in Russian."
            if lang == "ru"
            else "Respond in English."
        )

        return (
            "You are DeepAlpha Crypto Analyst — a final decision refinement layer.\n"
            "You do NOT fetch market data.\n"
            "You do NOT invent prices, news, levels, indicators, or sources.\n"
            "Analyze only the provided JSON context.\n"
            "Return JSON only — no markdown, no explanation outside JSON.\n\n"
            f"{lang_instruction}\n\n"
            "Decision definitions:\n"
            "- TRADE = active confirmed setup. TA, volume, and price structure all aligned.\n"
            "- CONDITIONAL TRADE = setup exists but entry needs confirmation "
            "(breakout, pullback, volume, reclaiming key level).\n"
            "- WAIT = conflicting signals, unclear direction, price at dangerous level.\n"
            "- NO TRADE = weak data, poor liquidity, no actionable setup.\n\n"
            "Hard rules:\n"
            "- If MACD bearish AND price below MA20/MA50 AND news neutral/limited "
            "→ do NOT return TRADE for long.\n"
            "- If MACD bullish AND price above MA20/MA50 AND news neutral/limited "
            "→ do NOT return TRADE for short.\n"
            "- If signals conflict → prefer WAIT.\n"
            "- If setup exists but needs trigger → return CONDITIONAL TRADE.\n"
            "- If rule_based_decision is TRADE but setup unconfirmed "
            "→ downgrade to WAIT or CONDITIONAL TRADE.\n"
            "- If news_quality is none AND TA mixed → prefer WAIT.\n"
            "- Do not promise profit. Do not give financial advice.\n"
            "- Be concise and practical.\n\n"
            "Return strict JSON only:\n"
            "{\n"
            '  "decision": "TRADE" | "CONDITIONAL TRADE" | "WAIT" | "NO TRADE",\n'
            '  "market_logic": "...",\n'
            '  "entry_logic": "...",\n'
            '  "risk": "...",\n'
            '  "conclusion": "...",\n'
            '  "confidence": "high" | "medium" | "low"\n'
            "}\n\n"
            "Context:\n"
            f"{context_json}"
        )

    # ═══════════════════════════════════════════
    # LLM CALL
    # ═══════════════════════════════════════════

    def _call_llm(self, prompt: str) -> Optional[str]:
        try:
            from services.llm_service import generate_decision_text
            result = generate_decision_text(prompt)
            return result if result else None
        except Exception as e:
            print(f"CryptoLLMAgent._call_llm error: {e}")
            return None

    # ═══════════════════════════════════════════
    # PARSE RESPONSE
    # ═══════════════════════════════════════════

    def _parse_response(self, raw: str) -> Optional[Dict]:
        if not raw:
            return None

        text = raw.strip()

        # Убираем markdown ```json ... ```
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

        # Пробуем прямой парсинг
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Извлекаем JSON между первой { и последней }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        print(f"CryptoLLMAgent: failed to parse JSON from response: {text[:200]}")
        return None

    # ═══════════════════════════════════════════
    # VALIDATE
    # ═══════════════════════════════════════════

    def _validate(
        self, parsed: Dict, fallback_data: Dict
    ) -> Optional[Dict]:
        result = {}

        # decision
        decision = str(parsed.get("decision", "")).strip().upper()
        if decision not in ALLOWED_DECISIONS:
            print(f"CryptoLLMAgent: unknown decision '{decision}', using fallback")
            decision = fallback_data.get("decision", "WAIT")
        result["decision"] = decision

        # confidence
        confidence = str(parsed.get("confidence", "medium")).strip().lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        result["confidence"] = confidence

        # text fields — берём из LLM или из fallback
        for field in ("market_logic", "entry_logic", "risk", "conclusion"):
            value = str(parsed.get(field, "")).strip()
            if not value or len(value) < 5:
                value = fallback_data.get(field, "")
            result[field] = value

        return result

    # ═══════════════════════════════════════════
    # MERGE / FALLBACK
    # ═══════════════════════════════════════════

    def _merge(self, original: Dict, refined: Dict) -> Dict:
        result = dict(original)
        result["decision"] = refined["decision"]
        result["market_logic"] = refined["market_logic"]
        result["entry_logic"] = refined["entry_logic"]
        result["risk"] = refined["risk"]
        result["conclusion"] = refined["conclusion"]
        result["llm_refined"] = True
        result["llm_confidence"] = refined["confidence"]
        return result

    def _fallback(self, decision_data: Dict, reason: str = "") -> Dict:
        if reason:
            print(f"CryptoLLMAgent fallback: {reason}")
        result = dict(decision_data)
        result["llm_refined"] = False
        result["llm_confidence"] = None
        return result
