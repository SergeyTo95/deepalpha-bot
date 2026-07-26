import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from db.database import get_connection
from services.developer_api_opportunity_service import ensure_api_opportunity_tables


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serializable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def list_user_opportunity_scans(*, user_id: int, client_id: int, limit: int = 30) -> Dict[str, Any]:
    ensure_api_opportunity_tables()
    uid = int(user_id)
    cid = int(client_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT c.id, c.name
            FROM api_clients c
            JOIN api_client_owners o ON o.client_id=c.id
            WHERE o.user_id=%s AND c.id=%s
            LIMIT 1
            """,
            (uid, cid),
        )
        project = _row_to_dict(cursor, cursor.fetchone())
        if not project:
            return {"project": None, "summary": {}, "jobs": []}

        cursor.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM api_jobs
            WHERE client_id=%s AND job_type='opportunity_scan'
            GROUP BY status
            """,
            (cid,),
        )
        counts: Dict[str, int] = {}
        for raw in cursor.fetchall() or []:
            item = _row_to_dict(cursor, raw) or {}
            counts[str(item.get("status") or "unknown")] = int(item.get("count") or 0)

        cursor.execute(
            """
            SELECT j.job_id, j.status, j.progress, j.attempt_count,
                   j.units_reserved, j.units_charged, j.error,
                   j.created_at, j.started_at, j.finished_at,
                   j.request_json, j.result_json,
                   r.status AS reservation_status,
                   EXTRACT(EPOCH FROM (
                       COALESCE(j.finished_at, NOW()) - COALESCE(j.started_at, j.created_at)
                   )) AS duration_seconds
            FROM api_jobs j
            LEFT JOIN api_credit_reservations r ON r.job_id=j.job_id
            WHERE j.client_id=%s AND j.job_type='opportunity_scan'
            ORDER BY j.created_at DESC
            LIMIT %s
            """,
            (cid, max(1, min(int(limit), 100))),
        )
        jobs: List[Dict[str, Any]] = []
        for raw in cursor.fetchall() or []:
            item = _row_to_dict(cursor, raw) or {}
            request_payload = _parse_json(item.pop("request_json", "{}"))
            result_payload = _parse_json(item.pop("result_json", "{}"))
            candidates = result_payload.get("candidates") if isinstance(result_payload.get("candidates"), list) else []
            top_candidates = []
            for candidate in candidates[:5]:
                if not isinstance(candidate, dict):
                    continue
                top_candidates.append({
                    "question": str(candidate.get("question") or "")[:300],
                    "url": str(candidate.get("url") or "")[:1200],
                    "score": int(candidate.get("score") or 0),
                    "tier": str(candidate.get("tier") or ""),
                    "yes_price": float(candidate.get("yes_price") or 0),
                    "no_price": float(candidate.get("no_price") or 0),
                })
            jobs.append({
                "job_id": str(item.get("job_id") or ""),
                "status": str(item.get("status") or "queued"),
                "progress": int(item.get("progress") or 0),
                "attempt_count": int(item.get("attempt_count") or 0),
                "category": str(request_payload.get("category") or "All"),
                "language": str(request_payload.get("language") or "en"),
                "result_limit": int(request_payload.get("result_limit") or 0),
                "min_score": int(request_payload.get("min_score") or 0),
                "candidate_count": int(result_payload.get("candidate_count") or 0),
                "top_candidates": top_candidates,
                "units_reserved": int(item.get("units_reserved") or 0),
                "units_charged": int(item.get("units_charged") or 0),
                "reservation_status": str(item.get("reservation_status") or ""),
                "error": str(item.get("error") or ""),
                "duration_seconds": round(float(item.get("duration_seconds") or 0), 1),
                "created_at": _serializable(item.get("created_at")),
                "started_at": _serializable(item.get("started_at")),
                "finished_at": _serializable(item.get("finished_at")),
            })

        return {
            "project": {
                "id": int(project.get("id") or 0),
                "name": str(project.get("name") or ""),
            },
            "summary": {
                "queued": counts.get("queued", 0),
                "running": counts.get("running", 0),
                "success": counts.get("success", 0),
                "error": counts.get("error", 0),
                "refund_pending": counts.get("refund_pending", 0),
                "total": sum(counts.values()),
            },
            "jobs": jobs,
        }
    finally:
        cursor.close()
        conn.close()
