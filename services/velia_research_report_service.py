"""Deterministic, immutable Research Report snapshots for VELIA Research Center."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_meta_analysis_service as meta_analysis
from services.velia_chat_service import _iso


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def status() -> Dict[str, Any]:
    return {
        "enabled": center.enabled(),
        "deterministic": True,
        "extra_model_calls": 0,
        "immutable_snapshots": True,
        "source_provenance": True,
        "computational_provenance": True,
        "dataset_provenance": True,
        "protocol_provenance": True,
        "claim_ledger_provenance": True,
        "claim_language_stage_bounded": True,
        "meta_analysis_provenance": True,
        "meta_analysis_may_promote_claim": False,
        "versioned_snapshots": True,
    }


def ensure_tables() -> None:
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_reports (
            report_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            synthesis_id TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            report_hash TEXT NOT NULL,
            report_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE,
            FOREIGN KEY(synthesis_id)
                REFERENCES velia_research_syntheses(synthesis_id) ON DELETE CASCADE)""")
        cur.execute("ALTER TABLE velia_research_reports DROP CONSTRAINT IF EXISTS velia_research_reports_mission_id_user_id_synthesis_id_key")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_velia_research_report_snapshot
            ON velia_research_reports(mission_id,user_id,synthesis_id,report_hash)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_reports_mission
            ON velia_research_reports(mission_id,user_id,created_at DESC)""")


def _report_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["report_id"],
        "mission_id": row["mission_id"],
        "synthesis_id": row["synthesis_id"],
        "evidence_hash": row["evidence_hash"],
        "report_hash": row["report_hash"],
        "report": json.loads(row["report_json"]),
        "created_at": _iso(row["created_at"]),
    }


def _latest_synthesis(cur, user_id: int, mission_id: str) -> Dict[str, Any]:
    cur.execute("""SELECT synthesis_id,evidence_hash,source_ids_json,result_json,safety_json,
               provider,created_at
        FROM velia_research_syntheses
        WHERE mission_id=%s AND user_id=%s AND status='completed'
        ORDER BY created_at DESC,synthesis_id DESC LIMIT 1""",
        (str(mission_id), int(user_id)))
    row = cur.fetchone()
    if not row or not row["result_json"]:
        raise projects.ProjectError("research_synthesis_required", 409)
    return row


def _source_rows(cur, user_id: int, mission_id: str, source_ids: List[str]) -> List[Dict[str, Any]]:
    if not source_ids:
        return []
    cur.execute("""SELECT source_id,provider,external_id,doi,title,authors_json,
               published_year,venue,source_type,evidence_hint,source_url,
               citation_count,retrieved_at
        FROM velia_research_sources
        WHERE mission_id=%s AND user_id=%s
        ORDER BY retrieved_at DESC,ordinal ASC,source_id ASC""",
        (str(mission_id), int(user_id)))
    by_id = {row["source_id"]: row for row in cur.fetchall()}
    return [by_id[source_id] for source_id in source_ids if source_id in by_id]


def _computational_evidence(cur, user_id: int, mission_id: str) -> Dict[str, Any]:
    cur.execute("SELECT to_regclass('velia_research_experiment_reviews') AS table_name")
    table = cur.fetchone()
    if not table or not table.get("table_name"):
        return {
            "review_count": 0,
            "reviews": [],
            "boundary": "No reviewed safe-compute experiments are attached to this report snapshot.",
        }
    cur.execute("""SELECT r.review_id,r.experiment_id,r.compute_run_id,r.operation,r.result_hash,
               r.statistician_json,r.skeptic_json,r.replication_json,r.safety_json,r.created_at,
               e.method_json
        FROM velia_research_experiment_reviews r
        JOIN velia_research_experiments e
          ON e.experiment_id=r.experiment_id AND e.user_id=r.user_id
        WHERE r.mission_id=%s AND r.user_id=%s
        ORDER BY r.created_at ASC,r.review_id ASC LIMIT 20""",
        (str(mission_id), int(user_id)))
    reviews: List[Dict[str, Any]] = []
    dataset_snapshots: List[Dict[str, Any]] = []
    protocol_snapshots: List[Dict[str, Any]] = []
    seen_datasets = set()
    seen_protocols = set()
    for row in cur.fetchall():
        method = json.loads(row["method_json"])
        data_provenance: Dict[str, Any] = {"data_origin": method.get("data_origin")}
        if method.get("data_origin") == "dataset_registry":
            snapshot = method.get("dataset_snapshot")
            if isinstance(snapshot, dict):
                protocol_snapshot = method.get("protocol_snapshot")
                data_provenance = {
                    **snapshot,
                    "analysis_mode": method.get("analysis_mode"),
                    "protocol_snapshot": protocol_snapshot,
                }
                key = (
                    str(snapshot.get("dataset_id") or ""),
                    str(snapshot.get("dataset_hash") or ""),
                    str(snapshot.get("split_hash") or ""),
                    str(snapshot.get("split") or ""),
                    tuple(snapshot.get("columns") or []),
                )
                if key not in seen_datasets:
                    seen_datasets.add(key)
                    dataset_snapshots.append(snapshot)
                if isinstance(protocol_snapshot, dict):
                    pkey = (
                        str(protocol_snapshot.get("protocol_id") or ""),
                        str(protocol_snapshot.get("protocol_hash") or ""),
                    )
                    if pkey not in seen_protocols:
                        seen_protocols.add(pkey)
                        protocol_snapshots.append(protocol_snapshot)
        reviews.append({
            "review_id": row["review_id"],
            "experiment_id": row["experiment_id"],
            "compute_run_id": row["compute_run_id"],
            "operation": row["operation"],
            "result_hash": row["result_hash"],
            "data_provenance": data_provenance,
            "statistician": json.loads(row["statistician_json"]),
            "skeptic": json.loads(row["skeptic_json"]),
            "replication": json.loads(row["replication_json"]),
            "safety": json.loads(row["safety_json"]),
            "created_at": _iso(row["created_at"]),
        })
    return {
        "review_count": len(reviews),
        "reviews": reviews,
        "datasets": dataset_snapshots,
        "protocols": protocol_snapshots,
        "boundary": (
            "Computational results are supporting evidence under explicit numerical inputs and "
            "model assumptions; they are not independent empirical replication."
        ),
    }


def build_report(user_id: int, mission_id: str) -> Dict[str, Any]:
    if not center.enabled():
        raise projects.ProjectError("research_center_disabled", 503)
    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled"}:
        raise projects.ProjectError("research_mission_not_reportable", 409)

    claim_ledger = claims.mission_ledger(user_id, mission_id) if claims.enabled() else None
    meta_evidence = (
        meta_analysis.mission_meta_evidence(user_id, mission_id)
        if meta_analysis.enabled() else None
    )

    with projects.transaction(user_id) as cur:
        synthesis = _latest_synthesis(cur, user_id, mission_id)
        result = json.loads(synthesis["result_json"])
        source_ids = json.loads(synthesis["source_ids_json"])
        if not isinstance(source_ids, list):
            source_ids = []
        source_ids = [str(value) for value in source_ids[:20]]
        rows = _source_rows(cur, user_id, mission_id, source_ids)
        computational = _computational_evidence(cur, user_id, mission_id)

        profile: Dict[str, int] = {}
        citations: List[Dict[str, Any]] = []
        for row in rows:
            hint = str(row["evidence_hint"] or "unknown")
            profile[hint] = profile.get(hint, 0) + 1
            citations.append({
                "source_id": row["source_id"],
                "provider": row["provider"],
                "external_id": row["external_id"],
                "doi": row["doi"],
                "title": row["title"],
                "authors": json.loads(row["authors_json"]),
                "published_year": row["published_year"],
                "venue": row["venue"],
                "source_type": row["source_type"],
                "evidence_hint": hint,
                "url": row["source_url"],
                "citation_count": row["citation_count"],
                "retrieved_at": _iso(row["retrieved_at"]),
            })

        bounded_summary = result.get("summary", "")
        bounded_confidence = result.get("confidence", "uncertain")
        claim_findings: List[str] = []
        if claim_ledger is not None:
            claim_findings = [item["wording"] for item in claim_ledger.get("snapshots", [])]
            bounded_summary = (
                " ".join(claim_findings[:8])
                if claim_findings
                else "No registered claim has completed evidence; no claim-level conclusion is permitted."
            )
            bounded_confidence = "claim_ledger_bounded"

        report: Dict[str, Any] = {
            "version": 6 if meta_evidence is not None else (5 if claim_ledger is not None else 4),
            "title": "VELIA Research Report",
            "mission": {
                "id": str(mission_id),
                "goal": mission["goal"],
                "domain": mission["domain"],
                "status": mission["status"],
            },
            "conclusion": {
                "summary": bounded_summary,
                "confidence": bounded_confidence,
                "claim_findings": claim_findings,
                "literature_synthesis_summary": result.get("summary", ""),
                "literature_synthesis_confidence": result.get("confidence", "uncertain"),
                "boundary": (
                    "Final scientific claim language is limited by the deterministic Claim Ledger. "
                    "Literature synthesis and meta-analysis are contextual calibration layers and cannot "
                    "raise a claim above its ledger stage; meta-analysis may only add caution or contradiction."
                    if claim_ledger is not None
                    else "Claim Ledger is disabled; this report contains synthesis-level conclusions only."
                ),
            },
            "evidence": {
                "source_count": len(citations),
                "profile": profile,
                "assessments": result.get("evidence_assessment", []),
                "citations": citations,
            },
            "contradictions": result.get("contradictions", []),
            "limitations": result.get("limitations", []),
            "hypotheses": result.get("hypotheses", []),
            "open_questions": result.get("open_questions", []),
            "computational_evidence": computational,
            "claim_ledger": claim_ledger,
            "meta_analysis": meta_evidence,
            "provenance": {
                "synthesis_id": synthesis["synthesis_id"],
                "evidence_hash": synthesis["evidence_hash"],
                "provider": synthesis["provider"],
                "synthesis_created_at": _iso(synthesis["created_at"]),
                "immutable_source_ids": source_ids,
                "immutable_compute_review_ids": [row["review_id"] for row in computational["reviews"]],
                "immutable_compute_result_hashes": [row["result_hash"] for row in computational["reviews"]],
                "immutable_dataset_ids": [row["dataset_id"] for row in computational.get("datasets", [])],
                "immutable_dataset_hashes": [row["dataset_hash"] for row in computational.get("datasets", [])],
                "immutable_dataset_split_hashes": [row["split_hash"] for row in computational.get("datasets", [])],
                "immutable_protocol_ids": [row["protocol_id"] for row in computational.get("protocols", [])],
                "immutable_protocol_hashes": [row["protocol_hash"] for row in computational.get("protocols", [])],
                "immutable_claim_ids": (
                    [row["id"] for row in claim_ledger.get("claims", [])]
                    if claim_ledger is not None else []
                ),
                "immutable_claim_hashes": (
                    [row["claim_hash"] for row in claim_ledger.get("claims", [])]
                    if claim_ledger is not None else []
                ),
                "immutable_claim_snapshot_ids": (
                    [row["id"] for row in claim_ledger.get("snapshots", [])]
                    if claim_ledger is not None else []
                ),
                "immutable_claim_evidence_hashes": (
                    [row["evidence_hash"] for row in claim_ledger.get("snapshots", [])]
                    if claim_ledger is not None else []
                ),
                "immutable_meta_snapshot_ids": (
                    list(meta_evidence.get("snapshot_ids", []))
                    if meta_evidence is not None else []
                ),
                "immutable_meta_evidence_hashes": (
                    list(meta_evidence.get("evidence_hashes", []))
                    if meta_evidence is not None else []
                ),
            },
            "safety": {
                "mission": mission["safety"],
                "synthesis": json.loads(synthesis["safety_json"]),
                "operational_harmful_instructions_allowed": False,
                "claim_overstatement_allowed": False,
                "claim_ledger_enabled": claim_ledger is not None,
                "meta_analysis_enabled": meta_evidence is not None,
                "meta_analysis_claim_promotion_allowed": False,
            },
        }
        if mission["domain"] == "medicine":
            report["medical_boundary"] = {
                "research_use": True,
                "patient_specific_diagnosis": False,
                "patient_specific_prescribing": False,
                "note": "This report is a research synthesis, not an individual clinical decision.",
            }

        encoded = _json(report)
        if len(encoded) > 120000:
            raise projects.ProjectError("research_report_too_large", 413)
        report_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        report_id = hashlib.sha256(
            (str(mission_id) + "|" + str(synthesis["synthesis_id"]) + "|" + report_hash).encode("utf-8")
        ).hexdigest()

        cur.execute("""INSERT INTO velia_research_reports(
            report_id,mission_id,user_id,synthesis_id,evidence_hash,report_hash,report_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(mission_id,user_id,synthesis_id,report_hash) DO NOTHING""",
            (
                report_id, str(mission_id), int(user_id), synthesis["synthesis_id"],
                synthesis["evidence_hash"], report_hash, encoded,
            ))
        cur.execute("""SELECT * FROM velia_research_reports
            WHERE mission_id=%s AND user_id=%s AND synthesis_id=%s AND report_hash=%s""",
            (str(mission_id), int(user_id), synthesis["synthesis_id"], report_hash))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_report_state_missing", 500)
        center._event(cur, str(mission_id), user_id, "research_report_created", {
            "report_id": row["report_id"],
            "synthesis_id": row["synthesis_id"],
            "report_hash": row["report_hash"],
            "source_count": len(citations),
        })
        return _report_row(row)


def get_report(user_id: int, report_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_reports
            WHERE report_id=%s AND user_id=%s""", (str(report_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_report_not_found", 404)
        return _report_row(row)


def list_reports(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 200:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_reports
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,report_id DESC LIMIT 21 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
        return {
            "reports": [_report_row(row) for row in rows[:20]],
            "next_offset": offset + 20 if len(rows) > 20 else None,
        }
