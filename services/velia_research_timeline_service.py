"""Research Knowledge Timeline & material Scientific Alerting for VELIA Research Center.

The timeline is reconstructed from immutable Claim Ledger snapshots and completed
Living Reassessment results. Alerts are persisted only for scientifically material
changes; candidate discovery alone never creates a notification.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services.velia_chat_service import _iso


TIMELINE_VERSION = 1
MAX_TIMELINE_ITEMS = 200
MAX_ALERTS_PAGE = 50
MAX_ALERT_SYNC_SNAPSHOTS = 120
MAX_ALERT_SYNC_REASSESSMENTS = 80
MATERIAL_REASSESSMENT_CHANGES = {
    "claim_stage_changed",
    "retracted_or_withdrawn_evidence_removed_from_living_pool",
    "citation_integrity_alert",
    "caution_added",
    "living_reanalysis_contradicts_claim",
}
STAGE_RANK = {
    "exploratory": 1,
    "preregistered": 2,
    "replicated": 3,
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return (
        center.enabled()
        and claims.enabled()
        and _env_bool("VELIA_RESEARCH_KNOWLEDGE_TIMELINE_ENABLED", False)
    )


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "timeline_version": TIMELINE_VERSION,
        "immutable_source_history": True,
        "candidate_discovery_alerts": False,
        "material_change_alerts_only": True,
        "confidence_direction_tracking": True,
        "citation_integrity_tracking": True,
        "retraction_tracking": True,
        "report_as_of_supported": True,
        "extra_model_calls": 0,
        "arbitrary_code": False,
        "arbitrary_shell": False,
        "arbitrary_file_access": False,
        "arbitrary_url_fetch": False,
    }


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def ensure_tables() -> None:
    claims.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_scientific_alerts (
            alert_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            claim_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            alert_kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            summary TEXT NOT NULL,
            details_json TEXT NOT NULL,
            alert_hash TEXT NOT NULL,
            acknowledged_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(user_id,alert_hash),
            FOREIGN KEY(claim_id) REFERENCES velia_research_claims(claim_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_scientific_alerts
            ON velia_research_scientific_alerts(
                mission_id,user_id,acknowledged_at,created_at DESC
            )""")


def _parse_as_of(value: Any) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise projects.ProjectError("invalid_research_as_of")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise projects.ProjectError("invalid_research_as_of") from exc
    if parsed.tzinfo is None:
        raise projects.ProjectError("research_as_of_timezone_required")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _snapshot_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["snapshot_id"],
        "claim_id": row["claim_id"],
        "mission_id": row["mission_id"],
        "stage": row["stage"],
        "evidence_hash": row["evidence_hash"],
        "evidence": json.loads(row["evidence_json"]),
        "wording": row["wording"],
        "created_at": _iso(row["created_at"]),
    }


def _report_item(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row["report_id"],
        "mission_id": row["mission_id"],
        "report_hash": row["report_hash"],
        "version": (
            json.loads(row["report_json"]).get("version")
            if row.get("report_json") else None
        ),
        "created_at": _iso(row["created_at"]),
    }


def _alert_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["alert_id"],
        "mission_id": row["mission_id"],
        "claim_id": row["claim_id"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "kind": row["alert_kind"],
        "severity": row["severity"],
        "summary": row["summary"],
        "details": json.loads(row["details_json"]),
        "acknowledged": row["acknowledged_at"] is not None,
        "acknowledged_at": _iso(row["acknowledged_at"]) if row["acknowledged_at"] else None,
        "created_at": _iso(row["created_at"]),
    }


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS table_name", (name,))
    row = cur.fetchone()
    return bool(row and row.get("table_name"))


def _confidence_transition(
    before: Optional[Dict[str, Any]],
    after: Dict[str, Any],
) -> str:
    if before is None:
        return "baseline"
    before_stage = str(before.get("stage") or "")
    after_stage = str(after.get("stage") or "")
    if before_stage != after_stage:
        if after_stage == "contradicted":
            return "decreased"
        if before_stage == "contradicted":
            return "increased"
        before_rank = STAGE_RANK.get(before_stage, 0)
        after_rank = STAGE_RANK.get(after_stage, 0)
        if after_rank > before_rank:
            return "increased"
        if after_rank < before_rank:
            return "decreased"
        return "changed"

    evidence = after.get("evidence") or {}
    calibration = evidence.get("evidence_calibration")
    if isinstance(calibration, dict):
        action = (calibration.get("calibration") or {}).get("claim_action")
        if action in {"caution", "contradict"}:
            return "decreased"
    if before.get("evidence_hash") != after.get("evidence_hash"):
        return "updated"
    return "stable"


