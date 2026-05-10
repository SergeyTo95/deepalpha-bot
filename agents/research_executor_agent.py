import logging
import os
import re
import unicodedata
from urllib.parse import urlparse, unquote
from typing import Any, Dict, List, Tuple

from agents.schemas.research_execution import empty_research_execution, safe_list, safe_str

try:
    from services.news_service import search_google_news, classify_freshness, enrich_news_item
except Exception:
    search_google_news = None
    classify_freshness = None
    enrich_news_item = None

try:
    from services.web_search_service import search_web
except Exception:
    search_web = None


logger = logging.getLogger(__name__)


class ResearchExecutorAgent:
    PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    H2H_DRIVERS = {"recent_form", "ranking", "injury", "withdrawal", "surface", "h2h", "head_to_head"}
    AMBIGUOUS_TERMS = {"bengaluru", "texas", "june", "england", "open", "finals", "race", "market", "launch", "approval"}
    TRUSTED_H2H_DOMAINS = {
        "atptour.com",
        "itftennis.com",
        "sofascore.com",
        "scores24.live",
        "flashscore.com",
        "tennisexplorer.com",
        "aiscore.com",
        "ultimatetennisstatistics.com",
        "tennisabstract.com",
    }

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
        web_provider_name = safe_str(os.getenv("WEB_SEARCH_PROVIDER")).lower()
        web_provider_key = safe_str(os.getenv("WEB_SEARCH_API_KEY"))
        web_provider_configured = callable(search_web) and web_provider_name not in {"", "disabled"} and bool(web_provider_key)
        web_used = False
        web_empty = False
        web_failed = False

        if not web_provider_configured:
            execution["notes"].append("Optional web search provider was not configured.")

        for query in selected:
            logger.debug("ResearchExecutor query=%s", safe_str(query.get("query")))
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

            query_results = self._deduplicate_sources(matched + google_results)

            if len(query_results) < 1 and web_provider_configured:
                web_used = True
                try:
                    web_raw = search_web(self._preserve_query_text(query.get("query")), limit=3)
                    logger.debug("Web search raw_count=%s query=%s", len(web_raw) if isinstance(web_raw, list) else 0, safe_str(query.get("query")))
                    web_normalized, web_filtered = self._normalize_google_results(web_raw, query, question, event_profile)
                    filtered_sources_count += web_filtered
                    if not web_normalized:
                        web_empty = True
                    query_results = self._deduplicate_sources(query_results + web_normalized)
                except Exception:
                    web_failed = True

            execution["executed_queries"].append({
                **query,
                "status": status,
                "results": query_results,
                "error": error,
            })

        collected = self._deduplicate_sources([src for item in execution["executed_queries"] for src in item.get("results", [])])
        collected = sorted(collected, key=lambda x: float(x.get("source_quality_score") or 0), reverse=True)
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
        if web_used:
            execution["notes"].append("Optional web search provider was used for targeted research.")
        if web_empty:
            execution["notes"].append("Optional web search provider returned no relevant results.")
        if web_failed:
            execution["notes"].append("Optional web search provider failed for some queries but pipeline continued.")
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
            quality = self._source_quality_score(query, source_row, event_profile, relevance)
            if relevance is None or relevance == "low" or quality < 0.25:
                filtered_low += 1
                continue
            normalized.append({**source_row, "relevance": relevance or "low", "source_quality_score": round(quality,3)})
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
        text = self._source_relevance_text(source)
        if not text:
            return None
        query_text = safe_str(query.get("query"))
        if (
            safe_str(event_profile.get("market_type")).lower() == "head_to_head"
            or safe_str(event_profile.get("event_type")).lower() == "tennis_head_to_head"
            or (" vs " in query_text.lower() and len(self._extract_names(query_text)) >= 2)
        ):
            return self._h2h_relevance(query, source, text)
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

    def _h2h_relevance(self, query: Dict[str, str], source: Dict[str, str], text: str) -> str:
        participants = self._extract_names(safe_str(query.get("query")))
        if len(participants) < 2:
            participants = self._extract_names(safe_str(query.get("outcome_label")))
        if len(participants) < 2:
            return "low"
        p1_aliases = self._aliases(participants[0])
        p2_aliases = self._aliases(participants[1])
        p1_hit = self._has_name(text, p1_aliases)
        p2_hit = self._has_name(text, p2_aliases)

        url = safe_str(source.get("url") or source.get("link"))
        display_url = safe_str(source.get("display_url") or source.get("source_url"))
        trusted_domain = self._is_trusted_h2h_domain(url)
        has_h2h_context = any(x in text for x in ["h2h", "head to head", "head_to_head", "live score", "match"])
        has_both_slug_tokens = self._source_has_player_pair(source, p1_aliases, p2_aliases)
        logger.debug(
            "H2H gate domain=%s trusted=%s p1_hit=%s p2_hit=%s slug_pair=%s h2h_ctx=%s p1_aliases=%s p2_aliases=%s",
            self._extract_domain(url),
            trusted_domain,
            p1_hit,
            p2_hit,
            has_both_slug_tokens,
            has_h2h_context,
            p1_aliases[:4],
            p2_aliases[:4],
        )
        if trusted_domain and p1_hit and p2_hit and (has_h2h_context or has_both_slug_tokens):
            logger.debug(
                "Accepted trusted H2H source domain=%s reason=both_players_and_context title=%s",
                self._extract_domain(url),
                safe_str(source.get("title"))[:120],
            )
            return "high"

        driver = safe_str(query.get("driver")).lower()
        is_h2h_query = "h2h" in driver or "head_to_head" in driver or "head to head" in safe_str(query.get("query")).lower()
        strong = any(x in text for x in ["recent form", "ranking", "injury", "withdrawal", "surface", "hard court", "clay", "grass", "h2h", "head to head"])
        if p1_hit and p2_hit:
            return "high"
        if is_h2h_query:
            logger.debug(
                "Rejected H2H source reason=missing_both_participants trusted=%s p1_hit=%s p2_hit=%s url=%s display_url=%s title=%s",
                trusted_domain,
                p1_hit,
                p2_hit,
                url[:120],
                display_url[:120],
                safe_str(source.get("title"))[:120],
            )
            return "low"
        if (p1_hit or p2_hit) and (driver in self.H2H_DRIVERS or strong):
            return "medium"
        return "low"

    def _canonical_norm(self, text: str) -> str:
        norm = self._norm(text)
        return re.sub(r"\bkesharwani\b", "kesarwani", norm)

    def _source_relevance_text(self, source: Dict[str, str]) -> str:
        url = safe_str(source.get("url") or source.get("link"))
        display_url = safe_str(source.get("display_url") or source.get("source_url"))
        url_tokens = self._tokenize_url(url)
        display_tokens = self._tokenize_url(display_url)
        raw_text = " ".join([
            safe_str(source.get("title")),
            safe_str(source.get("snippet")),
            safe_str(source.get("source")),
            url,
            display_url,
            url_tokens,
            display_tokens,
        ])
        normalized = self._canonical_norm(raw_text)
        logger.debug("Relevance text preview=%s", normalized[:180])
        return normalized

    def _tokenize_url(self, value: str) -> str:
        decoded = unquote(safe_str(value)).lower()
        return re.sub(r"[^a-z0-9]+", " ", decoded)

    def _extract_domain(self, value: str) -> str:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        return parsed.netloc.lower().lstrip("www.")

    def _is_trusted_h2h_domain(self, url: str) -> bool:
        domain = self._extract_domain(url)
        return any(domain == d or domain.endswith(f".{d}") for d in self.TRUSTED_H2H_DOMAINS)

    def _source_has_player_pair(self, source: Dict[str, str], p1_aliases: List[str], p2_aliases: List[str]) -> bool:
        url = safe_str(source.get("url") or source.get("link"))
        display_url = safe_str(source.get("display_url") or source.get("source_url"))
        slug_text = self._canonical_norm(" ".join([self._tokenize_url(url), self._tokenize_url(display_url)]))
        return self._has_name(slug_text, p1_aliases) and self._has_name(slug_text, p2_aliases)

    def _extract_names(self, text: str) -> List[str]:
        raw = safe_str(text)
        if not raw:
            return []

        def _clean_name_tokens(value: str) -> List[str]:
            cleaned = re.sub(r"\b(?:h2h|head\s*to\s*head|match|live\s*score|results?)\b", " ", value, flags=re.IGNORECASE)
            return re.findall(r"[A-Za-z]+", cleaned)

        split = re.split(r"\b(?:vs\.?|v\.?|versus)\b", raw, maxsplit=1, flags=re.IGNORECASE)
        if len(split) == 2:
            names = []
            for side in split:
                tokens = _clean_name_tokens(side)
                if len(tokens) >= 2:
                    names.append(" ".join(tokens[:2]))
                elif len(tokens) == 1:
                    names.append(tokens[0])
            if len(names) == 2:
                return names

        fallback_tokens = _clean_name_tokens(raw)
        if len(fallback_tokens) == 4 and re.search(r"\bh2h\b", raw, flags=re.IGNORECASE):
            return [" ".join(fallback_tokens[:2]), " ".join(fallback_tokens[2:])]

        return [m.strip() for m in re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+)+", raw)]

    def _aliases(self, full_name: str) -> List[str]:
        norm = self._canonical_norm(full_name)
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
        norm_text = self._canonical_norm(text)
        tokens = set(norm_text.split())
        canonical_aliases = [self._canonical_norm(alias) for alias in aliases if alias]
        for alias in canonical_aliases:
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


    def _source_quality_score(self, query: Dict[str, str], source: Dict[str, str], event_profile: Dict[str, Any], relevance: str) -> float:
        text = self._source_relevance_text(source)
        url = safe_str(source.get("url") or source.get("link"))
        domain = self._extract_domain(safe_str(url or source.get("source")))
        bad_patterns = ["horoscope", "rasi palan", "forum", "youtube", "entertainment"]
        noise_penalty = 0.0
        if any(p in text for p in bad_patterns):
            noise_penalty += 0.45
        if "tennistemple.com" in domain and any(x in text for x in ["comment", "/comment", "comments"]):
            noise_penalty += 0.35

        entity_match = 0.0
        names = self._extract_names(safe_str(query.get("query")))
        if len(names) >= 2:
            a1,a2=self._aliases(names[0]),self._aliases(names[1])
            entity_match = 1.0 if self._has_name(text,a1) and self._has_name(text,a2) else 0.4 if (self._has_name(text,a1) or self._has_name(text,a2)) else 0.0
            is_h2h_source = self._is_trusted_h2h_domain(url) or any(x in text for x in ["h2h", "head to head", "head_to_head"])
            if is_h2h_source and entity_match < 1.0:
                noise_penalty += 0.35

        subtype = safe_str(event_profile.get("market_subtype")).lower()
        subtype_terms = {"set_total_games":["set 1","first set","over","under","games"],"price_target":["price","target","by","before"],"election_winner":["poll","election","vote"]}.get(subtype,[])
        subtype_match = 1.0 if subtype_terms and sum(1 for t in subtype_terms if t in text)>=2 else 0.5
        trust = 1.0 if self._is_trusted_h2h_domain(url) or any(d in domain for d in ["reuters","apnews","sec.gov","noaa.gov"]) else 0.45
        if "tennisabstract.com" in domain:
            trust = max(trust, 0.95)
        freshness = 0.8 if safe_str(source.get("published")) else 0.5
        if any(x in text for x in ["2018", "2019", "2020", "2021"]) and any(x in text for x in ["preview", "draw", "match page"]):
            noise_penalty += 0.25
        rel = {"high":1.0,"medium":0.7,"low":0.3,None:0.0}.get(relevance,0.0)
        return max(0.0, rel*0.25 + entity_match*0.2 + subtype_match*0.2 + trust*0.2 + freshness*0.15 - noise_penalty)

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
