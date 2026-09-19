"""Living Research and citation-integrity monitoring for VELIA Research Center.

This layer periodically re-runs only the frozen systematic-review search plan,
compares fresh scholarly metadata against immutable prior evidence, verifies DOI
post-publication updates through fixed Crossref metadata, maps candidate impact
to claims, and persists immutable revision snapshots. It never rewrites earlier
reports/graphs, auto-includes new papers, or executes arbitrary code/URLs/files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_literature_service as literature
from services import velia_research_systematic_review_service as systematic
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


LIVING_VERSION = 1
ALLOWED_CADENCE_HOURS = {24, 72, 168, 336, 720}
MAX_WATCHES_PER_USER = 20
MAX_CANDIDATE_WORKS_PER_SCAN = 50
MAX_CITATION_CHECKS_PER_SCAN = 20
MAX_CUMULATIVE_FINGERPRINTS = 800
LEASE_SECONDS = 900
MATERIAL_STATUSES = {
    "new_evidence",
    "citation_integrity_alert",
    "new_evidence_and_citation_integrity_alert",
}
INTEGRITY_ALERT_STATUSES = {
    "retracted_or_withdrawn",
    "expression_of_concern",
    "corrected",
    "updated",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return (
        systematic.enabled()
        and _env_bool("VELIA_RESEARCH_LIVING_RESEARCH_ENABLED", False)
    )


def worker_enabled() -> bool:
    return enabled() and _env_bool("VELIA_RESEARCH_LIVING_RESEARCH_WORKER_ENABLED", False)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "worker_enabled": worker_enabled(),
        "living_version": LIVING_VERSION,
        "allowed_cadence_hours": sorted(ALLOWED_CADENCE_HOURS),
        "frozen_queries_only": True,
        "new_papers_auto_included": False,
        "old_reports_mutated": False,
        "old_graphs_mutated": False,
        "citation_verification_provider": "crossref_fixed_endpoint",
        "retraction_or_correction_auto_invalidates_claim": False,
        "claim_stage_promotion_allowed": False,
        "claim_stage_demotion_allowed": False,
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


def _fingerprint(row: Dict[str, Any]) -> str:
    doi = str(row.get("doi") or "").strip().casefold()
    if doi:
        return _sha({"doi": doi})
    title = re.sub(r"\W+", "", str(row.get("title") or "").casefold())
    return _sha({
        "title": title,
        "published_year": row.get("published_year"),
    })


def _bounded_source(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": str(row.get("provider") or "")[:40],
        "external_id": str(row.get("external_id") or "")[:160],
        "doi": str(row.get("doi") or "")[:300],
        "title": str(row.get("title") or "")[:600],
        "published_year": row.get("published_year"),
        "venue": str(row.get("venue") or "")[:240],
        "source_type": str(row.get("source_type") or "")[:100],
        "evidence_hint": str(row.get("evidence_hint") or "unknown")[:80],
        "citation_count": max(0, int(row.get("citation_count") or 0)),
        "publication_fingerprint": _fingerprint(row),
        "metadata_hash": _sha({
            "provider": row.get("provider"),
            "external_id": row.get("external_id"),
            "doi": row.get("doi"),
            "title": row.get("title"),
            "published_year": row.get("published_year"),
            "venue": row.get("venue"),
            "source_type": row.get("source_type"),
            "evidence_hint": row.get("evidence_hint"),
            "citation_count": row.get("citation_count"),
        }),
    }


def ensure_tables() -> None:
    systematic.ensure_tables()
    claims.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_living_watches (
            watch_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            cadence_hours INTEGER NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            last_scan_at TIMESTAMP NULL,
            next_scan_at TIMESTAMP NOT NULL DEFAULT NOW(),
            lease_owner TEXT NULL,
            lease_until TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(review_id,user_id),
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_living_watches_due
            ON velia_research_living_watches(active,next_scan_at,lease_until)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_living_scans (
            scan_id TEXT PRIMARY KEY,
            watch_id TEXT NULL,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            trigger_kind TEXT NOT NULL,
            prior_scan_id TEXT NULL,
            base_graph_snapshot_id TEXT NOT NULL,
            base_graph_hash TEXT NOT NULL,
            previous_report_id TEXT NULL,
            previous_report_hash TEXT NULL,
            material_status TEXT NOT NULL,
            new_work_count INTEGER NOT NULL,
            citation_alert_count INTEGER NOT NULL,
            scan_hash TEXT NOT NULL,
            scan_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_living_scans
            ON velia_research_living_scans(review_id,user_id,created_at DESC)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_citation_checks (
            check_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            source_id TEXT NOT NULL,
            doi TEXT NOT NULL,
            status TEXT NOT NULL,
            verification_hash TEXT NOT NULL,
            verification_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(scan_id,user_id,source_id),
            FOREIGN KEY(scan_id) REFERENCES velia_research_living_scans(scan_id) ON DELETE CASCADE,
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_living_graph_revisions (
            revision_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            base_graph_snapshot_id TEXT NOT NULL,
            base_graph_hash TEXT NOT NULL,
            graph_hash TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(scan_id,user_id),
            FOREIGN KEY(scan_id) REFERENCES velia_research_living_scans(scan_id) ON DELETE CASCADE,
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE)""")


