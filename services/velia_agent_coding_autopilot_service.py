from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from aiohttp import web

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection
from services import velia_agent_coding_autopilot_policy_service as policy_service
from services import velia_developer_coding_service as coding_service
from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_421
_CLAIM_ADVISORY_KEY = 8_618_270_422
_ACTIVE_RUN_STATUSES = ("claimed", "planning", "executing")
_TASK_ACTIVE_STATUSES = ("claimed", "planning", "executing")


class CodingAutopilotError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def autopilot_enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_AUTOPILOT_ENABLED", False)


def worker_enabled() -> bool:
    return autopilot_enabled() and _env_bool("VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED", False)


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


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def ensure_coding_autopilot_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        project_service.ensure_developer_tables()
        coding_service.ensure_coding_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_autopilot_missions (
                    mission_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id),
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'paused',
                    mode TEXT NOT NULL DEFAULT 'draft_pr_only',
                    base_branch TEXT NOT NULL,
                    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
                    blocked_paths_json TEXT NOT NULL DEFAULT '[]',
                    max_steps INTEGER NOT NULL DEFAULT 4,
                    max_files INTEGER NOT NULL DEFAULT 8,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('paused','active','archived')),
                    CHECK (mode='draft_pr_only'),
                    CHECK (max_steps BETWEEN 1 AND 5),
                    CHECK (max_files BETWEEN 1 AND 12)
                )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_autopilot_mission_project
                ON velia_developer_autopilot_missions(user_id,project_id)
                WHERE status IN ('paused','active')
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_autopilot_tasks (
                    task_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL REFERENCES velia_developer_autopilot_missions(mission_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    instruction TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    client_request_id TEXT NOT NULL DEFAULT '',
                    latest_run_id TEXT NOT NULL DEFAULT '',
                    error_code TEXT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (priority BETWEEN 0 AND 100),
                    CHECK (status IN ('queued','claimed','planning','executing','ready_for_review','failed','blocked','cancelled'))
                )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_autopilot_task_request
                ON velia_developer_autopilot_tasks(mission_id,client_request_id)
                WHERE client_request_id<>''
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_autopilot_tasks_queue
                ON velia_developer_autopilot_tasks(status,priority DESC,created_at ASC)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_autopilot_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES velia_developer_autopilot_tasks(task_id) ON DELETE CASCADE,
                    mission_id TEXT NOT NULL REFERENCES velia_developer_autopilot_missions(mission_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'claimed',
                    conversation_id TEXT NOT NULL,
                    coding_job_id TEXT NOT NULL DEFAULT '',
                    work_branch TEXT NOT NULL DEFAULT '',
                    pull_request_number INTEGER NULL,
                    pull_request_url TEXT NOT NULL DEFAULT '',
                    estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
                    claimed_by TEXT NOT NULL,
                    claimed_until TIMESTAMP NOT NULL,
                    error_code TEXT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    started_at TIMESTAMP NULL,
                    finished_at TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('claimed','planning','executing','ready_for_review','failed','blocked','cancelled'))
                )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_autopilot_run_project_active
                ON velia_developer_autopilot_runs(project_id)
                WHERE status IN ('claimed','planning','executing')
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_autopilot_runs_mission
                ON velia_developer_autopilot_runs(mission_id,created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_autopilot_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES velia_developer_autopilot_runs(run_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_autopilot_events_run
                ON velia_developer_autopilot_events(run_id,created_at ASC)
                """
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _mission_from_row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "mission_id": str(_value(row, "mission_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "project_id": str(_value(row, "project_id", 2, "")),
        "name": str(_value(row, "name", 3, "")),
        "status": str(_value(row, "status", 4, "")),
        "mode": str(_value(row, "mode", 5, "draft_pr_only")),
        "base_branch": str(_value(row, "base_branch", 6, "")),
        "allowed_paths": _loads(_value(row, "allowed_paths_json", 7, "[]"), []),
        "blocked_paths": _loads(_value(row, "blocked_paths_json", 8, "[]"), []),
        "max_steps": int(_value(row, "max_steps", 9, 0) or 0),
        "max_files": int(_value(row, "max_files", 10, 0) or 0),
        "created_at": _iso(_value(row, "created_at", 11)),
        "updated_at": _iso(_value(row, "updated_at", 12)),
        "draft_pr_only": True,
    }


def _task_from_row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "task_id": str(_value(row, "task_id", 0, "")),
        "mission_id": str(_value(row, "mission_id", 1, "")),
        "user_id": int(_value(row, "user_id", 2, 0) or 0),
        "instruction": str(_value(row, "instruction", 3, "")),
        "priority": int(_value(row, "priority", 4, 0) or 0),
        "status": str(_value(row, "status", 5, "")),
        "client_request_id": str(_value(row, "client_request_id", 6, "")),
        "latest_run_id": str(_value(row, "latest_run_id", 7, "")),
        "error_code": str(_value(row, "error_code", 8, "") or "") or None,
        "result": _loads(_value(row, "result_json", 9, "{}"), {}),
        "created_at": _iso(_value(row, "created_at", 10)),
        "updated_at": _iso(_value(row, "updated_at", 11)),
    }


def _run_from_row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "run_id": str(_value(row, "run_id", 0, "")),
        "task_id": str(_value(row, "task_id", 1, "")),
        "mission_id": str(_value(row, "mission_id", 2, "")),
        "user_id": int(_value(row, "user_id", 3, 0) or 0),
        "project_id": str(_value(row, "project_id", 4, "")),
        "status": str(_value(row, "status", 5, "")),
        "conversation_id": str(_value(row, "conversation_id", 6, "")),
        "coding_job_id": str(_value(row, "coding_job_id", 7, "")),
        "work_branch": str(_value(row, "work_branch", 8, "")),
        "pull_request_number": int(_value(row, "pull_request_number", 9, 0) or 0),
        "pull_request_url": str(_value(row, "pull_request_url", 10, "")),
        "estimated_cost_usd": float(_value(row, "estimated_cost_usd", 11, 0.0) or 0.0),
        "claimed_by": str(_value(row, "claimed_by", 12, "")),
        "claimed_until": _iso(_value(row, "claimed_until", 13)),
        "error_code": str(_value(row, "error_code", 14, "") or "") or None,
        "result": _loads(_value(row, "result_json", 15, "{}"), {}),
        "started_at": _iso(_value(row, "started_at", 16)),
        "finished_at": _iso(_value(row, "finished_at", 17)),
        "created_at": _iso(_value(row, "created_at", 18)),
        "updated_at": _iso(_value(row, "updated_at", 19)),
    }


_MISSION_COLUMNS = (
    "mission_id,user_id,project_id,name,status,mode,base_branch,allowed_paths_json,"
    "blocked_paths_json,max_steps,max_files,created_at,updated_at"
)
_TASK_COLUMNS = (
    "task_id,mission_id,user_id,instruction,priority,status,client_request_id,"
    "latest_run_id,error_code,result_json,created_at,updated_at"
)
_RUN_COLUMNS = (
    "run_id,task_id,mission_id,user_id,project_id,status,conversation_id,coding_job_id,"
    "work_branch,pull_request_number,pull_request_url,estimated_cost_usd,claimed_by,"
    "claimed_until,error_code,result_json,started_at,finished_at,created_at,updated_at"
)


def create_mission(
    user_id: int,
    project_id: str,
    name: str,
    *,
    allowed_paths: Any,
    blocked_paths: Any = None,
    max_steps: Any = 4,
    max_files: Any = 8,
) -> Dict[str, Any]:
    ensure_coding_autopilot_tables()
    project = project_service.get_project(int(user_id), str(project_id))
    if bool(project.get("archived")):
        raise CodingAutopilotError("velia_coding_autopilot_project_archived", status=409)
    base_branch = github_service.validate_branch(str(project.get("selected_branch") or ""))
    normalized_policy = policy_service.normalize_policy(
        allowed_paths=allowed_paths,
        blocked_paths=blocked_paths,
        max_steps=max_steps,
        max_files=max_files,
    )
    normalized_name = str(name or "").strip()[:200] or (
        "VELIA Autopilot · " + str(project.get("repository_full_name") or "repository")
    )
    mission_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"""
            INSERT INTO velia_developer_autopilot_missions (
                mission_id,user_id,project_id,name,status,mode,base_branch,
                allowed_paths_json,blocked_paths_json,max_steps,max_files,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,'paused','draft_pr_only',%s,%s,%s,%s,%s,%s,%s)
            RETURNING {_MISSION_COLUMNS}
            """,
            (
                mission_id,
                int(user_id),
                str(project_id),
                normalized_name,
                base_branch,
                _json(normalized_policy["allowed_paths"]),
                _json(normalized_policy["blocked_paths"]),
                int(normalized_policy["max_steps"]),
                int(normalized_policy["max_files"]),
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _mission_from_row(row)
    except Exception as exc:
        conn.rollback()
        if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
            raise CodingAutopilotError("velia_coding_autopilot_mission_exists", status=409) from exc
        raise
    finally:
        cursor.close()
        conn.close()


def list_missions(user_id: int) -> List[Dict[str, Any]]:
    ensure_coding_autopilot_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_MISSION_COLUMNS} FROM velia_developer_autopilot_missions "
            "WHERE user_id=%s ORDER BY created_at DESC LIMIT 50",
            (int(user_id),),
        )
        return [_mission_from_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def get_mission(user_id: int, mission_id: str) -> Dict[str, Any]:
    ensure_coding_autopilot_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_MISSION_COLUMNS} FROM velia_developer_autopilot_missions "
            "WHERE mission_id=%s AND user_id=%s",
            (str(mission_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise CodingAutopilotError("velia_coding_autopilot_mission_not_found", status=404)
        return _mission_from_row(row)
    finally:
        cursor.close()
        conn.close()


def set_mission_status(user_id: int, mission_id: str, status: str) -> Dict[str, Any]:
    normalized = str(status or "").strip().lower()
    if normalized not in {"active", "paused"}:
        raise CodingAutopilotError("velia_coding_autopilot_mission_status_invalid")
    mission = get_mission(user_id, mission_id)
    if mission["status"] == "archived":
        raise CodingAutopilotError("velia_coding_autopilot_mission_archived", status=409)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_developer_autopilot_missions SET status=%s,updated_at=%s "
            "WHERE mission_id=%s AND user_id=%s AND status<>'archived'",
            (normalized, _utcnow(), str(mission_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            raise CodingAutopilotError("velia_coding_autopilot_mission_not_found", status=404)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_mission(user_id, mission_id)


def enqueue_task(
    user_id: int,
    mission_id: str,
    instruction: str,
    *,
    priority: Any = 0,
    client_request_id: str = "",
) -> Dict[str, Any]:
    ensure_coding_autopilot_tables()
    mission = get_mission(user_id, mission_id)
    if mission["status"] == "archived":
        raise CodingAutopilotError("velia_coding_autopilot_mission_archived", status=409)
    normalized_instruction = str(instruction or "").strip()
    if not normalized_instruction or len(normalized_instruction) > 12000:
        raise CodingAutopilotError("velia_coding_autopilot_task_invalid")
    try:
        normalized_priority = int(priority)
    except (TypeError, ValueError) as exc:
        raise CodingAutopilotError("velia_coding_autopilot_priority_invalid") from exc
    if normalized_priority < 0 or normalized_priority > 100:
        raise CodingAutopilotError("velia_coding_autopilot_priority_invalid")
    request_id = str(client_request_id or "").strip()[:160]
    task_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM velia_developer_autopilot_tasks "
            "WHERE mission_id=%s AND status IN ('queued','claimed','planning','executing')",
            (str(mission_id),),
        )
        count = int(_value(cursor.fetchone(), "count", 0, 0) or 0)
        maximum = _env_int("VELIA_DEVELOPER_AUTOPILOT_MAX_QUEUED_TASKS", 50, 1, 200)
        if count >= maximum:
            raise CodingAutopilotError("velia_coding_autopilot_queue_limit", status=409)
        cursor.execute(
            f"""
            INSERT INTO velia_developer_autopilot_tasks (
                task_id,mission_id,user_id,instruction,priority,status,client_request_id,
                latest_run_id,error_code,result_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,'queued',%s,'',NULL,'{{}}',%s,%s)
            ON CONFLICT (mission_id,client_request_id) WHERE client_request_id<>''
            DO UPDATE SET updated_at=EXCLUDED.updated_at
            RETURNING {_TASK_COLUMNS}
            """,
            (
                task_id,
                str(mission_id),
                int(user_id),
                normalized_instruction,
                normalized_priority,
                request_id,
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _task_from_row(row)
    except CodingAutopilotError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_tasks(user_id: int, mission_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
    get_mission(user_id, mission_id)
    normalized_limit = min(200, max(1, int(limit)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_TASK_COLUMNS} FROM velia_developer_autopilot_tasks "
            "WHERE mission_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT %s",
            (str(mission_id), int(user_id), normalized_limit),
        )
        return [_task_from_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def get_task(user_id: int, task_id: str) -> Dict[str, Any]:
    ensure_coding_autopilot_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_TASK_COLUMNS} FROM velia_developer_autopilot_tasks "
            "WHERE task_id=%s AND user_id=%s",
            (str(task_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise CodingAutopilotError("velia_coding_autopilot_task_not_found", status=404)
        return _task_from_row(row)
    finally:
        cursor.close()
        conn.close()


def cancel_task(user_id: int, task_id: str) -> Dict[str, Any]:
    task = get_task(user_id, task_id)
    if task["status"] not in {"queued", "failed", "blocked"}:
        raise CodingAutopilotError("velia_coding_autopilot_task_not_cancellable", status=409)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_developer_autopilot_tasks SET status='cancelled',updated_at=%s "
            "WHERE task_id=%s AND user_id=%s AND status IN ('queued','failed','blocked')",
            (_utcnow(), str(task_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            raise CodingAutopilotError("velia_coding_autopilot_task_not_cancellable", status=409)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_task(user_id, task_id)


def get_run(user_id: int, run_id: str) -> Dict[str, Any]:
    ensure_coding_autopilot_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_RUN_COLUMNS} FROM velia_developer_autopilot_runs "
            "WHERE run_id=%s AND user_id=%s",
            (str(run_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise CodingAutopilotError("velia_coding_autopilot_run_not_found", status=404)
        return _run_from_row(row)
    finally:
        cursor.close()
        conn.close()


def list_runs(user_id: int, mission_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
    get_mission(user_id, mission_id)
    normalized_limit = min(200, max(1, int(limit)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_RUN_COLUMNS} FROM velia_developer_autopilot_runs "
            "WHERE mission_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT %s",
            (str(mission_id), int(user_id), normalized_limit),
        )
        return [_run_from_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def _insert_event_cursor(
    cursor: Any,
    *,
    run_id: str,
    task_id: str,
    user_id: int,
    event_type: str,
    payload: Any = None,
) -> None:
    cursor.execute(
        """
        INSERT INTO velia_developer_autopilot_events (
            event_id,run_id,task_id,user_id,event_type,payload_json,created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            str(uuid.uuid4()),
            str(run_id),
            str(task_id),
            int(user_id),
            str(event_type)[:120],
            _json(payload or {}, 20000),
            _utcnow(),
        ),
    )


def _record_event(run: Mapping[str, Any], event_type: str, payload: Any = None) -> None:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            _insert_event_cursor(
                cursor,
                run_id=str(run.get("run_id") or ""),
                task_id=str(run.get("task_id") or ""),
                user_id=int(run.get("user_id") or 0),
                event_type=event_type,
                payload=payload,
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception:
        logger.exception(
            "VELIA_CODING_AUTOPILOT_EVENT_FAILED run_id=%s event_type=%s",
            str(run.get("run_id") or ""),
            str(event_type),
        )


def _recover_stale_runs(cursor: Any, now: datetime) -> None:
    cursor.execute(
        """
        SELECT run_id,task_id,user_id,status,coding_job_id
        FROM velia_developer_autopilot_runs
        WHERE status IN ('claimed','planning','executing') AND claimed_until<%s
        FOR UPDATE
        """,
        (now,),
    )
    for raw in cursor.fetchall() or []:
        run_id = str(_value(raw, "run_id", 0, ""))
        task_id = str(_value(raw, "task_id", 1, ""))
        user_id = int(_value(raw, "user_id", 2, 0) or 0)
        status = str(_value(raw, "status", 3, ""))
        coding_job_id = str(_value(raw, "coding_job_id", 4, ""))
        safe_to_requeue = status in {"claimed", "planning"} and not coding_job_id
        if safe_to_requeue:
            code = "velia_coding_autopilot_lease_expired_before_write"
            cursor.execute(
                "UPDATE velia_developer_autopilot_runs SET status='failed',error_code=%s,"
                "finished_at=%s,updated_at=%s WHERE run_id=%s",
                (code, now, now, run_id),
            )
            cursor.execute(
                "UPDATE velia_developer_autopilot_tasks SET status='queued',error_code=NULL,"
                "latest_run_id='',updated_at=%s WHERE task_id=%s",
                (now, task_id),
            )
        else:
            code = "velia_coding_autopilot_lease_expired_after_write_started"
            cursor.execute(
                "UPDATE velia_developer_autopilot_runs SET status='blocked',error_code=%s,"
                "finished_at=%s,updated_at=%s WHERE run_id=%s",
                (code, now, now, run_id),
            )
            cursor.execute(
                "UPDATE velia_developer_autopilot_tasks SET status='blocked',error_code=%s,"
                "updated_at=%s WHERE task_id=%s",
                (code, now, task_id),
            )
        _insert_event_cursor(
            cursor,
            run_id=run_id,
            task_id=task_id,
            user_id=user_id,
            event_type="lease_recovered",
            payload={"safe_to_requeue": safe_to_requeue, "error_code": code},
        )


def _claim_next_task(worker_id: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    ensure_coding_autopilot_tables()
    current = now or _utcnow()
    lease_seconds = _env_int("VELIA_DEVELOPER_AUTOPILOT_LEASE_SECONDS", 3600, 300, 7200)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (_CLAIM_ADVISORY_KEY,))
        if not bool(_value(cursor.fetchone(), "pg_try_advisory_lock", 0, False)):
            return None
        _recover_stale_runs(cursor, current)
        cursor.execute(
            """
            SELECT t.task_id,t.mission_id,t.user_id,m.project_id
            FROM velia_developer_autopilot_tasks t
            JOIN velia_developer_autopilot_missions m ON m.mission_id=t.mission_id
            WHERE t.status='queued' AND m.status='active'
              AND NOT EXISTS (
                  SELECT 1 FROM velia_developer_autopilot_runs r
                  WHERE r.project_id=m.project_id
                    AND r.status IN ('claimed','planning','executing')
              )
            ORDER BY t.priority DESC,t.created_at ASC
            FOR UPDATE OF t SKIP LOCKED
            LIMIT 1
            """
        )
        selected = cursor.fetchone()
        if not selected:
            conn.commit()
            return None
        task_id = str(_value(selected, "task_id", 0, ""))
        mission_id = str(_value(selected, "mission_id", 1, ""))
        user_id = int(_value(selected, "user_id", 2, 0) or 0)
        project_id = str(_value(selected, "project_id", 3, ""))
        run_id = str(uuid.uuid4())
        conversation_id = f"autopilot:{run_id}"
        claimed_until = current + timedelta(seconds=lease_seconds)
        cursor.execute(
            f"""
            INSERT INTO velia_developer_autopilot_runs (
                run_id,task_id,mission_id,user_id,project_id,status,conversation_id,
                coding_job_id,work_branch,pull_request_url,estimated_cost_usd,claimed_by,
                claimed_until,error_code,result_json,started_at,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,'claimed',%s,'','','',0,%s,%s,NULL,'{{}}',%s,%s,%s)
            RETURNING {_RUN_COLUMNS}
            """,
            (
                run_id,
                task_id,
                mission_id,
                user_id,
                project_id,
                conversation_id,
                str(worker_id)[:120],
                claimed_until,
                current,
                current,
                current,
            ),
        )
        run_row = cursor.fetchone()
        cursor.execute(
            "UPDATE velia_developer_autopilot_tasks SET status='claimed',latest_run_id=%s,"
            "error_code=NULL,updated_at=%s WHERE task_id=%s",
            (run_id, current, task_id),
        )
        _insert_event_cursor(
            cursor,
            run_id=run_id,
            task_id=task_id,
            user_id=user_id,
            event_type="claimed",
            payload={"worker_id": str(worker_id)[:120]},
        )
        conn.commit()
        return _run_from_row(run_row)
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (_CLAIM_ADVISORY_KEY,))
            conn.commit()
        except Exception:
            conn.rollback()
        cursor.close()
        conn.close()


def _transition(
    run: Mapping[str, Any],
    status: str,
    *,
    coding_job_id: Optional[str] = None,
    result: Any = None,
    error_code: str = "",
    work_branch: str = "",
    pull_request_number: int = 0,
    pull_request_url: str = "",
    estimated_cost_usd: float = 0.0,
    finished: bool = False,
) -> None:
    if status not in {"claimed", "planning", "executing", "ready_for_review", "failed", "blocked", "cancelled"}:
        raise CodingAutopilotError("velia_coding_autopilot_state_invalid")
    lease_seconds = _env_int("VELIA_DEVELOPER_AUTOPILOT_LEASE_SECONDS", 3600, 300, 7200)
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_runs
            SET status=%s,coding_job_id=COALESCE(%s,coding_job_id),
                work_branch=CASE WHEN %s<>'' THEN %s ELSE work_branch END,
                pull_request_number=CASE WHEN %s>0 THEN %s ELSE pull_request_number END,
                pull_request_url=CASE WHEN %s<>'' THEN %s ELSE pull_request_url END,
                estimated_cost_usd=CASE WHEN %s>0 THEN %s ELSE estimated_cost_usd END,
                claimed_until=%s,error_code=%s,
                result_json=CASE WHEN %s IS NOT NULL THEN %s ELSE result_json END,
                finished_at=CASE WHEN %s THEN %s ELSE finished_at END,
                updated_at=%s
            WHERE run_id=%s
            """,
            (
                status,
                coding_job_id,
                str(work_branch or ""),
                str(work_branch or ""),
                int(pull_request_number or 0),
                int(pull_request_number or 0),
                str(pull_request_url or ""),
                str(pull_request_url or ""),
                float(estimated_cost_usd or 0.0),
                float(estimated_cost_usd or 0.0),
                now + timedelta(seconds=lease_seconds),
                str(error_code or "")[:120] or None,
                _json(result) if result is not None else None,
                _json(result) if result is not None else None,
                bool(finished),
                now,
                now,
                str(run.get("run_id") or ""),
            ),
        )
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_tasks
            SET status=%s,error_code=%s,
                result_json=CASE WHEN %s IS NOT NULL THEN %s ELSE result_json END,
                updated_at=%s
            WHERE task_id=%s
            """,
            (
                status,
                str(error_code or "")[:120] or None,
                _json(result) if result is not None else None,
                _json(result) if result is not None else None,
                now,
                str(run.get("task_id") or ""),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _coding_snapshot(user_id: int, coding_job_id: str) -> Dict[str, Any]:
    if not str(coding_job_id or ""):
        return {}
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT job_id,status,work_branch,pull_request_number,pull_request_url,
                   estimated_cost_usd,error_code
            FROM velia_developer_coding_jobs
            WHERE job_id=%s AND user_id=%s
            """,
            (str(coding_job_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        return {
            "job_id": str(_value(row, "job_id", 0, "")),
            "status": str(_value(row, "status", 1, "")),
            "work_branch": str(_value(row, "work_branch", 2, "")),
            "pull_request_number": int(_value(row, "pull_request_number", 3, 0) or 0),
            "pull_request_url": str(_value(row, "pull_request_url", 4, "")),
            "estimated_cost_usd": float(_value(row, "estimated_cost_usd", 5, 0.0) or 0.0),
            "error_code": str(_value(row, "error_code", 6, "") or ""),
        }
    finally:
        cursor.close()
        conn.close()


def _mission_policy(mission: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "allowed_paths": list(mission.get("allowed_paths") or []),
        "blocked_paths": list(mission.get("blocked_paths") or []),
        "max_steps": int(mission.get("max_steps") or 0),
        "max_files": int(mission.get("max_files") or 0),
        "draft_pr_only": True,
    }


def _execute_claimed(run: Mapping[str, Any]) -> Dict[str, Any]:
    user_id = int(run.get("user_id") or 0)
    run_id = str(run.get("run_id") or "")
    task = get_task(user_id, str(run.get("task_id") or ""))
    mission = get_mission(user_id, str(run.get("mission_id") or ""))
    if mission.get("status") != "active":
        _transition(run, "failed", error_code="velia_coding_autopilot_mission_paused", finished=True)
        raise CodingAutopilotError("velia_coding_autopilot_mission_paused", status=409)
    project = dict(project_service.get_project(user_id, str(mission["project_id"])))
    project["selected_branch"] = str(mission["base_branch"])
    coding_job_id = ""

    def progress(phase: str, details: Dict[str, Any]) -> None:
        try:
            _transition(run, "planning" if phase == "planning" else "executing", coding_job_id=coding_job_id or None)
            _record_event(run, str(phase), details)
        except Exception:
            logger.exception("VELIA_CODING_AUTOPILOT_PROGRESS_FAILED run_id=%s", run_id)

    try:
        _transition(run, "planning")
        _record_event(run, "planning_started", {"base_branch": mission["base_branch"]})
        coding_job = coding_service.plan_job(
            user_id=user_id,
            conversation_id=str(run["conversation_id"]),
            project=project,
            goal=str(task["instruction"]),
            on_progress=progress,
        )
        coding_job_id = str(coding_job.get("job_id") or "")
        _transition(run, "planning", coding_job_id=coding_job_id)
        validation = policy_service.validate_plan(coding_job.get("plan"), _mission_policy(mission))
        _record_event(run, "plan_validated", validation)
        _transition(run, "executing", coding_job_id=coding_job_id)
        result = coding_service.execute_job(
            user_id=user_id,
            conversation_id=str(run["conversation_id"]),
            project=project,
            on_progress=progress,
        )
        pull_request = result.get("pull_request") if isinstance(result.get("pull_request"), dict) else {}
        if str(result.get("status") or "") != "completed" or not str(pull_request.get("url") or ""):
            raise CodingAutopilotError("velia_coding_autopilot_draft_pr_missing", status=502)
        final_result = {
            "coding_job_id": coding_job_id,
            "work_branch": str(result.get("work_branch") or ""),
            "pull_request": {
                "number": int(pull_request.get("number") or 0),
                "url": str(pull_request.get("url") or ""),
                "draft": True,
            },
            "checks": result.get("checks") if isinstance(result.get("checks"), dict) else {},
            "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
            "steps": result.get("steps") if isinstance(result.get("steps"), list) else [],
        }
        _transition(
            run,
            "ready_for_review",
            coding_job_id=coding_job_id,
            result=final_result,
            work_branch=final_result["work_branch"],
            pull_request_number=int(final_result["pull_request"]["number"]),
            pull_request_url=str(final_result["pull_request"]["url"]),
            estimated_cost_usd=float(final_result["estimated_cost_usd"]),
            finished=True,
        )
        _record_event(run, "draft_pr_ready", final_result["pull_request"])
        return {**dict(run), "status": "ready_for_review", "result": final_result}
    except policy_service.CodingAutopilotPolicyError as exc:
        if coding_job_id:
            try:
                coding_service.cancel_active_job(user_id, str(run["conversation_id"]))
            except Exception:
                logger.exception("VELIA_CODING_AUTOPILOT_PLAN_CANCEL_FAILED run_id=%s", run_id)
        _transition(
            run,
            "blocked",
            coding_job_id=coding_job_id or None,
            error_code=exc.code,
            result={"detail": exc.detail},
            finished=True,
        )
        _record_event(run, "blocked", {"error_code": exc.code, "detail": exc.detail})
        return {**dict(run), "status": "blocked", "error_code": exc.code}
    except Exception as exc:
        code = str(getattr(exc, "code", "velia_coding_autopilot_run_failed") or "velia_coding_autopilot_run_failed")[:120]
        snapshot = _coding_snapshot(user_id, coding_job_id)
        _transition(
            run,
            "failed",
            coding_job_id=coding_job_id or None,
            error_code=code,
            result={"coding_job": snapshot},
            work_branch=str(snapshot.get("work_branch") or ""),
            pull_request_number=int(snapshot.get("pull_request_number") or 0),
            pull_request_url=str(snapshot.get("pull_request_url") or ""),
            estimated_cost_usd=float(snapshot.get("estimated_cost_usd") or 0.0),
            finished=True,
        )
        _record_event(run, "failed", {"error_code": code, "coding_job": snapshot})
        logger.exception("VELIA_CODING_AUTOPILOT_RUN_FAILED run_id=%s code=%s", run_id, code)
        return {**dict(run), "status": "failed", "error_code": code, "result": snapshot}


def run_autopilot_once() -> List[Dict[str, Any]]:
    if not worker_enabled() or not coding_service.coding_enabled():
        return []
    worker_id = f"worker:{uuid.uuid4()}"
    maximum = _env_int("VELIA_DEVELOPER_AUTOPILOT_MAX_RUNS_PER_TICK", 1, 1, 3)
    results: List[Dict[str, Any]] = []
    for _ in range(maximum):
        claimed = _claim_next_task(worker_id)
        if not claimed:
            break
        results.append(_execute_claimed(claimed))
    return results


async def _worker_loop() -> None:
    interval = _env_int("VELIA_DEVELOPER_AUTOPILOT_INTERVAL_SECONDS", 60, 30, 3600)
    while True:
        try:
            await asyncio.to_thread(run_autopilot_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VELIA_CODING_AUTOPILOT_TICK_FAILED")
        await asyncio.sleep(interval)


def install_coding_autopilot(app: web.Application) -> None:
    if app.get("velia_coding_autopilot_installed"):
        return
    app["velia_coding_autopilot_installed"] = True
    if not worker_enabled():
        return

    async def worker_context(_app: web.Application):
        task = asyncio.create_task(_worker_loop(), name="velia-coding-autopilot")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.cleanup_ctx.append(worker_context)
