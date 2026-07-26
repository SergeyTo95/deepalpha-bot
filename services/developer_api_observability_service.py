import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from db.database import get_connection

QUICK_ANALYSIS_JOB_TYPE = "quick_analysis"
_OBSERVABILITY_TABLES_READY = False


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _rows_to_dicts(cursor, rows) -> List[Dict[str, Any]]:
    return [item for item in (_row_to_dict(cursor, row) for row in rows or []) if item]


def _safe_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def worker_stale_seconds() -> int:
    timeout = _safe_env_int("API_ANALYSIS_TIMEOUT_SECONDS", 120, 30, 600)
    return _safe_env_int(
        "API_WORKER_STALE_SECONDS",
        max(180, timeout + 60),
        30,
        3600,
    )


def queue_warning_size() -> int:
    return _safe_env_int("API_QUEUE_WARNING_SIZE", 10, 1, 100000)


def queue_warning_age_seconds() -> int:
    return _safe_env_int("API_QUEUE_WARNING_AGE_SECONDS", 180, 15, 86400)


def ensure_api_observability_tables() -> None:
    global _OBSERVABILITY_TABLES_READY
    if _OBSERVABILITY_TABLES_READY:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_worker_heartbeats (
                worker_id TEXT PRIMARY KEY,
                worker_type TEXT NOT NULL DEFAULT 'quick_analysis',
                status TEXT NOT NULL DEFAULT 'starting',
                current_job_id TEXT,
                started_at TIMESTAMP NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_worker_heartbeats_seen ON api_worker_heartbeats(last_seen_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_worker_heartbeats_type ON api_worker_heartbeats(worker_type, last_seen_at DESC)"
        )
        conn.commit()
        _OBSERVABILITY_TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def touch_api_worker_heartbeat(
    worker_id: str,
    *,
    status: str,
    current_job_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    ensure_api_observability_tables()
    identity = str(worker_id or "").strip()[:120]
    if not identity:
        raise ValueError("worker_id_required")
    clean_status = str(status or "unknown").strip().lower()[:40] or "unknown"
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, default=str)[:8000]
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_worker_heartbeats (
                worker_id, worker_type, status, current_job_id, started_at, last_seen_at, metadata_json
            ) VALUES (%s, 'quick_analysis', %s, %s, NOW(), NOW(), %s)
            ON CONFLICT (worker_id) DO UPDATE SET
                status=EXCLUDED.status,
                current_job_id=EXCLUDED.current_job_id,
                last_seen_at=NOW(),
                metadata_json=EXCLUDED.metadata_json
            """,
            (identity, clean_status, str(current_job_id or "")[:80] or None, metadata_json),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serializable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _normalize_job(item: Dict[str, Any]) -> Dict[str, Any]:
    request_payload = _parse_json_object(item.get("request_json"))
    result_payload = _parse_json_object(item.get("result_json"))
    normalized = {
        key: _serializable(value)
        for key, value in item.items()
        if key not in {"request_json", "result_json"}
    }
    normalized.update({
        "market_url": str(request_payload.get("market_url") or ""),
        "language": str(request_payload.get("language") or "en"),
        "mode": str(request_payload.get("mode") or "quick"),
        "decision": str(result_payload.get("decision") or ""),
        "side": str(result_payload.get("side") or ""),
        "question": str(result_payload.get("question") or "")[:500],
        "summary": str(result_payload.get("summary") or "")[:600],
    })
    return normalized


def _job_where(status: Optional[str], client_id: Optional[int]) -> tuple[str, List[Any]]:
    clauses = ["j.job_type=%s"]
    params: List[Any] = [QUICK_ANALYSIS_JOB_TYPE]
    clean_status = str(status or "").strip().lower()
    if clean_status in {"queued", "running", "success", "error", "refund_pending"}:
        clauses.append("j.status=%s")
        params.append(clean_status)
    if client_id is not None:
        clauses.append("j.client_id=%s")
        params.append(int(client_id))
    return " AND ".join(clauses), params


def list_admin_api_jobs(
    *,
    limit: int = 100,
    status: Optional[str] = None,
    client_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    ensure_api_observability_tables()
    where, params = _job_where(status, client_id)
    params.append(max(1, min(int(limit), 500)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT j.job_id, j.client_id, c.name AS client_name, j.key_id,
                   j.status, j.progress, j.attempt_count, j.worker_id,
                   j.units_reserved, j.units_charged, j.error,
                   j.created_at, j.started_at, j.finished_at, j.updated_at,
                   j.heartbeat_at, j.lease_until, j.request_json, j.result_json,
                   r.status AS reservation_status,
                   EXTRACT(EPOCH FROM (COALESCE(j.finished_at, NOW()) - COALESCE(j.started_at, j.created_at))) AS duration_seconds
            FROM api_jobs j
            JOIN api_clients c ON c.id=j.client_id
            LEFT JOIN api_credit_reservations r ON r.job_id=j.job_id
            WHERE {where}
            ORDER BY j.created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        return [_normalize_job(item) for item in _rows_to_dicts(cursor, cursor.fetchall())]
    finally:
        cursor.close()
        conn.close()


def list_user_api_jobs(
    *,
    user_id: int,
    client_id: int,
    limit: int = 30,
) -> Dict[str, Any]:
    ensure_api_observability_tables()
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
            WHERE client_id=%s AND job_type=%s
            GROUP BY status
            """,
            (cid, QUICK_ANALYSIS_JOB_TYPE),
        )
        counts = {
            str(item.get("status") or "unknown"): int(item.get("count") or 0)
            for item in _rows_to_dicts(cursor, cursor.fetchall())
        }

        cursor.execute(
            """
            SELECT j.job_id, j.client_id, j.status, j.progress, j.attempt_count,
                   j.units_reserved, j.units_charged, j.error,
                   j.created_at, j.started_at, j.finished_at, j.updated_at,
                   j.request_json, j.result_json,
                   r.status AS reservation_status,
                   EXTRACT(EPOCH FROM (COALESCE(j.finished_at, NOW()) - COALESCE(j.started_at, j.created_at))) AS duration_seconds
            FROM api_jobs j
            LEFT JOIN api_credit_reservations r ON r.job_id=j.job_id
            WHERE j.client_id=%s AND j.job_type=%s
            ORDER BY j.created_at DESC
            LIMIT %s
            """,
            (cid, QUICK_ANALYSIS_JOB_TYPE, max(1, min(int(limit), 100))),
        )
        jobs = [_normalize_job(item) for item in _rows_to_dicts(cursor, cursor.fetchall())]
        return {
            "project": {"id": int(project.get("id") or 0), "name": str(project.get("name") or "")},
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


def get_api_runtime_health(*, include_workers: bool = False) -> Dict[str, Any]:
    ensure_api_observability_tables()
    stale_after = worker_stale_seconds()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status='queued') AS queued,
                COUNT(*) FILTER (WHERE status='running') AS running,
                COUNT(*) FILTER (WHERE status='refund_pending') AS refund_pending,
                COUNT(*) FILTER (WHERE status='running' AND (lease_until IS NULL OR lease_until < NOW())) AS stale_running,
                COUNT(*) FILTER (WHERE status='success' AND finished_at >= NOW() - INTERVAL '24 hours') AS success_24h,
                COUNT(*) FILTER (WHERE status='error' AND finished_at >= NOW() - INTERVAL '24 hours') AS error_24h,
                EXTRACT(EPOCH FROM (NOW() - MIN(created_at) FILTER (WHERE status='queued'))) AS oldest_queued_age_seconds,
                AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) FILTER (
                    WHERE status IN ('success', 'error')
                      AND started_at IS NOT NULL
                      AND finished_at >= NOW() - INTERVAL '24 hours'
                ) AS avg_duration_seconds_24h
            FROM api_jobs
            WHERE job_type=%s
            """,
            (QUICK_ANALYSIS_JOB_TYPE,),
        )
        metrics = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            SELECT worker_id, worker_type, status, current_job_id,
                   started_at, last_seen_at, metadata_json,
                   EXTRACT(EPOCH FROM (NOW() - last_seen_at)) AS heartbeat_age_seconds,
                   (last_seen_at >= NOW() - make_interval(secs => %s)) AS fresh
            FROM api_worker_heartbeats
            WHERE worker_type='quick_analysis'
            ORDER BY last_seen_at DESC
            LIMIT 20
            """,
            (stale_after,),
        )
        workers = _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()

    for worker in workers:
        worker["metadata"] = _parse_json_object(worker.pop("metadata_json", "{}"))
        for key, value in list(worker.items()):
            worker[key] = _serializable(value)

    queue = {
        "queued": int(metrics.get("queued") or 0),
        "running": int(metrics.get("running") or 0),
        "refund_pending": int(metrics.get("refund_pending") or 0),
        "stale_running": int(metrics.get("stale_running") or 0),
        "oldest_queued_age_seconds": round(float(metrics.get("oldest_queued_age_seconds") or 0), 1),
    }
    recent = {
        "success_24h": int(metrics.get("success_24h") or 0),
        "error_24h": int(metrics.get("error_24h") or 0),
        "avg_duration_seconds_24h": round(float(metrics.get("avg_duration_seconds_24h") or 0), 1),
    }
    fresh_workers = sum(1 for worker in workers if bool(worker.get("fresh")))
    warnings: List[str] = []
    if fresh_workers == 0:
        warnings.append("no_fresh_api_worker")
    if queue["queued"] >= queue_warning_size():
        warnings.append("api_queue_size_high")
    if queue["oldest_queued_age_seconds"] >= queue_warning_age_seconds():
        warnings.append("api_queue_wait_high")
    if queue["stale_running"] > 0:
        warnings.append("stale_running_jobs")
    if queue["refund_pending"] > 0:
        warnings.append("refunds_pending")

    status = "operational" if not warnings else "degraded"
    result: Dict[str, Any] = {
        "status": status,
        "worker_available": fresh_workers > 0,
        "fresh_workers": fresh_workers,
        "worker_stale_after_seconds": stale_after,
        "queue": queue,
        "recent": recent,
        "warnings": warnings,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }
    if include_workers:
        result["workers"] = workers
    return result
