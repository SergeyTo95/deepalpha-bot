import json
import logging
import re
from typing import Any, Dict, List, Optional

from agents.decision_agent import DecisionAgent as _BaseDecisionAgent

logger = logging.getLogger(__name__)


_FIELD_ALIASES = {
    "system probability": "System Probability",
    "probability": "System Probability",
    "вероятность системы": "System Probability",
    "системная вероятность": "System Probability",
    "confidence": "Confidence",
    "уверенность": "Confidence",
    "reasoning": "Reasoning",
    "logic": "Reasoning",
    "логика": "Reasoning",
    "рассуждение": "Reasoning",
    "main scenario": "Main Scenario",
    "основной сценарий": "Main Scenario",
    "alternative scenario": "Alternative Scenario",
    "альтернативный сценарий": "Alternative Scenario",
    "conclusion": "Conclusion",
    "вывод": "Conclusion",
    "заключение": "Conclusion",
    "options breakdown": "Options Breakdown",
    "расклад по вариантам": "Options Breakdown",
    "trigger watch": "Trigger Watch",
    "trigger high": "Trigger High",
    "trigger medium": "Trigger Medium",
    "trigger low": "Trigger Low",
    "mispricing": "Mispricing",
    "расхождение": "Mispricing",
    "market psychology": "Market Psychology",
    "психология рынка": "Market Psychology",
    "alpha note": "Alpha Note",
    "альфа": "Alpha Note",
    "trade insight": "Trade Insight",
    "анализ входа": "Trade Insight",
    "trade strategy": "Trade Strategy",
    "стратегия": "Trade Strategy",
    "trade entry": "Trade Entry",
    "условия входа": "Trade Entry",
    "trade risk": "Trade Risk",
    "риск": "Trade Risk",
}


