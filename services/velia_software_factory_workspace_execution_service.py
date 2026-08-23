from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from aiohttp import web

from db.database import get_connection
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_workspace_service as workspace_service
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_611
_ACTIVE_STATUSES = {"created", "running", "blocked"}
_TERMINAL_STATUSES = {"review_ready", "cancelled", "failed"}
_AUTOPILOT_ACTIVE = {"queued", "claimed", "planning", "executing"}


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


def workspace_execution_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED", False)


def workspace_supervisor_enabled() -> bool:
    return (
        workspace_execution_enabled()
        and autonomy.supervisor_enabled()
        and rollout.supervisor_allowed()
        and autopilot.worker_enabled()
    )


def _utcnow() -> datetime:
    return datetime.utcnow()


def _json(value: Any, limit: int = 120000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _dict_cursor(conn):
    try:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        return conn.cursor()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _lock_key(execution_id: str) -> int:
    raw = hashlib.sha256(str(execution_id).encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=False)
    return value - (1 << 64) if value >= (1 << 63) else value


def ensure_workspace_execution_tables() -> None:
    global _SCHEMA_READY
    workspace_service.ensure_workspace_tables()
    autopilot.ensure_coding_autopilot_tables()
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_executions (
                    execution_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES velia_software_factory_workspaces(workspace_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    plan_fingerprint TEXT NOT NULL,
                    blocker_json TEXT NOT NULL DEFAULT '{}',
                    stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('created','running','blocked','review_ready','cancelled','failed'))
                )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_factory_workspace_execution_active
                ON velia_software_factory_workspace_executions(user_id,workspace_id)
                WHERE status IN ('created','running','blocked')
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_execution_missions (
                    execution_id TEXT NOT NULL REFERENCES velia_software_factory_workspace_executions(execution_id) ON DELETE CASCADE,
                    workspace_id TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL,
                    mission_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (execution_id,project_id),
                    UNIQUE(mission_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_execution_tasks (
                    execution_id TEXT NOT NULL REFERENCES velia_software_factory_workspace_executions(execution_id) ON DELETE CASCADE,
                    workspace_task_id TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL,
                    mission_id TEXT NOT NULL DEFAULT '',
                    autopilot_task_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (execution_id,workspace_task_id),
                    CHECK (status IN ('pending','queued','claimed','planning','executing','ready_for_review','failed','blocked','cancelled'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_workspace_execution_tasks_status "
                "ON velia_software_factory_workspace_execution_tasks(execution_id,status,project_id)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_execution_events (
                    event_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES velia_software_factory_workspace_executions(execution_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    event_type TEXT NOT NULL,
                    workspace_task_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_workspace_execution_events "
                "ON velia_software_factory_workspace_execution_events(execution_id,created_at ASC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _append_event(cursor: Any, execution_id: str, user_id: int, event_type: str, payload: Any = None, task_id: str = "") -> None:
    cursor.execute(
        "INSERT INTO velia_software_factory_workspace_execution_events "
        "(event_id,execution_id,user_id,event_type,workspace_task_id,payload_json,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            str(uuid.uuid4()), str(execution_id), int(user_id), str(event_type)[:120],
            str(task_id)[:120], _json(payload or {}, 30000), _utcnow(),
        ),
    )


def _execution_row(row: Any) -> Dict[str, Any]:
    return {
        "execution_id": str(_value(row, "execution_id", 0, "")),
        "workspace_id": str(_value(row, "workspace_id", 1, "")),
        "user_id": int(_value(row, "user_id", 2, 0) or 0),
        "status": str(_value(row, "status", 3, "")),
        "plan": _loads(_value(row, "plan_json", 4, "{}"), {}),
        "plan_fingerprint": str(_value(row, "plan_fingerprint", 5, "")),
        "blocker": _loads(_value(row, "blocker_json", 6, "{}"), {}),
        "stop_requested": bool(_value(row, "stop_requested", 7, False)),
        "created_at": str(_value(row, "created_at", 8, "") or ""),
        "updated_at": str(_value(row, "updated_at", 9, "") or ""),
    }


def _task_row(row: Any) -> Dict[str, Any]:
    return {
        "workspace_task_id": str(_value(row, "workspace_task_id", 0, "")),
        "project_id": str(_value(row, "project_id", 1, "")),
        "mission_id": str(_value(row, "mission_id", 2, "")),
        "autopilot_task_id": str(_value(row, "autopilot_task_id", 3, "")),
        "status": str(_value(row, "status", 4, "pending")),
        "depends_on": _loads(_value(row, "depends_on_json", 5, "[]"), []),
        "payload": _loads(_value(row, "payload_json", 6, "{}"), {}),
        "result": _loads(_value(row, "result_json", 7, "{}"), {}),
        "error_code": str(_value(row, "error_code", 8, "") or "") or None,
        "created_at": str(_value(row, "created_at", 9, "") or ""),
        "updated_at": str(_value(row, "updated_at", 10, "") or ""),
    }


def _require_live(user_id: int) -> None:
    if not workspace_execution_enabled():
        raise SoftwareFactoryError("velia_factory_workspace_execution_disabled", status=503)
    if not rollout.live_execution_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_workspace_live_rollout_required", status=403)
    if not autopilot.autopilot_enabled():
        raise SoftwareFactoryError("velia_coding_autopilot_disabled", status=503)
    if not autopilot.worker_enabled():
        raise SoftwareFactoryError("velia_coding_autopilot_worker_disabled", status=503)


def _mission_name(execution: Mapping[str, Any], project_id: str, repository: str) -> str:
    repo = str(repository or project_id).rsplit("/", 1)[-1]
    return f"VELIA Workspace {str(execution.get('workspace_id') or '')[:8]} · {str(execution.get('execution_id') or '')[:8]} · {repo}"[:200]


def _member_map(workspace: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("project_id") or ""): dict(item)
        for item in workspace.get("repositories") or []
        if isinstance(item, Mapping) and str(item.get("project_id") or "")
    }


def get_execution(user_id: int, execution_id: str) -> Dict[str, Any]:
    ensure_workspace_execution_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT execution_id,workspace_id,user_id,status,plan_json,plan_fingerprint,blocker_json,"
            "stop_requested,created_at,updated_at FROM velia_software_factory_workspace_executions "
            "WHERE execution_id=%s AND user_id=%s",
            (str(execution_id), int(user_id)),
        )
        raw = cursor.fetchone()
        if not raw:
            raise SoftwareFactoryError("velia_factory_workspace_execution_not_found", status=404)
        result = _execution_row(raw)
        cursor.execute(
            "SELECT workspace_task_id,project_id,mission_id,autopilot_task_id,status,depends_on_json,"
            "payload_json,result_json,error_code,created_at,updated_at "
            "FROM velia_software_factory_workspace_execution_tasks WHERE execution_id=%s AND user_id=%s "
            "ORDER BY created_at,workspace_task_id",
            (str(execution_id), int(user_id)),
        )
        result["tasks"] = [_task_row(row) for row in (cursor.fetchall() or [])]
        cursor.execute(
            "SELECT project_id,mission_id FROM velia_software_factory_workspace_execution_missions "
            "WHERE execution_id=%s AND user_id=%s ORDER BY project_id",
            (str(execution_id), int(user_id)),
        )
        result["missions"] = [
            {"project_id": str(_value(row, "project_id", 0, "")), "mission_id": str(_value(row, "mission_id", 1, ""))}
            for row in (cursor.fetchall() or [])
        ]
        counts: Dict[str, int] = {}
        for task in result["tasks"]:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        result["progress"] = counts
        result["completion_scope"] = "review_ready"
        result["dependency_gate"] = "ready_for_review"
        return result
    finally:
        cursor.close()
        conn.close()


def list_executions(user_id: int, workspace_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    ensure_workspace_execution_tables()
    workspace_service.get_workspace(int(user_id), str(workspace_id))
    safe_limit = min(50, max(1, int(limit or 20)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT execution_id FROM velia_software_factory_workspace_executions "
            "WHERE workspace_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT %s",
            (str(workspace_id), int(user_id), safe_limit),
        )
        ids = [str(row[0]) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()
    return [get_execution(int(user_id), item) for item in ids]


def _set_execution_state(execution_id: str, user_id: int, status: str, blocker: Optional[Mapping[str, Any]] = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_workspace_executions SET status=%s,blocker_json=%s,updated_at=%s "
            "WHERE execution_id=%s AND user_id=%s",
            (str(status), _json(dict(blocker or {})), _utcnow(), str(execution_id), int(user_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_execution(user_id: int, workspace_id: str, plan_payload: Mapping[str, Any]) -> Dict[str, Any]:
    _require_live(int(user_id))
    ensure_workspace_execution_tables()
    workspace = workspace_service.get_workspace(int(user_id), str(workspace_id))
    if str(workspace.get("status") or "") != "active":
        raise SoftwareFactoryError("velia_factory_workspace_archived", status=409)
    plan = workspace_service.normalize_workspace_plan(plan_payload, workspace)
    if not bool(plan.get("execution_ready")):
        raise SoftwareFactoryError("velia_factory_workspace_scopes_not_approved", status=409)
    execution_id = str(uuid.uuid4())
    fingerprint = _fingerprint(plan)
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO velia_software_factory_workspace_executions "
            "(execution_id,workspace_id,user_id,status,plan_json,plan_fingerprint,blocker_json,stop_requested,created_at,updated_at) "
            "VALUES (%s,%s,%s,'created',%s,%s,'{}',FALSE,%s,%s)",
            (execution_id, str(workspace_id), int(user_id), _json(plan), fingerprint, now, now),
        )
        for task in plan.get("tasks") or []:
            cursor.execute(
                "INSERT INTO velia_software_factory_workspace_execution_tasks "
                "(execution_id,workspace_task_id,user_id,project_id,mission_id,autopilot_task_id,status,depends_on_json,payload_json,result_json,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,'','','pending',%s,%s,'{}',%s,%s)",
                (
                    execution_id, str(task.get("id") or ""), int(user_id), str(task.get("project_id") or ""),
                    _json(task.get("depends_on") or []), _json(task), now, now,
                ),
            )
        _append_event(cursor, execution_id, int(user_id), "workspace_execution.created", {"plan_fingerprint": fingerprint})
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
            raise SoftwareFactoryError("velia_factory_workspace_execution_exists", status=409) from exc
        raise
    finally:
        cursor.close()
        conn.close()
    # Mission creation and task dispatch are reconciled by tick_execution. Keeping
    # execution creation separate makes the operation restart-safe across crashes.
    return tick_execution(int(user_id), execution_id)


def _mission_bindings(execution_id: str, user_id: int) -> Dict[str, str]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT project_id,mission_id FROM velia_software_factory_workspace_execution_missions "
            "WHERE execution_id=%s AND user_id=%s",
            (str(execution_id), int(user_id)),
        )
        return {str(row[0]): str(row[1]) for row in cursor.fetchall() or []}
    finally:
        cursor.close()
        conn.close()


def _bind_mission(execution: Mapping[str, Any], project_id: str, mission_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO velia_software_factory_workspace_execution_missions "
            "(execution_id,workspace_id,user_id,project_id,mission_id,created_at) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (execution_id,project_id) DO UPDATE SET mission_id=EXCLUDED.mission_id",
            (
                str(execution["execution_id"]), str(execution["workspace_id"]), int(execution["user_id"]),
                str(project_id), str(mission_id), _utcnow(),
            ),
        )
        cursor.execute(
            "UPDATE velia_software_factory_workspace_execution_tasks SET mission_id=%s,updated_at=%s "
            "WHERE execution_id=%s AND user_id=%s AND project_id=%s",
            (str(mission_id), _utcnow(), str(execution["execution_id"]), int(execution["user_id"]), str(project_id)),
        )
        _append_event(cursor, str(execution["execution_id"]), int(execution["user_id"]), "workspace_mission.bound", {"project_id": str(project_id), "mission_id": str(mission_id)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _ensure_missions(execution: Mapping[str, Any]) -> Dict[str, str]:
    user_id = int(execution["user_id"])
    workspace = workspace_service.get_workspace(user_id, str(execution["workspace_id"]))
    members = _member_map(workspace)
    plan_projects = sorted({str(task.get("project_id") or "") for task in (execution.get("plan") or {}).get("tasks") or []})
    bindings = _mission_bindings(str(execution["execution_id"]), user_id)
    all_missions = autopilot.list_missions(user_id)

    for project_id in plan_projects:
        member = members.get(project_id)
        if not member:
            raise SoftwareFactoryError("velia_factory_workspace_project_not_found", detail=project_id, status=404)
        if not bool(member.get("scope_approved")) or not member.get("allowed_paths"):
            raise SoftwareFactoryError("velia_factory_workspace_scopes_not_approved", detail=project_id, status=409)
        expected_name = _mission_name(execution, project_id, str(member.get("repository_full_name") or ""))
        mission_id = bindings.get(project_id, "")
        if mission_id:
            mission = autopilot.get_mission(user_id, mission_id)
        else:
            candidates = [
                item for item in all_missions
                if str(item.get("project_id") or "") == project_id and str(item.get("status") or "") in {"paused", "active"}
            ]
            recovered = next((item for item in candidates if str(item.get("name") or "") == expected_name), None)
            if recovered:
                mission = recovered
            elif candidates:
                raise SoftwareFactoryError(
                    "velia_factory_workspace_mission_conflict",
                    detail=str(candidates[0].get("mission_id") or ""),
                    status=409,
                )
            else:
                mission = autopilot.create_mission(
                    user_id,
                    project_id,
                    expected_name,
                    allowed_paths=member.get("allowed_paths") or [],
                    blocked_paths=member.get("blocked_paths") or [],
                    max_steps=5,
                    max_files=12,
                )
                all_missions.append(mission)
            mission_id = str(mission.get("mission_id") or "")
            _bind_mission(execution, project_id, mission_id)
            bindings[project_id] = mission_id
        if not execution.get("stop_requested") and str(mission.get("status") or "") != "active":
            autopilot.set_mission_status(user_id, mission_id, "active")
    return bindings


def _load_tasks(execution_id: str, user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT workspace_task_id,project_id,mission_id,autopilot_task_id,status,depends_on_json,payload_json,"
            "result_json,error_code,created_at,updated_at FROM velia_software_factory_workspace_execution_tasks "
            "WHERE execution_id=%s AND user_id=%s ORDER BY created_at,workspace_task_id",
            (str(execution_id), int(user_id)),
        )
        return [_task_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def _update_task(execution_id: str, user_id: int, task_id: str, *, status: str, autopilot_task_id: Optional[str] = None, result: Any = None, error_code: Any = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        fields = ["status=%s", "updated_at=%s"]
        values: List[Any] = [str(status), _utcnow()]
        if autopilot_task_id is not None:
            fields.append("autopilot_task_id=%s")
            values.append(str(autopilot_task_id))
        if result is not None:
            fields.append("result_json=%s")
            values.append(_json(result or {}))
        if error_code is not None:
            fields.append("error_code=%s")
            values.append(str(error_code or "") or None)
        values.extend([str(execution_id), int(user_id), str(task_id)])
        cursor.execute(
            "UPDATE velia_software_factory_workspace_execution_tasks SET " + ",".join(fields) +
            " WHERE execution_id=%s AND user_id=%s AND workspace_task_id=%s",
            tuple(values),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _reconcile_tasks(execution: Mapping[str, Any]) -> List[Dict[str, Any]]:
    user_id = int(execution["user_id"])
    tasks = _load_tasks(str(execution["execution_id"]), user_id)
    for task in tasks:
        autopilot_task_id = str(task.get("autopilot_task_id") or "")
        if not autopilot_task_id:
            continue
        remote = autopilot.get_task(user_id, autopilot_task_id)
        status = str(remote.get("status") or "blocked")
        result = dict(remote.get("result") or {})
        latest_run_id = str(remote.get("latest_run_id") or "")
        if latest_run_id:
            try:
                run = autopilot.get_run(user_id, latest_run_id)
                result["autopilot_run_id"] = latest_run_id
                result["work_branch"] = str(run.get("work_branch") or "")
                result["pull_request_number"] = int(run.get("pull_request_number") or 0)
                result["pull_request_url"] = str(run.get("pull_request_url") or "")
                result["estimated_cost_usd"] = float(run.get("estimated_cost_usd") or 0.0)
            except Exception:
                logger.exception("VELIA_WORKSPACE_AUTOPILOT_RUN_READ_FAILED task_id=%s", autopilot_task_id)
        _update_task(
            str(execution["execution_id"]), user_id, str(task["workspace_task_id"]),
            status=status, result=result, error_code=remote.get("error_code"),
        )
    return _load_tasks(str(execution["execution_id"]), user_id)


def _dependency_context(task: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for dependency_id in task.get("depends_on") or []:
        dependency = by_id.get(str(dependency_id)) or {}
        remote_result = dependency.get("result") if isinstance(dependency.get("result"), Mapping) else {}
        result.append(
            {
                "task_id": str(dependency_id),
                "project_id": str(dependency.get("project_id") or ""),
                "status": str(dependency.get("status") or ""),
                "pull_request_url": str(remote_result.get("pull_request_url") or ""),
                "work_branch": str(remote_result.get("work_branch") or ""),
            }
        )
    return result


def _instruction(execution: Mapping[str, Any], task: Mapping[str, Any], dependencies: List[Dict[str, Any]]) -> str:
    payload = task.get("payload") if isinstance(task.get("payload"), Mapping) else {}
    allowed = [str(item) for item in payload.get("allowed_paths") or []]
    acceptance = (execution.get("plan") or {}).get("acceptance_criteria") or []
    return (
        "VELIA multi-repo workspace task. Work ONLY in this task's repository and approved write scope.\n"
        f"Workspace objective: {str((execution.get('plan') or {}).get('objective') or '')[:4000]}\n"
        f"Task: {str(payload.get('title') or task.get('workspace_task_id') or '')[:500]}\n"
        f"Goal: {str(payload.get('goal') or '')[:6000]}\n"
        f"Repository: {str(payload.get('repository_full_name') or '')[:300]}\n"
        f"Base branch: {str(payload.get('selected_branch') or '')[:200]}\n"
        f"Approved paths: {', '.join(allowed)}\n"
        f"Upstream review-ready dependencies: {_json(dependencies, 8000)}\n"
        f"Acceptance context: {_json(acceptance, 6000)}\n"
        "Do not modify any other repository. Preserve compatibility. Run relevant tests and stop at draft-PR/review-ready; never merge."
    )[:12000]


def _dispatch_ready(execution: Mapping[str, Any], tasks: List[Dict[str, Any]], bindings: Mapping[str, str]) -> int:
    user_id = int(execution["user_id"])
    by_id = {str(item["workspace_task_id"]): item for item in tasks}
    dispatched = 0
    for task in tasks:
        if str(task.get("status") or "") != "pending":
            continue
        dependencies = [str(item) for item in task.get("depends_on") or []]
        if not all(str((by_id.get(dep) or {}).get("status") or "") == "ready_for_review" for dep in dependencies):
            continue
        mission_id = str(bindings.get(str(task.get("project_id") or "")) or "")
        if not mission_id:
            raise SoftwareFactoryError("velia_factory_workspace_mission_missing", detail=str(task.get("project_id") or ""), status=409)
        remote = autopilot.enqueue_task(
            user_id,
            mission_id,
            _instruction(execution, task, _dependency_context(task, by_id)),
            priority=50,
            client_request_id=f"workspace:{str(execution['execution_id'])}:{str(task['workspace_task_id'])}"[:160],
        )
        _update_task(
            str(execution["execution_id"]), user_id, str(task["workspace_task_id"]),
            status=str(remote.get("status") or "queued"), autopilot_task_id=str(remote.get("task_id") or ""),
        )
        dispatched += 1
    return dispatched


def _pause_missions(execution: Mapping[str, Any]) -> None:
    user_id = int(execution["user_id"])
    for mission_id in _mission_bindings(str(execution["execution_id"]), user_id).values():
        try:
            mission = autopilot.get_mission(user_id, mission_id)
            if str(mission.get("status") or "") == "active":
                autopilot.set_mission_status(user_id, mission_id, "paused")
        except Exception:
            logger.exception("VELIA_WORKSPACE_MISSION_PAUSE_FAILED mission_id=%s", mission_id)


def _handle_stop(execution: Mapping[str, Any], tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    user_id = int(execution["user_id"])
    _pause_missions(execution)
    for task in tasks:
        remote_id = str(task.get("autopilot_task_id") or "")
        if str(task.get("status") or "") == "queued" and remote_id:
            try:
                remote = autopilot.cancel_task(user_id, remote_id)
                _update_task(str(execution["execution_id"]), user_id, str(task["workspace_task_id"]), status=str(remote.get("status") or "cancelled"))
            except Exception:
                logger.exception("VELIA_WORKSPACE_TASK_CANCEL_FAILED task_id=%s", remote_id)
    tasks = _reconcile_tasks(execution)
    in_flight = [item for item in tasks if str(item.get("status") or "") in {"claimed", "planning", "executing"}]
    if not in_flight:
        _set_execution_state(str(execution["execution_id"]), user_id, "cancelled", {"reason": "user_stop"})
    return get_execution(user_id, str(execution["execution_id"]))


def tick_execution(user_id: int, execution_id: str) -> Dict[str, Any]:
    _require_live(int(user_id))
    ensure_workspace_execution_tables()
    lock_conn = get_connection()
    lock_cursor = lock_conn.cursor()
    locked = False
    try:
        lock_cursor.execute("SELECT pg_try_advisory_lock(%s)", (_lock_key(execution_id),))
        row = lock_cursor.fetchone()
        locked = bool(row[0] if row and not isinstance(row, dict) else (next(iter(row.values())) if row else False))
        if not locked:
            return get_execution(int(user_id), str(execution_id))

        execution = get_execution(int(user_id), str(execution_id))
        if execution["status"] in _TERMINAL_STATUSES:
            return execution
        if execution["status"] == "blocked":
            return execution

        try:
            bindings = _ensure_missions(execution)
        except Exception as exc:
            code = str(getattr(exc, "code", "velia_factory_workspace_mission_setup_failed"))
            _set_execution_state(str(execution_id), int(user_id), "blocked", {"code": code, "detail": str(getattr(exc, "detail", ""))})
            _pause_missions(execution)
            return get_execution(int(user_id), str(execution_id))

        execution = get_execution(int(user_id), str(execution_id))
        tasks = _reconcile_tasks(execution)
        if bool(execution.get("stop_requested")):
            return _handle_stop(execution, tasks)

        bad = [item for item in tasks if str(item.get("status") or "") in {"failed", "blocked", "cancelled"}]
        if bad:
            blocker = {
                "code": "velia_factory_workspace_task_blocked",
                "tasks": [
                    {"task_id": item["workspace_task_id"], "status": item["status"], "error_code": item.get("error_code")}
                    for item in bad[:20]
                ],
            }
            _set_execution_state(str(execution_id), int(user_id), "blocked", blocker)
            _pause_missions(execution)
            return get_execution(int(user_id), str(execution_id))

        if tasks and all(str(item.get("status") or "") == "ready_for_review" for item in tasks):
            _set_execution_state(str(execution_id), int(user_id), "review_ready", {})
            _pause_missions(execution)
            return get_execution(int(user_id), str(execution_id))

        dispatched = _dispatch_ready(execution, tasks, bindings)
        _set_execution_state(str(execution_id), int(user_id), "running", {})
        if dispatched:
            conn = get_connection()
            cursor = conn.cursor()
            try:
                _append_event(cursor, str(execution_id), int(user_id), "workspace_scheduler.dispatched", {"count": dispatched})
                conn.commit()
            finally:
                cursor.close()
                conn.close()
        return get_execution(int(user_id), str(execution_id))
    finally:
        if locked:
            try:
                lock_cursor.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(execution_id),))
                lock_conn.commit()
            except Exception:
                lock_conn.rollback()
        lock_cursor.close()
        lock_conn.close()


def request_stop(user_id: int, execution_id: str) -> Dict[str, Any]:
    # Stop stays available even if rollout is later disabled. Emergency shutdown
    # must never depend on permission to start new repository work.
    ensure_workspace_execution_tables()
    execution = get_execution(int(user_id), str(execution_id))
    if execution["status"] in _TERMINAL_STATUSES:
        return execution
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_workspace_executions SET stop_requested=TRUE,updated_at=%s "
            "WHERE execution_id=%s AND user_id=%s",
            (_utcnow(), str(execution_id), int(user_id)),
        )
        _append_event(cursor, str(execution_id), int(user_id), "workspace_execution.stop_requested", {})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    execution = get_execution(int(user_id), str(execution_id))
    _pause_missions(execution)
    tasks = _reconcile_tasks(execution)
    return _handle_stop(execution, tasks)


def list_events(user_id: int, execution_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    get_execution(int(user_id), str(execution_id))
    safe_limit = min(500, max(1, int(limit or 200)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT event_type,workspace_task_id,payload_json,created_at FROM velia_software_factory_workspace_execution_events "
            "WHERE execution_id=%s AND user_id=%s ORDER BY created_at ASC LIMIT %s",
            (str(execution_id), int(user_id), safe_limit),
        )
        return [
            {
                "event_type": str(_value(row, "event_type", 0, "")),
                "workspace_task_id": str(_value(row, "workspace_task_id", 1, "")),
                "payload": _loads(_value(row, "payload_json", 2, "{}"), {}),
                "created_at": str(_value(row, "created_at", 3, "") or ""),
            }
            for row in (cursor.fetchall() or [])
        ]
    finally:
        cursor.close()
        conn.close()


def _candidate_executions(limit: int) -> List[tuple[int, str]]:
    ensure_workspace_execution_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT user_id,execution_id FROM velia_software_factory_workspace_executions "
            "WHERE status IN ('created','running') ORDER BY updated_at ASC LIMIT %s",
            (min(100, max(1, int(limit))),),
        )
        return [(int(row[0]), str(row[1])) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def run_workspace_supervisor_once() -> List[Dict[str, Any]]:
    if not workspace_supervisor_enabled():
        return []
    results: List[Dict[str, Any]] = []
    for user_id, execution_id in _candidate_executions(_env_int("VELIA_SOFTWARE_FACTORY_WORKSPACE_SUPERVISOR_MAX_RUNS_PER_TICK", 20, 1, 100)):
        if not rollout.user_allowed(int(user_id)):
            continue
        try:
            results.append(tick_execution(int(user_id), str(execution_id)))
        except Exception:
            logger.exception("VELIA_WORKSPACE_SUPERVISOR_EXECUTION_FAILED execution_id=%s", execution_id)
    return results


async def _supervisor_loop() -> None:
    interval = _env_int("VELIA_SOFTWARE_FACTORY_WORKSPACE_SUPERVISOR_INTERVAL_SECONDS", 20, 5, 300)
    while True:
        try:
            await asyncio.to_thread(run_workspace_supervisor_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VELIA_WORKSPACE_SUPERVISOR_TICK_FAILED")
        await asyncio.sleep(interval)


def install_workspace_execution(app: web.Application) -> None:
    if app.get("velia_software_factory_workspace_execution_installed"):
        return
    app["velia_software_factory_workspace_execution_installed"] = True
    if not workspace_supervisor_enabled():
        logger.info(
            "VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_INSTALLED enabled=%s supervisor=false",
            str(workspace_execution_enabled()).lower(),
        )
        return

    async def supervisor_context(_app: web.Application):
        task = asyncio.create_task(_supervisor_loop(), name="velia-workspace-execution-supervisor")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.cleanup_ctx.append(supervisor_context)
    logger.info("VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_INSTALLED enabled=true supervisor=true")
