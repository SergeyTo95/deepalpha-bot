"""Autonomous Living Reassessment Pipeline for VELIA Research Center.

A material Living Research scan is converted into a bounded, immutable scientific
reassessment. New papers are screened against the frozen review criteria. Papers
that may affect a registered claim require a structured, full-text-verified
extraction packet before they may enter a living pooled analysis. Citation
integrity alerts can remove retracted/withdrawn studies from the living pool.
Nothing here mutates the original systematic review, meta-analysis snapshots,
Claim Ledger definitions, evidence graphs, or prior reports.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_living_service as living
from services import velia_research_meta_analysis_service as meta
from services import velia_research_safety_service as safety
from services import velia_research_systematic_review_service as systematic
from services.velia_chat_service import _iso


REASSESSMENT_VERSION = 1
RUN_STATES = {"queued", "running", "awaiting_extraction", "reporting", "completed", "failed"}
MAX_ATTEMPTS = 3
LEASE_SECONDS = 900
MAX_CANDIDATES_PER_RUN = 20
MAX_AFFECTED_CLAIMS = 8
MAX_EXTRACTIONS_PER_RUN = 20
EXTRACTION_DECISIONS = {"include", "exclude"}
EXTRACTION_EXCLUSION_REASONS = {
    "population_mismatch",
    "intervention_or_exposure_mismatch",
    "comparator_mismatch",
    "outcome_mismatch",
    "study_design_mismatch",
    "date_out_of_range",
    "duplicate_publication",
    "insufficient_full_text_evidence",
    "not_primary_or_eligible_evidence",
    "other_preregistered_reason",
}
EXTRACTED_BY = {"trusted_agent", "human_reviewer", "verified_import"}
INTEGRITY_REMOVE_STATUSES = {"retracted_or_withdrawn"}
INTEGRITY_CAUTION_STATUSES = {
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
        living.enabled()
        and claims.enabled()
        and meta.enabled()
        and _env_bool("VELIA_RESEARCH_LIVING_REASSESSMENT_ENABLED", False)
    )


def worker_enabled() -> bool:
    return enabled() and _env_bool(
        "VELIA_RESEARCH_LIVING_REASSESSMENT_WORKER_ENABLED", False
    )


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "worker_enabled": worker_enabled(),
        "reassessment_version": REASSESSMENT_VERSION,
        "material_scan_only": True,
        "frozen_review_criteria_only": True,
        "full_text_verified_extraction_required_for_new_meta_evidence": True,
        "llm_guessed_effect_sizes_allowed": False,
        "new_papers_auto_included": False,
        "original_systematic_review_mutated": False,
        "original_meta_snapshot_mutated": False,
        "old_report_mutated": False,
        "claim_promotion_allowed": False,
        "living_calibration_may_caution_or_contradict": True,
        "max_candidates_per_run": MAX_CANDIDATES_PER_RUN,
        "max_affected_claims": MAX_AFFECTED_CLAIMS,
        "max_attempts": MAX_ATTEMPTS,
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
    if value is None and not required:
        return ""
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    out = re.sub(r"\s+", " ", value).strip()
    if required and not out:
        raise projects.ProjectError(code)
    return out


def ensure_tables() -> None:
    living.ensure_tables()
    meta.ensure_tables()
    claims.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_living_reassessments (
            run_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            status TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            worker_id TEXT NULL,
            lease_until TIMESTAMP NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            previous_report_id TEXT NULL,
            previous_report_hash TEXT NULL,
            result_hash TEXT NULL,
            result_json TEXT NULL,
            new_report_id TEXT NULL,
            new_report_hash TEXT NULL,
            error_code TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(scan_id,user_id),
            CHECK(status IN ('queued','running','awaiting_extraction','reporting','completed','failed')),
            FOREIGN KEY(scan_id) REFERENCES velia_research_living_scans(scan_id) ON DELETE CASCADE,
            FOREIGN KEY(review_id) REFERENCES velia_research_review_protocols(review_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_living_reassess_queue
            ON velia_research_living_reassessments(status,updated_at,created_at)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_living_extractions (
            extraction_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            scan_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            publication_fingerprint TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            exclusion_reason TEXT NOT NULL,
            study_design TEXT NOT NULL,
            effect_type TEXT NOT NULL,
            statistics_json TEXT NOT NULL,
            normalized_json TEXT NOT NULL,
            risk_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            extraction_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(run_id,user_id,publication_fingerprint,claim_id),
            FOREIGN KEY(run_id) REFERENCES velia_research_living_reassessments(run_id) ON DELETE CASCADE,
            FOREIGN KEY(claim_id) REFERENCES velia_research_claims(claim_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_living_extractions
            ON velia_research_living_extractions(run_id,user_id,created_at ASC)""")


