import json
import re
from typing import Any, Dict, List

from services.llm_service import generate_decision_text


_ALLOWED_IMPACTS = {"low", "medium", "high", "very_high"}


class DynamicDriverAgent:
    def __init__(self) -> None:
        self.max_yes = 8
        self.max_no = 8
        self.max_neutral = 8
        self.max_must_find = 12
        self.max_keywords = 20

    def build(self, event_profile: Dict[str, Any], question: str, market_options: List[str]) -> Dict[str, Any]:
        try:
            prompt = self._build_prompt(event_profile=event_profile, question=question, market_options=market_options)
            raw = generate_decision_text(prompt)
            parsed = self._safe_json_loads(raw)
            if not isinstance(parsed, dict):
                print("DYNAMIC_DRIVER: invalid JSON payload type")
                return self._empty_result()
            return self._normalize_payload(parsed)
        except Exception as exc:
            print(f"DYNAMIC_DRIVER: build failed, fallback to static templates: {exc}")
            return self._empty_result()

    def _build_prompt(self, event_profile: Dict[str, Any], question: str, market_options: List[str]) -> str:
        return (
            "You are a market driver planning assistant for DeepAlpha.\n"
            "DeepAlpha is a forecasting engine, not an AI summary bot.\n"
            "You are NOT forecasting.\n"
            "Do NOT assign probabilities.\n"
            "Do NOT recommend trades.\n"
            "Do NOT invent facts.\n"
            "Only produce a research/driver plan.\n"
            "Output JSON only. No markdown, no prose.\n"
            "Language rule: Use English for all internal planning fields.\n"
            "English-only fields: market_subtype, driver ids, labels, descriptions, data_needed, suggested_queries, preferred_sources, evidence_criteria, must_find, high_impact_keywords, risk_flags.\n"
            "Named entities must keep their official/original names (people, organizations, locations, competitions, assets), e.g. Celta Vigo, UEFA Europa League, Bitcoin, Ukraine, Russia, Brent crude.\n"
            "Driver IDs must be English snake_case using only a-z, 0-9, and underscores.\n"
            "Suggested queries should be English first. If local-language variants are useful, include them only after the English query.\n\n"
            "Return this exact schema:\n"
            "{\n"
            '  "market_subtype": str,\n'
            '  "entities": [str],\n'
            '  "yes_drivers": [{"id": str, "label": str, "description": str, "impact": "low|medium|high|very_high", "data_needed": [str], "suggested_queries": [str], "preferred_sources": [str], "evidence_criteria": str}],\n'
            '  "no_drivers": [...],\n'
            '  "neutral_drivers": [...],\n'
            '  "must_find": [str],\n'
            '  "high_impact_keywords": [str],\n'
            '  "risk_flags": [str],\n'
            '  "confidence": "low|medium|high"\n'
            "}\n\n"
            f"event_profile={json.dumps(event_profile, ensure_ascii=False)}\n"
            f"question={question}\n"
            f"market_options={json.dumps(market_options, ensure_ascii=False)}\n"
        )

    def _safe_json_loads(self, raw: str) -> Any:
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text).strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print("DYNAMIC_DRIVER: JSON decode failed")
            return None

    def _normalize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "market_subtype": self._to_str(payload.get("market_subtype")),
            "entities": self._to_str_list(payload.get("entities")),
            "yes_drivers": self._normalize_driver_list(payload.get("yes_drivers"), self.max_yes),
            "no_drivers": self._normalize_driver_list(payload.get("no_drivers"), self.max_no),
            "neutral_drivers": self._normalize_driver_list(payload.get("neutral_drivers"), self.max_neutral),
            "must_find": self._to_str_list(payload.get("must_find"))[: self.max_must_find],
            "high_impact_keywords": self._to_str_list(payload.get("high_impact_keywords"))[: self.max_keywords],
            "risk_flags": self._to_str_list(payload.get("risk_flags")),
            "confidence": self._normalize_confidence(payload.get("confidence")),
        }

    def _normalize_driver_list(self, value: Any, cap: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if not isinstance(value, list):
            return items
        for row in value:
            if not isinstance(row, dict):
                continue
            normalized_id = self._normalize_driver_id(row.get("id"))
            if not normalized_id:
                continue
            impact = self._to_str(row.get("impact")).lower()
            if impact not in _ALLOWED_IMPACTS:
                impact = "medium"
            items.append(
                {
                    "id": normalized_id,
                    "label": self._to_str(row.get("label")) or normalized_id.replace("_", " ").title(),
                    "description": self._to_str(row.get("description")) or f"Driver {normalized_id} impacts resolution.",
                    "impact": impact,
                    "data_needed": self._to_str_list(row.get("data_needed")),
                    "suggested_queries": self._to_str_list(row.get("suggested_queries")),
                    "preferred_sources": self._to_str_list(row.get("preferred_sources")),
                    "evidence_criteria": self._to_str(row.get("evidence_criteria")),
                }
            )
            if len(items) >= cap:
                break
        return items

    def _normalize_driver_id(self, value: Any) -> str:
        base = self._to_str(value).strip().lower()
        if not base:
            return ""
        base = re.sub(r"[^a-z0-9]+", "_", base)
        return re.sub(r"_+", "_", base).strip("_")

    def _normalize_confidence(self, value: Any) -> str:
        v = self._to_str(value).lower()
        return v if v in {"low", "medium", "high"} else "medium"

    def _to_str(self, value: Any) -> str:
        return value if isinstance(value, str) else ""

    def _to_str_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        for x in value:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "market_subtype": "",
            "entities": [],
            "yes_drivers": [],
            "no_drivers": [],
            "neutral_drivers": [],
            "must_find": [],
            "high_impact_keywords": [],
            "risk_flags": [],
            "confidence": "medium",
        }