def _latest_report_before(
    reports: List[Dict[str, Any]], when: datetime
) -> Optional[Dict[str, Any]]:
    current = None
    for row in reports:
        if row["created_at"] <= when:
            current = row
        else:
            break
    return _report_item(current)


def _claim_snapshots(user_id: int, claim_id: str) -> List[Dict[str, Any]]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_claim_snapshots
            WHERE claim_id=%s AND user_id=%s
            ORDER BY created_at ASC,snapshot_id ASC LIMIT %s""",
            (str(claim_id), int(user_id), MAX_TIMELINE_ITEMS))
        return [_snapshot_item(row) for row in cur.fetchall()]


def _mission_reports(user_id: int, mission_id: str) -> List[Dict[str, Any]]:
    with projects.transaction() as cur:
        if not _table_exists(cur, "velia_research_reports"):
            return []
        cur.execute("""SELECT report_id,mission_id,report_hash,report_json,created_at
            FROM velia_research_reports
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at ASC,report_id ASC LIMIT 500""",
            (str(mission_id), int(user_id)))
        return list(cur.fetchall())


def _reassessment_events(
    user_id: int,
    mission_id: str,
    claim_id: str,
) -> List[Dict[str, Any]]:
    with projects.transaction() as cur:
        if not _table_exists(cur, "velia_research_living_reassessments"):
            return []
        cur.execute("""SELECT run_id,scan_id,status,result_json,
                   previous_report_id,previous_report_hash,
                   new_report_id,new_report_hash,created_at,updated_at
            FROM velia_research_living_reassessments
            WHERE mission_id=%s AND user_id=%s
              AND status='completed' AND result_json IS NOT NULL
            ORDER BY updated_at ASC,run_id ASC LIMIT %s""",
            (
                str(mission_id), int(user_id),
                MAX_ALERT_SYNC_REASSESSMENTS,
            ))
        rows = list(cur.fetchall())

    output: List[Dict[str, Any]] = []
    for row in rows:
        result = json.loads(row["result_json"])
        diffs = result.get("scientific_diff")
        if not isinstance(diffs, list):
            continue
        claim_results = result.get("claim_results")
        by_claim = {
            str(item.get("claim_id")): item
            for item in claim_results
            if isinstance(item, dict) and item.get("claim_id")
        } if isinstance(claim_results, list) else {}
        for diff in diffs:
            if not isinstance(diff, dict) or str(diff.get("claim_id") or "") != str(claim_id):
                continue
            related = by_claim.get(str(claim_id), {})
            pooled = related.get("living_meta") if isinstance(related, dict) else {}
            calibration = pooled.get("calibration") if isinstance(pooled, dict) else {}
            reasons = calibration.get("reasons") if isinstance(calibration, dict) else []
            changes = [
                str(value) for value in (diff.get("changes") or [])
                if isinstance(value, str)
            ]
            material_reasons = [
                value for value in changes if value in MATERIAL_REASSESSMENT_CHANGES
            ]
            output.append({
                "id": _sha({
                    "kind": "living_reassessment",
                    "run_id": row["run_id"],
                    "claim_id": claim_id,
                    "changes": changes,
                }),
                "type": "living_reassessment",
                "source_id": row["run_id"],
                "scan_id": row["scan_id"],
                "claim_id": str(claim_id),
                "occurred_at": _iso(row["updated_at"]),
                "_occurred_dt": row["updated_at"],
                "before": {
                    "snapshot_id": diff.get("before_snapshot_id"),
                    "stage": diff.get("before_stage"),
                    "evidence_hash": diff.get("before_evidence_hash"),
                    "wording": diff.get("before_wording"),
                },
                "after": {
                    "snapshot_id": diff.get("after_snapshot_id"),
                    "stage": diff.get("after_stage"),
                    "evidence_hash": diff.get("after_evidence_hash"),
                    "wording": diff.get("after_wording"),
                },
                "changes": changes,
                "material": bool(material_reasons),
                "material_reasons": material_reasons,
                "confidence_transition": (
                    "decreased"
                    if (
                        "living_reanalysis_contradicts_claim" in changes
                        or "caution_added" in changes
                        or "citation_integrity_alert" in changes
                        or "retracted_or_withdrawn_evidence_removed_from_living_pool" in changes
                    )
                    else (
                        "changed"
                        if "claim_stage_changed" in changes
                        else "stable"
                    )
                ),
                "new_verified_external_evidence": (
                    "new_verified_external_evidence_added" in changes
                ),
                "citation_integrity_alert": (
                    "citation_integrity_alert" in changes
                ),
                "retraction_or_withdrawal": (
                    "retracted_or_withdrawn_evidence_removed_from_living_pool" in changes
                ),
                "calibration_action": diff.get("living_calibration_action"),
                "calibration_reasons": list(reasons or [])[:12],
                "previous_report_id": row["previous_report_id"],
                "previous_report_hash": row["previous_report_hash"],
                "current_report_id": row["new_report_id"],
                "current_report_hash": row["new_report_hash"],
            })
    return output


def claim_timeline(
    user_id: int,
    claim_id: str,
    offset: int = 0,
) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_knowledge_timeline_disabled", 503)
    claim = claims.get_claim(user_id, claim_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")

    snapshots = _claim_snapshots(user_id, claim_id)
    reports = _mission_reports(user_id, claim["mission_id"])
    events: List[Dict[str, Any]] = [{
        "id": _sha({"kind": "claim_registered", "claim_id": claim["id"]}),
        "type": "claim_registered",
        "source_id": claim["id"],
        "claim_id": claim["id"],
        "occurred_at": claim["created_at"],
        "_occurred_dt": datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00")).replace(tzinfo=None),
        "statement": claim["statement"],
        "material": False,
        "material_reasons": [],
        "confidence_transition": "baseline",
    }]

    previous: Optional[Dict[str, Any]] = None
    for snapshot in snapshots:
        when = datetime.fromisoformat(snapshot["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        transition = _confidence_transition(previous, snapshot)
        evidence = snapshot.get("evidence") or {}
        calibration = evidence.get("evidence_calibration")
        action = (
            (calibration.get("calibration") or {}).get("claim_action")
            if isinstance(calibration, dict) else None
        )
        material_reasons: List[str] = []
        if previous is not None and previous.get("stage") != snapshot.get("stage"):
            material_reasons.append("claim_stage_changed")
        if action == "caution":
            material_reasons.append("caution_added")
        elif action == "contradict":
            material_reasons.append("claim_contradicted")
        events.append({
            "id": _sha({
                "kind": "claim_snapshot",
                "snapshot_id": snapshot["id"],
            }),
            "type": "claim_snapshot",
            "source_id": snapshot["id"],
            "claim_id": claim["id"],
            "occurred_at": snapshot["created_at"],
            "_occurred_dt": when,
            "stage": snapshot["stage"],
            "wording": snapshot["wording"],
            "evidence_hash": snapshot["evidence_hash"],
            "confidence_transition": transition,
            "material": bool(material_reasons),
            "material_reasons": material_reasons,
            "calibration": calibration,
            "report_current_at_event": _latest_report_before(reports, when),
        })
        previous = snapshot

    events.extend(_reassessment_events(user_id, claim["mission_id"], claim["id"]))
    for item in events:
        if "report_current_at_event" not in item:
            item["report_current_at_event"] = _latest_report_before(
                reports, item["_occurred_dt"]
            )
    events.sort(key=lambda item: (item["_occurred_dt"], item["id"]), reverse=True)
    for item in events:
        item.pop("_occurred_dt", None)

    page = events[offset:offset + 50]
    return {
        "claim": claim,
        "timeline_version": TIMELINE_VERSION,
        "timeline": page,
        "next_offset": offset + 50 if len(events) > offset + 50 else None,
        "total_known_events": len(events),
        "policy": {
            "historical_sources_are_immutable": True,
            "candidate_discovery_alone_is_material": False,
            "alerts_require_material_scientific_change": True,
        },
    }


def _insert_alert(
    user_id: int,
    mission_id: str,
    claim_id: str,
    source_type: str,
    source_id: str,
    alert_kind: str,
    severity: str,
    summary: str,
    details: Dict[str, Any],
) -> None:
    payload = {
        "mission_id": mission_id,
        "claim_id": claim_id,
        "source_type": source_type,
        "source_id": source_id,
        "alert_kind": alert_kind,
        "severity": severity,
        "summary": summary,
        "details": details,
    }
    alert_hash = _sha(payload)
    alert_id = _sha({"alert_hash": alert_hash, "user_id": int(user_id)})
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_scientific_alerts(
            alert_id,mission_id,claim_id,user_id,source_type,source_id,
            alert_kind,severity,summary,details_json,alert_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(user_id,alert_hash) DO NOTHING""",
            (
                alert_id, str(mission_id), str(claim_id), int(user_id),
                source_type, source_id, alert_kind, severity, summary,
                _json(details), alert_hash,
            ))


