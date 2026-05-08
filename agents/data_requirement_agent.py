from typing import Any, Dict

from agents.schemas.data_plan import DataPlan, empty_data_plan


class DataRequirementAgent:
    def build(self, event_profile: Dict[str, Any], driver_map: Dict[str, Any]) -> DataPlan:
        event_type = str(event_profile.get("event_type") or "generic_binary_event")
        data_plan = empty_data_plan(event_type=event_type)

        required_data = []
        for side in ("yes_drivers", "no_drivers", "neutral_drivers"):
            for driver in driver_map.get(side, []):
                needed = driver.get("data_needed") or []
                for datum in needed:
                    required_data.append({
                        "driver_id": str(driver.get("id") or "unknown_driver"),
                        "description": str(datum),
                        "query": f"{event_type} {datum}",
                        "source_type": self._source_type_for_requirement(str(datum)),
                        "priority": str(driver.get("impact") or "medium"),
                    })

        dedup = {(x["driver_id"], x["description"]): x for x in required_data}
        data_plan["required_data"] = list(dedup.values())
        data_plan["missing_data"] = list(driver_map.get("must_find") or [])
        data_plan["suggested_queries"] = [x["query"] for x in data_plan["required_data"][:12]]
        data_plan["priority_sources"] = sorted({x["source_type"] for x in data_plan["required_data"]})
        return data_plan

    def _source_type_for_requirement(self, requirement: str) -> str:
        r = requirement.lower()
        if "odds" in r:
            return "odds"
        if "lineup" in r or "injur" in r or "form" in r or "opponent" in r:
            return "sports_data"
        if "price" in r or "threshold" in r or "volatility" in r:
            return "crypto_price"
        if "official" in r or "filing" in r or "regulator" in r:
            return "official"
        return "news"
