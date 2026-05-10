from typing import Any, Dict, List

from agents.schemas.research_plan import empty_research_plan, safe_outcomes


class ResearchPlanAgent:
    def build(
        self,
        question: str,
        outcome_map: Dict[str, Any] = None,
        event_profile: Dict[str, Any] = None,
        driver_map: Dict[str, Any] = None,
        data_plan: Dict[str, Any] = None,
        market_options: Dict[str, float] = None,
    ) -> Dict[str, Any]:
        plan = empty_research_plan()
        outcome_map = outcome_map if isinstance(outcome_map, dict) else {}
        event_profile = event_profile if isinstance(event_profile, dict) else {}
        driver_map = driver_map if isinstance(driver_map, dict) else {}
        data_plan = data_plan if isinstance(data_plan, dict) else {}

        market = outcome_map.get("market") if isinstance(outcome_map.get("market"), dict) else {}
        market_type = str(market.get("market_type") or event_profile.get("market_type") or "unknown")
        category_type = str(market.get("category_type") or event_profile.get("category_type") or "other")
        subcategory = str(market.get("subcategory") or event_profile.get("subcategory") or "unknown")
        event_type = str(event_profile.get("event_type") or "unknown")

        plan["market"].update({
            "question": str(question or market.get("question") or ""),
            "market_type": market_type,
            "category_type": category_type,
            "subcategory": subcategory,
            "event_type": event_type,
        })

        outcomes = safe_outcomes(outcome_map)
        if not outcomes and isinstance(market_options, dict):
            outcomes = [{"id": str(k).lower(), "label": str(k)} for k in market_options.keys()]

        context = self._template_context(event_type, market_type)

        for outcome in outcomes:
            label = str(outcome.get("label") or "Unknown")
            plan["outcome_research"].append({
                "outcome_id": str(outcome.get("id") or "option"),
                "outcome_label": label,
                "queries": self._outcome_queries(label, plan["market"]["question"], context),
                "required_facts": list(context["required_facts"]),
                "minimum_facts_needed": int(context["minimum_per_outcome"]),
            })

        shared = self._shared_queries(plan["market"]["question"], outcomes, context)
        plan["shared_research"] = shared
        plan["minimum_data_policy"] = {
            "minimum_total_facts": max(int(context["minimum_total_facts"]), len(outcomes) * int(context["minimum_per_outcome"])),
            "minimum_per_outcome": int(context["minimum_per_outcome"]),
            "critical_drivers": list(context["critical_drivers"]),
            "can_forecast_without_sources": False,
        }

        extra_drivers = self._extra_driver_labels(driver_map, data_plan)
        if extra_drivers:
            plan["warnings"].append("Driver/data plan enrichment added to required facts.")
            for item in plan["outcome_research"]:
                for d in extra_drivers[:4]:
                    if d not in item["required_facts"]:
                        item["required_facts"].append(d)

        plan["parser"]["confidence"] = "high" if outcomes else "medium"
        if not outcomes:
            plan["warnings"].append("No parsed outcomes found; generated market-level research only.")
        return plan

    def _template_context(self, event_type: str, market_type: str) -> Dict[str, Any]:
        key = event_type or ""
        if market_type == "date_range":
            key = "date_range"
        base = {
            "required_facts": ["latest_developments", "primary_sources", "market_context"],
            "minimum_per_outcome": 1,
            "minimum_total_facts": 3,
            "critical_drivers": ["latest_developments", "primary_sources", "outcome_specific_evidence", "market_context"],
            "outcome_patterns": [
                ("{label} probability latest evidence", "latest_developments"),
                ("{label} recent developments", "latest_developments"),
                ("{label} odds analysis", "market_context"),
                ("{label} supporting factors", "outcome_specific_evidence"),
                ("{label} risks", "outcome_specific_evidence"),
            ],
            "shared_patterns": ["{question} latest news", "{question} market context", "{question} primary source"],
        }
        templates = {
            "tennis_head_to_head": {
                "required_facts": ["recent_form", "surface_fit", "ranking_level", "injury_fatigue", "h2h", "tournament_surface", "schedule_context"],
                "minimum_per_outcome": 2, "minimum_total_facts": 4,
                "critical_drivers": ["recent_form", "surface_fit", "injury_fatigue", "ranking_level"],
                "outcome_patterns": [("{label} recent matches form", "recent_form"), ("{label} surface record hard clay grass", "surface_fit"), ("{label} injury withdrawal fatigue schedule", "injury_fatigue"), ("{label} ranking ATP Challenger ITF", "ranking_level")],
                "shared_patterns": ["{a} vs {b} H2H", "{question} tennis tournament surface", "{question} tournament conditions"],
            },
            "company_product_release": {
                "required_facts": ["official_statement", "launch_timeline", "product_readiness", "credible_delay", "deadline_context"],
                "minimum_per_outcome": 2, "minimum_total_facts": 4,
                "critical_drivers": ["official_statement", "launch_timeline", "product_readiness", "credible_delay", "deadline_context"],
                "outcome_patterns": [("{base} official announcement", "official_statement"), ("{base} roadmap timeline", "launch_timeline"), ("{base} launch event", "product_readiness"), ("{base} delayed safety testing", "credible_delay")],
                "shared_patterns": ["{base} latest official statement", "{base} upcoming release schedule"],
            },
            "legal_regulatory_approval": {
                "required_facts": ["filing_status", "regulatory_deadline", "precedent", "agency_statement", "legal_risk"],
                "minimum_per_outcome": 2, "minimum_total_facts": 4,
                "critical_drivers": ["filing_status", "regulatory_deadline", "precedent", "agency_statement", "legal_risk"],
                "outcome_patterns": [("{question} filing status deadline", "filing_status"), ("{question} regulator statement", "agency_statement"), ("{question} approval precedent", "precedent"), ("{question} delay rejection risk", "legal_risk")],
                "shared_patterns": ["{question} latest filing", "{question} regulator statements"],
            },
            "crypto_price_threshold": {
                "required_facts": ["current_price_distance", "deadline", "volatility", "liquidity_flows", "resistance_support", "macro_context"],
                "minimum_per_outcome": 2, "minimum_total_facts": 4,
                "critical_drivers": ["current_price_distance", "deadline", "volatility", "liquidity_flows", "resistance_support", "macro_context"],
                "outcome_patterns": [("Bitcoin current price distance to threshold", "current_price_distance"), ("Bitcoin ETF inflows price target", "liquidity_flows"), ("BTC resistance breakout", "resistance_support"), ("Bitcoin macro Fed CPI risk", "macro_context")],
                "shared_patterns": ["Bitcoin current price", "BTC market structure support resistance"],
            },
            "football_tournament_winner_group": {
                "required_facts": ["teams_remaining", "combined_outright_odds", "bracket_path", "non_group_favorites", "injury_form"],
                "minimum_per_outcome": 2, "minimum_total_facts": 4,
                "critical_drivers": ["teams_remaining", "combined_outright_odds", "bracket_path", "non_group_favorites", "injury_form"],
                "outcome_patterns": [("{question} teams remaining", "teams_remaining"), ("{question} outright odds", "combined_outright_odds"), ("{question} bracket path", "bracket_path"), ("{question} favorites injury form", "injury_form")],
                "shared_patterns": ["{question} remaining teams", "{question} bracket draw", "{question} winner odds"],
            },
            "date_range": {
                "required_facts": ["official_timeline", "roadmap", "event_schedule", "delay_signals", "historical_release_pattern"],
                "minimum_per_outcome": 2, "minimum_total_facts": 4,
                "critical_drivers": ["official_timeline", "roadmap", "event_schedule", "delay_signals", "historical_release_pattern"],
                "outcome_patterns": [("{base} release {label} evidence", "official_timeline"), ("{base} roadmap {label}", "roadmap"), ("{base} launch event {label}", "event_schedule"), ("{base} delay signals {label}", "delay_signals")],
                "shared_patterns": ["{base} official announcement", "{base} roadmap", "{base} launch event", "{base} credible leaks", "{base} historical release timing"],
            },
        }
        if key in templates:
            base.update(templates[key])
        elif market_type == "head_to_head":
            base.update(templates["tennis_head_to_head"])
        return base

    def _outcome_queries(self, label: str, question: str, context: Dict[str, Any]) -> List[Dict[str, str]]:
        base = self._topic_base(question)
        rows = []
        for pattern, driver in context.get("outcome_patterns", []):
            q = pattern.format(label=label, question=question, base=base)
            rows.append({"query": q, "driver": driver, "priority": "high" if driver in context.get("critical_drivers", []) else "medium", "source_type": "news_search", "why": f"Validate {driver} for outcome '{label}'."})
        return rows

    def _shared_queries(self, question: str, outcomes: List[Dict[str, str]], context: Dict[str, Any]) -> List[Dict[str, str]]:
        base = self._topic_base(question)
        a = outcomes[0]["label"] if len(outcomes) > 0 else "Outcome A"
        b = outcomes[1]["label"] if len(outcomes) > 1 else "Outcome B"
        res = []
        for raw in context.get("shared_patterns", []):
            q = raw.format(question=question, base=base, a=a, b=b)
            res.append({"query": q, "driver": "market_context", "priority": "high", "source_type": "news_search", "why": "Build shared market context before comparing outcomes."})
        return res

    def _topic_base(self, question: str) -> str:
        s = str(question or "").strip()
        if not s:
            return "market"
        parts = s.split(" by ")[0].split("?")[0]
        return parts.strip()

    def _extra_driver_labels(self, driver_map: Dict[str, Any], data_plan: Dict[str, Any]) -> List[str]:
        labels: List[str] = []
        for key in ["yes_drivers", "no_drivers", "neutral_drivers"]:
            arr = driver_map.get(key)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict):
                        label = str(item.get("id") or item.get("label") or "").strip()
                        if label and label not in labels:
                            labels.append(label)
        req = data_plan.get("required_data") if isinstance(data_plan.get("required_data"), list) else []
        for item in req:
            label = str(item).strip()
            if label and label not in labels:
                labels.append(label)
        return labels
