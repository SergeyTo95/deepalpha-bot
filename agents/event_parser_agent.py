import re
from typing import Dict, List

from agents.event_type_agent import EventTypeAgent
from agents.schemas.event_profile import EventProfile, empty_event_profile


class EventParserAgent:
    def __init__(self) -> None:
        self._type_agent = EventTypeAgent()

    def parse(self, question: str, market_options: Dict[str, float], category_type: str = "", subcategory: str = "", market_type: str = "") -> EventProfile:
        profile = empty_event_profile()
        text = str(question or "").strip()
        event_type = self._type_agent.classify(text, market_options or {})
        profile["event_type"] = event_type

        if event_type == "football_team_win":
            team = self._extract_team_name(text)
            profile.update({
                "category_type": "sports",
                "subcategory": "football",
                "market_type": "binary_team_win",
                "target_entity": team,
                "yes_condition": f"{team} wins the match.",
                "no_condition": f"{team} does not win the match, including draw/loss where market rules define that.",
                "confidence": "high",
            })
        elif event_type == "football_tournament_advancement":
            team = self._extract_team_name(text)
            stage = self._extract_stage(text)
            comp = self._extract_competition(text)
            profile.update({
                "category_type": "sports",
                "subcategory": "football",
                "market_type": "tournament_advancement",
                "target_entity": team,
                "competition": comp,
                "event_target": stage,
                "yes_condition": f"{team} reaches the specified tournament stage under market rules.",
                "no_condition": f"{team} does not reach the specified tournament stage.",
                "confidence": "high" if team else "medium",
            })
        elif event_type == "football_tournament_winner_group":
            group = self._extract_target_group(text)
            comp = self._extract_competition(text)
            profile.update({
                "category_type": "sports",
                "subcategory": "football",
                "market_type": "tournament_winner_group",
                "target_group": group,
                "competition": comp,
                "yes_condition": "A team matching the target group wins the specified competition.",
                "no_condition": "The winner does not match the target group.",
                "confidence": "high",
            })
        elif event_type == "crypto_price_threshold":
            asset = self._extract_asset(text)
            price = self._extract_price(text)
            deadline = self._extract_deadline(text)
            profile.update({
                "category_type": "crypto",
                "subcategory": "crypto",
                "market_type": "threshold",
                "target_entity": asset,
                "event_target": price,
                "deadline": deadline,
                "yes_condition": f"{asset} meets the price condition before/by the deadline under market rules.",
                "no_condition": f"{asset} does not meet the price condition.",
                "confidence": "high" if asset and price else "medium",
            })
        elif event_type == "tennis_head_to_head":
            p1, p2 = self._extract_h2h_players(text)
            profile.update({
                "category_type": "sports",
                "subcategory": "tennis",
                "market_type": "head_to_head",
                "target_entity": p1,
                "event_target": p2,
                "yes_condition": f"{p1} wins the match.",
                "no_condition": f"{p1} does not win the match.",
                "confidence": "high" if p1 and p2 else "medium",
            })
        elif event_type == "company_product_release":
            company, product = self._extract_company_product(text)
            profile.update({
                "category_type": "company_tech",
                "subcategory": "product_release",
                "market_type": "binary_event",
                "target_entity": company,
                "event_target": product,
                "deadline": self._extract_deadline(text),
                "yes_condition": "The specified company releases/launches the specified product under market rules.",
                "no_condition": "The specified company does not release/launch the specified product under market rules.",
                "confidence": "medium",
            })
        elif event_type == "legal_regulatory_approval":
            profile.update({
                "category_type": "legal_regulatory",
                "subcategory": "approval_or_action",
                "market_type": "binary_event",
                "target_entity": self._extract_regulator(text),
                "event_target": self._extract_target_phrase(text),
                "deadline": self._extract_deadline(text),
                "yes_condition": "The specified regulator/legal actor takes the specified approval/action under market rules.",
                "no_condition": "The specified approval/action does not occur under market rules.",
                "confidence": "medium",
            })
        else:
            profile["market_type"] = "generic_multi_outcome" if len(market_options or {}) > 2 else "binary_event"
            if profile["market_type"] == "generic_multi_outcome":
                profile["event_type"] = "generic_multi_outcome"

        if category_type and profile["category_type"] == "other":
            profile["category_type"] = category_type
        if subcategory and profile["subcategory"] == "unknown":
            profile["subcategory"] = subcategory

        profile["resolution_notes"] = self._resolution_notes(market_options)
        return profile

    def _extract_team_name(self, text: str) -> str:
        m = re.search(r"^\s*Will\s+(.+?)\s+(?:win|beat|defeat|reach|qualify for|advance to|make)\b", text, re.IGNORECASE | re.UNICODE)
        return m.group(1).strip(" ,.;:-?") if m else ""

    def _extract_stage(self, text: str) -> str:
        m = re.search(r"\b(semi-?final|quarter-?final|final)\b", text, re.IGNORECASE)
        return m.group(1) if m else ""

    def _extract_competition(self, text: str) -> str:
        m = re.search(r"\b(\d{4}\s+)?(champions league|europa league|uefa europa league)\b", text, re.IGNORECASE)
        return (m.group(0) or "").strip() if m else ""

    def _extract_target_group(self, text: str) -> str:
        m = re.search(r"(team from england|english team|team from spain|spanish team|team from germany|german team|premier league team)", text, re.IGNORECASE)
        return (m.group(1) if m else "").strip()

    def _extract_asset(self, text: str) -> str:
        m = re.search(r"\b(bitcoin|btc|ethereum|eth|solana|sol)\b", text, re.IGNORECASE)
        return m.group(1) if m else ""

    def _extract_price(self, text: str) -> str:
        m = re.search(r"(\$\s?\d[\d,]*(?:k|m)?)", text, re.IGNORECASE)
        return m.group(1).replace(" ", "") if m else ""

    def _extract_deadline(self, text: str) -> str:
        m = re.search(r"\b(?:by|before|on|in)\s+([A-Za-z]+\s+\d{1,2}|\d{4}-\d{2}-\d{2}|\d{4}|[A-Za-z]+)\b", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _extract_h2h_players(self, text: str):
        m = re.search(r"\bWill\s+(.+?)\s+(?:beat|defeat|win against)\s+(.+?)\?*$", text, re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip(" ?.,;:")
        m = re.search(r"^\s*(.+?)\s+vs\s+(.+?)\s*$", text, re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", ""

    def _extract_company_product(self, text: str):
        m = re.search(r"\bWill\s+(.+?)\s+(?:release|launch|ship)\s+(.+?)(?:\s+by\s+|\s+in\s+\d{4}|\?|$)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return "", ""

    def _extract_regulator(self, text: str) -> str:
        m = re.search(r"\b(SEC|CFTC|DOJ)\b", text, re.IGNORECASE)
        return m.group(1).upper() if m else ""

    def _extract_target_phrase(self, text: str) -> str:
        m = re.search(r"\b(?:approve|sue|deny|reject)\s+(.+?)(?:\s+in\s+\d{4}|\?|$)", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _resolution_notes(self, market_options: Dict[str, float]) -> List[str]:
        notes = ["Interpretation is deterministic and based on explicit parsing patterns."]
        if len(market_options or {}) > 2:
            notes.append("Multi-outcome market detected from options count.")
        return notes