def _run_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["run_id"],
        "scan_id": row["scan_id"],
        "review_id": row["review_id"],
        "mission_id": row["mission_id"],
        "status": row["status"],
        "input_hash": row["input_hash"],
        "attempt_count": int(row["attempt_count"]),
        "previous_report_id": row["previous_report_id"],
        "previous_report_hash": row["previous_report_hash"],
        "result_hash": row["result_hash"],
        "result": json.loads(row["result_json"]) if row.get("result_json") else None,
        "new_report_id": row["new_report_id"],
        "new_report_hash": row["new_report_hash"],
        "error_code": row["error_code"] or "",
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _extraction_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["extraction_id"],
        "run_id": row["run_id"],
        "scan_id": row["scan_id"],
        "publication_fingerprint": row["publication_fingerprint"],
        "claim_id": row["claim_id"],
        "decision": row["decision"],
        "exclusion_reason": row["exclusion_reason"],
        "study_design": row["study_design"],
        "effect_type": row["effect_type"],
        "statistics": json.loads(row["statistics_json"]),
        "normalized": json.loads(row["normalized_json"]),
        "risk_of_bias": json.loads(row["risk_json"]),
        "provenance": json.loads(row["provenance_json"]),
        "extraction_hash": row["extraction_hash"],
        "safety": json.loads(row["safety_json"]),
        "created_at": _iso(row["created_at"]),
    }


