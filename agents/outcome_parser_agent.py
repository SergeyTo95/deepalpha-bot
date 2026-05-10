import re
from typing import Any, Dict, List

from agents.schemas.outcome_map import empty_outcome_map, normalize_market_options


class OutcomeParserAgent:
    def parse(
        self,
        question: str,
        market_options: Any,
        event_profile: Dict[str, Any] = None,
        category_type: str = "other",
        subcategory: str = "unknown",
        market_type: str = "unknown",
        deadline: str = "",
        resolution_summary: str = "",
    ) -> Dict[str, Any]:
        event_profile = event_profile if isinstance(event_profile, dict) else {}
        options = normalize_market_options(market_options)
        outcome_map = empty_outcome_map()

        outcome_map["market"].update({
            "question": str(question or ""),
            "market_type": str(market_type or "unknown"),
            "category_type": str(category_type or "other"),
            "subcategory": str(subcategory or "unknown"),
            "deadline": str(deadline or ""),
            "resolution_summary": str(resolution_summary or ""),
        })

        outcome_map["target_entity"] = str(event_profile.get("target_entity") or "")
        outcome_map["target_group"] = str(event_profile.get("target_group") or "")
        outcome_map["event_target"] = str(event_profile.get("event_target") or "")
        outcome_map["competition"] = str(event_profile.get("competition") or "")

        labels = list(options.keys())
        normalized_upper = {x.upper() for x in labels}
        is_binary_yes_no = len(labels) == 2 and normalized_upper == {"YES", "NO"}

        if is_binary_yes_no:
            return self._build_binary(outcome_map, options, event_profile)

        if len(labels) == 2:
            return self._build_head_to_head(outcome_map, options, event_profile)

        if labels and self._is_obvious_range_labels(labels):
            return self._build_multi(outcome_map, options, market_type_hint="date_range")

        if labels and self._is_obvious_numeric_labels(labels):
            return self._build_multi(outcome_map, options, market_type_hint="numeric_range")

        return self._build_multi(outcome_map, options, market_type_hint="multi_outcome")

    def _build_binary(self, outcome_map: Dict[str, Any], options: Dict[str, float], event_profile: Dict[str, Any]) -> Dict[str, Any]:
        event_type = str(event_profile.get("event_type") or "")
        target_group = str(event_profile.get("target_group") or "")

        market_type = "binary_event"
        logic_type = "binary_yes_no"
        yes_condition = str(event_profile.get("yes_condition") or "YES resolves if the event occurs under market rules.")
        no_condition = str(event_profile.get("no_condition") or "NO resolves if the event does not occur under market rules.")

        if event_type == "football_tournament_winner_group":
            market_type = "tournament_winner_group"
            logic_type = "tournament_group_winner"
            if not event_profile.get("yes_condition"):
                yes_condition = "A team matching the target group wins the specified competition."
            if not event_profile.get("no_condition"):
                no_condition = "The winner does not match the target group."

        outcome_map["market"]["market_type"] = market_type
        outcome_map["resolution_logic"]["type"] = logic_type
        outcome_map["resolution_logic"]["notes"] = []

        primary_entities: List[str] = []
        if target_group:
            primary_entities.append(target_group)

        for label, price in options.items():
            upper = label.upper()
            is_yes = upper == "YES"
            condition = yes_condition if is_yes else no_condition
            outcome_map["outcomes"].append({
                "id": "yes" if is_yes else "no",
                "label": upper,
                "condition": condition,
                "market_price": price,
                "normalized_price": price,
                "entity": target_group if is_yes and target_group else "",
                "side": upper,
                "is_binary_yes": is_yes,
                "is_binary_no": not is_yes,
            })

        outcome_map["primary_entities"] = primary_entities
        outcome_map["parser"]["confidence"] = "high"
        return outcome_map

    def _build_head_to_head(self, outcome_map: Dict[str, Any], options: Dict[str, float], event_profile: Dict[str, Any]) -> Dict[str, Any]:
        outcome_map["market"]["market_type"] = "head_to_head"
        event_type = str(event_profile.get("event_type") or "")
        outcome_map["resolution_logic"]["type"] = "head_to_head_match_winner" if event_type == "tennis_head_to_head" else "named_head_to_head_winner"

        entities: List[str] = []
        for label, price in options.items():
            slug = self._slug(label)
            entities.append(label)
            outcome_map["outcomes"].append({
                "id": slug,
                "label": label,
                "condition": f"{label} wins the event/match under market rules.",
                "market_price": price,
                "normalized_price": price,
                "entity": label,
                "side": label,
                "is_binary_yes": False,
                "is_binary_no": False,
            })

        outcome_map["primary_entities"] = entities
        outcome_map["parser"]["confidence"] = "high"
        return outcome_map

    def _build_multi(self, outcome_map: Dict[str, Any], options: Dict[str, float], market_type_hint: str) -> Dict[str, Any]:
        outcome_map["market"]["market_type"] = market_type_hint
        outcome_map["resolution_logic"]["type"] = market_type_hint
        for label, price in options.items():
            is_range = market_type_hint in {"date_range", "numeric_range"}
            condition = (
                f"The event resolves in the {label} range under market rules."
                if is_range
                else f"{label} is the resolving outcome under market rules."
            )
            outcome_map["outcomes"].append({
                "id": self._slug(label),
                "label": label,
                "condition": condition,
                "market_price": price,
                "normalized_price": price,
                "entity": label,
                "side": label,
                "is_binary_yes": False,
                "is_binary_no": False,
            })
        outcome_map["primary_entities"] = [x for x in options.keys()]
        outcome_map["parser"]["confidence"] = "medium" if market_type_hint in {"date_range", "numeric_range"} else "high"
        return outcome_map

    def _slug(self, value: str) -> str:
        text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
        text = re.sub(r"_+", "_", text).strip("_")
        return text or "option"

    def _is_obvious_range_labels(self, labels: List[str]) -> bool:
        patterns = [r"\bbefore\b", r"\bafter\b", r"\bq[1-4]\b", r"\b\d{4}\b", r"\bnot in\b", r"\bjan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec\b", r"-"]
        score = 0
        for label in labels:
            t = label.lower()
            if any(re.search(p, t) for p in patterns):
                score += 1
        return score >= max(2, len(labels) // 2)

    def _is_obvious_numeric_labels(self, labels: List[str]) -> bool:
        score = 0
        for label in labels:
            t = label.lower().replace(" ", "")
            if any(x in t for x in ["<", ">", "%", "below", "above"]) or re.search(r"\d+\s*[-–]\s*\d+", t):
                score += 1
        return score >= max(2, len(labels) // 2)
