"""Preregistered systematic-review workflow and deterministic evidence graph for VELIA.

The review protocol must be frozen before any literature search for the mission.
Only fixed scholarly providers from the existing Literature Engine are used.
Screening is append-only and source-backed; VELIA never fetches arbitrary URLs,
opens user files, executes code, or treats a PRISMA-like accounting snapshot as
a substitute for methodological judgment.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_literature_service as literature
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


REVIEW_VERSION = 1
FRAMEWORKS = {"pico", "peco", "general"}
ALLOWED_STUDY_DESIGNS = {
    "rct", "cohort", "case_control", "cross_sectional", "preclinical",
    "systematic_review", "meta_analysis", "other",
}
ALLOWED_EVIDENCE_HINTS = {
    "meta_analysis", "systematic_review", "rct", "observational",
    "preclinical", "in_vitro", "unknown",
}
SCREENING_STAGES = {"title_abstract", "eligibility"}
SCREENING_DECISIONS = {"include", "exclude", "duplicate"}
EXCLUSION_REASONS = {
    "population_mismatch",
    "intervention_or_exposure_mismatch",
    "comparator_mismatch",
    "outcome_mismatch",
    "study_design_mismatch",
    "date_out_of_range",
    "duplicate_publication",
    "insufficient_information",
    "not_primary_or_eligible_evidence",
    "other_preregistered_reason",
}
MAX_PROTOCOLS_PER_MISSION = 10
MAX_QUERIES = 8
MAX_CRITERIA = 12
MAX_SCREENING_ACTIONS_PER_SOURCE = 4
MAX_SEARCH_ATTEMPTS = 3
MAX_GRAPH_NODES = 1200
MAX_GRAPH_EDGES = 2400


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return (
        center.enabled()
        and literature.enabled()
        and _env_bool("VELIA_RESEARCH_SYSTEMATIC_REVIEW_ENABLED", False)
    )


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "review_version": REVIEW_VERSION,
        "frameworks": sorted(FRAMEWORKS),
        "protocol_must_precede_literature_search": True,
        "immutable_search_plan": True,
        "append_only_screening": True,
        "duplicate_publication_detection": True,
        "prisma_like_flow_accounting": True,
        "evidence_graph": True,
        "meta_analysis_requires_final_include_when_enabled": True,
        "full_text_fetch_by_velia": False,
        "arbitrary_code": False,
        "arbitrary_shell": False,
        "arbitrary_file_access": False,
        "arbitrary_url_fetch": False,
        "dynamic_expression_eval": False,
    }


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int, code: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise projects.ProjectError(code)
        return ""
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    out = re.sub(r"\s+", " ", value).strip()
    if required and not out:
        raise projects.ProjectError(code)
    return out


def _text_list(value: Any, code: str, *, required: bool = False) -> List[str]:
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > MAX_CRITERIA:
        raise projects.ProjectError(code)
    out: List[str] = []
    for item in value:
        clean = _text(item, 500, code, required=True)
        if clean not in out:
            out.append(clean)
    if required and not out:
        raise projects.ProjectError(code)
    return out


def _normalize_query(value: Any) -> str:
    return _text(value, 800, "invalid_research_review_search_query", required=True)


def _query_hash(value: str) -> str:
    return hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()


def _publication_fingerprint(row: Dict[str, Any]) -> str:
    doi = str(row.get("doi") or "").strip().casefold()
    if doi:
        return _sha({"doi": doi})
    title = re.sub(r"\W+", "", str(row.get("title") or "").casefold())
    return _sha({
        "title": title,
        "published_year": row.get("published_year"),
    })


def _question(framework: str, value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise projects.ProjectError("invalid_research_review_question")
    if framework == "pico":
        expected = {"population", "intervention", "comparator", "outcome"}
    elif framework == "peco":
        expected = {"population", "exposure", "comparator", "outcome"}
    else:
        expected = {"question"}
    if set(value) != expected:
        raise projects.ProjectError("invalid_research_review_question")
    return {
        key: _text(value.get(key), 600, "invalid_research_review_question", required=True)
        for key in sorted(expected)
    }


def _criteria(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "inclusion", "exclusion", "study_designs", "evidence_hints", "year_from", "year_to"
    }:
        raise projects.ProjectError("invalid_research_review_criteria")
    inclusion = _text_list(value.get("inclusion"), "invalid_research_review_criteria", required=True)
    exclusion = _text_list(value.get("exclusion"), "invalid_research_review_criteria")
    designs = value.get("study_designs", [])
    hints = value.get("evidence_hints", [])
    if (
        not isinstance(designs, list) or len(designs) > len(ALLOWED_STUDY_DESIGNS)
        or any(str(item) not in ALLOWED_STUDY_DESIGNS for item in designs)
    ):
        raise projects.ProjectError("invalid_research_review_criteria")
    if (
        not isinstance(hints, list) or len(hints) > len(ALLOWED_EVIDENCE_HINTS)
        or any(str(item) not in ALLOWED_EVIDENCE_HINTS for item in hints)
    ):
        raise projects.ProjectError("invalid_research_review_criteria")
    year_from = value.get("year_from")
    year_to = value.get("year_to")
    if year_from is not None and (isinstance(year_from, bool) or not isinstance(year_from, int) or not 1500 <= year_from <= 2100):
        raise projects.ProjectError("invalid_research_review_criteria")
    if year_to is not None and (isinstance(year_to, bool) or not isinstance(year_to, int) or not 1500 <= year_to <= 2100):
        raise projects.ProjectError("invalid_research_review_criteria")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise projects.ProjectError("invalid_research_review_criteria")
    return {
        "inclusion": inclusion,
        "exclusion": exclusion,
        "study_designs": sorted(set(str(item) for item in designs)),
        "evidence_hints": sorted(set(str(item) for item in hints)),
        "year_from": year_from,
        "year_to": year_to,
    }


def _search_plan(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"queries", "max_results_per_query"}:
        raise projects.ProjectError("invalid_research_review_search_plan")
    raw_queries = value.get("queries")
    if not isinstance(raw_queries, list) or not 1 <= len(raw_queries) <= MAX_QUERIES:
        raise projects.ProjectError("invalid_research_review_search_plan")
    queries: List[str] = []
    for raw in raw_queries:
        query = _normalize_query(raw)
        if query.casefold() not in {item.casefold() for item in queries}:
            queries.append(query)
    max_results = value.get("max_results_per_query", 12)
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= literature.MAX_RESULTS:
        raise projects.ProjectError("invalid_research_review_search_plan")
    return {
        "queries": queries,
        "query_hashes": [_query_hash(query) for query in queries],
        "max_results_per_query": max_results,
        "providers": list(literature.status().get("providers", [])),
        "fixed_endpoints_only": True,
    }


def ensure_tables() -> None:
    claims.ensure_tables()
    literature.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_review_protocols (
            review_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            claim_id TEXT NULL,
            framework TEXT NOT NULL,
            question_json TEXT NOT NULL,
            criteria_json TEXT NOT NULL,
            search_plan_json TEXT NOT NULL,
            protocol_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(mission_id,user_id,protocol_hash),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_review_protocols
            ON velia_research_review_protocols(mission_id,user_id,created_at DESC)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_review_search_runs (
            search_run_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            attempt INTEGER NOT NULL,
            protocol_hash TEXT NOT NULL,
            result_json TEXT NOT NULL,
            result_hash TEXT NOT NULL,
            partial BOOLEAN NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(review_id,user_id,attempt),
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_screening_actions (
            action_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            source_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            note TEXT NOT NULL,
            publication_fingerprint TEXT NOT NULL,
            canonical_source_id TEXT NULL,
            action_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_screening_actions
            ON velia_research_screening_actions(review_id,user_id,source_id,stage,created_at DESC)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_evidence_graph_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            graph_hash TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(review_id,user_id,graph_hash),
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_evidence_graph
            ON velia_research_evidence_graph_snapshots(review_id,user_id,created_at DESC)""")


