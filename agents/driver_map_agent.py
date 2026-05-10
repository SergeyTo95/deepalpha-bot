import os
from typing import Any, Dict, List

from agents.dynamic_driver_agent import DynamicDriverAgent
from agents.intelligence.crypto_intelligence import build_crypto_driver_templates
from agents.intelligence.football_intelligence import build_football_driver_templates
from agents.intelligence.generic_intelligence import build_generic_driver_templates
from agents.intelligence.tennis_intelligence import build_tennis_driver_templates
from agents.schemas.driver_map import DriverMap, empty_driver_map


class DriverMapAgent:
    def __init__(self) -> None:
        self.templates: Dict[str, Dict[str, Any]] = {}
        self.templates.update(build_football_driver_templates())
        self.templates.update(build_crypto_driver_templates())
        self.templates.update(build_tennis_driver_templates())
        self.templates.update(build_generic_driver_templates())
        self.dynamic_enabled = os.getenv("GEMINI_DYNAMIC_DRIVERS_ENABLED", "false").lower() == "true"
        self.dynamic_agent = DynamicDriverAgent()

    def build(self, event_profile: Dict[str, Any]) -> DriverMap:
        event_type = str(event_profile.get("event_type") or "generic_binary_event")
        category_type = str(event_profile.get("category_type") or "other")
        market_type = str(event_profile.get("market_type") or "binary_event")
        out = empty_driver_map(event_type=event_type, category_type=category_type, market_type=market_type)
        tpl = self.templates.get(event_type, self.templates.get("generic_binary_event", {}))

        out["yes_drivers"] = self._enrich_driver_data(tpl.get("yes", []), tpl.get("required", []))
        out["no_drivers"] = self._enrich_driver_data(tpl.get("no", []), tpl.get("required", []))
        out["neutral_drivers"] = [{"id": "deadline_sensitivity", "label": "Deadline sensitivity", "description": "Distance to deadline changes risk.", "impact": "medium", "data_needed": ["deadline timing"]}]
        out["must_find"] = list(tpl.get("required", []))
        out["high_impact_keywords"] = ["official", "confirmed", "injury", "odds", "deadline", "approval", "lineup"]

        if self.dynamic_enabled:
            dynamic = self.dynamic_agent.build(
                event_profile=event_profile,
                question=str(event_profile.get("question") or ""),
                market_options=event_profile.get("market_options") or [],
            )
            self._merge_dynamic_drivers(out, dynamic)

        out["confidence"] = "medium" if out["yes_drivers"] or out["no_drivers"] else "low"
        return out

    def _enrich_driver_data(self, drivers: List[Dict[str, Any]], required: List[str]) -> List[Dict[str, Any]]:
        enriched = []
        for d in drivers:
            item = dict(d)
            if not item.get("description"):
                item["description"] = f"Driver {item.get('id', '')} impacts resolution."
            if not item.get("data_needed"):
                item["data_needed"] = required[:2] if required else []
            enriched.append(item)
        return enriched

    def _merge_dynamic_drivers(self, out: DriverMap, dynamic: Dict[str, Any]) -> None:
        if not dynamic:
            return
        out["yes_drivers"] = self._merge_driver_bucket(out["yes_drivers"], dynamic.get("yes_drivers"), cap=8)
        out["no_drivers"] = self._merge_driver_bucket(out["no_drivers"], dynamic.get("no_drivers"), cap=8)
        out["neutral_drivers"] = self._merge_driver_bucket(out["neutral_drivers"], dynamic.get("neutral_drivers"), cap=8)
        out["must_find"] = self._merge_unique_strings(out["must_find"], dynamic.get("must_find"), cap=12)
        out["high_impact_keywords"] = self._merge_unique_strings(out["high_impact_keywords"], dynamic.get("high_impact_keywords"), cap=20)

    def _merge_driver_bucket(self, base: List[Dict[str, Any]], extra: Any, cap: int) -> List[Dict[str, Any]]:
        result = list(base)
        seen = {self._normalize_id(row.get("id")) for row in result}
        if not isinstance(extra, list):
            return result[:cap]
        for row in extra:
            if not isinstance(row, dict):
                continue
            norm = self._normalize_id(row.get("id"))
            if not norm or norm in seen:
                continue
            item = {
                "id": norm,
                "label": row.get("label") or norm.replace("_", " ").title(),
                "description": row.get("description") or f"Driver {norm} impacts resolution.",
                "impact": row.get("impact") if row.get("impact") in {"low", "medium", "high", "very_high"} else "medium",
                "data_needed": row.get("data_needed") if isinstance(row.get("data_needed"), list) else [],
            }
            result.append(item)
            seen.add(norm)
            if len(result) >= cap:
                break
        return result[:cap]

    def _merge_unique_strings(self, base: List[str], extra: Any, cap: int) -> List[str]:
        result = list(base)
        seen = {x.strip().lower() for x in result if isinstance(x, str) and x.strip()}
        if not isinstance(extra, list):
            return result[:cap]
        for x in extra:
            if not isinstance(x, str) or not x.strip():
                continue
            key = x.strip().lower()
            if key in seen:
                continue
            result.append(x.strip())
            seen.add(key)
            if len(result) >= cap:
                break
        return result[:cap]

    def _normalize_id(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return "_".join(part for part in text.replace("-", "_").split("_") if part)
