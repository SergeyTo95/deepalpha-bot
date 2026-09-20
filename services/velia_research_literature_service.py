"""Read-only scholarly discovery for VELIA Research Center.

This layer intentionally supports only fixed scholarly metadata APIs. User supplied
URLs are never fetched. Literature discovery is durable, owner-scoped and
idempotent by normalized query so later research agents can reason over a stable
evidence snapshot rather than silently changing search results mid-run.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import quote

import requests

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_safety_service as safety
from services import velia_research_search_quality_service as search_quality
from services.velia_chat_service import _iso


CROSSREF_URL = "https://api.crossref.org/works"
EUROPE_PMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ALLOWED_ENDPOINTS = {CROSSREF_URL, EUROPE_PMC_URL}
MAX_HTTP_BYTES = 1024 * 1024
MAX_RESULTS = 20
MAX_SEARCHES_PER_MISSION = 50
MAX_SOURCES_PER_MISSION = 500
STALE_RUNNING_SECONDS = 600
MAX_QUERY_INPUT_CHARS = 6000
MAX_PROVIDER_QUERY_CHARS = 800
QUERY_INSTRUCTION_MARKERS = (
    " цель исследования:",
    " проведи полноцен",
    " проведи исслед",
    " выполни анализ",
    " финальный отчет",
    " финальный отчёт",
    " формат результата:",
    " research goal:",
    " methodology:",
    " requirements:",
    " final report:",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return center.enabled() and _env_bool("VELIA_RESEARCH_LITERATURE_ENABLED", False)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "providers": ["crossref", "europe_pmc"],
        "fixed_endpoints_only": True,
        "arbitrary_url_fetch": False,
        "max_results_per_search": MAX_RESULTS,
        "max_searches_per_mission": MAX_SEARCHES_PER_MISSION,
        "max_sources_per_mission": MAX_SOURCES_PER_MISSION,
        "search_quality_version": search_quality.QUALITY_VERSION,
        "llm_query_planning": True,
        "deterministic_relevance_gate": True,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _query(value: Any) -> str:
    """Validate the full research intent before deriving a provider query."""
    if not isinstance(value, str) or "\x00" in value or len(value) > MAX_QUERY_INPUT_CHARS:
        raise projects.ProjectError("invalid_literature_query")
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) < 2:
        raise projects.ProjectError("literature_query_required")
    return value


def _provider_query(value: str) -> str:
    """Bound a long research brief into a scholarly-search query.

    Mission goals intentionally allow substantially more detail than scholarly
    providers accept. Safety classification must run on the full goal first;
    only the provider-facing query is compacted here.
    """
    if len(value) <= MAX_PROVIDER_QUERY_CHARS:
        return value

    low = value.casefold()
    marker_positions = [
        low.find(marker)
        for marker in QUERY_INSTRUCTION_MARKERS
        if low.find(marker) >= 60
    ]
    cut = min(marker_positions) if marker_positions else MAX_PROVIDER_QUERY_CHARS
    cut = min(cut, MAX_PROVIDER_QUERY_CHARS)
    bounded = value[:cut].strip(" \t\r\n:;-")

    if not marker_positions and len(bounded) >= MAX_PROVIDER_QUERY_CHARS:
        sentence_ends = [
            match.end()
            for match in re.finditer(r"[.!?](?:\s|$)", bounded)
            if match.end() >= 120
        ]
        if sentence_ends:
            bounded = bounded[: sentence_ends[-1]].strip()

    if len(bounded) < 2:
        bounded = value[:MAX_PROVIDER_QUERY_CHARS].strip()
    return bounded[:MAX_PROVIDER_QUERY_CHARS]


def _hash_query(value: str) -> str:
    frozen = search_quality.QUALITY_VERSION + "|" + value.casefold()
    return hashlib.sha256(frozen.encode("utf-8")).hexdigest()


def _clean(value: Any, limit: int) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]{0,300}>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _year(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1500 <= number <= 2100 else None


def _doi(value: Any) -> str:
    text = _clean(value, 300)
    if not text or not re.fullmatch(r"10\.\d{4,9}/[^\s]+", text, flags=re.IGNORECASE):
        return ""
    return text


def _doi_url(doi: str) -> str:
    return "https://doi.org/" + quote(doi, safe="/:().-_;") if doi else ""


def _evidence_hint(title: str, types: List[str]) -> str:
    haystack = (" ".join([title, *types])).casefold()
    if "meta-analysis" in haystack or "meta analysis" in haystack or "метаанализ" in haystack:
        return "meta_analysis"
    if "systematic review" in haystack or "систематическ" in haystack:
        return "systematic_review"
    if any(token in haystack for token in (
        "randomized controlled trial", "randomised controlled trial", "randomized clinical trial",
        "randomised clinical trial", "рандомизирован",
    )):
        return "rct"
    if any(token in haystack for token in (
        "cohort", "case-control", "case control", "observational", "prospective study",
        "retrospective study", "когорт", "наблюдательн",
    )):
        return "observational"
    if any(token in haystack for token in ("in vitro", "cell line", "cell culture")):
        return "in_vitro"
    if any(token in haystack for token in ("animal study", "mouse model", "murine", "preclinical")):
        return "preclinical"
    return "unknown"


def _fetch_json(url: str, *, params: Dict[str, Any]) -> Dict[str, Any]:
    if url not in ALLOWED_ENDPOINTS:
        raise ValueError("research_literature_endpoint_not_allowed")
    headers = {
        "Accept": "application/json",
        "User-Agent": "VELIA-Research-Center/1.0",
    }
    with requests.get(
        url,
        params=params,
        headers=headers,
        timeout=(4, 12),
        allow_redirects=False,
        stream=True,
    ) as response:
        if response.status_code != 200:
            raise ValueError("research_literature_provider_unavailable")
        chunks: List[bytes] = []
        size = 0
        for chunk in response.iter_content(32768):
            size += len(chunk)
            if size > MAX_HTTP_BYTES:
                raise ValueError("research_literature_response_too_large")
            chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("research_literature_invalid_response") from exc
    if not isinstance(data, dict):
        raise ValueError("research_literature_invalid_response")
    return data


def _crossref(query: str, limit: int) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "query.bibliographic": query,
        "rows": min(limit, 12),
        "select": "DOI,title,author,published-print,published-online,created,type,container-title,is-referenced-by-count,abstract",
    }
    contact = str(os.getenv("VELIA_RESEARCH_CONTACT_EMAIL", "") or "").strip()
    if contact and len(contact) <= 200 and "@" in contact:
        params["mailto"] = contact
    payload = _fetch_json(CROSSREF_URL, params=params)
    message = payload.get("message")
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        raise ValueError("research_literature_invalid_response")

    result: List[Dict[str, Any]] = []
    for raw in items[: min(limit, 12)]:
        if not isinstance(raw, dict):
            continue
        titles = raw.get("title") if isinstance(raw.get("title"), list) else []
        title = _clean(titles[0] if titles else "", 600)
        if not title:
            continue
        doi = _doi(raw.get("DOI"))
        authors: List[str] = []
        for author in raw.get("author") if isinstance(raw.get("author"), list) else []:
            if not isinstance(author, dict):
                continue
            name = _clean(" ".join(filter(None, [author.get("given"), author.get("family")])), 160)
            if name:
                authors.append(name)
            if len(authors) >= 8:
                break

        published_year = None
        for field in ("published-online", "published-print", "created"):
            value = raw.get(field)
            parts = value.get("date-parts") if isinstance(value, dict) else None
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                published_year = _year(parts[0][0])
                if published_year:
                    break
        containers = raw.get("container-title") if isinstance(raw.get("container-title"), list) else []
        source_type = _clean(raw.get("type"), 80) or "work"
        external_id = doi or hashlib.sha256(
            (title.casefold() + "|" + str(published_year or "")).encode("utf-8")
        ).hexdigest()
        result.append({
            "provider": "crossref",
            "external_id": external_id,
            "doi": doi,
            "title": title,
            "authors": authors,
            "published_year": published_year,
            "venue": _clean(containers[0] if containers else "", 240),
            "source_type": source_type,
            "evidence_hint": _evidence_hint(title, [source_type]),
            "url": _doi_url(doi),
            "excerpt": _clean(raw.get("abstract"), 700),
            "citation_count": max(0, int(raw.get("is-referenced-by-count") or 0)),
        })
    return result


def _europe_pmc(query: str, limit: int) -> List[Dict[str, Any]]:
    payload = _fetch_json(EUROPE_PMC_URL, params={
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": min(limit, 12),
    })
    result_list = payload.get("resultList")
    items = result_list.get("result") if isinstance(result_list, dict) else None
    if not isinstance(items, list):
        raise ValueError("research_literature_invalid_response")

    result: List[Dict[str, Any]] = []
    for raw in items[: min(limit, 12)]:
        if not isinstance(raw, dict):
            continue
        title = _clean(raw.get("title"), 600)
        if not title:
            continue
        source = _clean(raw.get("source"), 20).upper()
        record_id = _clean(raw.get("id") or raw.get("pmid") or raw.get("pmcid"), 80)
        doi = _doi(raw.get("doi"))
        external_id = (source + ":" + record_id) if source and record_id else (
            doi or hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()
        )
        author_list = raw.get("authorList")
        raw_authors = author_list.get("author") if isinstance(author_list, dict) else []
        authors: List[str] = []
        if isinstance(raw_authors, list):
            for author in raw_authors:
                if not isinstance(author, dict):
                    continue
                name = _clean(author.get("fullName") or author.get("lastName"), 160)
                if name:
                    authors.append(name)
                if len(authors) >= 8:
                    break
        pub_types = raw.get("pubTypeList")
        types = pub_types.get("pubType") if isinstance(pub_types, dict) else []
        if not isinstance(types, list):
            types = [str(types)] if types else []
        year = _year(raw.get("pubYear"))
        safe_source = re.sub(r"[^A-Z0-9_-]", "", source)[:20]
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "", record_id)[:80]
        page_url = (
            f"https://europepmc.org/article/{safe_source}/{safe_id}"
            if safe_source and safe_id else _doi_url(doi)
        )
        citation_count = raw.get("citedByCount")
        try:
            citation_count = max(0, int(citation_count or 0))
        except (TypeError, ValueError):
            citation_count = 0
        result.append({
            "provider": "europe_pmc",
            "external_id": external_id,
            "doi": doi,
            "title": title,
            "authors": authors,
            "published_year": year,
            "venue": _clean(raw.get("journalTitle"), 240),
            "source_type": _clean(types[0] if types else "publication", 100),
            "evidence_hint": _evidence_hint(title, [_clean(x, 100) for x in types]),
            "url": page_url,
            "excerpt": _clean(raw.get("abstractText"), 1000),
            "citation_count": citation_count,
        })
    return result


def _dedupe(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    seen = set()
    output: List[Dict[str, Any]] = []
    for row in rows:
        doi = str(row.get("doi") or "").casefold()
        title = re.sub(r"\W+", "", str(row.get("title") or "").casefold())
        key = ("doi", doi) if doi else ("title", title)
        if not key[1] or key in seen:
            continue
        seen.add(key)
        output.append(row)
        if len(output) >= limit:
            break
    return output


def live_discover(query: str, max_results: int = 12) -> Dict[str, Any]:
    """Read-only fresh discovery for a frozen Living Research query.

    Unlike collect(), this does not mutate or replace the original systematic-review
    search snapshot. It only queries the existing fixed scholarly endpoints and
    returns a fresh, bounded metadata snapshot for comparison.
    """
    if not enabled():
        raise projects.ProjectError("research_literature_disabled", 503)
    query = _query(query)
    if type(max_results) is not int or not 1 <= max_results <= MAX_RESULTS:
        raise projects.ProjectError("invalid_max_results")
    decision = safety.classify(query, phase="literature")
    if decision["decision"] == "blocked":
        raise projects.ProjectError("research_literature_safety_blocked", 403)
    provider_query = _provider_query(query)

    rows: List[Dict[str, Any]] = []
    providers: List[str] = []
    errors: List[str] = []
    for provider, fetcher in (("crossref", _crossref), ("europe_pmc", _europe_pmc)):
        try:
            provider_rows = fetcher(provider_query, max_results)
            rows.extend(provider_rows)
            providers.append(provider)
        except Exception:
            errors.append(provider)
    if not rows and errors:
        raise projects.ProjectError("research_literature_unavailable", 502)
    rows = search_quality.rank_relevant(rows, provider_query, "general", max_results)
    if not rows:
        raise projects.ProjectError("research_literature_no_relevant_sources", 502)
    return {
        "query": provider_query,
        "query_hash": _hash_query(query),
        "query_compacted": provider_query != query,
        "partial": bool(errors),
        "provider_gaps": errors,
        "providers": providers,
        "quality_version": search_quality.QUALITY_VERSION,
        "safety": decision,
        "sources": rows,
    }


def verify_doi_status(value: str) -> Dict[str, Any]:
    """Verify DOI registration and post-publication updates through fixed Crossref metadata.

    This is metadata verification only. Absence of an update is not proof that a
    publication has never been corrected or retracted outside indexed metadata.
    """
    if not enabled():
        raise projects.ProjectError("research_literature_disabled", 503)
    doi = _doi(value)
    if not doi:
        raise projects.ProjectError("invalid_research_doi")
    params: Dict[str, Any] = {
        "filter": "doi:" + doi,
        "rows": 1,
    }
    contact = str(os.getenv("VELIA_RESEARCH_CONTACT_EMAIL", "") or "").strip()
    if contact and len(contact) <= 200 and "@" in contact:
        params["mailto"] = contact
    try:
        payload = _fetch_json(CROSSREF_URL, params=params)
    except Exception as exc:
        raise projects.ProjectError("research_citation_verification_unavailable", 502) from exc
    message = payload.get("message")
    items = message.get("items") if isinstance(message, dict) else None
    raw = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if raw is None or _doi(raw.get("DOI")).casefold() != doi.casefold():
        return {
            "doi": doi,
            "registered": False,
            "status": "unverified",
            "updates": [],
            "provider": "crossref",
            "coverage_note": (
                "No exact Crossref DOI record was returned. This is not proof that the work does not exist."
            ),
        }

    updates: List[Dict[str, str]] = []
    raw_updates = raw.get("update-to")
    if isinstance(raw_updates, list):
        for item in raw_updates[:20]:
            if not isinstance(item, dict):
                continue
            update_type = _clean(item.get("type"), 80).casefold().replace(" ", "_")
            if not update_type:
                continue
            updates.append({
                "type": update_type,
                "doi": _doi(item.get("DOI")),
                "label": _clean(item.get("label"), 160),
                "source": _clean(item.get("source"), 80),
            })

    relation = raw.get("relation")
    if isinstance(relation, dict):
        for relation_type, values in relation.items():
            normalized_type = _clean(relation_type, 100).casefold().replace(" ", "_")
            if not any(token in normalized_type for token in ("retract", "correct", "concern", "withdraw", "reinstate", "update")):
                continue
            values = values if isinstance(values, list) else [values]
            for item in values[:20]:
                if isinstance(item, dict):
                    target = _doi(item.get("id") or item.get("DOI"))
                else:
                    target = _doi(item)
                updates.append({
                    "type": normalized_type,
                    "doi": target,
                    "label": "",
                    "source": "crossref_relation",
                })

    deduped: List[Dict[str, str]] = []
    seen = set()
    for item in updates:
        key = (item["type"], item["doi"], item["label"], item["source"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    updates = deduped[:20]
    types = {item["type"] for item in updates}
    joined = " ".join(types)
    if "retract" in joined or "withdraw" in joined:
        state = "retracted_or_withdrawn"
    elif "expression" in joined and "concern" in joined:
        state = "expression_of_concern"
    elif "correct" in joined:
        state = "corrected"
    elif updates:
        state = "updated"
    else:
        state = "verified_no_indexed_update"

    metadata = {
        "doi": doi,
        "title": _clean((raw.get("title") or [""])[0] if isinstance(raw.get("title"), list) else raw.get("title"), 600),
        "type": _clean(raw.get("type"), 100),
        "publisher": _clean(raw.get("publisher"), 240),
        "updates": updates,
    }
    return {
        "doi": doi,
        "registered": True,
        "status": state,
        "updates": updates,
        "provider": "crossref",
        "metadata_hash": hashlib.sha256(_json(metadata).encode("utf-8")).hexdigest(),
        "coverage_note": (
            "Crossref post-publication metadata is checked, including indexed update/retraction metadata. "
            "No indexed update is not proof that no external correction or retraction exists."
        ),
    }


def ensure_tables() -> None:
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_literature_queries (
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            query_hash TEXT NOT NULL,
            query_text TEXT NOT NULL,
            status TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            providers_json TEXT NOT NULL,
            quality_version TEXT NOT NULL DEFAULT 'legacy',
            result_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY(mission_id,user_id,query_hash),
            CHECK(status IN ('running','completed','failed')),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("ALTER TABLE velia_research_literature_queries ADD COLUMN IF NOT EXISTS quality_version TEXT NOT NULL DEFAULT 'legacy'")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_sources (
            source_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            query_hash TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            doi TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            published_year INTEGER NULL,
            venue TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL,
            evidence_hint TEXT NOT NULL DEFAULT 'unknown',
            source_url TEXT NOT NULL DEFAULT '',
            excerpt TEXT NOT NULL DEFAULT '',
            citation_count INTEGER NOT NULL DEFAULT 0,
            metadata_hash TEXT NOT NULL,
            quality_version TEXT NOT NULL DEFAULT 'legacy',
            retrieved_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(mission_id,user_id,query_hash,provider,external_id),
            FOREIGN KEY(mission_id,user_id,query_hash)
                REFERENCES velia_research_literature_queries(mission_id,user_id,query_hash)
                ON DELETE CASCADE)""")
        cur.execute("ALTER TABLE velia_research_sources ADD COLUMN IF NOT EXISTS quality_version TEXT NOT NULL DEFAULT 'legacy'")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_velia_research_sources_mission ON velia_research_sources(mission_id,user_id,retrieved_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_velia_research_sources_doi ON velia_research_sources(doi) WHERE doi <> ''")


def _row_source(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["source_id"],
        "provider": row["provider"],
        "external_id": row["external_id"],
        "doi": row["doi"],
        "title": row["title"],
        "authors": json.loads(row["authors_json"]),
        "published_year": row["published_year"],
        "venue": row["venue"],
        "source_type": row["source_type"],
        "evidence_hint": row["evidence_hint"],
        "url": row["source_url"],
        "excerpt": row["excerpt"],
        "citation_count": row["citation_count"],
        "quality_version": row.get("quality_version") or "legacy",
        "retrieved_at": _iso(row["retrieved_at"]),
    }


def _sources_for_query(cur, user_id: int, mission_id: str, query_hash: str) -> List[Dict[str, Any]]:
    cur.execute("""SELECT * FROM velia_research_sources
        WHERE mission_id=%s AND user_id=%s AND query_hash=%s
        ORDER BY ordinal ASC,source_id ASC""", (str(mission_id), int(user_id), query_hash))
    return [_row_source(row) for row in cur.fetchall()]


def _completed_query_cache(
    user_id: int,
    mission_id: str,
    query_hash: str,
) -> Dict[str, Any] | None:
    with projects.transaction() as cur:
        cur.execute("""SELECT query_text,quality_version FROM velia_research_literature_queries
            WHERE mission_id=%s AND user_id=%s AND query_hash=%s AND status='completed'""",
            (str(mission_id), int(user_id), query_hash))
        row = cur.fetchone()
        if not row or row.get("quality_version") != search_quality.QUALITY_VERSION:
            return None
        return {
            "query": row["query_text"],
            "sources": _sources_for_query(cur, user_id, mission_id, query_hash),
        }


def _claim(user_id: int, mission_id: str, query: str, query_hash: str, decision: Dict[str, Any]) -> List[Dict[str, Any]] | None:
    with projects.transaction(user_id) as cur:
        cur.execute("SELECT status FROM velia_research_missions WHERE mission_id=%s AND user_id=%s FOR UPDATE",
                    (str(mission_id), int(user_id)))
        mission = cur.fetchone()
        if not mission:
            raise projects.ProjectError("research_mission_not_found", 404)
        if mission["status"] in {"blocked", "cancelled", "completed"}:
            raise projects.ProjectError("research_mission_not_active", 409)

        cur.execute("""SELECT *, EXTRACT(EPOCH FROM (NOW()-updated_at)) AS age_seconds
            FROM velia_research_literature_queries
            WHERE mission_id=%s AND user_id=%s AND query_hash=%s FOR UPDATE""",
            (str(mission_id), int(user_id), query_hash))
        existing = cur.fetchone()
        if existing:
            if existing["status"] == "completed":
                return _sources_for_query(cur, user_id, mission_id, query_hash)
            if existing["status"] == "running" and float(existing.get("age_seconds") or 0) < STALE_RUNNING_SECONDS:
                raise projects.ProjectError("literature_search_in_progress", 409)
            cur.execute("""UPDATE velia_research_literature_queries
                SET status='running',safety_json=%s,error_code=NULL,updated_at=NOW()
                WHERE mission_id=%s AND user_id=%s AND query_hash=%s""",
                (_json(decision), str(mission_id), int(user_id), query_hash))
            cur.execute("""DELETE FROM velia_research_sources
                WHERE mission_id=%s AND user_id=%s AND query_hash=%s""",
                (str(mission_id), int(user_id), query_hash))
            return None

        cur.execute("SELECT COUNT(*) AS count FROM velia_research_literature_queries WHERE mission_id=%s AND user_id=%s",
                    (str(mission_id), int(user_id)))
        if int(cur.fetchone()["count"]) >= MAX_SEARCHES_PER_MISSION:
            raise projects.ProjectError("literature_search_limit_exceeded", 429)
        cur.execute("SELECT COUNT(*) AS count FROM velia_research_sources WHERE mission_id=%s AND user_id=%s",
                    (str(mission_id), int(user_id)))
        if int(cur.fetchone()["count"]) >= MAX_SOURCES_PER_MISSION:
            raise projects.ProjectError("literature_source_limit_exceeded", 429)
        cur.execute("""INSERT INTO velia_research_literature_queries(
            mission_id,user_id,query_hash,query_text,status,safety_json,providers_json,quality_version)
            VALUES(%s,%s,%s,%s,'running',%s,'[]',%s)""",
            (
                str(mission_id), int(user_id), query_hash, query, _json(decision),
                search_quality.QUALITY_VERSION,
            ))
    return None


def _mark_failed(user_id: int, mission_id: str, query_hash: str, code: str) -> None:
    with projects.transaction(user_id) as cur:
        cur.execute("""UPDATE velia_research_literature_queries
            SET status='failed',error_code=%s,updated_at=NOW()
            WHERE mission_id=%s AND user_id=%s AND query_hash=%s""",
            (str(code)[:120], str(mission_id), int(user_id), query_hash))
        center._event(cur, str(mission_id), user_id, "literature_search_failed",
                      {"query_hash": query_hash, "error": str(code)[:120]})


def collect(user_id: int, mission_id: str, query: str = "", max_results: int = 12) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_literature_disabled", 503)
    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    query = _query(query or mission["goal"])
    if type(max_results) is not int or not 1 <= max_results <= MAX_RESULTS:
        raise projects.ProjectError("invalid_max_results")

    decision = safety.classify(query, phase="literature")
    if decision["decision"] == "blocked":
        raise projects.ProjectError("research_safety_blocked", 403)

    query_hash = _hash_query(query)
    completed_cache = _completed_query_cache(user_id, mission_id, query_hash)
    if completed_cache is not None:
        provider_query = str(completed_cache["query"])
        return {
            "query_hash": query_hash,
            "query": provider_query,
            "query_compacted": provider_query != query,
            "query_model_planned": provider_query != _provider_query(query),
            "quality_version": search_quality.QUALITY_VERSION,
            "cached": True,
            "partial": False,
            "safety": decision,
            "sources": completed_cache["sources"],
        }

    fallback_query = _provider_query(query)
    plan = search_quality.plan_query(
        full_intent=query,
        domain=mission["domain"],
        user_id=int(user_id),
        mission_id=str(mission_id),
        fallback_query=fallback_query,
    )
    provider_query = str(plan.get("query") or fallback_query)[:search_quality.MAX_CANONICAL_QUERY_CHARS]
    provider_decision = safety.classify(provider_query, phase="literature")
    if provider_decision["decision"] == "blocked":
        raise projects.ProjectError("research_safety_blocked", 403)
    cached = _claim(user_id, mission_id, provider_query, query_hash, decision)
    if cached is not None:
        return {
            "query_hash": query_hash,
            "query": provider_query,
            "query_compacted": provider_query != query,
            "query_model_planned": bool(plan.get("model_planned")),
            "quality_version": search_quality.QUALITY_VERSION,
            "cached": True,
            "partial": False,
            "safety": decision,
            "sources": cached,
        }

    provider_rows: List[Dict[str, Any]] = []
    providers: List[str] = []
    errors: List[str] = []
    try:
        if mission["domain"] in {"medicine", "biology"}:
            providers.append("europe_pmc")
            try:
                provider_rows.extend(_europe_pmc(provider_query, max_results))
            except ValueError:
                errors.append("europe_pmc_unavailable")
        providers.append("crossref")
        try:
            provider_rows.extend(_crossref(provider_query, max_results))
        except ValueError:
            errors.append("crossref_unavailable")

        rows = search_quality.rank_relevant(
            provider_rows,
            provider_query,
            mission["domain"],
            max_results,
        )
        if not rows:
            code = "research_literature_no_relevant_sources"
            _mark_failed(user_id, mission_id, query_hash, code)
            raise projects.ProjectError(code, 502)

        now = datetime.now(timezone.utc)
        with projects.transaction(user_id) as cur:
            cur.execute("SELECT status FROM velia_research_missions WHERE mission_id=%s AND user_id=%s FOR UPDATE",
                        (str(mission_id), int(user_id)))
            current = cur.fetchone()
            if not current:
                raise projects.ProjectError("research_mission_not_found", 404)
            if current["status"] in {"blocked", "cancelled", "completed"}:
                raise projects.ProjectError("research_mission_not_active", 409)

            cur.execute("SELECT COUNT(*) AS count FROM velia_research_sources WHERE mission_id=%s AND user_id=%s",
                        (str(mission_id), int(user_id)))
            remaining = max(0, MAX_SOURCES_PER_MISSION - int(cur.fetchone()["count"]))
            if remaining <= 0:
                raise projects.ProjectError("literature_source_limit_exceeded", 429)
            rows = rows[:remaining]

            for ordinal, row in enumerate(rows):
                metadata_hash = hashlib.sha256(_json(row).encode("utf-8")).hexdigest()
                source_id = hashlib.sha256(
                    (str(mission_id) + "|" + query_hash + "|" + row["provider"] + "|" + row["external_id"]).encode("utf-8")
                ).hexdigest()
                cur.execute("""INSERT INTO velia_research_sources(
                    source_id,mission_id,user_id,query_hash,ordinal,provider,external_id,doi,title,
                    authors_json,published_year,venue,source_type,evidence_hint,source_url,excerpt,
                    citation_count,metadata_hash,quality_version,retrieved_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(mission_id,user_id,query_hash,provider,external_id) DO NOTHING""",
                    (
                        source_id, str(mission_id), int(user_id), query_hash, ordinal,
                        row["provider"], row["external_id"], row["doi"], row["title"],
                        _json(row["authors"]), row["published_year"], row["venue"], row["source_type"],
                        row["evidence_hint"], row["url"], row["excerpt"], row["citation_count"],
                        metadata_hash, search_quality.QUALITY_VERSION, now,
                    ))
            cur.execute("""UPDATE velia_research_literature_queries
                SET status='completed',providers_json=%s,result_count=%s,error_code=NULL,updated_at=NOW()
                WHERE mission_id=%s AND user_id=%s AND query_hash=%s""",
                (_json(providers), len(rows), str(mission_id), int(user_id), query_hash))
            if current["status"] == "planned":
                cur.execute("""UPDATE velia_research_missions SET status='literature',updated_at=NOW()
                    WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
            center._event(cur, str(mission_id), user_id, "literature_search_completed", {
                "query_hash": query_hash,
                "providers": providers,
                "result_count": len(rows),
                "partial": bool(errors),
                "provider_gaps": errors,
                "quality_version": search_quality.QUALITY_VERSION,
                "query_model_planned": bool(plan.get("model_planned")),
                "safety": decision,
            })
            saved = _sources_for_query(cur, user_id, mission_id, query_hash)

        return {
            "query_hash": query_hash,
            "query": provider_query,
            "query_compacted": provider_query != query,
            "query_model_planned": bool(plan.get("model_planned")),
            "quality_version": search_quality.QUALITY_VERSION,
            "cached": False,
            "partial": bool(errors),
            "provider_gaps": errors,
            "safety": decision,
            "sources": saved,
        }
    except projects.ProjectError:
        raise
    except Exception:
        _mark_failed(user_id, mission_id, query_hash, "research_literature_internal_error")
        raise projects.ProjectError("research_literature_unavailable", 502)


def list_sources(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > MAX_SOURCES_PER_MISSION:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT EXISTS(
            SELECT 1 FROM velia_research_literature_queries
            WHERE mission_id=%s AND user_id=%s AND quality_version=%s
        ) AS has_quality""", (
            str(mission_id), int(user_id), search_quality.QUALITY_VERSION,
        ))
        has_quality = bool(cur.fetchone()["has_quality"])
        quality_clause = " AND quality_version=%s" if has_quality else ""
        params = [str(mission_id), int(user_id)]
        if has_quality:
            params.append(search_quality.QUALITY_VERSION)
        params.append(offset)
        cur.execute(f"""SELECT * FROM velia_research_sources
            WHERE mission_id=%s AND user_id=%s{quality_clause}
            ORDER BY retrieved_at DESC,ordinal ASC,source_id ASC
            LIMIT 51 OFFSET %s""", tuple(params))
        rows = list(cur.fetchall())
        return {
            "sources": [_row_source(row) for row in rows[:50]],
            "next_offset": offset + 50 if len(rows) > 50 else None,
        }
