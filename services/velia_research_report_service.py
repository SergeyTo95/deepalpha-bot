"""Deterministic, immutable Research Report snapshots for VELIA Research Center."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from services import velia_project_service as projects
from services import velia_research_center_service as center
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
            UNIQUE(mission_id,user_id,synthesis_id),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE,
            FOREIGN KEY(synthesis_id)
                REFERENCES velia_research_syntheses(synthesis_id) ON DELETE CASCADE)""")
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


def build_report(user_id: int, mission_id: str) -> Dict[str, Any]:
    if not center.enabled():
        raise projects.ProjectError("research_center_disabled", 503)
    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled"}:
        raise projects.ProjectError("research_mission_not_reportable", 409)

    with projects.transaction(user_id) as cur:
        synthesis = _latest_synthesis(cur, user_id, mission_id)
        cur.execute("""SELECT * FROM velia_research_reports
            WHERE mission_id=%s AND user_id=%s AND synthesis_id=%s""",
            (str(mission_id), int(user_id), synthesis["synthesis_id"]))
        existing = cur.fetchone()
        if existing:
            return _report_row(existing)

        result = json.loads(synthesis["result_json"])
        source_ids = json.loads(synthesis["source_ids_json"])
        if not isinstance(source_ids, list):
            source_ids = []
        source_ids = [str(value) for value in source_ids[:20]]
        rows = _source_rows(cur, user_id, mission_id, source_ids)

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

        report: Dict[str, Any] = {
            "version": 1,
            "title": "VELIA Research Report",
            "mission": {
                "id": str(mission_id),
                "goal": mission["goal"],
                "domain": mission["domain"],
                "status": mission["status"],
            },
            "conclusion": {
                "summary": result.get("summary", ""),
                "confidence": result.get("confidence", "uncertain"),
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
            "provenance": {
                "synthesis_id": synthesis["synthesis_id"],
                "evidence_hash": synthesis["evidence_hash"],
                "provider": synthesis["provider"],
                "synthesis_created_at": _iso(synthesis["created_at"]),
                "immutable_source_ids": source_ids,
            },
            "safety": {
                "mission": mission["safety"],
                "synthesis": json.loads(synthesis["safety_json"]),
                "operational_harmful_instructions_allowed": False,
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
            ON CONFLICT(mission_id,user_id,synthesis_id) DO NOTHING""",
            (
                report_id, str(mission_id), int(user_id), synthesis["synthesis_id"],
                synthesis["evidence_hash"], report_hash, encoded,
            ))
        cur.execute("""SELECT * FROM velia_research_reports
            WHERE mission_id=%s AND user_id=%s AND synthesis_id=%s""",
            (str(mission_id), int(user_id), synthesis["synthesis_id"]))
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