def _sync_snapshot_alerts(user_id: int, claim: Dict[str, Any]) -> None:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_claim_snapshots
            WHERE claim_id=%s AND user_id=%s
            ORDER BY created_at ASC,snapshot_id ASC LIMIT %s""",
            (claim["id"], int(user_id), MAX_ALERT_SYNC_SNAPSHOTS))
        rows = list(cur.fetchall())

    previous = None
    for row in rows:
        current = _snapshot_item(row)
        if previous is None:
            previous = current
            continue
        evidence = current.get("evidence") or {}
        calibration = evidence.get("evidence_calibration")
        action = (
            (calibration.get("calibration") or {}).get("claim_action")
            if isinstance(calibration, dict) else None
        )
        source_type = (
            str(calibration.get("source_type"))
            if isinstance(calibration, dict) and calibration.get("source_type")
            else "claim_ledger"
        )
        if source_type == "living_reassessment":
            previous = current
            continue

        before_stage = previous.get("stage")
        after_stage = current.get("stage")
        if before_stage != after_stage:
            transition = _confidence_transition(previous, current)
            if after_stage == "contradicted":
                kind, severity = "claim_contradicted", "critical"
            elif transition == "increased":
                kind, severity = "confidence_increased", "medium"
            else:
                kind, severity = "confidence_decreased", "high"
            _insert_alert(
                user_id,
                claim["mission_id"],
                claim["id"],
                source_type,
                current["id"],
                kind,
                severity,
                f"Scientific claim stage changed from {before_stage} to {after_stage}.",
                {
                    "before_snapshot_id": previous["id"],
                    "after_snapshot_id": current["id"],
                    "before_stage": before_stage,
                    "after_stage": after_stage,
                    "confidence_transition": transition,
                    "evidence_hash": current["evidence_hash"],
                },
            )
        elif action in {"caution", "contradict"}:
            kind = "claim_contradicted" if action == "contradict" else "evidence_caution"
            severity = "critical" if action == "contradict" else "high"
            _insert_alert(
                user_id,
                claim["mission_id"],
                claim["id"],
                source_type,
                current["id"],
                kind,
                severity,
                (
                    "External evidence contradicts the registered claim."
                    if action == "contradict"
                    else "External evidence requires a more cautious interpretation."
                ),
                {
                    "before_snapshot_id": previous["id"],
                    "after_snapshot_id": current["id"],
                    "calibration": calibration,
                    "confidence_transition": "decreased",
                },
            )
        previous = current


def _sync_reassessment_alerts(user_id: int, claim: Dict[str, Any]) -> None:
    for event in _reassessment_events(user_id, claim["mission_id"], claim["id"]):
        if not event["material"]:
            continue
        changes = set(event["changes"])
        if "living_reanalysis_contradicts_claim" in changes:
            kind, severity = "claim_contradicted", "critical"
            summary = "Living reassessment now contradicts the registered claim."
        elif "retracted_or_withdrawn_evidence_removed_from_living_pool" in changes:
            kind, severity = "evidence_retracted", "critical"
            summary = "Evidence used by this claim was retracted or withdrawn."
        elif "citation_integrity_alert" in changes:
            kind, severity = "citation_integrity_change", "high"
            summary = "A citation-integrity change affects evidence used by this claim."
        elif "caution_added" in changes:
            kind, severity = "evidence_caution", "high"
            summary = "Living reassessment added a material caution to this claim."
        elif "claim_stage_changed" in changes:
            after_stage = (event.get("after") or {}).get("stage")
            before_stage = (event.get("before") or {}).get("stage")
            kind = "claim_stage_changed"
            severity = "high" if after_stage == "contradicted" else "medium"
            summary = f"Scientific claim stage changed from {before_stage} to {after_stage}."
        else:
            continue
        _insert_alert(
            user_id,
            claim["mission_id"],
            claim["id"],
            "living_reassessment",
            event["source_id"],
            kind,
            severity,
            summary,
            {
                "scan_id": event.get("scan_id"),
                "changes": event.get("changes"),
                "material_reasons": event.get("material_reasons"),
                "calibration_action": event.get("calibration_action"),
                "calibration_reasons": event.get("calibration_reasons"),
                "previous_report_id": event.get("previous_report_id"),
                "current_report_id": event.get("current_report_id"),
                "confidence_transition": event.get("confidence_transition"),
            },
        )


def sync_mission_alerts(user_id: int, mission_id: str) -> None:
    if not enabled():
        return
    ensure_tables()
    center.get_mission(user_id, mission_id)
    for claim in claims.list_claims(user_id, mission_id)["claims"]:
        _sync_snapshot_alerts(user_id, claim)
        _sync_reassessment_alerts(user_id, claim)


def list_alerts(
    user_id: int,
    mission_id: str,
    offset: int = 0,
    unread_only: bool = False,
) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_knowledge_timeline_disabled", 503)
    offset = int(offset)
    if offset < 0 or offset > 1000:
        raise projects.ProjectError("invalid_offset")
    sync_mission_alerts(user_id, mission_id)

    where = "mission_id=%s AND user_id=%s"
    params: List[Any] = [str(mission_id), int(user_id)]
    if unread_only:
        where += " AND acknowledged_at IS NULL"
    with projects.transaction() as cur:
        cur.execute(
            f"""SELECT * FROM velia_research_scientific_alerts
                WHERE {where}
                ORDER BY created_at DESC,alert_id DESC LIMIT %s OFFSET %s""",
            tuple(params + [MAX_ALERTS_PAGE + 1, offset]),
        )
        rows = list(cur.fetchall())
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_scientific_alerts
            WHERE mission_id=%s AND user_id=%s AND acknowledged_at IS NULL""",
            (str(mission_id), int(user_id)))
        unread_count = int(cur.fetchone()["n"])
    return {
        "alerts": [_alert_row(row) for row in rows[:MAX_ALERTS_PAGE]],
        "unread_count": unread_count,
        "next_offset": (
            offset + MAX_ALERTS_PAGE
            if len(rows) > MAX_ALERTS_PAGE else None
        ),
        "material_change_only": True,
    }