def _protocol_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["review_id"],
        "mission_id": row["mission_id"],
        "claim_id": row["claim_id"],
        "framework": row["framework"],
        "question": json.loads(row["question_json"]),
        "criteria": json.loads(row["criteria_json"]),
        "search_plan": json.loads(row["search_plan_json"]),
        "protocol_hash": row["protocol_hash"],
        "safety": json.loads(row["safety_json"]),
        "created_at": _iso(row["created_at"]),
    }


def create_protocol(user_id: int, mission_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_systematic_review_disabled", 503)
    if not isinstance(data, dict) or set(data) - {
        "claim_id", "framework", "question", "criteria", "search_plan"
    }:
        raise projects.ProjectError("invalid_research_review_protocol")
    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    if mission["safety"].get("read_only_only"):
        raise projects.ProjectError("research_systematic_review_read_only", 403)

    claim_id = data.get("claim_id")
    if claim_id is not None:
        if not isinstance(claim_id, str) or not claim_id or len(claim_id) > 128:
            raise projects.ProjectError("invalid_research_review_protocol")
        claim = claims.get_claim(user_id, claim_id)
        if claim["mission_id"] != str(mission_id):
            raise projects.ProjectError("research_review_claim_mission_mismatch", 409)

    framework = str(data.get("framework") or "")
    if framework not in FRAMEWORKS:
        raise projects.ProjectError("invalid_research_review_protocol")
    question = _question(framework, data.get("question"))
    criteria = _criteria(data.get("criteria"))
    search_plan = _search_plan(data.get("search_plan"))

    with projects.transaction() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_literature_queries
            WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
        if int(cur.fetchone()["n"]) > 0:
            raise projects.ProjectError("research_review_protocol_must_precede_search", 409)
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_sources
            WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
        if int(cur.fetchone()["n"]) > 0:
            raise projects.ProjectError("research_review_protocol_must_precede_search", 409)
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_review_protocols
            WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
        if int(cur.fetchone()["n"]) >= MAX_PROTOCOLS_PER_MISSION:
            raise projects.ProjectError("research_review_protocol_limit_reached", 409)

    frozen = {
        "review_version": REVIEW_VERSION,
        "mission_id": str(mission_id),
        "claim_id": claim_id,
        "framework": framework,
        "question": question,
        "criteria": criteria,
        "search_plan": search_plan,
    }
    decision = safety.classify(_json(frozen), phase="literature")
    if decision["decision"] == "blocked":
        raise projects.ProjectError("research_review_protocol_safety_blocked", 403)
    protocol_hash = _sha(frozen)
    review_id = _sha({"mission_id": str(mission_id), "protocol_hash": protocol_hash})
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_review_protocols(
            review_id,mission_id,user_id,claim_id,framework,question_json,criteria_json,
            search_plan_json,protocol_hash,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(mission_id,user_id,protocol_hash) DO NOTHING""",
            (
                review_id, str(mission_id), int(user_id), claim_id, framework,
                _json(question), _json(criteria), _json(search_plan),
                protocol_hash, _json(decision),
            ))
        cur.execute("""SELECT * FROM velia_research_review_protocols
            WHERE review_id=%s AND user_id=%s""", (review_id, int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_review_protocol_state_missing", 500)
        center._event(cur, str(mission_id), user_id, "research_systematic_review_preregistered", {
            "review_id": review_id,
            "protocol_hash": protocol_hash,
            "framework": framework,
            "claim_id": claim_id,
            "query_count": len(search_plan["queries"]),
        })
        return _protocol_row(row)


def get_protocol(user_id: int, review_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_review_protocols
            WHERE review_id=%s AND user_id=%s""", (str(review_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_review_protocol_not_found", 404)
    return _protocol_row(row)


def list_protocols(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 200:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_review_protocols
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,review_id DESC LIMIT 21 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "reviews": [_protocol_row(row) for row in rows[:20]],
        "next_offset": offset + 20 if len(rows) > 20 else None,
    }


def _review_sources(user_id: int, review: Dict[str, Any]) -> List[Dict[str, Any]]:
    hashes = set(review["search_plan"]["query_hashes"])
    with projects.transaction() as cur:
        cur.execute("""SELECT source_id,query_hash,provider,external_id,doi,title,published_year,
                   source_type,evidence_hint,retrieved_at
            FROM velia_research_sources
            WHERE mission_id=%s AND user_id=%s
            ORDER BY retrieved_at ASC,ordinal ASC,source_id ASC""",
            (review["mission_id"], int(user_id)))
        rows = [dict(row) for row in cur.fetchall()]
    output: List[Dict[str, Any]] = []
    for row in rows:
        if row["query_hash"] not in hashes:
            continue
        row["publication_fingerprint"] = _publication_fingerprint(row)
        output.append(row)
    return output


def execute_search(user_id: int, review_id: str) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_systematic_review_disabled", 503)
    review = get_protocol(user_id, review_id)
    mission = center.get_mission(user_id, review["mission_id"])
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)

    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_review_search_runs
            WHERE review_id=%s AND user_id=%s ORDER BY attempt DESC LIMIT 1""",
            (review["id"], int(user_id)))
        previous = cur.fetchone()
        if previous and not bool(previous["partial"]):
            return {
                "cached": True,
                "search_run": json.loads(previous["result_json"]),
                "result_hash": previous["result_hash"],
                "attempt": int(previous["attempt"]),
            }
        attempt = int(previous["attempt"]) + 1 if previous else 1
        if attempt > MAX_SEARCH_ATTEMPTS:
            raise projects.ProjectError("research_review_search_retry_limit_reached", 409)

    outcomes: List[Dict[str, Any]] = []
    partial = False
    for query in review["search_plan"]["queries"]:
        try:
            result = literature.collect(
                user_id,
                review["mission_id"],
                query,
                review["search_plan"]["max_results_per_query"],
            )
            outcomes.append({
                "query": query,
                "query_hash": result["query_hash"],
                "partial": bool(result.get("partial")),
                "provider_gaps": list(result.get("provider_gaps", [])),
                "source_ids": [item["id"] for item in result.get("sources", [])],
            })
            partial = partial or bool(result.get("partial"))
        except projects.ProjectError as exc:
            outcomes.append({
                "query": query,
                "query_hash": _query_hash(query),
                "partial": True,
                "error": exc.code,
                "source_ids": [],
            })
            partial = True

    sources = _review_sources(user_id, review)
    fingerprints: Dict[str, List[str]] = {}
    for source in sources:
        fingerprints.setdefault(source["publication_fingerprint"], []).append(source["source_id"])
    duplicate_records = sum(max(0, len(ids) - 1) for ids in fingerprints.values())
    snapshot = {
        "review_version": REVIEW_VERSION,
        "review_id": review["id"],
        "protocol_hash": review["protocol_hash"],
        "attempt": attempt,
        "outcomes": outcomes,
        "identified_source_ids": [row["source_id"] for row in sources],
        "identified_records": len(sources),
        "unique_publications": len(fingerprints),
        "duplicate_records": duplicate_records,
        "partial": partial,
    }
    result_hash = _sha(snapshot)
    run_id = _sha({
        "review_id": review["id"],
        "attempt": attempt,
        "result_hash": result_hash,
    })
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_review_search_runs(
            search_run_id,review_id,mission_id,user_id,attempt,protocol_hash,
            result_json,result_hash,partial)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                run_id, review["id"], review["mission_id"], int(user_id), attempt,
                review["protocol_hash"], _json(snapshot), result_hash, bool(partial),
            ))
        center._event(cur, review["mission_id"], user_id, "research_systematic_review_search_completed", {
            "review_id": review["id"],
            "search_run_id": run_id,
            "attempt": attempt,
            "identified_records": len(sources),
            "unique_publications": len(fingerprints),
            "duplicate_records": duplicate_records,
            "partial": partial,
            "result_hash": result_hash,
        })
    return {
        "cached": False,
        "search_run": snapshot,
        "result_hash": result_hash,
        "attempt": attempt,
    }


def _source(user_id: int, review: Dict[str, Any], source_id: str) -> Dict[str, Any]:
    if not isinstance(source_id, str) or not source_id or len(source_id) > 128:
        raise projects.ProjectError("invalid_research_review_source")
    rows = _review_sources(user_id, review)
    by_id = {row["source_id"]: row for row in rows}
    if source_id not in by_id:
        raise projects.ProjectError("research_review_source_not_in_preregistered_search", 409)
    return by_id[source_id]


def _canonical_source(sources: List[Dict[str, Any]], fingerprint: str) -> str:
    matches = [row["source_id"] for row in sources if row["publication_fingerprint"] == fingerprint]
    if not matches:
        raise projects.ProjectError("research_review_source_not_found", 404)
    return matches[0]


def _latest_actions(user_id: int, review_id: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    with projects.transaction() as cur:
        cur.execute("""SELECT DISTINCT ON (source_id,stage) *
            FROM velia_research_screening_actions
            WHERE review_id=%s AND user_id=%s
            ORDER BY source_id,stage,created_at DESC,action_id DESC""",
            (str(review_id), int(user_id)))
        rows = cur.fetchall()
    return {(row["source_id"], row["stage"]): dict(row) for row in rows}


def _screening_locked(user_id: int, mission_id: str) -> bool:
    with projects.transaction() as cur:
        cur.execute("SELECT to_regclass('velia_research_meta_snapshots') AS table_name")
        table = cur.fetchone()
        if not table or not table.get("table_name"):
            return False
        cur.execute("""SELECT 1 FROM velia_research_meta_snapshots
            WHERE mission_id=%s AND user_id=%s LIMIT 1""", (str(mission_id), int(user_id)))
        return cur.fetchone() is not None


def _objective_filters(review: Dict[str, Any], source: Dict[str, Any]) -> List[str]:
    criteria = review["criteria"]
    failures: List[str] = []
    year = source.get("published_year")
    if criteria.get("year_from") is not None and year is not None and int(year) < int(criteria["year_from"]):
        failures.append("date_out_of_range")
    if criteria.get("year_to") is not None and year is not None and int(year) > int(criteria["year_to"]):
        failures.append("date_out_of_range")
    hints = set(criteria.get("evidence_hints") or [])
    if hints and str(source.get("evidence_hint") or "unknown") not in hints:
        failures.append("study_design_mismatch")
    return list(dict.fromkeys(failures))


def screen_source(user_id: int, review_id: str, source_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_systematic_review_disabled", 503)
    if not isinstance(data, dict) or set(data) - {"stage", "decision", "reason_code", "note"}:
        raise projects.ProjectError("invalid_research_screening_action")
    review = get_protocol(user_id, review_id)
    if _screening_locked(user_id, review["mission_id"]):
        raise projects.ProjectError("research_review_screening_locked_after_meta_analysis", 409)
    source = _source(user_id, review, source_id)
    sources = _review_sources(user_id, review)
    stage = str(data.get("stage") or "")
    decision = str(data.get("decision") or "")
    reason = str(data.get("reason_code") or "")
    note = _text(data.get("note"), 800, "invalid_research_screening_action")
    if stage not in SCREENING_STAGES or decision not in SCREENING_DECISIONS:
        raise projects.ProjectError("invalid_research_screening_action")
    if decision == "exclude" and reason not in EXCLUSION_REASONS:
        raise projects.ProjectError("invalid_research_screening_reason")
    if decision == "include" and reason:
        raise projects.ProjectError("invalid_research_screening_reason")

    canonical = _canonical_source(sources, source["publication_fingerprint"])
    is_duplicate = canonical != source["source_id"]
    if is_duplicate:
        decision = "duplicate"
        reason = "duplicate_publication"
    elif decision == "duplicate":
        raise projects.ProjectError("research_review_canonical_source_not_duplicate", 409)

    latest = _latest_actions(user_id, review_id)
    if stage == "eligibility":
        prior = latest.get((source["source_id"], "title_abstract"))
        if not prior or prior["decision"] != "include":
            raise projects.ProjectError("research_review_title_abstract_include_required", 409)
    objective_failures = _objective_filters(review, source)
    if decision == "include" and objective_failures:
        raise projects.ProjectError("research_review_source_fails_preregistered_filter", 409)

    with projects.transaction() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_screening_actions
            WHERE review_id=%s AND user_id=%s AND source_id=%s""",
            (review["id"], int(user_id), source["source_id"]))
        if int(cur.fetchone()["n"]) >= MAX_SCREENING_ACTIONS_PER_SOURCE:
            raise projects.ProjectError("research_review_screening_revision_limit_reached", 409)

    frozen = {
        "review_id": review["id"],
        "protocol_hash": review["protocol_hash"],
        "source_id": source["source_id"],
        "publication_fingerprint": source["publication_fingerprint"],
        "stage": stage,
        "decision": decision,
        "reason_code": reason,
        "note": note,
        "canonical_source_id": canonical if is_duplicate else None,
        "objective_filter_failures": objective_failures,
    }
    safety_decision = safety.classify(_json(frozen), phase="literature")
    if safety_decision["decision"] == "blocked":
        raise projects.ProjectError("research_review_screening_safety_blocked", 403)
    action_hash = _sha(frozen)
    action_id = _sha({
        "review_id": review["id"],
        "source_id": source["source_id"],
        "action_hash": action_hash,
        "revision": len([
            key for key in latest
            if key[0] == source["source_id"]
        ]),
    })
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_screening_actions(
            action_id,review_id,mission_id,user_id,source_id,stage,decision,reason_code,
            note,publication_fingerprint,canonical_source_id,action_hash,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                action_id, review["id"], review["mission_id"], int(user_id),
                source["source_id"], stage, decision, reason, note,
                source["publication_fingerprint"], canonical if is_duplicate else None,
                action_hash, _json(safety_decision),
            ))
        cur.execute("""SELECT * FROM velia_research_screening_actions
            WHERE action_id=%s AND user_id=%s""", (action_id, int(user_id)))
        row = cur.fetchone()
        center._event(cur, review["mission_id"], user_id, "research_systematic_review_screened", {
            "review_id": review["id"],
            "source_id": source["source_id"],
            "stage": stage,
            "decision": decision,
            "reason_code": reason,
            "action_hash": action_hash,
        })
    return {
        "id": row["action_id"],
        "review_id": row["review_id"],
        "source_id": row["source_id"],
        "stage": row["stage"],
        "decision": row["decision"],
        "reason_code": row["reason_code"],
        "note": row["note"],
        "publication_fingerprint": row["publication_fingerprint"],
        "canonical_source_id": row["canonical_source_id"],
        "action_hash": row["action_hash"],
        "created_at": _iso(row["created_at"]),
    }


def screening_history(user_id: int, review_id: str, source_id: Optional[str] = None) -> Dict[str, Any]:
    review = get_protocol(user_id, review_id)
    params: List[Any] = [review["id"], int(user_id)]
    where = "review_id=%s AND user_id=%s"
    if source_id is not None:
        _source(user_id, review, source_id)
        where += " AND source_id=%s"
        params.append(str(source_id))
    with projects.transaction() as cur:
        cur.execute(f"""SELECT * FROM velia_research_screening_actions
            WHERE {where}
            ORDER BY created_at ASC,action_id ASC LIMIT 1000""", tuple(params))
        rows = list(cur.fetchall())
    return {
        "actions": [{
            "id": row["action_id"],
            "source_id": row["source_id"],
            "stage": row["stage"],
            "decision": row["decision"],
            "reason_code": row["reason_code"],
            "note": row["note"],
            "publication_fingerprint": row["publication_fingerprint"],
            "canonical_source_id": row["canonical_source_id"],
            "action_hash": row["action_hash"],
            "created_at": _iso(row["created_at"]),
        } for row in rows]
    }


def prisma_flow(user_id: int, review_id: str) -> Dict[str, Any]:
    review = get_protocol(user_id, review_id)
    sources = _review_sources(user_id, review)
    latest = _latest_actions(user_id, review_id)
    fingerprints: Dict[str, List[str]] = {}
    for source in sources:
        fingerprints.setdefault(source["publication_fingerprint"], []).append(source["source_id"])
    duplicate_ids = {
        source_id
        for ids in fingerprints.values()
        for source_id in ids[1:]
    }
    title = {
        source_id: row for (source_id, stage), row in latest.items()
        if stage == "title_abstract"
    }
    eligibility = {
        source_id: row for (source_id, stage), row in latest.items()
        if stage == "eligibility"
    }
    included = [
        source_id for source_id, row in eligibility.items()
        if row["decision"] == "include" and source_id not in duplicate_ids
    ]
    flow = {
        "identified_records": len(sources),
        "duplicate_records_detected": len(duplicate_ids),
        "unique_publications": len(fingerprints),
        "title_abstract_screened": len(title),
        "title_abstract_excluded": sum(1 for row in title.values() if row["decision"] == "exclude"),
        "eligibility_assessed": len(eligibility),
        "eligibility_excluded": sum(1 for row in eligibility.values() if row["decision"] == "exclude"),
        "included_in_review": len(included),
        "awaiting_title_abstract_screen": sum(
            1 for source in sources
            if source["source_id"] not in duplicate_ids and source["source_id"] not in title
        ),
        "awaiting_eligibility": sum(
            1 for source_id, row in title.items()
            if row["decision"] == "include" and source_id not in eligibility
        ),
        "included_source_ids": included,
        "screening_locked_after_meta_analysis": _screening_locked(user_id, review["mission_id"]),
    }
    flow["flow_hash"] = _sha({
        "review_id": review["id"],
        "protocol_hash": review["protocol_hash"],
        "flow": flow,
    })
    return flow


def assert_source_eligible(user_id: int, mission_id: str, source_id: str) -> None:
    """Fail closed for meta-analysis when systematic review rollout is enabled."""
    if not enabled():
        return
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_review_protocols
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,review_id DESC LIMIT 1""",
            (str(mission_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_systematic_review_protocol_required", 409)
    review = _protocol_row(row)
    _source(user_id, review, source_id)
    latest = _latest_actions(user_id, review["id"])
    action = latest.get((str(source_id), "eligibility"))
    if not action or action["decision"] != "include":
        raise projects.ProjectError("research_meta_source_not_systematically_included", 409)


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS table_name", (name,))
    row = cur.fetchone()
    return bool(row and row.get("table_name"))


def _add_node(nodes: Dict[str, Dict[str, Any]], node_id: str, node_type: str, attrs: Dict[str, Any]) -> None:
    if node_id not in nodes and len(nodes) >= MAX_GRAPH_NODES:
        raise projects.ProjectError("research_evidence_graph_too_large", 413)
    nodes[node_id] = {"id": node_id, "type": node_type, **attrs}


def _add_edge(edges: Dict[str, Dict[str, Any]], source: str, target: str, relation: str, attrs: Optional[Dict[str, Any]] = None) -> None:
    edge_id = _sha({"source": source, "target": target, "relation": relation, "attrs": attrs or {}})
    if edge_id not in edges and len(edges) >= MAX_GRAPH_EDGES:
        raise projects.ProjectError("research_evidence_graph_too_large", 413)
    edges[edge_id] = {
        "id": edge_id,
        "source": source,
        "target": target,
        "relation": relation,
        **(attrs or {}),
    }


def build_evidence_graph(user_id: int, review_id: str) -> Dict[str, Any]:
    review = get_protocol(user_id, review_id)
    flow = prisma_flow(user_id, review_id)
    sources = _review_sources(user_id, review)
    latest = _latest_actions(user_id, review_id)
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[str, Dict[str, Any]] = {}
    review_node = "review:" + review["id"]
    _add_node(nodes, review_node, "systematic_review", {
        "protocol_hash": review["protocol_hash"],
        "framework": review["framework"],
    })

    for source in sources:
        sid = "source:" + source["source_id"]
        _add_node(nodes, sid, "scholarly_source", {
            "source_id": source["source_id"],
            "doi": source["doi"],
            "title": source["title"],
            "published_year": source["published_year"],
            "evidence_hint": source["evidence_hint"],
            "publication_fingerprint": source["publication_fingerprint"],
        })
        final = latest.get((source["source_id"], "eligibility"))
        title = latest.get((source["source_id"], "title_abstract"))
        relation = "identified"
        if final:
            relation = "included" if final["decision"] == "include" else final["decision"]
        elif title and title["decision"] in {"exclude", "duplicate"}:
            relation = title["decision"]
        _add_edge(edges, review_node, sid, relation)

    with projects.transaction() as cur:
        cur.execute("""SELECT claim_id,hypothesis_id,statement,claim_hash,source_ids_json
            FROM velia_research_claims WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at ASC,claim_id ASC""", (review["mission_id"], int(user_id)))
        claim_rows = [dict(row) for row in cur.fetchall()]
        for claim in claim_rows:
            cid = "claim:" + claim["claim_id"]
            _add_node(nodes, cid, "claim", {
                "claim_id": claim["claim_id"],
                "statement": claim["statement"],
                "claim_hash": claim["claim_hash"],
            })
            _add_edge(edges, review_node, cid, "evaluates_claim")
            for source_id in json.loads(claim["source_ids_json"]):
                sid = "source:" + str(source_id)
                if sid in nodes:
                    _add_edge(edges, cid, sid, "cites_source")

        if _table_exists(cur, "velia_research_meta_studies"):
            cur.execute("""SELECT study_id,claim_id,source_id,study_hash FROM velia_research_meta_studies
                WHERE mission_id=%s AND user_id=%s ORDER BY created_at ASC,study_id ASC""",
                (review["mission_id"], int(user_id)))
            for row in cur.fetchall():
                mid = "meta_study:" + row["study_id"]
                _add_node(nodes, mid, "meta_study", {
                    "study_id": row["study_id"],
                    "study_hash": row["study_hash"],
                })
                _add_edge(edges, "claim:" + row["claim_id"], mid, "has_meta_study")
                _add_edge(edges, mid, "source:" + row["source_id"], "extracts_from_source")

        if _table_exists(cur, "velia_research_meta_snapshots"):
            cur.execute("""SELECT snapshot_id,claim_id,evidence_hash,study_ids_json
                FROM velia_research_meta_snapshots
                WHERE mission_id=%s AND user_id=%s ORDER BY created_at ASC,snapshot_id ASC""",
                (review["mission_id"], int(user_id)))
            for row in cur.fetchall():
                mid = "meta_analysis:" + row["snapshot_id"]
                _add_node(nodes, mid, "meta_analysis", {
                    "snapshot_id": row["snapshot_id"],
                    "evidence_hash": row["evidence_hash"],
                })
                _add_edge(edges, "claim:" + row["claim_id"], mid, "calibrated_by")
                for study_id in json.loads(row["study_ids_json"]):
                    target = "meta_study:" + str(study_id)
                    if target in nodes:
                        _add_edge(edges, mid, target, "contains_study")

        if _table_exists(cur, "velia_research_datasets"):
            cur.execute("""SELECT dataset_id,dataset_hash,split_hash,name
                FROM velia_research_datasets WHERE mission_id=%s AND user_id=%s
                ORDER BY created_at ASC,dataset_id ASC""", (review["mission_id"], int(user_id)))
            for row in cur.fetchall():
                did = "dataset:" + row["dataset_id"]
                _add_node(nodes, did, "dataset", {
                    "dataset_id": row["dataset_id"],
                    "dataset_hash": row["dataset_hash"],
                    "split_hash": row["split_hash"],
                    "name": row["name"],
                })

        if _table_exists(cur, "velia_research_protocols"):
            cur.execute("""SELECT protocol_id,protocol_hash,hypothesis_id,dataset_id
                FROM velia_research_protocols WHERE mission_id=%s AND user_id=%s
                ORDER BY created_at ASC,protocol_id ASC""", (review["mission_id"], int(user_id)))
            protocol_rows = [dict(row) for row in cur.fetchall()]
            for row in protocol_rows:
                pid = "protocol:" + row["protocol_id"]
                _add_node(nodes, pid, "scientific_protocol", {
                    "protocol_id": row["protocol_id"],
                    "protocol_hash": row["protocol_hash"],
                })
                dataset_node = "dataset:" + str(row["dataset_id"])
                if dataset_node in nodes:
                    _add_edge(edges, pid, dataset_node, "preregisters_dataset")
                for claim in claim_rows:
                    if claim["hypothesis_id"] == row["hypothesis_id"]:
                        _add_edge(edges, "claim:" + claim["claim_id"], pid, "preregistered_by")

        if _table_exists(cur, "velia_research_experiment_reviews"):
            cur.execute("""SELECT r.review_id,r.result_hash,e.hypothesis_id,e.method_json
                FROM velia_research_experiment_reviews r
                JOIN velia_research_experiments e
                  ON e.experiment_id=r.experiment_id AND e.user_id=r.user_id
                WHERE r.mission_id=%s AND r.user_id=%s
                ORDER BY r.created_at ASC,r.review_id ASC""",
                (review["mission_id"], int(user_id)))
            for row in cur.fetchall():
                rid = "experiment_review:" + row["review_id"]
                _add_node(nodes, rid, "experiment_review", {
                    "review_id": row["review_id"],
                    "result_hash": row["result_hash"],
                })
                method = json.loads(row["method_json"])
                dataset_snapshot = method.get("dataset_snapshot") if isinstance(method, dict) else None
                protocol_snapshot = method.get("protocol_snapshot") if isinstance(method, dict) else None
                if isinstance(dataset_snapshot, dict):
                    did = "dataset:" + str(dataset_snapshot.get("dataset_id") or "")
                    if did in nodes:
                        _add_edge(edges, rid, did, "uses_dataset")
                if isinstance(protocol_snapshot, dict):
                    pid = "protocol:" + str(protocol_snapshot.get("protocol_id") or "")
                    if pid in nodes:
                        _add_edge(edges, rid, pid, "executes_protocol")
                for claim in claim_rows:
                    if claim["hypothesis_id"] == row["hypothesis_id"]:
                        _add_edge(edges, "claim:" + claim["claim_id"], rid, "tested_by")

    graph = {
        "version": REVIEW_VERSION,
        "review_id": review["id"],
        "protocol_hash": review["protocol_hash"],
        "flow_hash": flow["flow_hash"],
        "nodes": sorted(nodes.values(), key=lambda item: (item["type"], item["id"])),
        "edges": sorted(edges.values(), key=lambda item: (item["relation"], item["source"], item["target"], item["id"])),
    }
    graph_hash = _sha(graph)
    snapshot_id = _sha({"review_id": review["id"], "graph_hash": graph_hash})
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_evidence_graph_snapshots(
            snapshot_id,review_id,mission_id,user_id,graph_hash,graph_json)
            VALUES(%s,%s,%s,%s,%s,%s)
            ON CONFLICT(review_id,user_id,graph_hash) DO NOTHING""",
            (
                snapshot_id, review["id"], review["mission_id"], int(user_id),
                graph_hash, _json(graph),
            ))
        center._event(cur, review["mission_id"], user_id, "research_evidence_graph_snapshot_created", {
            "review_id": review["id"],
            "snapshot_id": snapshot_id,
            "graph_hash": graph_hash,
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
        })
    return {
        "id": snapshot_id,
        "review_id": review["id"],
        "graph_hash": graph_hash,
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "graph": graph,
    }


def mission_review_evidence(user_id: int, mission_id: str) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    center.get_mission(user_id, mission_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_review_protocols
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,review_id DESC LIMIT 1""",
            (str(mission_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        return None
    review = _protocol_row(row)
    flow = prisma_flow(user_id, review["id"])
    graph = build_evidence_graph(user_id, review["id"])
    return {
        "review": review,
        "flow": flow,
        "evidence_graph": {
            "snapshot_id": graph["id"],
            "graph_hash": graph["graph_hash"],
            "node_count": graph["node_count"],
            "edge_count": graph["edge_count"],
        },
        "boundary": (
            "The review protocol and search plan are preregistered before discovery. "
            "Screening remains source-backed and auditable; VELIA does not fetch arbitrary full text."
        ),
    }
