import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

from agents.schemas.targeted_research import empty_targeted_research, safe_list, safe_str


class TargetedResearchAgent:
    AMBIGUOUS_TERMS = {"bengaluru", "texas", "june", "england", "open", "finals", "race", "market", "launch", "approval"}
    SOURCE_PATHS = [
        ("existing_sources",),
        ("news_data", "relevant_sources"),
        ("news_data", "sources"),
        ("news_data", "raw_sources"),
        ("news_data", "news_items"),
        ("source_summary", "sources"),
        ("source_summary", "relevant_sources"),
        ("market_data", "relevant_sources"),
        ("market_data", "sources"),
        ("market_data", "raw_sources"),
    ]

    def run(self, question: str, outcome_map: Dict[str, Any], research_plan: Dict[str, Any], event_profile: Dict[str, Any], news_data: Dict[str, Any], market_data: Dict[str, Any], source_summary: Dict[str, Any], existing_sources: List[Any]) -> Dict[str, Any]:
        tr = empty_targeted_research()
        research_plan = research_plan if isinstance(research_plan, dict) else {}
        event_profile = event_profile if isinstance(event_profile, dict) else {}
        tr["market"] = {
            "question": safe_str(question),
            "market_type": safe_str((research_plan.get("market") or {}).get("market_type") or event_profile.get("market_type") or "unknown"),
            "category_type": safe_str((research_plan.get("market") or {}).get("category_type") or event_profile.get("category_type") or "other"),
            "subcategory": safe_str((research_plan.get("market") or {}).get("subcategory") or event_profile.get("subcategory") or "unknown"),
            "event_type": safe_str((research_plan.get("market") or {}).get("event_type") or event_profile.get("event_type") or "unknown"),
        }

        contexts = {
            "existing_sources": existing_sources if isinstance(existing_sources, list) else [],
            "news_data": news_data if isinstance(news_data, dict) else {},
            "market_data": market_data if isinstance(market_data, dict) else {},
            "source_summary": source_summary if isinstance(source_summary, dict) else {},
        }
        normalized = self._normalize_sources(contexts)
        tr["source_quality"]["raw_sources_count"] = len(normalized)

        query_results: List[Dict[str, Any]] = []
        matched_source_keys: Set[str] = set()
        claims_count = 0

        outcome_research = safe_list(research_plan.get("outcome_research"))
        for outcome in outcome_research:
            if not isinstance(outcome, dict):
                continue
            outcome_id = safe_str(outcome.get("outcome_id") or "")
            outcome_label = safe_str(outcome.get("outcome_label") or "")
            required_facts = [safe_str(x) for x in safe_list(outcome.get("required_facts")) if safe_str(x)]
            for q in safe_list(outcome.get("queries")):
                result, keys = self._process_query(q, outcome_id, outcome_label, required_facts, normalized, event_profile, True)
                query_results.append(result)
                claims_count += len(result["extracted_claims"])
                matched_source_keys.update(keys)

        for q in safe_list(research_plan.get("shared_research")):
            result, keys = self._process_query(q, "", "", [], normalized, event_profile, False)
            query_results.append(result)
            claims_count += len(result["extracted_claims"])
            matched_source_keys.update(keys)

        tr["query_results"] = query_results
        tr["source_quality"]["matched_sources_count"] = len(matched_source_keys)
        tr["source_quality"]["claims_count"] = claims_count
        tr["source_quality"]["high_relevance_sources_count"] = sum(
            1 for r in query_results for m in safe_list(r.get("matched_sources")) if safe_str((m or {}).get("relevance")) == "high"
        )
        tr["source_quality"]["medium_relevance_sources_count"] = sum(
            1 for r in query_results for m in safe_list(r.get("matched_sources")) if safe_str((m or {}).get("relevance")) == "medium"
        )

        tr["outcome_coverage"] = self._outcome_coverage(outcome_research, query_results)
        tr["shared_coverage"] = self._shared_coverage(safe_list(research_plan.get("shared_research")), query_results)
        tr["missing_research"] = self._missing_research(query_results)
        coverage_scores = [x.get("coverage_score", 0.0) for x in tr["outcome_coverage"]] + [tr["shared_coverage"].get("coverage_score", 0.0)]
        overall_coverage = sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0.0
        tr["source_quality"]["coverage_score"] = max(0.0, min(1.0, round(overall_coverage, 3)))

        policy = research_plan.get("minimum_data_policy") if isinstance(research_plan.get("minimum_data_policy"), dict) else {}
        min_total = int(policy.get("minimum_total_facts") or 0)
        min_per_outcome = int(policy.get("minimum_per_outcome") or 0)
        enough_per_outcome = all(int(o.get("facts_found") or 0) >= min_per_outcome for o in tr["outcome_coverage"]) if tr["outcome_coverage"] else False
        can_build = bool(tr["source_quality"]["coverage_score"] >= 0.5 and claims_count >= min_total and enough_per_outcome)
        if self._is_h2h_market(tr["market"]):
            has_shared_context = int(tr["shared_coverage"].get("facts_found") or 0) > 0 or any(
                d in {"h2h", "head_to_head", "surface", "tournament_context"} for d in safe_list(tr["shared_coverage"].get("covered_drivers"))
            )
            no_zero_drivers = all(len(safe_list(o.get("covered_drivers"))) > 0 for o in safe_list(tr.get("outcome_coverage")))
            can_build = bool(can_build and has_shared_context and no_zero_drivers and len(matched_source_keys) >= 2)
        tr["source_quality"]["can_build_forecast"] = can_build

        tr["notes"] = [
            "No external search was performed in this layer; only existing sources were matched.",
            "Forecast should be treated as incomplete if can_build_forecast is false.",
        ]
        if any(item.get("priority") in {"critical", "high"} for item in tr["missing_research"]):
            tr["notes"].append("No matched sources were found for critical drivers.")

        tr["parser"]["confidence"] = "high" if claims_count > 0 else "low"
        return tr

    def _normalize_sources(self, contexts: Dict[str, Any]) -> List[Dict[str, str]]:
        raw: List[Any] = []
        for path in self.SOURCE_PATHS:
            cursor = contexts
            for key in path:
                if isinstance(cursor, dict):
                    cursor = cursor.get(key)
                else:
                    cursor = None
            if isinstance(cursor, list):
                raw.extend(cursor)

        normalized: List[Dict[str, str]] = []
        seen_url: Set[str] = set()
        seen_title_prefix: Set[str] = set()
        for item in raw:
            source = self._normalize_item(item)
            if not source["title"] and not source["snippet"] and not source["text"]:
                continue
            url_key = source["url"].strip().lower()
            if url_key and url_key in seen_url:
                continue
            title_key = source["title"].strip().lower()[:80]
            if not url_key and title_key and title_key in seen_title_prefix:
                continue
            if url_key:
                seen_url.add(url_key)
            if title_key:
                seen_title_prefix.add(title_key)
            normalized.append(source)
        return normalized

    def _normalize_item(self, item: Any) -> Dict[str, str]:
        if isinstance(item, str):
            title = item.strip()
            snippet = ""
            summary = ""
            description = ""
            url = ""
            source_name = ""
            published = ""
        elif isinstance(item, dict):
            title = safe_str(item.get("title") or item.get("headline"))
            snippet = safe_str(item.get("snippet"))
            summary = safe_str(item.get("summary"))
            description = safe_str(item.get("description"))
            url = safe_str(item.get("url"))
            source_name = safe_str(item.get("source") or item.get("publisher"))
            published = safe_str(item.get("published") or item.get("date"))
        else:
            title = snippet = summary = description = url = source_name = published = ""
        text = " ".join(x for x in [title, snippet, summary, description] if x).strip()
        return {"title": title, "snippet": snippet or summary or description, "url": url, "source": source_name, "published": published, "text": text}

    def _process_query(self, query_item: Any, outcome_id: str, outcome_label: str, required_facts: List[str], sources: List[Dict[str, str]], event_profile: Dict[str, Any], outcome_specific: bool) -> Tuple[Dict[str, Any], Set[str]]:
        q = query_item if isinstance(query_item, dict) else {}
        query = safe_str(q.get("query"))
        driver = safe_str(q.get("driver") or "market_context")
        priority = safe_str(q.get("priority") or "medium")
        source_type = safe_str(q.get("source_type") or "existing")
        tokens = self._tokens(" ".join([query, driver, outcome_label, " ".join(required_facts), source_type, self._event_targets(event_profile)]))
        matched_sources = []
        claims = []
        matched_keys: Set[str] = set()
        for src in sources:
            rel = self._relevance(src.get("text", ""), tokens, outcome_label, driver, query, event_profile, outcome_specific)
            if rel is None or rel == "low":
                continue
            freshness = self._freshness(src.get("published", ""))
            matched_sources.append({
                "title": src.get("title", ""),
                "snippet": src.get("snippet", ""),
                "url": src.get("url", ""),
                "source": src.get("source", ""),
                "freshness": freshness,
                "relevance": rel,
            })
            matched_keys.add((src.get("url") or src.get("title", "")).strip().lower())
            claim = self._make_claim(src, rel, outcome_label if outcome_specific else "", driver)
            if claim:
                claims.append(claim)
        status = "matched_existing_source" if matched_sources else "missing"
        return {
            "query": query,
            "driver": driver,
            "outcome_id": outcome_id,
            "outcome_label": outcome_label,
            "priority": priority if priority in {"low", "medium", "high", "critical"} else "medium",
            "source_type": source_type,
            "status": status,
            "matched_sources": matched_sources[:5],
            "extracted_claims": claims[:5],
        }, matched_keys

    def _tokens(self, text: str) -> List[str]:
        return [t for t in re.findall(r"[a-zA-Z0-9$]+", text.lower()) if len(t) >= 3]

    def _relevance(self, text: str, tokens: List[str], outcome_label: str, driver: str, query: str, event_profile: Dict[str, Any], outcome_specific: bool) -> str:
        hay = (text or "").lower()
        if not hay:
            return None
        if self._is_h2h_market(event_profile):
            return self._h2h_relevance(hay, outcome_label, driver, query, outcome_specific)
        filtered_tokens = [t for t in set(tokens) if t and t not in self.AMBIGUOUS_TERMS]
        token_hits = sum(1 for t in filtered_tokens if t in hay)
        outcome_hit = bool(outcome_label and any(t in hay for t in self._tokens(outcome_label)))
        driver_hit = bool(driver and any(t in hay for t in self._tokens(driver)))
        market_cat = safe_str(event_profile.get("category_type")).lower()
        if outcome_specific and outcome_label and not outcome_hit:
            return None
        if (not outcome_specific) and token_hits < 2:
            return None
        if "mention" in market_cat and "powell" in query.lower() and "recession" in query.lower():
            if "powell" not in hay and "federal reserve" not in hay:
                return None
            if "recession" not in hay:
                return None
        if token_hits >= 4 and (outcome_hit or driver_hit):
            return "high"
        if token_hits >= 2 and (outcome_hit or driver_hit):
            return "medium"
        if token_hits >= 2:
            return "low"
        return None

    def _h2h_relevance(self, hay: str, outcome_label: str, driver: str, query: str, outcome_specific: bool) -> str:
        names = self._extract_names(query)
        if len(names) < 2:
            names = self._extract_names(outcome_label)
        outcome_aliases = self._aliases(outcome_label) if outcome_label else []
        p1 = self._aliases(names[0]) if len(names) > 0 else outcome_aliases
        p2 = self._aliases(names[1]) if len(names) > 1 else []
        p1_hit, p2_hit = self._has_alias(hay, p1), (self._has_alias(hay, p2) if p2 else False)
        if p1_hit and p2_hit:
            return "high"
        shared_q = (not outcome_specific) or driver in {"h2h", "head_to_head"}
        if shared_q:
            return None
        if (p1_hit or p2_hit) and driver in self.H2H_DRIVERS:
            return "medium"
        return None

    def _is_h2h_market(self, market: Dict[str, Any]) -> bool:
        mt = safe_str(market.get("market_type")).lower()
        et = safe_str(market.get("event_type")).lower()
        return mt == "head_to_head" or et == "tennis_head_to_head"

    def _extract_names(self, text: str) -> List[str]:
        return [m.strip() for m in re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+)+", text or "")]

    def _aliases(self, full_name: str) -> List[str]:
        n = self._norm(full_name)
        parts = n.split()
        out = [n] if n else []
        if parts:
            s = parts[-1]
            out.extend([s, s.replace("h", ""), s.replace("a", "e")])
        return list(dict.fromkeys([x for x in out if x]))

    def _has_alias(self, hay: str, aliases: List[str]) -> bool:
        n = self._norm(hay)
        toks = set(n.split())
        for a in aliases:
            if " " in a and a in n:
                return True
            if a in toks:
                return True
            if len(a) > 5 and any(self._ed1(a, t) for t in toks):
                return True
        return False

    def _norm(self, value: str) -> str:
        v = unicodedata.normalize("NFKD", safe_str(value)).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", v.lower())).strip()

    def _ed1(self, a: str, b: str) -> bool:
        if abs(len(a) - len(b)) > 1:
            return False
        if a == b:
            return True
        i = j = d = 0
        while i < len(a) and j < len(b):
            if a[i] == b[j]:
                i += 1; j += 1; continue
            d += 1
            if d > 1:
                return False
            if len(a) > len(b): i += 1
            elif len(b) > len(a): j += 1
            else: i += 1; j += 1
        return True

    def _make_claim(self, source: Dict[str, str], relevance: str, supports_outcome: str, driver: str) -> Dict[str, str]:
        snippet = safe_str(source.get("snippet"))
        title = safe_str(source.get("title"))
        claim = snippet if snippet and len(snippet) <= 240 else title
        if not claim:
            return {}
        confidence = "unknown"
        if relevance == "high" and title and (snippet or source.get("url")):
            confidence = "high"
        elif relevance == "medium":
            confidence = "medium"
        elif relevance == "low":
            confidence = "low"
        return {
            "claim": claim,
            "supports_outcome": supports_outcome,
            "driver": driver,
            "confidence": confidence,
            "source_title": title,
            "source_url": safe_str(source.get("url")),
        }

    def _event_targets(self, event_profile: Dict[str, Any]) -> str:
        vals = [event_profile.get("target_entity"), event_profile.get("event_target"), event_profile.get("target_group"), event_profile.get("competition")]
        return " ".join(safe_str(v) for v in vals if safe_str(v))

    def _freshness(self, published: str) -> str:
        p = safe_str(published)
        if not p:
            return "unknown"
        try:
            dt = datetime.fromisoformat(p.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - dt).days
            if days <= 2:
                return "fresh"
            if days <= 14:
                return "recent"
            return "stale"
        except Exception:
            return "unknown"

    def _outcome_coverage(self, outcome_research: List[Any], results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for outcome in outcome_research:
            if not isinstance(outcome, dict):
                continue
            oid = safe_str(outcome.get("outcome_id"))
            olabel = safe_str(outcome.get("outcome_label"))
            queries = [r for r in results if r.get("outcome_id") == oid]
            drivers = list({safe_str(q.get("driver")) for q in safe_list(outcome.get("queries")) if isinstance(q, dict) and safe_str(q.get("driver"))})
            covered = sorted({safe_str(r.get("driver")) for r in queries if r.get("status") == "matched_existing_source"})
            missing = sorted([d for d in drivers if d not in covered])
            facts = sum(len(safe_list(r.get("extracted_claims"))) for r in queries)
            score = round((len(covered) / len(drivers)) if drivers else 0.0, 3)
            out.append({"outcome_id": oid, "outcome_label": olabel, "covered_drivers": covered, "missing_drivers": missing, "facts_found": facts, "coverage_score": score})
        return out

    def _shared_coverage(self, shared_research: List[Any], results: List[Dict[str, Any]]) -> Dict[str, Any]:
        queries = [r for r in results if not safe_str(r.get("outcome_id"))]
        drivers = sorted({safe_str(q.get("driver")) for q in shared_research if isinstance(q, dict) and safe_str(q.get("driver"))})
        covered = sorted({safe_str(r.get("driver")) for r in queries if r.get("status") == "matched_existing_source"})
        missing = sorted([d for d in drivers if d not in covered])
        facts = sum(len(safe_list(r.get("extracted_claims"))) for r in queries)
        score = round((len(covered) / len(drivers)) if drivers else 0.0, 3)
        return {"covered_drivers": covered, "missing_drivers": missing, "facts_found": facts, "coverage_score": score}

    def _missing_research(self, results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        high = []
        medium_low = []
        for r in results:
            if r.get("status") != "missing":
                continue
            item = {
                "query": safe_str(r.get("query")),
                "driver": safe_str(r.get("driver")),
                "outcome_label": safe_str(r.get("outcome_label")),
                "priority": safe_str(r.get("priority") or "medium"),
                "why_needed": f"Needed to validate driver '{safe_str(r.get('driver'))}' for forecast readiness.",
            }
            if item["priority"] in {"high", "critical"}:
                high.append(item)
            else:
                medium_low.append(item)
        out = high + medium_low[: max(0, 20 - len(high))]
        return out[:20]
    H2H_DRIVERS = {"h2h", "head_to_head", "surface", "recent_form", "ranking", "injury", "withdrawal"}
