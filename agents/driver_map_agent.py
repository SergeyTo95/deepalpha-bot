from typing import Any, Dict, List

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