def _watch_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["watch_id"],
        "review_id": row["review_id"],
        "mission_id": row["mission_id"],
        "cadence_hours": int(row["cadence_hours"]),
        "active": bool(row["active"]),
        "last_scan_at": _iso(row["last_scan_at"]),
        "next_scan_at": _iso(row["next_scan_at"]),
        "lease_owner": row.get("lease_owner"),
        "lease_until": _iso(row.get("lease_until")),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def configure_watch(user_id: int, review_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_living_research_disabled", 503)
    if not isinstance(data, dict) or set(data) - {"cadence_hours", "active"}:
        raise projects.ProjectError("invalid_research_living_watch")
    review = systematic.get_protocol(user_id, review_id)
    mission = center.get_mission(user_id, review["mission_id"])
    if mission["status"] in {"blocked", "cancelled"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    cadence = data.get("cadence_hours", 168)
    active = data.get("active", True)
    if isinstance(cadence, bool) or not isinstance(cadence, int) or cadence not in ALLOWED_CADENCE_HOURS:
        raise projects.ProjectError("invalid_research_living_watch")
    if not isinstance(active, bool):
        raise projects.ProjectError("invalid_research_living_watch")

    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_living_watches
            WHERE user_id=%s""", (int(user_id),))
        existing_count = int(cur.fetchone()["n"])
        cur.execute("""SELECT watch_id FROM velia_research_living_watches
            WHERE review_id=%s AND user_id=%s""", (review["id"], int(user_id)))
        existing = cur.fetchone()
        if not existing and existing_count >= MAX_WATCHES_PER_USER:
            raise projects.ProjectError("research_living_watch_limit_reached", 409)
        watch_id = existing["watch_id"] if existing else _sha({
            "review_id": review["id"], "user_id": int(user_id)
        })
        cur.execute("""INSERT INTO velia_research_living_watches(
            watch_id,review_id,mission_id,user_id,cadence_hours,active,next_scan_at)
            VALUES(%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT(review_id,user_id) DO UPDATE SET
                cadence_hours=EXCLUDED.cadence_hours,
                active=EXCLUDED.active,
                next_scan_at=CASE
                    WHEN velia_research_living_watches.last_scan_at IS NULL THEN NOW()
                    ELSE velia_research_living_watches.last_scan_at
                         + (EXCLUDED.cadence_hours * INTERVAL '1 hour')
                END,
                updated_at=NOW()""",
            (
                watch_id, review["id"], review["mission_id"], int(user_id),
                cadence, active,
            ))
        cur.execute("""SELECT * FROM velia_research_living_watches
            WHERE review_id=%s AND user_id=%s""", (review["id"], int(user_id)))
        row = cur.fetchone()
        center._event(cur, review["mission_id"], user_id, "research_living_watch_configured", {
            "watch_id": row["watch_id"],
            "review_id": review["id"],
            "cadence_hours": cadence,
            "active": active,
        })
        return _watch_row(row)


def get_watch(user_id: int, review_id: str) -> Dict[str, Any]:
    systematic.get_protocol(user_id, review_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_watches
            WHERE review_id=%s AND user_id=%s""", (str(review_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_living_watch_not_found", 404)
    return _watch_row(row)


def _latest_scan_row(user_id: int, review_id: str) -> Optional[Dict[str, Any]]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_scans
            WHERE review_id=%s AND user_id=%s
            ORDER BY created_at DESC,scan_id DESC LIMIT 1""", (str(review_id), int(user_id)))
        row = cur.fetchone()
    return dict(row) if row else None


def _previous_report(user_id: int, mission_id: str) -> Tuple[Optional[str], Optional[str]]:
    with projects.transaction() as cur:
        cur.execute("SELECT to_regclass('velia_research_reports') AS table_name")
        exists = cur.fetchone()
        if not exists or not exists.get("table_name"):
            return None, None
        cur.execute("""SELECT report_id,report_hash FROM velia_research_reports
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,report_id DESC LIMIT 1""",
            (str(mission_id), int(user_id)))
        row = cur.fetchone()
    return (row["report_id"], row["report_hash"]) if row else (None, None)


def _claims_for_mission(user_id: int, mission_id: str) -> List[Dict[str, Any]]:
    with projects.transaction() as cur:
        cur.execute("""SELECT claim_id,statement,source_ids_json,claim_hash
            FROM velia_research_claims WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at ASC,claim_id ASC""", (str(mission_id), int(user_id)))
        rows = cur.fetchall()
    return [{
        "id": row["claim_id"],
        "statement": row["statement"],
        "source_ids": json.loads(row["source_ids_json"]),
        "claim_hash": row["claim_hash"],
    } for row in rows]


def _tokens(value: str) -> Set[str]:
    stop = {
        "with", "from", "that", "this", "were", "have", "into", "than",
        "the", "and", "for", "are", "was", "but", "not", "positive", "negative",
        "association", "effect", "measured", "study", "studies",
    }
    return {
        token for token in re.findall(r"[A-Za-z0-9_]{4,}", value.casefold())
        if token not in stop
    }


def _new_work_impacts(review: Dict[str, Any], mission_claims: List[Dict[str, Any]], work: Dict[str, Any]) -> List[Dict[str, Any]]:
    if review.get("claim_id"):
        return [{
            "claim_id": review["claim_id"],
            "impact_kind": "direct_review_scope",
            "score": 1.0,
        }]
    work_tokens = _tokens(str(work.get("title") or ""))
    impacts: List[Dict[str, Any]] = []
    for claim in mission_claims:
        claim_tokens = _tokens(claim["statement"])
        if not work_tokens or not claim_tokens:
            continue
        common = len(work_tokens & claim_tokens)
        score = common / max(1, len(claim_tokens))
        if common >= 2 or score >= 0.25:
            impacts.append({
                "claim_id": claim["id"],
                "impact_kind": "candidate_lexical_relevance",
                "score": round(score, 6),
            })
    return impacts


def _citation_claims(
    user_id: int,
    mission_id: str,
    source_id: str,
    mission_claims: List[Dict[str, Any]],
) -> List[str]:
    impacted = {
        claim["id"] for claim in mission_claims
        if source_id in claim["source_ids"]
    }
    with projects.transaction() as cur:
        cur.execute("SELECT to_regclass('velia_research_meta_studies') AS table_name")
        exists = cur.fetchone()
        if exists and exists.get("table_name"):
            cur.execute("""SELECT DISTINCT claim_id FROM velia_research_meta_studies
                WHERE mission_id=%s AND user_id=%s AND source_id=%s""",
                (str(mission_id), int(user_id), str(source_id)))
            impacted.update(row["claim_id"] for row in cur.fetchall())
    return sorted(impacted)


def _current_known_fingerprints(
    baseline_sources: List[Dict[str, Any]],
    prior_scan: Optional[Dict[str, Any]],
) -> Set[str]:
    known = {_fingerprint(row) for row in baseline_sources}
    if prior_scan:
        try:
            data = json.loads(prior_scan["scan_json"])
            for value in data.get("cumulative_fingerprints", []):
                if isinstance(value, str) and len(value) == 64:
                    known.add(value)
        except Exception:
            pass
    return set(sorted(known)[:MAX_CUMULATIVE_FINGERPRINTS])


def _material_status(new_count: int, alert_count: int) -> str:
    if new_count and alert_count:
        return "new_evidence_and_citation_integrity_alert"
    if alert_count:
        return "citation_integrity_alert"
    if new_count:
        return "new_evidence"
    return "no_material_change"


def _graph_revision(
    review: Dict[str, Any],
    base_graph: Dict[str, Any],
    scan_id: str,
    new_works: List[Dict[str, Any]],
    citation_checks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = [{
        "id": "base_graph:" + base_graph["id"],
        "type": "base_evidence_graph",
        "snapshot_id": base_graph["id"],
        "graph_hash": base_graph["graph_hash"],
    }]
    edges: List[Dict[str, Any]] = []
    for work in new_works:
        wid = "candidate_work:" + work["publication_fingerprint"]
        nodes.append({
            "id": wid,
            "type": "candidate_new_work",
            "doi": work["doi"],
            "title": work["title"],
            "published_year": work["published_year"],
            "metadata_hash": work["metadata_hash"],
        })
        edges.append({
            "source": "review:" + review["id"],
            "target": wid,
            "relation": "new_candidate_for_frozen_review",
        })
        for impact in work.get("impacted_claims", []):
            edges.append({
                "source": wid,
                "target": "claim:" + impact["claim_id"],
                "relation": "may_affect_claim",
                "impact_kind": impact["impact_kind"],
                "score": impact["score"],
            })

    for check in citation_checks:
        cid = "citation_check:" + check["check_id"]
        nodes.append({
            "id": cid,
            "type": "citation_integrity_check",
            "source_id": check["source_id"],
            "doi": check["doi"],
            "status": check["status"],
            "verification_hash": check["verification_hash"],
        })
        edges.append({
            "source": "source:" + check["source_id"],
            "target": cid,
            "relation": "verified_by",
        })
        for claim_id in check.get("impacted_claim_ids", []):
            edges.append({
                "source": cid,
                "target": "claim:" + claim_id,
                "relation": "integrity_alert_for_claim",
            })

    overlay = {
        "version": LIVING_VERSION,
        "scan_id": scan_id,
        "review_id": review["id"],
        "base_graph_snapshot_id": base_graph["id"],
        "base_graph_hash": base_graph["graph_hash"],
        "nodes": sorted(nodes, key=lambda item: (item["type"], item["id"])),
        "edges": sorted(
            edges,
            key=lambda item: (
                item["relation"], item["source"], item["target"]
            ),
        ),
    }
    return overlay


def scan_review(user_id: int, review_id: str, *, trigger_kind: str = "manual") -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_living_research_disabled", 503)
    if trigger_kind not in {"manual", "scheduled"}:
        raise projects.ProjectError("invalid_research_living_trigger")
    review = systematic.get_protocol(user_id, review_id)
    mission = center.get_mission(user_id, review["mission_id"])
    if mission["status"] in {"blocked", "cancelled"}:
        raise projects.ProjectError("research_mission_not_active", 409)

    base_graph = systematic.build_evidence_graph(user_id, review["id"])
    baseline_sources = systematic._review_sources(user_id, review)
    flow = systematic.prisma_flow(user_id, review["id"])
    included_ids = list(flow.get("included_source_ids", []))[:MAX_CITATION_CHECKS_PER_SCAN]
    by_source = {row["source_id"]: row for row in baseline_sources}
    prior_scan = _latest_scan_row(user_id, review["id"])
    known = _current_known_fingerprints(baseline_sources, prior_scan)
    current_seen: Set[str] = set()
    new_works_by_fp: Dict[str, Dict[str, Any]] = {}
    discovery_runs: List[Dict[str, Any]] = []
    discovery_partial = False

    for query in review["search_plan"]["queries"]:
        try:
            result = literature.live_discover(
                query, review["search_plan"]["max_results_per_query"]
            )
            discovery_partial = discovery_partial or bool(result.get("partial"))
            discovery_runs.append({
                "query_hash": result["query_hash"],
                "partial": bool(result.get("partial")),
                "provider_gaps": list(result.get("provider_gaps", [])),
                "result_count": len(result.get("sources", [])),
            })
            for raw in result.get("sources", []):
                work = _bounded_source(raw)
                fp = work["publication_fingerprint"]
                current_seen.add(fp)
                if fp not in known and fp not in new_works_by_fp:
                    new_works_by_fp[fp] = work
        except projects.ProjectError as exc:
            discovery_partial = True
            discovery_runs.append({
                "query_hash": systematic._query_hash(query),
                "partial": True,
                "error": exc.code,
                "result_count": 0,
            })

    mission_claims = _claims_for_mission(user_id, review["mission_id"])
    new_works = list(new_works_by_fp.values())[:MAX_CANDIDATE_WORKS_PER_SCAN]
    for work in new_works:
        work["impacted_claims"] = _new_work_impacts(review, mission_claims, work)

    citation_prepared: List[Dict[str, Any]] = []
    for source_id in included_ids:
        source = by_source.get(source_id)
        if not source:
            continue
        doi = str(source.get("doi") or "")
        if not doi:
            verification = {
                "doi": "",
                "registered": False,
                "status": "no_doi",
                "updates": [],
                "provider": "none",
                "coverage_note": "The included source has no DOI in the stored scholarly metadata.",
            }
        else:
            try:
                verification = literature.verify_doi_status(doi)
            except projects.ProjectError as exc:
                verification = {
                    "doi": doi,
                    "registered": None,
                    "status": "verification_unavailable",
                    "updates": [],
                    "provider": "crossref",
                    "error": exc.code,
                    "coverage_note": "Citation verification could not be completed for this scan.",
                }
        verification_hash = _sha(verification)
        citation_prepared.append({
            "source_id": source_id,
            "doi": doi,
            "status": verification["status"],
            "verification_hash": verification_hash,
            "verification": verification,
            "impacted_claim_ids": (
                _citation_claims(
                    user_id, review["mission_id"], source_id, mission_claims
                )
                if verification["status"] in INTEGRITY_ALERT_STATUSES
                else []
            ),
        })

    alert_checks = [
        item for item in citation_prepared
        if item["status"] in INTEGRITY_ALERT_STATUSES
    ]
    material_status = _material_status(len(new_works), len(alert_checks))
    previous_report_id, previous_report_hash = _previous_report(
        user_id, review["mission_id"]
    )
    prior_id = prior_scan["scan_id"] if prior_scan else None
    watch_id = None
    with projects.transaction() as cur:
        cur.execute("""SELECT watch_id FROM velia_research_living_watches
            WHERE review_id=%s AND user_id=%s""", (review["id"], int(user_id)))
        watch = cur.fetchone()
        watch_id = watch["watch_id"] if watch else None

    cumulative = sorted(
        (known | current_seen | set(new_works_by_fp.keys()))
    )[:MAX_CUMULATIVE_FINGERPRINTS]
    frozen_scan = {
        "living_version": LIVING_VERSION,
        "review_id": review["id"],
        "protocol_hash": review["protocol_hash"],
        "trigger_kind": trigger_kind,
        "prior_scan_id": prior_id,
        "base_graph_snapshot_id": base_graph["id"],
        "base_graph_hash": base_graph["graph_hash"],
        "previous_report_id": previous_report_id,
        "previous_report_hash": previous_report_hash,
        "discovery_runs": discovery_runs,
        "discovery_partial": discovery_partial,
        "new_works": new_works,
        "citation_checks": [{
            "source_id": item["source_id"],
            "doi": item["doi"],
            "status": item["status"],
            "verification_hash": item["verification_hash"],
            "impacted_claim_ids": item["impacted_claim_ids"],
        } for item in citation_prepared],
        "material_status": material_status,
        "cumulative_fingerprints": cumulative,
        "policy": {
            "new_papers_auto_included": False,
            "claim_stage_modified": False,
            "old_report_modified": False,
            "old_graph_modified": False,
            "reassessment_required_for_material_change": material_status in MATERIAL_STATUSES,
        },
    }
    safety_decision = safety.classify(_json(frozen_scan), phase="final_output")
    if safety_decision["decision"] == "blocked":
        raise projects.ProjectError("research_living_research_safety_blocked", 403)
    scan_hash = _sha(frozen_scan)
    scan_id = _sha({
        "review_id": review["id"],
        "prior_scan_id": prior_id,
        "scan_hash": scan_hash,
    })

    overlay = _graph_revision(
        review, base_graph, scan_id, new_works, []
    )

    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_living_scans(
            scan_id,watch_id,review_id,mission_id,user_id,trigger_kind,prior_scan_id,
            base_graph_snapshot_id,base_graph_hash,previous_report_id,previous_report_hash,
            material_status,new_work_count,citation_alert_count,scan_hash,scan_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                scan_id, watch_id, review["id"], review["mission_id"], int(user_id),
                trigger_kind, prior_id, base_graph["id"], base_graph["graph_hash"],
                previous_report_id, previous_report_hash, material_status,
                len(new_works), len(alert_checks), scan_hash, _json(frozen_scan),
            ))
        persisted_checks: List[Dict[str, Any]] = []
        for item in citation_prepared:
            check_id = _sha({
                "scan_id": scan_id,
                "source_id": item["source_id"],
                "verification_hash": item["verification_hash"],
            })
            cur.execute("""INSERT INTO velia_research_citation_checks(
                check_id,scan_id,review_id,mission_id,user_id,source_id,doi,status,
                verification_hash,verification_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    check_id, scan_id, review["id"], review["mission_id"], int(user_id),
                    item["source_id"], item["doi"], item["status"],
                    item["verification_hash"], _json({
                        **item["verification"],
                        "impacted_claim_ids": item["impacted_claim_ids"],
                    }),
                ))
            persisted_checks.append({
                **item,
                "check_id": check_id,
            })

        overlay = _graph_revision(
            review, base_graph, scan_id, new_works, persisted_checks
        )
        graph_hash = _sha(overlay)
        revision_id = _sha({"scan_id": scan_id, "graph_hash": graph_hash})
        cur.execute("""INSERT INTO velia_research_living_graph_revisions(
            revision_id,scan_id,review_id,mission_id,user_id,base_graph_snapshot_id,
            base_graph_hash,graph_hash,graph_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                revision_id, scan_id, review["id"], review["mission_id"], int(user_id),
                base_graph["id"], base_graph["graph_hash"], graph_hash, _json(overlay),
            ))
        if watch_id:
            cur.execute("""UPDATE velia_research_living_watches SET
                last_scan_at=NOW(),
                next_scan_at=NOW() + (cadence_hours * INTERVAL '1 hour'),
                lease_owner=NULL,lease_until=NULL,updated_at=NOW()
                WHERE watch_id=%s AND user_id=%s""", (watch_id, int(user_id)))
        center._event(cur, review["mission_id"], user_id, "research_living_revision_created", {
            "scan_id": scan_id,
            "review_id": review["id"],
            "material_status": material_status,
            "new_work_count": len(new_works),
            "citation_alert_count": len(alert_checks),
            "base_graph_hash": base_graph["graph_hash"],
            "living_graph_hash": graph_hash,
            "previous_report_id": previous_report_id,
        })

    return get_scan(user_id, scan_id)


def get_scan(user_id: int, scan_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_scans
            WHERE scan_id=%s AND user_id=%s""", (str(scan_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_living_scan_not_found", 404)
        cur.execute("""SELECT * FROM velia_research_citation_checks
            WHERE scan_id=%s AND user_id=%s ORDER BY source_id ASC""",
            (str(scan_id), int(user_id)))
        checks = list(cur.fetchall())
        cur.execute("""SELECT * FROM velia_research_living_graph_revisions
            WHERE scan_id=%s AND user_id=%s""", (str(scan_id), int(user_id)))
        graph = cur.fetchone()
    data = json.loads(row["scan_json"])
    return {
        "id": row["scan_id"],
        "watch_id": row["watch_id"],
        "review_id": row["review_id"],
        "mission_id": row["mission_id"],
        "trigger_kind": row["trigger_kind"],
        "prior_scan_id": row["prior_scan_id"],
        "base_graph_snapshot_id": row["base_graph_snapshot_id"],
        "base_graph_hash": row["base_graph_hash"],
        "previous_report_id": row["previous_report_id"],
        "previous_report_hash": row["previous_report_hash"],
        "material_status": row["material_status"],
        "new_work_count": int(row["new_work_count"]),
        "citation_alert_count": int(row["citation_alert_count"]),
        "scan_hash": row["scan_hash"],
        "scan": data,
        "citation_checks": [{
            "id": item["check_id"],
            "source_id": item["source_id"],
            "doi": item["doi"],
            "status": item["status"],
            "verification_hash": item["verification_hash"],
            "verification": json.loads(item["verification_json"]),
            "created_at": _iso(item["created_at"]),
        } for item in checks],
        "graph_revision": ({
            "id": graph["revision_id"],
            "base_graph_snapshot_id": graph["base_graph_snapshot_id"],
            "base_graph_hash": graph["base_graph_hash"],
            "graph_hash": graph["graph_hash"],
            "graph": json.loads(graph["graph_json"]),
            "created_at": _iso(graph["created_at"]),
        } if graph else None),
        "created_at": _iso(row["created_at"]),
    }


def list_scans(user_id: int, review_id: str, offset: int = 0) -> Dict[str, Any]:
    systematic.get_protocol(user_id, review_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT scan_id FROM velia_research_living_scans
            WHERE review_id=%s AND user_id=%s
            ORDER BY created_at DESC,scan_id DESC LIMIT 21 OFFSET %s""",
            (str(review_id), int(user_id), offset))
        ids = [row["scan_id"] for row in cur.fetchall()]
    return {
        "scans": [get_scan(user_id, scan_id) for scan_id in ids[:20]],
        "next_offset": offset + 20 if len(ids) > 20 else None,
    }


def latest_mission_evidence(user_id: int, mission_id: str) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    center.get_mission(user_id, mission_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT scan_id FROM velia_research_living_scans
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,scan_id DESC LIMIT 1""",
            (str(mission_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        return None
    scan = get_scan(user_id, row["scan_id"])
    return {
        "scan_id": scan["id"],
        "review_id": scan["review_id"],
        "material_status": scan["material_status"],
        "new_work_count": scan["new_work_count"],
        "citation_alert_count": scan["citation_alert_count"],
        "scan_hash": scan["scan_hash"],
        "previous_report_id": scan["previous_report_id"],
        "previous_report_hash": scan["previous_report_hash"],
        "graph_revision_id": scan["graph_revision"]["id"] if scan["graph_revision"] else None,
        "graph_revision_hash": scan["graph_revision"]["graph_hash"] if scan["graph_revision"] else None,
        "new_works": scan["scan"].get("new_works", []),
        "citation_checks": scan["citation_checks"],
        "reassessment_required": scan["material_status"] in MATERIAL_STATUSES,
        "boundary": (
            "Living Research never mutates old evidence. New works remain candidates until separately screened, "
            "and citation-integrity alerts require reassessment rather than automatically changing Claim Ledger stage."
        ),
    }


def claim_due_watch(worker_id: str) -> Optional[Dict[str, Any]]:
    if not worker_enabled():
        return None
    worker_id = str(worker_id)[:160]
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_watches
            WHERE active=TRUE AND next_scan_at<=NOW()
              AND (lease_until IS NULL OR lease_until<NOW())
            ORDER BY next_scan_at ASC,watch_id ASC
            FOR UPDATE SKIP LOCKED LIMIT 1""")
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("""UPDATE velia_research_living_watches
            SET lease_owner=%s,lease_until=NOW() + (%s * INTERVAL '1 second'),updated_at=NOW()
            WHERE watch_id=%s""", (worker_id, LEASE_SECONDS, row["watch_id"]))
        cur.execute("""SELECT * FROM velia_research_living_watches
            WHERE watch_id=%s""", (row["watch_id"],))
        return _watch_row(cur.fetchone())


def run_due_once(worker_id: str) -> Optional[Dict[str, Any]]:
    watch = claim_due_watch(worker_id)
    if not watch:
        return None
    try:
        return scan_review(
            _watch_owner(watch["id"]),
            watch["review_id"],
            trigger_kind="scheduled",
        )
    except Exception:
        with projects.transaction() as cur:
            cur.execute("""UPDATE velia_research_living_watches
                SET lease_owner=NULL,lease_until=NULL,
                    next_scan_at=NOW() + INTERVAL '1 hour',updated_at=NOW()
                WHERE watch_id=%s""", (watch["id"],))
        raise


def _watch_owner(watch_id: str) -> int:
    with projects.transaction() as cur:
        cur.execute("""SELECT user_id FROM velia_research_living_watches
            WHERE watch_id=%s""", (str(watch_id),))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_living_watch_not_found", 404)
    return int(row["user_id"])