def enqueue_for_scan(user_id: int, scan_id: str) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    scan = living.get_scan(user_id, scan_id)
    if scan["material_status"] not in living.MATERIAL_STATUSES:
        return None
    review = systematic.get_protocol(user_id, scan["review_id"])
    input_snapshot = {
        "reassessment_version": REASSESSMENT_VERSION,
        "scan_id": scan["id"],
        "scan_hash": scan["scan_hash"],
        "review_id": review["id"],
        "protocol_hash": review["protocol_hash"],
        "living_graph_revision_hash": (
            scan["graph_revision"]["graph_hash"] if scan["graph_revision"] else None
        ),
        "previous_report_id": scan["previous_report_id"],
        "previous_report_hash": scan["previous_report_hash"],
    }
    input_hash = _sha(input_snapshot)
    run_id = _sha({"scan_id": scan["id"], "input_hash": input_hash})
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_living_reassessments(
            run_id,scan_id,review_id,mission_id,user_id,status,input_hash,
            previous_report_id,previous_report_hash)
            VALUES(%s,%s,%s,%s,%s,'queued',%s,%s,%s)
            ON CONFLICT(scan_id,user_id) DO NOTHING""",
            (
                run_id, scan["id"], review["id"], review["mission_id"], int(user_id),
                input_hash, scan["previous_report_id"], scan["previous_report_hash"],
            ))
        cur.execute("""SELECT * FROM velia_research_living_reassessments
            WHERE scan_id=%s AND user_id=%s""", (scan["id"], int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_living_reassessment_state_missing", 500)
        center._event(cur, review["mission_id"], user_id, "research_living_reassessment_queued", {
            "run_id": row["run_id"],
            "scan_id": scan["id"],
            "input_hash": input_hash,
        })
        return _run_row(row)


def get_run(user_id: int, run_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_reassessments
            WHERE run_id=%s AND user_id=%s""", (str(run_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_living_reassessment_not_found", 404)
    item = _run_row(row)
    item["extractions"] = list_extractions(user_id, item["id"])
    return item


def get_for_scan(user_id: int, scan_id: str) -> Optional[Dict[str, Any]]:
    living.get_scan(user_id, scan_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT run_id FROM velia_research_living_reassessments
            WHERE scan_id=%s AND user_id=%s""", (str(scan_id), int(user_id)))
        row = cur.fetchone()
    return get_run(user_id, row["run_id"]) if row else None


def list_runs(user_id: int, review_id: str, offset: int = 0) -> Dict[str, Any]:
    systematic.get_protocol(user_id, review_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT run_id FROM velia_research_living_reassessments
            WHERE review_id=%s AND user_id=%s
            ORDER BY created_at DESC,run_id DESC LIMIT 21 OFFSET %s""",
            (str(review_id), int(user_id), offset))
        ids = [row["run_id"] for row in cur.fetchall()]
    return {
        "reassessments": [get_run(user_id, value) for value in ids[:20]],
        "next_offset": offset + 20 if len(ids) > 20 else None,
    }


def list_extractions(user_id: int, run_id: str) -> List[Dict[str, Any]]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_extractions
            WHERE run_id=%s AND user_id=%s
            ORDER BY created_at ASC,extraction_id ASC""", (str(run_id), int(user_id)))
        return [_extraction_row(row) for row in cur.fetchall()]


def _candidate_map(scan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    works = scan["scan"].get("new_works", [])
    output: Dict[str, Dict[str, Any]] = {}
    for raw in works[:MAX_CANDIDATES_PER_RUN]:
        if not isinstance(raw, dict):
            continue
        fp = str(raw.get("publication_fingerprint") or "")
        if len(fp) == 64:
            output[fp] = raw
    return output


def _candidate_claim_ids(candidate: Dict[str, Any]) -> Set[str]:
    output: Set[str] = set()
    for item in candidate.get("impacted_claims", []):
        if isinstance(item, dict):
            claim_id = str(item.get("claim_id") or "")
            if claim_id:
                output.add(claim_id)
    return output


def _objective_failures(review: Dict[str, Any], candidate: Dict[str, Any]) -> List[str]:
    criteria = review["criteria"]
    failures: List[str] = []
    year = candidate.get("published_year")
    if criteria.get("year_from") is not None and year is not None:
        if int(year) < int(criteria["year_from"]):
            failures.append("date_out_of_range")
    if criteria.get("year_to") is not None and year is not None:
        if int(year) > int(criteria["year_to"]):
            failures.append("date_out_of_range")
    hints = set(criteria.get("evidence_hints") or [])
    hint = str(candidate.get("evidence_hint") or "unknown")
    if hints and hint not in hints:
        failures.append("study_design_mismatch")
    return list(dict.fromkeys(failures))


def _provenance(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "evidence_basis", "extracted_by", "source_text_hash", "locator", "note"
    }:
        raise projects.ProjectError("invalid_research_living_extraction_provenance")
    basis = str(value.get("evidence_basis") or "")
    extracted_by = str(value.get("extracted_by") or "")
    source_text_hash = str(value.get("source_text_hash") or "").lower()
    locator = _text(
        value.get("locator"), 500,
        "invalid_research_living_extraction_provenance", required=True,
    )
    note = _text(
        value.get("note"), 1000,
        "invalid_research_living_extraction_provenance",
    )
    if basis != "full_text_verified" or extracted_by not in EXTRACTED_BY:
        raise projects.ProjectError("research_living_full_text_verified_extraction_required", 409)
    if not re.fullmatch(r"[0-9a-f]{64}", source_text_hash):
        raise projects.ProjectError("invalid_research_living_extraction_provenance")
    return {
        "evidence_basis": basis,
        "extracted_by": extracted_by,
        "source_text_hash": source_text_hash,
        "locator": locator,
        "note": note,
        "full_text_stored_by_velia": False,
        "full_text_fetched_by_velia": False,
    }


def submit_extraction(user_id: int, run_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_living_reassessment_disabled", 503)
    if not isinstance(data, dict) or set(data) - {
        "publication_fingerprint", "claim_id", "decision", "exclusion_reason",
        "study_design", "effect_type", "statistics", "risk_of_bias", "provenance",
    }:
        raise projects.ProjectError("invalid_research_living_extraction")
    run = get_run(user_id, run_id)
    if run["status"] in {"completed", "failed", "reporting"}:
        raise projects.ProjectError("research_living_reassessment_not_editable", 409)
    scan = living.get_scan(user_id, run["scan_id"])
    candidates = _candidate_map(scan)
    fingerprint = str(data.get("publication_fingerprint") or "")
    candidate = candidates.get(fingerprint)
    if candidate is None:
        raise projects.ProjectError("research_living_candidate_not_found", 404)
    claim_id = str(data.get("claim_id") or "")
    if claim_id not in _candidate_claim_ids(candidate):
        raise projects.ProjectError("research_living_candidate_claim_mismatch", 409)
    claim = claims.get_claim(user_id, claim_id)
    if claim["mission_id"] != run["mission_id"]:
        raise projects.ProjectError("research_living_candidate_claim_mismatch", 409)

    decision = str(data.get("decision") or "")
    if decision not in EXTRACTION_DECISIONS:
        raise projects.ProjectError("invalid_research_living_extraction")
    exclusion_reason = str(data.get("exclusion_reason") or "")
    design = ""
    effect_type = ""
    statistics: Dict[str, Any] = {}
    normalized: Dict[str, Any] = {}
    risk: Dict[str, Any] = {}
    provenance: Dict[str, Any] = {}

    if decision == "exclude":
        if exclusion_reason not in EXTRACTION_EXCLUSION_REASONS:
            raise projects.ProjectError("invalid_research_living_exclusion_reason")
        if any(data.get(key) not in (None, "", {}, []) for key in (
            "study_design", "effect_type", "statistics", "risk_of_bias", "provenance"
        )):
            raise projects.ProjectError("invalid_research_living_extraction")
    else:
        if exclusion_reason:
            raise projects.ProjectError("invalid_research_living_exclusion_reason")
        design = str(data.get("study_design") or "")
        effect_type = str(data.get("effect_type") or "")
        if design not in meta.ALLOWED_DESIGNS or effect_type not in meta.ALLOWED_EFFECT_TYPES:
            raise projects.ProjectError("invalid_research_living_extraction")
        compatible = meta.CLAIM_EFFECT_COMPATIBILITY.get(claim["analysis_kind"], set())
        if effect_type not in compatible:
            raise projects.ProjectError("research_living_effect_incompatible_with_claim", 409)
        statistics = data.get("statistics")
        if not isinstance(statistics, dict):
            raise projects.ProjectError("invalid_research_living_extraction")
        normalized = meta._normalize(effect_type, statistics)
        risk = meta._risk(data.get("risk_of_bias"))
        provenance = _provenance(data.get("provenance"))

    frozen = {
        "reassessment_version": REASSESSMENT_VERSION,
        "run_id": run["id"],
        "scan_id": run["scan_id"],
        "publication_fingerprint": fingerprint,
        "candidate_metadata_hash": candidate.get("metadata_hash"),
        "claim_id": claim["id"],
        "claim_hash": claim["claim_hash"],
        "decision": decision,
        "exclusion_reason": exclusion_reason,
        "study_design": design,
        "effect_type": effect_type,
        "statistics": statistics,
        "normalized": normalized,
        "risk_of_bias": risk,
        "provenance": provenance,
    }
    safety_decision = safety.classify(_json(frozen), phase="experiment")
    if safety_decision["decision"] != "allowed" or safety_decision.get("read_only_only"):
        raise projects.ProjectError("research_living_extraction_safety_blocked", 403)
    extraction_hash = _sha(frozen)
    extraction_id = _sha({
        "run_id": run["id"],
        "publication_fingerprint": fingerprint,
        "claim_id": claim["id"],
        "extraction_hash": extraction_hash,
    })

    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_living_extractions
            WHERE run_id=%s AND user_id=%s AND publication_fingerprint=%s AND claim_id=%s""",
            (run["id"], int(user_id), fingerprint, claim["id"]))
        existing = cur.fetchone()
        if existing:
            existing_item = _extraction_row(existing)
            if existing_item["extraction_hash"] != extraction_hash:
                raise projects.ProjectError("research_living_extraction_is_immutable", 409)
            return existing_item
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_living_extractions
            WHERE run_id=%s AND user_id=%s""", (run["id"], int(user_id)))
        if int(cur.fetchone()["n"]) >= MAX_EXTRACTIONS_PER_RUN:
            raise projects.ProjectError("research_living_extraction_limit_reached", 409)
        cur.execute("""INSERT INTO velia_research_living_extractions(
            extraction_id,run_id,scan_id,review_id,mission_id,user_id,
            publication_fingerprint,claim_id,decision,exclusion_reason,
            study_design,effect_type,statistics_json,normalized_json,risk_json,
            provenance_json,extraction_hash,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                extraction_id, run["id"], run["scan_id"], run["review_id"], run["mission_id"],
                int(user_id), fingerprint, claim["id"], decision, exclusion_reason,
                design, effect_type, _json(statistics), _json(normalized), _json(risk),
                _json(provenance), extraction_hash, _json(safety_decision),
            ))
        cur.execute("""UPDATE velia_research_living_reassessments
            SET status='queued',worker_id=NULL,lease_until=NULL,error_code=NULL,updated_at=NOW()
            WHERE run_id=%s AND user_id=%s AND status='awaiting_extraction'""",
            (run["id"], int(user_id)))
        cur.execute("""SELECT * FROM velia_research_living_extractions
            WHERE extraction_id=%s AND user_id=%s""", (extraction_id, int(user_id)))
        row = cur.fetchone()
        center._event(cur, run["mission_id"], user_id, "research_living_extraction_registered", {
            "run_id": run["id"],
            "scan_id": run["scan_id"],
            "extraction_id": extraction_id,
            "claim_id": claim["id"],
            "publication_fingerprint": fingerprint,
            "decision": decision,
            "extraction_hash": extraction_hash,
        })
        return _extraction_row(row)


