from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from db.database import get_connection
from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_review_service as review_service
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_coding_service as coding_service

logger = logging.getLogger(__name__)


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def recent_review_run_snapshot(limit: int = 50) -> List[Dict[str, Any]]:
    """Read-only bounded operational snapshot for Autopilot review debugging."""
    maximum = min(50, max(1, int(limit or 1)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                r.run_id,r.status,r.error_code,r.pull_request_number,r.updated_at,
                ci.attempt_number,ci.head_sha,ci.status,ci.error_code,
                ra.review_id,ra.state,ra.status,ra.error_code,ra.commit_sha,ra.updated_at
            FROM velia_developer_autopilot_runs r
            LEFT JOIN LATERAL (
                SELECT attempt_number,head_sha,status,error_code
                FROM velia_developer_autopilot_ci_attempts
                WHERE run_id=r.run_id
                ORDER BY attempt_number DESC
                LIMIT 1
            ) ci ON TRUE
            LEFT JOIN LATERAL (
                SELECT review_id,state,status,error_code,commit_sha,updated_at
                FROM velia_developer_autopilot_review_actions
                WHERE run_id=r.run_id
                ORDER BY updated_at DESC
                LIMIT 1
            ) ra ON TRUE
            WHERE r.pull_request_number>0
              AND (
                    r.status IN ('ready_for_review','waiting_ci','repairing')
                    OR r.status='blocked'
                  )
            ORDER BY r.updated_at DESC
            LIMIT %s
            """,
            (maximum,),
        )
        rows = cursor.fetchall() or []
        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "run_id": str(_value(row, "run_id", 0, "")),
                    "run_status": str(_value(row, "status", 1, "")),
                    "run_error": str(_value(row, "error_code", 2, "") or ""),
                    "pr": int(_value(row, "pull_request_number", 3, 0) or 0),
                    "run_updated_at": str(_value(row, "updated_at", 4, "") or ""),
                    "ci_attempt": int(_value(row, "attempt_number", 5, 0) or 0),
                    "ci_head": str(_value(row, "head_sha", 6, "") or "")[:40],
                    "ci_status": str(_value(row, "status", 7, "") or ""),
                    "ci_error": str(_value(row, "error_code", 8, "") or ""),
                    "review_id": int(_value(row, "review_id", 9, 0) or 0),
                    "review_state": str(_value(row, "state", 10, "") or ""),
                    "review_status": str(_value(row, "status", 11, "") or ""),
                    "review_error": str(_value(row, "error_code", 12, "") or ""),
                    "review_commit": str(_value(row, "commit_sha", 13, "") or "")[:40],
                    "review_updated_at": str(_value(row, "updated_at", 14, "") or ""),
                }
            )
        return result
    finally:
        cursor.close()
        conn.close()


def log_runtime_snapshot() -> None:
    """Best-effort startup diagnostics. Never blocks app startup."""
    try:
        rows = recent_review_run_snapshot()
        logger.info(
            "VELIA_AUTOPILOT_REVIEW_RUNTIME_SNAPSHOT enabled=%s worker_enabled=%s "
            "coding_enabled=%s ci_enabled=%s rows=%s",
            review_service.review_loop_enabled(),
            autopilot.worker_enabled(),
            coding_service.coding_enabled(),
            ci_service.ci_watch_enabled(),
            json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str),
        )
    except Exception as exc:
        logger.warning(
            "VELIA_AUTOPILOT_REVIEW_RUNTIME_SNAPSHOT_FAILED error=%s",
            exc.__class__.__name__,
        )