class SafeDecisionAgent(_BaseDecisionAgent):
    """Runtime guard around DecisionAgent for provider-format and metadata drift.

    The guard never fabricates independent evidence. If the provider output cannot
    be parsed or an internal optional stage raises, it returns the existing
    market-aligned fallback instead of allowing ChiefAgent to degrade to N/A.
    """

    def run(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str = "en",
        user_context: str = "",
        is_background: bool = False,
        cycle_id: str = None,
        job_id: str = None,
        request_id: str = None,
    ) -> Dict[str, Any]:
        safe_market = self._normalize_market_data(market_data)
        safe_news = self._normalize_news_data(news_data)
        try:
            result = super().run(
                safe_market,
                safe_news,
                lang=lang,
                user_context=user_context,
                is_background=is_background,
                cycle_id=cycle_id,
                job_id=job_id,
                request_id=request_id,
            )
            if self._has_usable_probability(result):
                result.setdefault("decision_runtime_guard", "ok")
                return result
            logger.warning(
                "DECISION_RUNTIME_GUARD_FALLBACK reason=unusable_result question=%s",
                str(safe_market.get("question") or "")[:120],
            )
            return self._safe_market_fallback(
                safe_market,
                safe_news,
                lang=lang,
                reason="unusable_result",
            )
        except Exception as exc:
            logger.exception(
                "DECISION_RUNTIME_GUARD_FALLBACK reason=exception exception_type=%s question=%s",
                exc.__class__.__name__,
                str(safe_market.get("question") or "")[:120],
            )
            return self._safe_market_fallback(
                safe_market,
                safe_news,
                lang=lang,
                reason=f"exception:{exc.__class__.__name__}",
            )

    def _parse_llm_output(self, text: str, market_type: str = "binary") -> Dict[str, str]:
        raw = str(text or "").strip()
        if not raw:
            return super()._parse_llm_output(raw, market_type=market_type)

        json_fields = self._parse_json_fields(raw)
        normalized = self._normalize_provider_text(raw)
        fields = super()._parse_llm_output(normalized, market_type=market_type)

        for key, value in json_fields.items():
            if key in fields and value and not fields.get(key):
                fields[key] = value

        if not fields.get("System Probability"):
            probability = self._find_probability(raw)
            if probability:
                fields["System Probability"] = probability

        return fields

    @staticmethod
    def _normalize_market_data(value: Any) -> Dict[str, Any]:
        data = dict(value) if isinstance(value, dict) else {}
        for key in ("market_microstructure", "market_structure"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        for key in ("options", "sub_markets", "related_markets"):
            if not isinstance(data.get(key), list):
                data[key] = []
        return data

    @staticmethod
    def _safe_count(value: Any) -> int:
        try:
            return max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _normalize_news_data(cls, value: Any) -> Dict[str, Any]:
        data = dict(value) if isinstance(value, dict) else {}
        summary = data.get("source_summary")
        summary = dict(summary) if isinstance(summary, dict) else {}
        for key in ("tier1", "tier2", "tier3", "fresh", "usable", "stale"):
            summary[key] = cls._safe_count(summary.get(key))
        data["source_summary"] = summary
        for key in ("sources", "key_signals", "relevant_sources"):
            if not isinstance(data.get(key), list):
                data[key] = []
        if not isinstance(data.get("news_evidence"), dict):
            data["news_evidence"] = {}
        return data

    @staticmethod
    def _normalize_provider_text(text: str) -> str:
        text = re.sub(r"^\s*```(?:json|text|markdown)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
        lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append("")
                continue
            line = re.sub(r"^#{1,6}\s*", "", line)
            line = re.sub(r"^(?:[-*•]+|\d+[.)])\s*", "", line)
            line = line.replace("**", "").replace("__", "").replace("`", "")

            # Kimi may use an em dash instead of a colon after a field label.
            lower = line.lower()
            for alias, canonical in _FIELD_ALIASES.items():
                if lower.startswith(alias):
                    remainder = line[len(alias):].lstrip()
                    if remainder.startswith((":", "—", "–", "-")):
                        remainder = remainder[1:].strip()
                    line = f"{canonical}: {remainder}".rstrip()
                    break
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_json_fields(text: str) -> Dict[str, str]:
        candidate = re.sub(r"^\s*```json\s*|\s*```\s*$", "", text, flags=re.IGNORECASE).strip()
        if not (candidate.startswith("{") and candidate.endswith("}")):
            return {}
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}

        fields: Dict[str, str] = {}
        for raw_key, raw_value in payload.items():
            alias = str(raw_key or "").strip().lower().replace("_", " ")
            canonical = _FIELD_ALIASES.get(alias)
            if not canonical:
                continue
            if isinstance(raw_value, (dict, list)):
                value = json.dumps(raw_value, ensure_ascii=False)
            else:
                value = str(raw_value or "").strip()
            if value:
                fields[canonical] = value
        return fields

    @staticmethod
    def _find_probability(text: str) -> str:
        patterns = [
            r"\b(Yes|No)\b\s*(?:—|–|-|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"\b(Да|Нет)\b\s*(?:—|–|-|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            r"(?:system probability|вероятность системы|системная вероятность)[^\n%]{0,60}?([0-9]+(?:\.[0-9]+)?)\s*%",
        ]
        for index, pattern in enumerate(patterns):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            if index < 2:
                outcome = match.group(1).lower()
                outcome = "Yes" if outcome in {"yes", "да"} else "No"
                return f"{outcome} — {float(match.group(2)):.1f}%"
            return f"Yes — {float(match.group(1)):.1f}%"
        return ""

    @staticmethod
    def _has_usable_probability(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        probability = str(result.get("probability") or "").strip()
        if not probability or probability.upper() in {"N/A", "NA", "NONE", "UNKNOWN"}:
            return False
        return bool(re.search(r"[0-9]+(?:\.[0-9]+)?\s*%", probability))

    def _safe_market_fallback(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        *,
        lang: str,
        reason: str,
    ) -> Dict[str, Any]:
        question = str(market_data.get("question") or "Unknown market")
        category = str(market_data.get("category") or "Unknown")
        market_probability = str(market_data.get("market_probability") or "Unknown")
        options = market_data.get("options") if isinstance(market_data.get("options"), list) else []
        market_type = str(market_data.get("market_type") or "binary")
        market_prob_value, market_leader = self._parse_market_probability(
            market_probability,
            options,
            market_type,
        )
        days_to_event = self._days_to_event(str(market_data.get("date_context") or "Unknown"))
        market_balance = self._classify_balance(market_prob_value)
        result = self._market_aligned_fallback(
            question=question,
            category=category,
            market_probability=market_probability,
            market_prob_value=market_prob_value,
            market_leader=market_leader,
            options=options,
            market_type=market_type,
            days_to_event=days_to_event,
            market_balance=market_balance,
            news_summary=str(news_data.get("news_summary") or ""),
            trend_summary=str(market_data.get("trend_summary") or ""),
            lang=lang,
        )
        result["decision_runtime_guard"] = "market_aligned_fallback"
        result["decision_fallback_reason"] = reason
        return result
