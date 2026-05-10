import re
from typing import Any, Dict, List

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

        subtype = self._detect_market_subtype(text, event_type, profile)
        profile.update(subtype)

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

    def _extract_threshold(self, text: str) -> str:
        raw = str(text or "")
        patterns = [
            r"(?:o/u)\s*(\d+(?:\.\d+)?)",
            r"\bover\s*(\d+(?:\.\d+)?)",
            r"\bunder\s*(\d+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            m = re.search(pattern, raw, re.IGNORECASE)
            if m:
                return m.group(1)

        numerics = list(re.finditer(r"\b(\d+(?:\.\d+)?)\b", raw))
        if not numerics:
            return ""
        if len(numerics) == 1:
            return numerics[0].group(1)

        last = numerics[-1].group(1)
        if "." in last:
            return last
        if re.search(r"\b(?:set|game|games|match)\s+%s\b" % re.escape(last), raw, re.IGNORECASE):
            return ""
        return last

    def _detect_market_subtype(self, text: str, event_type: str, profile: Dict[str, str]) -> Dict[str, Any]:
        t = (text or "").lower()
        if re.search(r"set\s*1|first set", t) and re.search(r"games", t) and re.search(r"o/u|over|under", t):
            threshold = self._extract_threshold(text)
            return {
                "market_subtype": "set_total_games",
                "resolution_metric": "games",
                "threshold": threshold,
                "period": "set_1",
                "set_number": 1,
                "side_semantics": ["OVER", "UNDER"],
                "driver_family": ["first_set_games_average", "hold_rate", "break_rate", "tiebreak_frequency", "serve_strength", "return_strength", "surface_speed", "recent_first_set_lengths", "over_under_tendency", "early_match_volatility"],
                "subtype_confidence": "high",
            }
        out = {
            "market_subtype": "generic_binary" if "multi" not in str(profile.get("market_type","")) else "generic_multi_outcome",
            "resolution_metric": "",
            "threshold": "",
            "period": "",
            "set_number": 0,
            "side_semantics": ["YES", "NO"],
            "driver_family": ["market_context", "primary_evidence"],
            "subtype_confidence": "medium",
        }
        if event_type == "tennis_head_to_head" or "tennis" in str(profile.get("subcategory","")).lower() or (" vs " in t and any(k in t for k in ["set", "games", "winner", "over", "under"])):
            out.update({"market_subtype": "match_winner", "resolution_metric": "match_result", "driver_family": ["ranking", "recent_form", "injury", "surface", "h2h", "fatigue"]})
            if re.search(r"set\s*1\s*winner|first set winner", t):
                out.update({"market_subtype": "first_set_winner", "resolution_metric": "set_result", "period": "set_1", "set_number": 1, "driver_family": ["first_set_form", "hold_rate", "break_rate", "surface_speed", "early_match_volatility"]})
            m = re.search(r"(?:set\s*(\d+).{0,20}?games?.{0,20}?(?:o/u|over|under))|(?:first set total games)", t)
            threshold = self._extract_threshold(text)
            if m:
                sn = int(m.group(1)) if m.group(1) else 1
                out.update({"market_subtype": "set_total_games", "resolution_metric": "games", "period": f"set_{sn}", "set_number": sn, "threshold": threshold, "side_semantics": ["OVER", "UNDER"], "driver_family": ["first_set_games_average", "hold_rate", "break_rate", "tiebreak_frequency", "serve_strength", "return_strength", "surface_speed", "recent_first_set_lengths", "over_under_tendency", "early_match_volatility"], "subtype_confidence": "high"})
            if re.search(r"total games?\s*(?:o/u|over|under)|match games", t) and "set" not in t:
                out.update({"market_subtype": "match_total_games", "resolution_metric": "games", "threshold": threshold, "side_semantics": ["OVER", "UNDER"], "driver_family": ["hold_rate", "break_rate", "set_length_profile", "surface_speed"]})
            if re.search(r"[+-]\d+(?:\.\d+)?\s*games|handicap|spread", t):
                out.update({"market_subtype": "handicap", "resolution_metric": "game_spread", "driver_family": ["game_differential_profile", "serve_dominance", "break_margin", "opponent_strength"]})
            if re.search(r"total games over|total games under", t) and "vs" in t:
                out.update({"market_subtype": "player_total_games", "resolution_metric": "player_games", "threshold": threshold})
            if re.search(r"correct score|wins\s*\d-\d", t):
                out.update({"market_subtype": "correct_score", "resolution_metric": "set_score", "driver_family": ["straight_sets_probability", "matchup_profile", "serve_hold_break"]})
            return out
        if any(k in t for k in ["btc", "bitcoin", "eth", "ethereum", "hit $", "reach $"]):
            out.update({"market_subtype": "price_target", "resolution_metric": "price", "driver_family": ["spot_price", "timeframe", "volatility", "liquidity", "macro_catalysts", "regulatory_catalysts", "technical_trend"]})
        elif "election" in t or "candidate" in t:
            out.update({"market_subtype": "election_winner", "resolution_metric": "official_result", "driver_family": ["polling_averages", "forecast_models", "turnout", "fundraising", "demographic_shifts"]})
        elif any(k in t for k in ["snow", "rain", "temperature", "hurricane", "storm"]):
            out.update({"market_subtype": "deadline_threshold", "resolution_metric": "weather_measurement", "driver_family": ["official_forecast", "model_runs", "alerts", "climatology", "update_recency"]})
        elif any(k in t for k in ["mention", "mentions"]):
            out.update({"market_subtype": "mentions", "resolution_metric": "count", "driver_family": ["official_transcript", "keyword_matching", "measurement_rules"]})
        elif any(k in t for k in ["approve", "approval", "regulatory"]):
            out.update({"market_subtype": "approval_event", "resolution_metric": "official_action", "driver_family": ["official_filings", "agency_signals", "deadline"]})
        elif len(re.findall(r"\b(yes|no)\b", t))>0:
            out.update({"market_subtype": "yes_no_event"})
        return out

    def _resolution_notes(self, market_options: Dict[str, float]) -> List[str]:
        notes = ["Interpretation is deterministic and based on explicit parsing patterns."]
        if len(market_options or {}) > 2:
            notes.append("Multi-outcome market detected from options count.")
        return notes