def acknowledge_alert(user_id: int, alert_id: str) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_knowledge_timeline_disabled", 503)
    ensure_tables()
    with projects.transaction(user_id) as cur:
        cur.execute("""UPDATE velia_research_scientific_alerts
            SET acknowledged_at=COALESCE(acknowledged_at,NOW())
            WHERE alert_id=%s AND user_id=%s
            RETURNING *""", (str(alert_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_scientific_alert_not_found", 404)
    return _alert_row(row)


def report_as_of(user_id: int, mission_id: str, at: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_knowledge_timeline_disabled", 503)
    center.get_mission(user_id, mission_id)
    moment = _parse_as_of(at)
    with projects.transaction() as cur:
        if not _table_exists(cur, "velia_research_reports"):
            return {"at": at, "report": None}
        cur.execute("""SELECT report_id,mission_id,report_hash,report_json,created_at
            FROM velia_research_reports
            WHERE mission_id=%s AND user_id=%s AND created_at<=%s
            ORDER BY created_at DESC,report_id DESC LIMIT 1""",
            (str(mission_id), int(user_id), moment))
        row = cur.fetchone()
    return {
        "at": moment.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "report": _report_item(row),
    }


def claim_as_of(user_id: int, claim_id: str, at: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_knowledge_timeline_disabled", 503)
    claim = claims.get_claim(user_id, claim_id)
    moment = _parse_as_of(at)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_claim_snapshots
            WHERE claim_id=%s AND user_id=%s AND created_at<=%s
            ORDER BY created_at DESC,snapshot_id DESC LIMIT 1""",
            (claim["id"], int(user_id), moment))
        row = cur.fetchone()
    return {
        "at": moment.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "claim": claim,
        "snapshot": _snapshot_item(row) if row else None,
        "report": report_as_of(user_id, claim["mission_id"], at)["report"],
    }
