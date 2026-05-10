from typing import Any, Dict, List, Tuple

from agents.schemas.research_execution import empty_research_execution, safe_list, safe_str


class ResearchExecutorAgent:
    PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    def run(
        self,
        question: str,
        research_plan: Dict[str, Any],
        outcome_map: Dict[str, Any],
        event_profile: Dict[str, Any],
        existing_sources: List[Any] = None,
        max_queries: int = 12,
    ) -> Dict[str, Any]:
        execution = empty_research_execution()
        research_plan = research_plan if isinstance(research_plan, dict) else {}
        event_profile = event_profile if isinstance(event_profile, dict) else {}
        existing_sources = existing_sources if isinstance(existing_sources, list) else []

        market = research_plan.get("market") if isinstance(research_plan.get("market"), dict) else {}
        execution["market"] = {
            "question": safe_str(question) or safe_str(market.get("question")),
            "market_type": safe_str(market.get("market_type") or event_profile.get("market_type") or "unknown"),
            "category_type": safe_str(market.get("category_type") or event_profile.get("category_type") or "other"),
            "subcategory": safe_str(market.get("subcategory") or event_profile.get("subcategory") or "unknown"),
            "event_type": safe_str(market.get("event_type") or event_profile.get("event_type") or "unknown"),
        }

        planned = self._flatten_planned_queries(research_plan)
        selected = self._select_queries(planned, max_queries=max_queries)
        execution["coverage_attempt"]["queries_total"] = len(planned)
        execution["coverage_attempt"]["critical_queries_total"] = sum(1 for q in selected if q["priority"] == "critical")

        normalized_sources = self._normalize_existing_sources(existing_sources)
        provider_available = False
        if not provider_available:
            execution["notes"].append("No executable search provider was available in this layer.")

        for query in selected:
            matched = self._match_existing_sources(query, normalized_sources, event_profile)
            status = "not_available"
            if matched:
                status = "executed"
            execution["executed_queries"].append({
                **query,
                "status": status,
                "results": matched,
                "error": "",
            })

        collected = self._deduplicate_sources([src for item in execution["executed_queries"] for src in item.get("results", [])])
        execution["collected_sources"] = collected
        execution["coverage_attempt"]["queries_executed"] = len(selected)
        execution["coverage_attempt"]["queries_with_results"] = sum(1 for item in execution["executed_queries"] if item.get("results"))
        execution["coverage_attempt"]["sources_collected"] = len(collected)
        execution["coverage_attempt"]["critical_queries_with_results"] = sum(
            1 for item in execution["executed_queries"] if item.get("priority") == "critical" and item.get("results")
        )
        execution["parser"]["confidence"] = "high" if collected else ("medium" if selected else "low")
        return execution

    def _flatten_planned_queries(self, research_plan: Dict[str, Any]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for shared in safe_list(research_plan.get("shared_research")):
            if isinstance(shared, dict):
                rows.append(self._query_row(shared, "", ""))

        for outcome in safe_list(research_plan.get("outcome_research")):
            if not isinstance(outcome, dict):
                continue
            outcome_id = safe_str(outcome.get("outcome_id"))
            outcome_label = safe_str(outcome.get("outcome_label"))
            for query in safe_list(outcome.get("queries")):
                if isinstance(query, dict):
                    rows.append(self._query_row(query, outcome_id, outcome_label))
        return rows

    def _query_row(self, query: Dict[str, Any], outcome_id: str, outcome_label: str) -> Dict[str, str]:
        priority = safe_str(query.get("priority") or "medium").lower()
        if priority not in self.PRIORITY_ORDER:
            priority = "medium"
        return {
            "query": safe_str(query.get("query")),
            "driver": safe_str(query.get("driver")),
            "outcome_id": outcome_id,
            "outcome_label": outcome_label,
            "priority": priority,
            "source_type": safe_str(query.get("source_type") or "news_search"),
        }

    def _select_queries(self, planned: List[Dict[str, str]], max_queries: int) -> List[Dict[str, str]]:
        if max_queries <= 0:
            return []
        sorted_planned = sorted(planned, key=lambda q: (self.PRIORITY_ORDER.get(q["priority"], 2), 0 if not q["outcome_id"] else 1))
        selected: List[Dict[str, str]] = []
        used = set()

        outcomes = []
        for q in sorted_planned:
            if q["outcome_id"] and q["outcome_id"] not in outcomes:
                outcomes.append(q["outcome_id"])

        for outcome_id in outcomes:
            if len(selected) >= max_queries:
                break
            for idx, q in enumerate(sorted_planned):
                if idx in used:
                    continue
                if q["outcome_id"] == outcome_id:
                    selected.append(q)
                    used.add(idx)
                    break

        for idx, q in enumerate(sorted_planned):
            if len(selected) >= max_queries:
                break
            if idx in used:
                continue
            selected.append(q)
            used.add(idx)

        return selected[:max_queries]

    def _normalize_existing_sources(self, existing_sources: List[Any]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for item in existing_sources:
            if isinstance(item, str):
                title = item.strip()
                snippet = ""
                url = ""
                source = ""
                published = ""
            elif isinstance(item, dict):
                title = safe_str(item.get("title") or item.get("headline"))
                snippet = safe_str(item.get("snippet") or item.get("summary") or item.get("description"))
                url = safe_str(item.get("url"))
                source = safe_str(item.get("source") or item.get("publisher"))
                published = safe_str(item.get("published") or item.get("date"))
            else:
                continue
            if not (title or snippet):
                continue
            out.append({"title": title, "snippet": snippet, "url": url, "source": source, "published": published})
        return self._deduplicate_sources(out)

    def _match_existing_sources(self, query: Dict[str, str], sources: List[Dict[str, str]], event_profile: Dict[str, Any]) -> List[Dict[str, str]]:
        out = []
        for source in sources:
            relevance = self._relevance(query, source, event_profile)
            if relevance is None:
                continue
            out.append({
                "title": safe_str(source.get("title")),
                "snippet": safe_str(source.get("snippet")),
                "url": safe_str(source.get("url")),
                "source": safe_str(source.get("source")),
                "published": safe_str(source.get("published")),
                "relevance": relevance,
            })
            if len(out) >= 5:
                break
        return out

    def _relevance(self, query: Dict[str, str], source: Dict[str, str], event_profile: Dict[str, Any]) -> str:
        text = " ".join([
            safe_str(source.get("title")).lower(),
            safe_str(source.get("snippet")).lower(),
            safe_str(source.get("source")).lower(),
        ])
        if not text:
            return None
        query_terms = [t for t in safe_str(query.get("query")).lower().split() if len(t) >= 4]
        outcome_terms = [t for t in safe_str(query.get("outcome_label")).lower().split() if len(t) >= 4]
        driver_terms = [t for t in safe_str(query.get("driver")).lower().split("_") if len(t) >= 3]
        event_terms = [
            safe_str(event_profile.get("target_entity")).lower(),
            safe_str(event_profile.get("event_target")).lower(),
            safe_str(event_profile.get("competition")).lower(),
        ]
        event_terms = [t for block in event_terms for t in block.split() if len(t) >= 4]

        q_hits = sum(1 for t in query_terms if t in text)
        o_hits = sum(1 for t in outcome_terms if t in text)
        d_hits = sum(1 for t in driver_terms if t in text)
        e_hits = sum(1 for t in event_terms if t in text)

        if (o_hits > 0 and (d_hits > 0 or q_hits >= 2)) or (e_hits > 0 and d_hits > 0 and q_hits > 0):
            return "high"
        if o_hits > 0 or q_hits >= 2 or (e_hits > 0 and q_hits > 0):
            return "medium"
        if q_hits > 0 or d_hits > 0:
            return "low"
        return None

    def _deduplicate_sources(self, sources: List[Dict[str, str]]) -> List[Dict[str, str]]:
        seen_url = set()
        seen_title = set()
        out = []
        for item in sources:
            url = safe_str(item.get("url")).lower()
            title = safe_str(item.get("title")).lower()
            if url and url in seen_url:
                continue
            if not url and title and title in seen_title:
                continue
            if url:
                seen_url.add(url)
            if title:
                seen_title.add(title)
            out.append(item)
        return out
