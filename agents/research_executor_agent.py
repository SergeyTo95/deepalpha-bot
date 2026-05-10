import re
import unicodedata
from typing import Any, Dict, List, Tuple

from agents.schemas.research_execution import empty_research_execution, safe_list, safe_str

try:
    from services.news_service import search_google_news, classify_freshness, enrich_news_item
except Exception:
    search_google_news = None
    classify_freshness = None
    enrich_news_item = None


class ResearchExecutorAgent:
    PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    H2H_DRIVERS = {"recent_form", "ranking", "injury", "withdrawal", "surface", "h2h", "head_to_head"}
    AMBIGUOUS_TERMS = {"bengaluru", "texas", "june", "england", "open", "finals", "race", "market", "launch", "approval"}

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
        provider_available = callable(search_google_news)
        if provider_available:
            execution["notes"].append("Google News RSS provider was used for targeted research execution.")
        else:
            execution["notes"].append("No executable search provider was available in this layer.")

        had_failures = False
        filtered_sources_count = 0
        for query in selected:
            matched = self._match_existing_sources(query, normalized_sources, event_profile)
            google_results: List[Dict[str, str]] = []
            status = "not_available"
            error = ""

            if provider_available:
                try:
                    raw_results = search_google_news(self._preserve_query_text(query.get("query")), limit=3)
                    google_results, filtered = self._normalize_google_results(raw_results, query, question, event_profile)
                    filtered_sources_count += filtered
                    status = "executed"
                except Exception as exc:
                    status = "failed"
                    error = safe_str(exc)
                    had_failures = True

            merged_results = self._deduplicate_sources(matched + google_results)
            execution["executed_queries"].append({
                **query,
                "status": status,
                "results": merged_results,
                "error": error,
            })

        collected = self._deduplicate_sources([src for item in execution["executed_queries"] for src in item.get("results", [])])
        execution["collected_sources"] = collected
        execution["coverage_attempt"]["queries_executed"] = sum(1 for item in execution["executed_queries"] if item.get("status") == "executed")
        execution["coverage_attempt"]["queries_with_results"] = sum(
            1 for item in execution["executed_queries"] if item.get("status") == "executed" and item.get("results")
        )
        execution["coverage_attempt"]["sources_collected"] = len(collected)
        execution["coverage_attempt"]["filtered_sources_count"] = filtered_sources_count
        execution["coverage_attempt"]["critical_queries_with_results"] = sum(
            1
            for item in execution["executed_queries"]
            if item.get("priority") == "critical" and item.get("status") == "executed" and item.get("results")
        )
        if had_failures:
            execution["notes"].append("Some research queries failed but the pipeline continued safely.")
        if not collected:
            execution["notes"].append("No sources were collected from executed research queries.")
        if filtered_sources_count:
            execution["notes"].append("Some sources were filtered because they did not match market entities/outcomes.")
            execution["notes"].append("Some sources matched only ambiguous terms and were ignored.")
        execution["parser"]["confidence"] = "high" if collected else ("medium" if selected else "low")
        return execution

    def _normalize_google_results(
        self,
        raw_results: Any,
        query: Dict[str, str],
        question: str,
        event_profile: Dict[str, Any],
    ) -> Tuple[List[Dict[str, str]], int]:
        if not isinstance(raw_results, list):
            return [], 0
        normalized: List[Dict[str, str]] = []
        filtered_low = 0
        strict_h2h = (
            safe_str(event_profile.get("market_type")).lower() == "head_to_head"
            or safe_str(event_profile.get("event_type")).lower() == "tennis_head_to_head"
        )
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            enriched = item
            if callable(enrich_news_item):
                try:
                    enriched = enrich_news_item(item, question=question, user_context=safe_str(query.get("query"))) or item
                except Exception:
                    enriched = item
            title = safe_str(enriched.get("title") or enriched.get("headline"))
            snippet = safe_str(enriched.get("snippet") or enriched.get("summary"))
            url = safe_str(enriched.get("url") or enriched.get("link"))
            source = safe_str(enriched.get("source") or enriched.get("publisher"))
            published = safe_str(enriched.get("published") or enriched.get("date"))
            if not (title or snippet or url):
                continue
            source_row = {
                "title": title,
                "snippet": snippet,
                "url": url,
                "link": url,
                "source": source,
                "published": published,
            }
            relevance = self._relevance(query, source_row, event_profile)
            if relevance is None or relevance == "low":
                filtered_low += 1
                continue
            normalized.append({**source_row, "relevance": relevance or "low"})
        return self._deduplicate_sources(normalized), filtered_low

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
            "query": self._preserve_query_text(query.get("query")),
            "driver": safe_str(query.get("driver")),
            "outcome_id": outcome_id,
            "outcome_label": outcome_label,
            "priority": priority,
            "source_type": safe_str(query.get("source_type") or "news_search"),
        }

    def _preserve_query_text(self, query: Any) -> str:
        if isinstance(query, str):
            return query.strip()
        return safe_str(query)

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
            if relevance is None or relevance == "low":
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
        if (
            safe_str(event_profile.get("market_type")).lower() == "head_to_head"
            or safe_str(event_profile.get("event_type")).lower() == "tennis_head_to_head"
        ):
            return self._h2h_relevance(query, text)
        query_terms = [t for t in safe_str(query.get("query")).lower().split() if len(t) >= 4 and t not in self.AMBIGUOUS_TERMS]
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

        cat = safe_str(event_profile.get("category_type")).lower()
        if (o_hits > 0 and (d_hits > 0 or q_hits >= 2)) or (e_hits > 0 and d_hits > 0 and q_hits > 0):
            return "high"
        if "crypto" in cat and o_hits == 0:
            return None
        if any(x in cat for x in ["politic", "econom", "weather", "legal", "tech"]) and (o_hits + e_hits) == 0:
            return None
        if o_hits > 0 or q_hits >= 2 or (e_hits > 0 and q_hits > 0):
            return "medium"
        return None

    def _h2h_relevance(self, query: Dict[str, str], text: str) -> str:
        participants = self._extract_names(safe_str(query.get("query")))
        if len(participants) < 2:
            participants = self._extract_names(safe_str(query.get("outcome_label")))
        if len(participants) < 2:
            return "low"
        p1_aliases = self._aliases(participants[0])
        p2_aliases = self._aliases(participants[1])
        p1_hit = self._has_name(text, p1_aliases)
        p2_hit = self._has_name(text, p2_aliases)
        driver = safe_str(query.get("driver")).lower()
        is_h2h_query = "h2h" in driver or "head_to_head" in driver or "head to head" in safe_str(query.get("query")).lower()
        strong = any(x in self._norm(text) for x in ["recent form", "ranking", "injury", "withdrawal", "surface", "hard court", "clay", "grass", "h2h", "head to head"])
        if p1_hit and p2_hit:
            return "high"
        if is_h2h_query:
            return "low"
        if (p1_hit or p2_hit) and (driver in self.H2H_DRIVERS or strong):
            return "medium"
        return "low"

    def _extract_names(self, text: str) -> List[str]:
        return [m.strip() for m in re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+)+", text or "")]

    def _aliases(self, full_name: str) -> List[str]:
        norm = self._norm(full_name)
        parts = [p for p in norm.split() if p]
        out = [norm] if norm else []
        if parts:
            surname = parts[-1]
            out.append(surname)
            if len(surname) > 5:
                out.append(surname.replace("h", ""))
                out.append(surname.replace("a", "e"))
        return list(dict.fromkeys([x for x in out if x]))

    def _has_name(self, text: str, aliases: List[str]) -> bool:
        norm_text = self._norm(text)
        tokens = set(norm_text.split())
        for alias in aliases:
            if " " in alias and alias in norm_text:
                return True
            if alias in tokens:
                return True
            if len(alias) > 5 and any(self._ed1(alias, t) for t in tokens):
                return True
        return False

    def _norm(self, value: str) -> str:
        v = unicodedata.normalize("NFKD", safe_str(value)).encode("ascii", "ignore").decode("ascii")
        v = re.sub(r"[^a-z0-9 ]+", " ", v.lower())
        return re.sub(r"\s+", " ", v).strip()

    def _ed1(self, a: str, b: str) -> bool:
        if abs(len(a) - len(b)) > 1:
            return False
        if a == b:
            return True
        i = j = mism = 0
        while i < len(a) and j < len(b):
            if a[i] == b[j]:
                i += 1
                j += 1
                continue
            mism += 1
            if mism > 1:
                return False
            if len(a) > len(b):
                i += 1
            elif len(b) > len(a):
                j += 1
            else:
                i += 1
                j += 1
        return True

    def _deduplicate_sources(self, sources: List[Dict[str, str]]) -> List[Dict[str, str]]:
        seen_url = set()
        seen_title = set()
        out = []
        for item in sources:
            url = safe_str(item.get("url")).lower()
            title = safe_str(item.get("title")).lower()
            title_prefix = title[:80]
            if url and url in seen_url:
                continue
            if title_prefix and title_prefix in seen_title:
                continue
            if url:
                seen_url.add(url)
            if title_prefix:
                seen_title.add(title_prefix)
            out.append(item)
        return out
