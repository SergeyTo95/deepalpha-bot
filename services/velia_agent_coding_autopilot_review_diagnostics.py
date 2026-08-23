from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from db.database import get_connection
from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_merge_policy_service as merge_policy
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


def recent_merge_policy_targets(limit: int = 3) -> List[Dict[str, Any]]:
    """Return only the internal identifiers needed to execute bounded dry-runs."""
    maximum = min(3, max(1, int(limit or 1)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT run_id,user_id,pull_request_number
            FROM velia_developer_autopilot_runs
            WHERE pull_request_number>0
              AND status IN ('ready_for_review','waiting_ci','blocked')
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (maximum,),
        )
        return [
            {
                "run_id": str(_value(row, "run_id", 0, "")),
                "user_id": int(_value(row, "user_id", 1, 0) or 0),
                "pr": int(_value(row, "pull_request_number", 2, 0) or 0),
            }
            for row in (cursor.fetchall() or [])
        ]
    finally:
        cursor.close()
        conn.close()


def _safe_merge_policy_result(result: Dict[str, Any], pr: int) -> Dict[str, Any]:
    gates = result.get("gates") if isinstance(result.get("gates"), dict) else {}
    attempt = gates.get("ci_attempt") if isinstance(gates.get("ci_attempt"), dict) else {}
    diff = gates.get("diff") if isinstance(gates.get("diff"), dict) else {}
    pull = gates.get("pull_request") if isinstance(gates.get("pull_request"), dict) else {}
    reasons = result.get("reasons") if isinstance(result.get("reasons"), list) else []
    return {
        "run_id": str(result.get("run_id") or ""),
        "pr": int(pr or pull.get("number") or 0),
        "mode": str(result.get("mode") or ""),
        "execution_supported": bool(result.get("execution_supported")),
        "auto_merge": bool(result.get("auto_merge")),
        "deployment": bool(result.get("deployment")),
        "recommendation": str(result.get("recommendation") or ""),
        "would_allow_merge": bool(result.get("would_allow_merge")),
        "reason_codes": [str(item.get("code") or "")[:120] for item in reasons if isinstance(item, dict)],
        "run_status": str(gates.get("run_status") or ""),
        "branch_head": str(gates.get("branch_head") or "")[:40],
        "ci_attempt": int(attempt.get("attempt_number") or 0),
        "ci_status": str(attempt.get("status") or ""),
        "ci_head": str(attempt.get("head_sha") or "")[:40],
        "requested_changes_count": len(gates.get("requested_changes") or []),
        "unresolved_review_actions": int(gates.get("unresolved_review_actions") or 0),
        "diff": {
            "file_count": int(diff.get("file_count") or 0),
            "additions": int(diff.get("additions") or 0),
            "deletions": int(diff.get("deletions") or 0),
            "changes": int(diff.get("changes") or 0),
        },
        "pull_request": {
            "number": int(pull.get("number") or pr or 0),
            "state": str(pull.get("state") or ""),
            "draft": bool(pull.get("draft")),
            "mergeable": pull.get("mergeable"),
            "mergeable_state": str(pull.get("mergeable_state") or ""),
            "base_ref": str(pull.get("base_ref") or "")[:200],
            "head_ref": str(pull.get("head_ref") or "")[:200],
            "head_sha": str(pull.get("head_sha") or "")[:40],
        },
    }


def log_merge_policy_dry_run_snapshot() -> None:
    """Execute the real read-only merge policy for a few recent runs and log safe gates."""
    try:
        if not merge_policy.merge_policy_enabled():
            logger.info(
                "VELIA_AUTOPILOT_MERGE_POLICY_DRY_RUN_SNAPSHOT enabled=False results=[]"
            )
            return
        results: List[Dict[str, Any]] = []
        for target in recent_merge_policy_targets():
            try:
                evaluated = merge_policy.evaluate_merge_policy(
                    int(target["user_id"]), str(target["run_id"])
                )
                results.append(_safe_merge_policy_result(evaluated, int(target["pr"])))
            except Exception as exc:
                results.append(
                    {
                        "run_id": str(target.get("run_id") or ""),
                        "pr": int(target.get("pr") or 0),
                        "error": str(getattr(exc, "code", exc.__class__.__name__))[:120],
                    }
                )
        logger.info(
            "VELIA_AUTOPILOT_MERGE_POLICY_DRY_RUN_SNAPSHOT enabled=True results=%s",
            json.dumps(results, ensure_ascii=False, separators=(",", ":"), default=str),
        )
    except Exception as exc:
        logger.warning(
            "VELIA_AUTOPILOT_MERGE_POLICY_DRY_RUN_SNAPSHOT_FAILED error=%s",
            exc.__class__.__name__,
        )


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
    log_merge_policy_dry_run_snapshot()