def _candidate_assessments(
    user_id: int,
    run: Dict[str, Any],
    scan: Dict[str, Any],
    review: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    extractions = list_extractions(user_id, run["id"])
    extraction_by_key = {
        (item["publication_fingerprint"], item["claim_id"]): item
        for item in extractions
    }
    assessments: List[Dict[str, Any]] = []
    waiting: List[Tuple[str, str]] = []
    for fingerprint, candidate in _candidate_map(scan).items():
        objective = _objective_failures(review, candidate)
        claim_ids = sorted(_candidate_claim_ids(candidate))[:MAX_AFFECTED_CLAIMS]
        if objective:
            assessments.append({
                "publication_fingerprint": fingerprint,
                "title": candidate.get("title"),
                "decision": "auto_exclude_objective_filter",
                "reasons": objective,
                "claim_ids": claim_ids,
                "requires_extraction": False,
            })
            continue
        if not claim_ids:
            assessments.append({
                "publication_fingerprint": fingerprint,
                "title": candidate.get("title"),
                "decision": "no_registered_claim_impact",
                "reasons": [],
                "claim_ids": [],
                "requires_extraction": False,
            })
            continue
        for claim_id in claim_ids:
            extraction = extraction_by_key.get((fingerprint, claim_id))
            if extraction is None:
                waiting.append((fingerprint, claim_id))
                assessments.append({
                    "publication_fingerprint": fingerprint,
                    "title": candidate.get("title"),
                    "claim_id": claim_id,
                    "decision": "awaiting_full_text_verified_extraction",
                    "reasons": [],
                    "requires_extraction": True,
                })
            else:
                assessments.append({
                    "publication_fingerprint": fingerprint,
                    "title": candidate.get("title"),
                    "claim_id": claim_id,
                    "decision": (
                        "include_in_living_reanalysis"
                        if extraction["decision"] == "include"
                        else "exclude_from_living_reanalysis"
                    ),
                    "reasons": (
                        [extraction["exclusion_reason"]]
                        if extraction["decision"] == "exclude" else []
                    ),
                    "requires_extraction": False,
                    "extraction_id": extraction["id"],
                    "extraction_hash": extraction["extraction_hash"],
                })
    return assessments, waiting


def _integrity_by_claim(scan: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    output: Dict[str, List[Dict[str, Any]]] = {}
    for check in scan["citation_checks"]:
        status = str(check.get("status") or "")
        if status not in INTEGRITY_CAUTION_STATUSES:
            continue
        verification = check.get("verification") or {}
        for claim_id in verification.get("impacted_claim_ids", []):
            output.setdefault(str(claim_id), []).append({
                "source_id": check["source_id"],
                "doi": check["doi"],
                "status": status,
                "verification_hash": check["verification_hash"],
            })
    return output


def _extracted_meta_items(
    user_id: int, run_id: str, claim_id: str
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for item in list_extractions(user_id, run_id):
        if item["claim_id"] != claim_id or item["decision"] != "include":
            continue
        output.append({
            "id": "living:" + item["id"],
            "claim_id": claim_id,
            "source_id": "",
            "publication_fingerprint": item["publication_fingerprint"],
            "study_design": item["study_design"],
            "effect_type": item["effect_type"],
            "input": item["statistics"],
            "normalized": item["normalized"],
            "risk_of_bias": item["risk_of_bias"],
            "study_hash": item["extraction_hash"],
            "provenance": item["provenance"],
            "living_reassessment": True,
        })
    return output


def _living_meta(
    user_id: int,
    run: Dict[str, Any],
    claim: Dict[str, Any],
    integrity_alerts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    base = meta.list_studies(user_id, claim["id"])["studies"]
    removed_source_ids = {
        item["source_id"] for item in integrity_alerts
        if item["status"] in INTEGRITY_REMOVE_STATUSES
    }
    retained = [
        item for item in base if item["source_id"] not in removed_source_ids
    ]
    extracted = _extracted_meta_items(user_id, run["id"], claim["id"])
    items = retained + extracted
    effect_types = {item["effect_type"] for item in items}
    reasons: List[str] = []

    if removed_source_ids:
        reasons.append("retracted_or_withdrawn_source_removed_from_living_pool")
    if any(
        item["status"] in {"expression_of_concern", "corrected", "updated"}
        for item in integrity_alerts
    ):
        reasons.append("citation_integrity_update_requires_caution")

    if len(items) < meta.MIN_META_STUDIES:
        calibration = {
            "claim_action": "caution" if integrity_alerts else "no_change",
            "pooled_signal": "not_recomputed",
            "reasons": list(dict.fromkeys(
                reasons + (
                    ["insufficient_studies_for_living_meta_reanalysis"]
                    if integrity_alerts else []
                )
            )),
            "claim_promotion_allowed": False,
            "claim_stage_ceiling_changed": False,
            "policy": (
                "Living reassessment may add caution or contradiction but never promotes a Claim Ledger stage."
            ),
        }
        return {
            "available": False,
            "reason": "insufficient_studies",
            "base_study_count": len(base),
            "retained_study_count": len(retained),
            "new_extraction_count": len(extracted),
            "removed_source_ids": sorted(removed_source_ids),
            "calibration": calibration,
        }

    if len(effect_types) != 1:
        calibration = {
            "claim_action": "caution",
            "pooled_signal": "not_recomputed",
            "reasons": list(dict.fromkeys(reasons + ["living_reanalysis_mixed_effect_types"])),
            "claim_promotion_allowed": False,
            "claim_stage_ceiling_changed": False,
            "policy": (
                "Living reassessment fails closed when effect metrics are not homogeneous."
            ),
        }
        return {
            "available": False,
            "reason": "mixed_effect_types",
            "effect_types": sorted(effect_types),
            "base_study_count": len(base),
            "retained_study_count": len(retained),
            "new_extraction_count": len(extracted),
            "removed_source_ids": sorted(removed_source_ids),
            "calibration": calibration,
        }

    effect_type = next(iter(effect_types))
    fixed_raw = meta._pool(items)
    heterogeneity = meta._heterogeneity(items, fixed_raw)
    random_raw = meta._pool(items, heterogeneity["tau_squared"])
    fixed = {key: value for key, value in fixed_raw.items() if key != "weights"}
    random = {key: value for key, value in random_raw.items() if key != "weights"}
    fixed.update({
        "model": "inverse_variance_fixed_effect",
        "display_estimate": meta._display(effect_type, fixed["estimate"]),
        "display_ci_low": meta._display(effect_type, fixed["ci_low"]),
        "display_ci_high": meta._display(effect_type, fixed["ci_high"]),
    })
    random.update({
        "model": "dersimonian_laird_random_effects",
        "display_estimate": meta._display(effect_type, random["estimate"]),
        "display_ci_low": meta._display(effect_type, random["ci_low"]),
        "display_ci_high": meta._display(effect_type, random["ci_high"]),
        "primary_for_calibration": True,
    })
    loo = meta._leave_one_out(items, effect_type)
    risk = meta._risk_summary(items)
    publication_bias = meta._egger(items)
    calibration = meta._calibration(
        claim, items, random, heterogeneity, loo, risk, publication_bias
    )
    if reasons:
        calibration["reasons"] = list(dict.fromkeys(
            list(calibration.get("reasons") or []) + reasons
        ))
        if calibration["claim_action"] == "no_change":
            calibration["claim_action"] = "caution"
    calibration["claim_promotion_allowed"] = False
    calibration["policy"] = (
        "Living pooled reanalysis may add caution or robust contradiction, but new external evidence "
        "cannot promote a Claim Ledger stage."
    )
    return {
        "available": True,
        "effect_type": effect_type,
        "base_study_count": len(base),
        "retained_study_count": len(retained),
        "new_extraction_count": len(extracted),
        "removed_source_ids": sorted(removed_source_ids),
        "study_ids": [item["id"] for item in items],
        "study_hashes": [item["study_hash"] for item in items],
        "publication_fingerprints": [item["publication_fingerprint"] for item in items],
        "fixed_effect": fixed,
        "random_effects": random,
        "heterogeneity": heterogeneity,
        "leave_one_out": loo,
        "risk_of_bias": risk,
        "publication_bias": publication_bias,
        "calibration": calibration,
    }


def _old_report_claims(user_id: int, report_id: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not report_id:
        return {}
    from services import velia_research_report_service as reports
    try:
        report = reports.get_report(user_id, report_id)["report"]
    except projects.ProjectError:
        return {}
    ledger = report.get("claim_ledger") if isinstance(report, dict) else None
    snapshots = ledger.get("snapshots") if isinstance(ledger, dict) else []
    output: Dict[str, Dict[str, Any]] = {}
    for item in snapshots if isinstance(snapshots, list) else []:
        if isinstance(item, dict) and item.get("claim_id"):
            output[str(item["claim_id"])] = item
    return output


def _claim_diff(
    claim: Dict[str, Any],
    before: Dict[str, Any],
    after: Dict[str, Any],
    old_report_snapshot: Optional[Dict[str, Any]],
    living_meta: Dict[str, Any],
    integrity_alerts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    baseline = old_report_snapshot or before
    changes: List[str] = []
    if baseline.get("stage") != after.get("stage"):
        changes.append("claim_stage_changed")
    if baseline.get("wording") != after.get("wording"):
        changes.append("claim_wording_changed")
    if living_meta.get("new_extraction_count", 0):
        changes.append("new_verified_external_evidence_added")
    if living_meta.get("removed_source_ids"):
        changes.append("retracted_or_withdrawn_evidence_removed_from_living_pool")
    if integrity_alerts:
        changes.append("citation_integrity_alert")
    action = (living_meta.get("calibration") or {}).get("claim_action")
    if action == "caution":
        changes.append("caution_added")
    if action == "contradict":
        changes.append("living_reanalysis_contradicts_claim")
    if not changes:
        changes.append("no_material_claim_change")
    return {
        "claim_id": claim["id"],
        "claim_hash": claim["claim_hash"],
        "statement": claim["statement"],
        "before_snapshot_id": baseline.get("id"),
        "before_stage": baseline.get("stage"),
        "before_evidence_hash": baseline.get("evidence_hash"),
        "before_wording": baseline.get("wording"),
        "after_snapshot_id": after.get("id"),
        "after_stage": after.get("stage"),
        "after_evidence_hash": after.get("evidence_hash"),
        "after_wording": after.get("wording"),
        "changes": list(dict.fromkeys(changes)),
        "living_calibration_action": action,
        "claim_promotion_allowed": False,
    }


def _set_awaiting(
    user_id: int,
    run: Dict[str, Any],
    assessments: List[Dict[str, Any]],
    waiting: List[Tuple[str, str]],
) -> Dict[str, Any]:
    snapshot = {
        "reassessment_version": REASSESSMENT_VERSION,
        "run_id": run["id"],
        "scan_id": run["scan_id"],
        "status": "awaiting_extraction",
        "candidate_assessments": assessments,
        "awaiting_extractions": [
            {"publication_fingerprint": fp, "claim_id": claim_id}
            for fp, claim_id in waiting
        ],
        "policy": {
            "effect_size_guessing_allowed": False,
            "full_text_verified_extraction_required": True,
            "old_evidence_mutation_allowed": False,
        },
    }
    result_hash = _sha(snapshot)
    with projects.transaction(user_id) as cur:
        cur.execute("""UPDATE velia_research_living_reassessments
            SET status='awaiting_extraction',worker_id=NULL,lease_until=NULL,
                result_hash=%s,result_json=%s,error_code=NULL,updated_at=NOW()
            WHERE run_id=%s AND user_id=%s AND status='running'""",
            (result_hash, _json(snapshot), run["id"], int(user_id)))
        center._event(cur, run["mission_id"], user_id, "research_living_reassessment_awaiting_extraction", {
            "run_id": run["id"],
            "scan_id": run["scan_id"],
            "awaiting_count": len(waiting),
            "result_hash": result_hash,
        })
    return get_run(user_id, run["id"])


def _execute(user_id: int, run: Dict[str, Any]) -> Dict[str, Any]:
    scan = living.get_scan(user_id, run["scan_id"])
    review = systematic.get_protocol(user_id, run["review_id"])
    assessments, waiting = _candidate_assessments(user_id, run, scan, review)
    if waiting:
        return _set_awaiting(user_id, run, assessments, waiting)

    integrity = _integrity_by_claim(scan)
    affected: Set[str] = set(integrity)
    for item in assessments:
        claim_id = item.get("claim_id")
        if claim_id and item["decision"] == "include_in_living_reanalysis":
            affected.add(str(claim_id))
    affected_ids = sorted(affected)[:MAX_AFFECTED_CLAIMS]
    old_report_claims = _old_report_claims(user_id, run["previous_report_id"])
    claim_results: List[Dict[str, Any]] = []
    diffs: List[Dict[str, Any]] = []

    for claim_id in affected_ids:
        claim = claims.get_claim(user_id, claim_id)
        before = claims.latest_snapshot(user_id, claim_id)
        pooled = _living_meta(
            user_id, run, claim, integrity.get(claim_id, [])
        )
        calibration = pooled["calibration"]
        calibration_snapshot_id = _sha({
            "run_id": run["id"],
            "claim_id": claim_id,
            "living_meta": pooled,
        })
        record = claims.record_calibration(
            user_id,
            claim_id,
            "living_reassessment",
            calibration_snapshot_id,
            calibration,
        )
        after = claims.refresh_claim(user_id, claim_id)
        claim_results.append({
            "claim_id": claim_id,
            "living_meta": pooled,
            "calibration_record": record,
            "claim_snapshot": after,
            "integrity_alerts": integrity.get(claim_id, []),
        })
        diffs.append(_claim_diff(
            claim, before, after, old_report_claims.get(claim_id),
            pooled, integrity.get(claim_id, []),
        ))

    result = {
        "reassessment_version": REASSESSMENT_VERSION,
        "run_id": run["id"],
        "scan_id": run["scan_id"],
        "scan_hash": scan["scan_hash"],
        "review_id": review["id"],
        "protocol_hash": review["protocol_hash"],
        "previous_report_id": run["previous_report_id"],
        "previous_report_hash": run["previous_report_hash"],
        "candidate_assessments": assessments,
        "claim_results": claim_results,
        "scientific_diff": diffs,
        "affected_claim_count": len(affected_ids),
        "policy": {
            "original_systematic_review_mutated": False,
            "original_meta_snapshot_mutated": False,
            "prior_report_mutated": False,
            "claim_promotion_allowed": False,
            "new_external_evidence_requires_full_text_verified_extraction": True,
            "retracted_or_withdrawn_study_removed_only_from_living_pool": True,
        },
    }
    decision = safety.classify(_json(result), phase="final_output")
    if decision["decision"] == "blocked":
        raise projects.ProjectError("research_living_reassessment_safety_blocked", 403)
    result["safety"] = decision
    result_hash = _sha(result)
    diff_hash = _sha(diffs)

    with projects.transaction(user_id) as cur:
        cur.execute("""UPDATE velia_research_living_reassessments
            SET status='reporting',result_hash=%s,result_json=%s,error_code=NULL,updated_at=NOW()
            WHERE run_id=%s AND user_id=%s AND status='running'""",
            (result_hash, _json(result), run["id"], int(user_id)))
        if cur.rowcount != 1:
            raise projects.ProjectError("research_living_reassessment_lease_lost", 409)
        center._event(cur, run["mission_id"], user_id, "research_living_reassessment_completed_analysis", {
            "run_id": run["id"],
            "scan_id": run["scan_id"],
            "affected_claim_count": len(affected_ids),
            "result_hash": result_hash,
            "scientific_diff_hash": diff_hash,
        })

    from services import velia_research_report_service as reports
    report = reports.build_report(user_id, run["mission_id"])
    with projects.transaction(user_id) as cur:
        cur.execute("""UPDATE velia_research_living_reassessments
            SET status='completed',worker_id=NULL,lease_until=NULL,
                new_report_id=%s,new_report_hash=%s,updated_at=NOW()
            WHERE run_id=%s AND user_id=%s AND status='reporting'""",
            (
                report["id"], report["report_hash"], run["id"], int(user_id),
            ))
        if cur.rowcount != 1:
            raise projects.ProjectError("research_living_reassessment_state_missing", 500)
        center._event(cur, run["mission_id"], user_id, "research_living_reassessment_reported", {
            "run_id": run["id"],
            "scan_id": run["scan_id"],
            "new_report_id": report["id"],
            "new_report_hash": report["report_hash"],
            "previous_report_id": run["previous_report_id"],
        })
    return get_run(user_id, run["id"])


def run_now(user_id: int, run_id: str) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_living_reassessment_disabled", 503)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_reassessments
            WHERE run_id=%s AND user_id=%s FOR UPDATE""",
            (str(run_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_living_reassessment_not_found", 404)
        if row["status"] == "completed":
            return get_run(user_id, run_id)
        if row["status"] == "awaiting_extraction":
            cur.execute("""UPDATE velia_research_living_reassessments
                SET status='queued',updated_at=NOW() WHERE run_id=%s AND user_id=%s""",
                (str(run_id), int(user_id)))
        elif row["status"] not in {"queued", "failed"}:
            raise projects.ProjectError("research_living_reassessment_not_runnable", 409)
        if int(row["attempt_count"]) >= MAX_ATTEMPTS:
            raise projects.ProjectError("research_living_reassessment_attempt_limit_reached", 409)
        cur.execute("""UPDATE velia_research_living_reassessments
            SET status='running',worker_id='manual',lease_until=NOW() + (%s * INTERVAL '1 second'),
                attempt_count=attempt_count+1,error_code=NULL,updated_at=NOW()
            WHERE run_id=%s AND user_id=%s
            RETURNING *""", (LEASE_SECONDS, str(run_id), int(user_id)))
        claimed = _run_row(cur.fetchone())
    try:
        return _execute(user_id, claimed)
    except Exception as exc:
        code = getattr(exc, "code", "research_living_reassessment_internal_error")
        with projects.transaction(user_id) as cur:
            cur.execute("""UPDATE velia_research_living_reassessments
                SET status='failed',worker_id=NULL,lease_until=NULL,error_code=%s,updated_at=NOW()
                WHERE run_id=%s AND user_id=%s AND status IN ('running','reporting')""",
                (str(code)[:120], claimed["id"], int(user_id)))
        raise


def claim_next(worker_id: str) -> Optional[Dict[str, Any]]:
    if not worker_enabled():
        return None
    worker_id = str(worker_id)[:160]
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_reassessments
            WHERE (
                status='queued'
                OR (status IN ('running','reporting') AND lease_until<NOW())
            )
            AND attempt_count<%s
            ORDER BY updated_at ASC,created_at ASC
            FOR UPDATE SKIP LOCKED LIMIT 1""", (MAX_ATTEMPTS,))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("""UPDATE velia_research_living_reassessments
            SET status='running',worker_id=%s,
                lease_until=NOW() + (%s * INTERVAL '1 second'),
                attempt_count=attempt_count+1,error_code=NULL,updated_at=NOW()
            WHERE run_id=%s RETURNING *""",
            (worker_id, LEASE_SECONDS, row["run_id"]))
        return _run_row(cur.fetchone())


def execute_claimed(run: Dict[str, Any], worker_id: str) -> Dict[str, Any]:
    user_id = _run_owner(run["id"])
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_reassessments
            WHERE run_id=%s AND user_id=%s AND status='running' AND worker_id=%s""",
            (run["id"], int(user_id), str(worker_id)[:160]))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_living_reassessment_lease_lost", 409)
    try:
        return _execute(user_id, _run_row(row))
    except Exception as exc:
        code = getattr(exc, "code", "research_living_reassessment_internal_error")
        with projects.transaction(user_id) as cur:
            cur.execute("""UPDATE velia_research_living_reassessments
                SET status='failed',worker_id=NULL,lease_until=NULL,error_code=%s,updated_at=NOW()
                WHERE run_id=%s AND user_id=%s AND status IN ('running','reporting')""",
                (str(code)[:120], run["id"], int(user_id)))
        raise


def _run_owner(run_id: str) -> int:
    with projects.transaction() as cur:
        cur.execute("""SELECT user_id FROM velia_research_living_reassessments
            WHERE run_id=%s""", (str(run_id),))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_living_reassessment_not_found", 404)
    return int(row["user_id"])


def latest_mission_evidence(user_id: int, mission_id: str) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    center.get_mission(user_id, mission_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_living_reassessments
            WHERE mission_id=%s AND user_id=%s AND result_json IS NOT NULL
            ORDER BY created_at DESC,run_id DESC LIMIT 1""",
            (str(mission_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        return None
    run = _run_row(row)
    result = run["result"] or {}
    return {
        "run_id": run["id"],
        "scan_id": run["scan_id"],
        "result_hash": run["result_hash"],
        "previous_report_id": run["previous_report_id"],
        "previous_report_hash": run["previous_report_hash"],
        "affected_claim_count": result.get("affected_claim_count", 0),
        "scientific_diff": result.get("scientific_diff", []),
        "scientific_diff_hash": _sha(result.get("scientific_diff", [])),
        "claim_results": result.get("claim_results", []),
        "candidate_assessments": result.get("candidate_assessments", []),
        "reassessment_complete": run["status"] in {"reporting", "completed"},
        "boundary": (
            "Living reassessment is an immutable successor layer. It may add caution or robust contradiction "
            "but never promotes a claim from external evidence and never mutates prior reports."
        ),
    }
