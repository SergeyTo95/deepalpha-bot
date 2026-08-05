from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List

from db.database import get_connection
from services.velia_agent_protocol_service import ActionRequest, ActionStatus, JobStatus

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


class AgentJobError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:300]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _row(row: Any, columns: Iterable[str]) -> Dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    names = list(columns)
    return {name: row[index] if index < len(row) else None for index, name in enumerate(names)}


def ensure_velia_agent_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    goal TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_actions (
                    action_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES velia_agent_jobs(job_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
                    idempotency_key TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id, idempotency_key)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_approvals (
                    approval_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    decision TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_audit_events (
                    event_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    job_id TEXT,
                    action_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_task_drafts (
                    draft_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    completed BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_schedules (
                    schedule_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    instruction TEXT NOT NULL,
                    cron_expression TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_connector_accounts (
                    connector_account_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    connector TEXT NOT NULL,
                    external_account_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id, connector, external_account_id)
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_velia_agent_jobs_user ON velia_agent_jobs(user_id, created_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_velia_agent_actions_job ON velia_agent_actions(job_id, sequence_no)")
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def audit(user_id: int, event_type: str, *, job_id: str = "", action_id: str = "", payload: Any = None) -> None:
    ensure_velia_agent_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO velia_agent_audit_events (event_id,user_id,job_id,action_id,event_type,payload_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), int(user_id), str(job_id or ""), str(action_id or ""), str(event_type), _json(payload or {}), datetime.utcnow()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_job(user_id: int, goal: str, mode: str, actions: List[ActionRequest]) -> Dict[str, Any]:
    ensure_velia_agent_tables()
    if not actions:
        raise AgentJobError("velia_agent_actions_empty")
    job_id = str(uuid.uuid4())
    status = JobStatus.AWAITING_APPROVAL if any(item.requires_approval for item in actions) else JobStatus.PLANNED
    now = datetime.utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO velia_agent_jobs (job_id,user_id,goal,mode,status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (job_id, int(user_id), str(goal)[:4000], str(mode), status.value, now, now),
        )
        for index, action in enumerate(actions, start=1):
            cursor.execute(
                "INSERT INTO velia_agent_actions (action_id,job_id,user_id,sequence_no,tool_name,arguments_json,risk,status,requires_approval,idempotency_key,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (action.action_id, job_id, int(user_id), index, action.tool_name, _json(action.arguments), action.risk.value, action.status.value, action.requires_approval, action.idempotency_key, now, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    audit(user_id, "job_created", job_id=job_id, payload={"mode": mode, "action_count": len(actions)})
    return get_job(user_id, job_id)


def get_job(user_id: int, job_id: str) -> Dict[str, Any]:
    ensure_velia_agent_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT job_id,user_id,goal,mode,status,created_at,updated_at FROM velia_agent_jobs WHERE job_id=%s AND user_id=%s", (str(job_id), int(user_id)))
        raw_job = cursor.fetchone()
        if not raw_job:
            raise AgentJobError("velia_agent_job_not_found", status=404)
        job = _row(raw_job, ["job_id", "user_id", "goal", "mode", "status", "created_at", "updated_at"])
        cursor.execute(
            "SELECT action_id,job_id,sequence_no,tool_name,arguments_json,risk,status,requires_approval,idempotency_key,result_json,error_code,created_at,updated_at FROM velia_agent_actions WHERE job_id=%s ORDER BY sequence_no ASC",
            (str(job_id),),
        )
        columns = ["action_id", "job_id", "sequence_no", "tool_name", "arguments_json", "risk", "status", "requires_approval", "idempotency_key", "result_json", "error_code", "created_at", "updated_at"]
        actions = []
        for raw in cursor.fetchall() or []:
            item = _row(raw, columns)
            item["arguments"] = _loads(item.pop("arguments_json", "{}"), {})
            item["result"] = _loads(item.pop("result_json", ""), None)
            actions.append(item)
        job["actions"] = actions
        return job
    finally:
        cursor.close()
        conn.close()


def set_job_status(user_id: int, job_id: str, status: JobStatus) -> None:
    ensure_velia_agent_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE velia_agent_jobs SET status=%s,updated_at=%s WHERE job_id=%s AND user_id=%s", (JobStatus(status).value, datetime.utcnow(), str(job_id), int(user_id)))
        if cursor.rowcount != 1:
            raise AgentJobError("velia_agent_job_not_found", status=404)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def decide_action(user_id: int, job_id: str, action_id: str, decision: str) -> Dict[str, Any]:
    ensure_velia_agent_tables()
    normalized = str(decision).strip().lower()
    if normalized not in {"approved", "rejected"}:
        raise AgentJobError("velia_agent_approval_invalid")
    next_status = ActionStatus.APPROVED if normalized == "approved" else ActionStatus.REJECTED
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT a.status FROM velia_agent_actions a JOIN velia_agent_jobs j ON j.job_id=a.job_id WHERE a.action_id=%s AND a.job_id=%s AND j.user_id=%s", (str(action_id), str(job_id), int(user_id)))
        row = cursor.fetchone()
        if not row:
            raise AgentJobError("velia_agent_action_not_found", status=404)
        current = str(row.get("status") if isinstance(row, dict) else row[0])
        if current != ActionStatus.AWAITING_APPROVAL.value:
            raise AgentJobError("velia_agent_action_not_awaiting_approval", status=409)
        cursor.execute("UPDATE velia_agent_actions SET status=%s,updated_at=%s WHERE action_id=%s", (next_status.value, datetime.utcnow(), str(action_id)))
        cursor.execute("INSERT INTO velia_agent_approvals (approval_id,job_id,action_id,user_id,decision,created_at) VALUES (%s,%s,%s,%s,%s,%s)", (str(uuid.uuid4()), str(job_id), str(action_id), int(user_id), normalized, datetime.utcnow()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    audit(user_id, f"action_{normalized}", job_id=job_id, action_id=action_id)
    return get_job(user_id, job_id)


def update_action(user_id: int, job_id: str, action_id: str, status: ActionStatus, *, result: Any = None, error_code: str = "") -> None:
    ensure_velia_agent_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_agent_actions a SET status=%s,result_json=%s,error_code=%s,updated_at=%s FROM velia_agent_jobs j WHERE a.job_id=j.job_id AND a.action_id=%s AND a.job_id=%s AND j.user_id=%s",
            (ActionStatus(status).value, _json(result) if result is not None else None, str(error_code or "") or None, datetime.utcnow(), str(action_id), str(job_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            raise AgentJobError("velia_agent_action_not_found", status=404)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_task_draft(user_id: int, title: str, notes: str = "") -> Dict[str, Any]:
    ensure_velia_agent_tables()
    normalized_title = str(title or "").strip()[:300]
    if not normalized_title:
        raise AgentJobError("velia_agent_task_title_required")
    draft_id = str(uuid.uuid4())
    now = datetime.utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO velia_agent_task_drafts (draft_id,user_id,title,notes,completed,created_at,updated_at) VALUES (%s,%s,%s,%s,FALSE,%s,%s)", (draft_id, int(user_id), normalized_title, str(notes or "")[:4000], now, now))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return {"draft_id": draft_id, "title": normalized_title, "notes": str(notes or "")[:4000], "completed": False}


def list_task_drafts(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    ensure_velia_agent_tables()
    bounded = max(1, min(100, int(limit)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT draft_id,title,notes,completed,created_at,updated_at FROM velia_agent_task_drafts WHERE user_id=%s ORDER BY created_at DESC LIMIT %s", (int(user_id), bounded))
        columns = ["draft_id", "title", "notes", "completed", "created_at", "updated_at"]
        return [_row(raw, columns) for raw in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()
