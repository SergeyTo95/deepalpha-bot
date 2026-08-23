from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover
    psycopg2 = None

from db.database import get_connection
from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_review_github_service as review_github
from services import velia_agent_coding_autopilot_review_service as review_service
from services import velia_agent_coding_autopilot_review_store as review_store
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_github_write_service as write_service

logger = logging.getLogger(__name__)
_RECOVERABLE_ERROR = "github_not_found"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def max_candidates_per_tick() -> int:
    return _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_REVIEW_RECOVERY_MAX_PER_TICK",
        2,
        1,
        5,
    )


def _utcnow() -> datetime:
    return datetime.utcnow()


def _dict_cursor(conn):
    factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=factory) if factory else conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _json(value: Any, limit: int = 120000) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:limit]


def _claim_candidate() -> Optional[Dict[str, Any]]:
    """Claim one legacy 404-blocked run without changing its terminal state."""
    review_store.ensure_review_tables()
    now = _utcnow()
    lease_seconds = _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_REVIEW_RECOVERY_CLAIM_SECONDS",
        600,
        120,
        1800,
    )
    claim_id = f"review:recovery:{uuid.uuid4()}"
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"""
            SELECT {autopilot._RUN_COLUMNS}
            FROM velia_developer_autopilot_runs
            WHERE status='blocked'
              AND error_code=%s
              AND pull_request_number>0
              AND (claimed_by='' OR claimed_until<=%s)
            ORDER BY updated_at DESC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (_RECOVERABLE_ERROR, now),
        )
        selected = cursor.fetchone()
        if not selected:
            conn.commit()
            return None
        run_id = str(_value(selected, "run_id", 0, ""))
        cursor.execute(
            f"""
            UPDATE velia_developer_autopilot_runs
            SET claimed_by=%s,claimed_until=%s,updated_at=%s
            WHERE run_id=%s
              AND status='blocked'
              AND error_code=%s
            RETURNING {autopilot._RUN_COLUMNS}
            """,
            (
                claim_id,
                now + timedelta(seconds=lease_seconds),
                now,
                run_id,
                _RECOVERABLE_ERROR,
            ),
        )
        claimed = cursor.fetchone()
        conn.commit()
        return autopilot._run_from_row(claimed) if claimed else None
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _pull_metadata(project: Mapping[str, Any], pull_number: int) -> Dict[str, Any]:
    owner, name, token = review_github._access(project)
    data = review_github._request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/pulls/{int(pull_number)}",
        token=token,
    )
    if not isinstance(data, dict):
        raise review_github.CodingAutopilotReviewGithubError(
            "github_invalid_response", status=502
        )
    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    return {
        "number": int(data.get("number") or 0),
        "state": str(data.get("state") or "").lower(),
        "draft": bool(data.get("draft")),
        "head_ref": str(head.get("ref") or ""),
        "head_sha": str(head.get("sha") or ""),
        "head_repo": str(head_repo.get("full_name") or ""),
        "base_ref": str(base.get("ref") or ""),
        "merged": bool(data.get("merged") or data.get("merged_at")),
    }


def _recovery_reason(
    run: Mapping[str, Any],
    project: Mapping[str, Any],
    job: Mapping[str, Any],
    attempt: Mapping[str, Any],
    pull: Mapping[str, Any],
    branch_sha: str,
) -> str:
    if str(attempt.get("status") or "") != "success":
        return "ci_not_green"
    ci_sha = str(attempt.get("head_sha") or "")
    if not ci_sha or branch_sha != ci_sha:
        return "branch_head_drift"
    if int(pull.get("number") or 0) != int(run.get("pull_request_number") or 0):
        return "pr_number_mismatch"
    if str(pull.get("state") or "") != "open" or bool(pull.get("merged")):
        return "pr_not_open"
    if str(pull.get("head_ref") or "") != str(run.get("work_branch") or ""):
        return "pr_head_branch_mismatch"
    if str(pull.get("head_sha") or "") != ci_sha:
        return "pr_head_sha_mismatch"
    if str(pull.get("base_ref") or "") != str(job.get("base_branch") or ""):
        return "pr_base_mismatch"
    expected_repo = str(project.get("repository_full_name") or "")
    if expected_repo and str(pull.get("head_repo") or "") != expected_repo:
        return "pr_repository_mismatch"
    max_actions = review_service._env_int(
        "VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_ACTIONS", 2, 0, 2
    )
    if review_store.addressed_count(str(run.get("run_id") or "")) >= max_actions:
        return "review_actions_exhausted"
    return ""


def _restore_ready_for_review(run: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    now = _utcnow()
    payload = dict(run.get("result") if isinstance(run.get("result"), dict) else {})
    review = dict(payload.get("review") if isinstance(payload.get("review"), dict) else {})
    review["recovered_from_error"] = _RECOVERABLE_ERROR
    review["recovered_at"] = now.isoformat() + "Z"
    payload["review"] = review
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"""
            UPDATE velia_developer_autopilot_runs
            SET status='ready_for_review',
                result_json=%s,
                error_code=NULL,
                claimed_by='',
                claimed_until=%s,
                finished_at=NULL,
                updated_at=%s
            WHERE run_id=%s
              AND status='blocked'
              AND error_code=%s
              AND claimed_by LIKE 'review:recovery:%%'
            RETURNING {autopilot._RUN_COLUMNS}
            """,
            (
                _json(payload),
                now,
                now,
                str(run.get("run_id") or ""),
                _RECOVERABLE_ERROR,
            ),
        )
        restored = cursor.fetchone()
        if not restored:
            conn.commit()
            return None
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_tasks
            SET status='ready_for_review',result_json=%s,error_code=NULL,updated_at=%s
            WHERE task_id=%s
            """,
            (_json(payload), now, str(run.get("task_id") or "")),
        )
        conn.commit()
        return autopilot._run_from_row(restored)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def recover_reopened_github_not_found_once() -> Optional[Dict[str, Any]]:
    """
    Recover at most one run whose historical GitHub 404 is no longer true.

    Recovery is state-only. It never edits code. The normal Review Loop must still
    observe an explicit REQUEST_CHANGES event before any repair can occur.
    """
    if not review_service.review_loop_enabled():
        return None

    for _ in range(max_candidates_per_tick()):
        run = _claim_candidate()
        if not run:
            return None
        run_id = str(run.get("run_id") or "")
        pr = int(run.get("pull_request_number") or 0)
        try:
            project, _mission = ci_service._project_and_mission(run)
            job = ci_service._coding_job(run)
            attempt = ci_service._current_attempt(run_id)
            if not attempt:
                logger.info(
                    "VELIA_AUTOPILOT_REVIEW_RECOVERY_SKIPPED run=%s pr=%s reason=ci_missing",
                    run_id,
                    pr,
                )
                continue
            branch = str(run.get("work_branch") or "")
            branch_sha = str(write_service.branch_head(project, branch).get("sha") or "")
            pull = _pull_metadata(project, pr)
            reason = _recovery_reason(run, project, job, attempt, pull, branch_sha)
            if reason:
                logger.info(
                    "VELIA_AUTOPILOT_REVIEW_RECOVERY_SKIPPED run=%s pr=%s reason=%s",
                    run_id,
                    pr,
                    reason,
                )
                continue
            restored = _restore_ready_for_review(run)
            if not restored:
                continue
            autopilot._record_event(
                restored,
                "review_run_recovered",
                {
                    "from_error": _RECOVERABLE_ERROR,
                    "pull_request_number": pr,
                    "head_sha": str(attempt.get("head_sha") or ""),
                    "ci_attempt": int(attempt.get("attempt_number") or 0),
                },
            )
            logger.info(
                "VELIA_AUTOPILOT_REVIEW_RECOVERED run=%s pr=%s head=%s ci_attempt=%s",
                run_id,
                pr,
                str(attempt.get("head_sha") or "")[:12],
                int(attempt.get("attempt_number") or 0),
            )
            return restored
        except (
            review_github.CodingAutopilotReviewGithubError,
            write_service.DeveloperWriteError,
        ) as exc:
            logger.info(
                "VELIA_AUTOPILOT_REVIEW_RECOVERY_SKIPPED run=%s pr=%s reason=%s",
                run_id,
                pr,
                str(getattr(exc, "code", exc.__class__.__name__))[:120],
            )
        except Exception as exc:
            logger.warning(
                "VELIA_AUTOPILOT_REVIEW_RECOVERY_FAILED run=%s pr=%s error=%s",
                run_id,
                pr,
                exc.__class__.__name__,
            )
    return None
