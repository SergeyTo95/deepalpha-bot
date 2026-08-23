from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

from db.database import get_connection
from services import velia_agent_coding_autopilot_integration_repair_service as repair_adapter
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_software_factory_integration_validator_service as validator
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_623
_REPAIR_STATES = {"dispatching", "waiting_ci", "revalidating", "succeeded", "blocked"}


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


def integration_repair_enabled() -> bool:
    return validator.integration_validator_enabled() and _env_bool(
        "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED", False
    )


def integration_repair_max_attempts() -> int:
    return _env_int("VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_MAX_ATTEMPTS", 2, 0, 2)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": integration_repair_enabled(),
        "max_attempts": integration_repair_max_attempts(),
        "same_pull_request_only": True,
        "new_branch_allowed": False,
        "new_pull_request_allowed": False,
        "write_owner": "coding_autopilot",
        "requires_green_ci": True,
        "revalidates_after_repair": True,
    }


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


def ensure_integration_repair_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    execution_module.ensure_workspace_execution_tables()
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_integration_repairs (
                    repair_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES velia_software_factory_workspace_executions(execution_id) ON DELETE CASCADE,
                    validation_id TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    contract_id TEXT NOT NULL DEFAULT '',
                    workspace_task_id TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT '',
                    autopilot_run_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'dispatching',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(execution_id, validation_id),
                    CHECK (attempt_number BETWEEN 1 AND 2),
                    CHECK (status IN ('dispatching','waiting_ci','revalidating','succeeded','blocked'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_integration_repairs_active "
                "ON velia_software_factory_integration_repairs(execution_id,user_id,status,updated_at ASC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "repair_id": str(_value(row, "repair_id", 0, "")),
        "execution_id": str(_value(row, "execution_id", 1, "")),
        "validation_id": str(_value(row, "validation_id", 2, "")),
        "user_id": int(_value(row, "user_id", 3, 0) or 0),
        "attempt_number": int(_value(row, "attempt_number", 4, 0) or 0),
        "contract_id": str(_value(row, "contract_id", 5, "")),
        "workspace_task_id": str(_value(row, "workspace_task_id", 6, "")),
        "project_id": str(_value(row, "project_id", 7, "")),
        "autopilot_run_id": str(_value(row, "autopilot_run_id", 8, "")),
        "status": str(_value(row, "status", 9, "")),
        "evidence": _loads(_value(row, "evidence_json", 10, "{}"), {}),
        "result": _loads(_value(row, "result_json", 11, "{}"), {}),
        "error_code": str(_value(row, "error_code", 12, "") or "") or None,
        "created_at": str(_value(row, "created_at", 13, "") or ""),
        "updated_at": str(_value(row, "updated_at", 14, "") or ""),
    }


_COLUMNS = (
    "repair_id,execution_id,validation_id,user_id,attempt_number,contract_id,"
    "workspace_task_id,project_id,autopilot_run_id,status,evidence_json,result_json,"
    "error_code,created_at,updated_at"
)


def latest_repair(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
    ensure_integration_repair_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM velia_software_factory_integration_repairs "
            "WHERE execution_id=%s AND user_id=%s ORDER BY attempt_number DESC LIMIT 1",
            (str(execution_id), int(user_id)),
        )
        raw = cursor.fetchone()
        return _row(raw) if raw else {}
    finally:
        cursor.close()
        conn.close()


def _repair_for_validation(
    execution_module: Any, user_id: int, execution_id: str, validation_id: str
) -> Dict[str, Any]:
    ensure_integration_repair_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM velia_software_factory_integration_repairs "
            "WHERE execution_id=%s AND validation_id=%s AND user_id=%s LIMIT 1",
            (str(execution_id), str(validation_id), int(user_id)),
        )
        raw = cursor.fetchone()
        return _row(raw) if raw else {}
    finally:
        cursor.close()
        conn.close()


def _attempt_count(execution_module: Any, user_id: int, execution_id: str) -> int:
    ensure_integration_repair_tables(execution_module)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM velia_software_factory_integration_repairs "
            "WHERE execution_id=%s AND user_id=%s",
            (str(execution_id), int(user_id)),
        )
        raw = cursor.fetchone()
        return int(raw[0] if raw and not isinstance(raw, dict) else (next(iter(raw.values())) if raw else 0))
    finally:
        cursor.close()
        conn.close()


def _set_repair(
    repair_id: str,
    status: str,
    *,
    result: Optional[Mapping[str, Any]] = None,
    error_code: str = "",
) -> None:
    if status not in _REPAIR_STATES:
        raise SoftwareFactoryError("velia_factory_integration_repair_state_invalid")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_integration_repairs SET status=%s,"
            "result_json=CASE WHEN %s IS NULL THEN result_json ELSE %s END,error_code=%s,updated_at=NOW() "
            "WHERE repair_id=%s",
            (
                str(status),
                _json(dict(result)) if isinstance(result, Mapping) else None,
                _json(dict(result)) if isinstance(result, Mapping) else None,
                str(error_code or "")[:120] or None,
                str(repair_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _set_execution_blocker(
    execution_module: Any,
    execution: Mapping[str, Any],
    code: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> None:
    blocker = {"code": str(code), **dict(payload or {})}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_workspace_executions "
            "SET status='blocked',blocker_json=%s,updated_at=%s "
            "WHERE execution_id=%s AND user_id=%s AND status IN ('created','running','blocked')",
            (
                execution_module._json(blocker),
                execution_module._utcnow(),
                str(execution.get("execution_id") or ""),
                int(execution.get("user_id") or 0),
            ),
        )
        execution_module._append_event(
            cursor,
            str(execution.get("execution_id") or ""),
            int(execution.get("user_id") or 0),
            "workspace_integration_repair.blocked",
            blocker,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _scope_paths(evidence: Mapping[str, Any]) -> List[str]:
    paths: List[str] = []
    for raw in evidence.get("matched_contract_files") or []:
        value = str(raw or "").strip()
        if value and value not in paths:
            paths.append(value)
    for snippet in evidence.get("snippets") or []:
        if not isinstance(snippet, Mapping):
            continue
        value = str(snippet.get("path") or "").strip()
        if value and value not in paths:
            paths.append(value)
    return paths[:12]


def select_repair_target(
    execution: Mapping[str, Any], validation: Mapping[str, Any]
) -> Dict[str, Any]:
    """Pick one repository task for one bounded repair, then revalidate globally.

    Explicit provider/consumer evidence wins. For a semantic compatibility
    mismatch without a path-specific issue, the consumer is adapted to the
    already review-ready provider contract. We repair one side at a time so a
    successful CI run is always followed by fresh cross-repository evidence.
    """
    report = validation.get("report") if isinstance(validation.get("report"), Mapping) else {}
    if str(report.get("status") or validation.get("status") or "") != "failed":
        raise SoftwareFactoryError("velia_factory_integration_repair_requires_failed_validation", status=409)
    tasks = {
        str(item.get("workspace_task_id") or ""): item
        for item in execution.get("tasks") or []
        if isinstance(item, Mapping)
    }
    plan_contracts = {
        str(item.get("id") or ""): item
        for item in (execution.get("plan") or {}).get("integration_contracts") or []
        if isinstance(item, Mapping)
    }
    for contract_report in report.get("contracts") or []:
        if not isinstance(contract_report, Mapping) or str(contract_report.get("status") or "") != "failed":
            continue
        contract_id = str(contract_report.get("id") or "")
        provider = contract_report.get("provider") if isinstance(contract_report.get("provider"), Mapping) else {}
        consumers = [
            item for item in contract_report.get("consumers") or [] if isinstance(item, Mapping)
        ]
        issues = [str(item or "") for item in contract_report.get("issues") or []]
        target: Mapping[str, Any] = {}
        for issue in issues:
            if issue.startswith("consumer_") and ":" in issue:
                task_id = issue.rsplit(":", 1)[-1]
                target = next((item for item in consumers if str(item.get("task_id") or "") == task_id), {})
                if target:
                    break
            if issue.startswith("provider_") and provider:
                target = provider
                break
        if not target and consumers:
            target = consumers[0]
        if not target and provider:
            target = provider
        task_id = str(target.get("task_id") or "")
        task = tasks.get(task_id)
        scope_paths = _scope_paths(target)
        if not task or not scope_paths:
            continue
        project_id = str(task.get("project_id") or "")
        if str(target.get("project_id") or project_id) != project_id:
            continue
        task_result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
        autopilot_run_id = str(task_result.get("autopilot_run_id") or "")
        if not autopilot_run_id:
            continue
        return {
            "contract_id": contract_id,
            "workspace_task_id": task_id,
            "project_id": project_id,
            "autopilot_run_id": autopilot_run_id,
            "scope_roots": scope_paths,
            "evidence": {
                "contract": dict(plan_contracts.get(contract_id) or {}),
                "validation_contract": dict(contract_report),
                "target_task_id": task_id,
                "target_project_id": project_id,
                "issues": issues,
            },
        }
    raise SoftwareFactoryError("velia_factory_integration_repair_target_unmappable", status=409)


def _insert_repair(
    execution_module: Any,
    execution: Mapping[str, Any],
    validation: Mapping[str, Any],
    target: Mapping[str, Any],
    attempt_number: int,
) -> Dict[str, Any]:
    ensure_integration_repair_tables(execution_module)
    repair_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"""
            INSERT INTO velia_software_factory_integration_repairs (
                repair_id,execution_id,validation_id,user_id,attempt_number,contract_id,
                workspace_task_id,project_id,autopilot_run_id,status,evidence_json,result_json,
                error_code,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'dispatching',%s,'{{}}',NULL,NOW(),NOW())
            ON CONFLICT (execution_id,validation_id) DO NOTHING
            RETURNING {_COLUMNS}
            """,
            (
                repair_id,
                str(execution.get("execution_id") or ""),
                str(validation.get("validation_id") or ""),
                int(execution.get("user_id") or 0),
                int(attempt_number),
                str(target.get("contract_id") or ""),
                str(target.get("workspace_task_id") or ""),
                str(target.get("project_id") or ""),
                str(target.get("autopilot_run_id") or ""),
                _json(target.get("evidence") or {}),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        if row:
            return _row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return _repair_for_validation(
        execution_module,
        int(execution.get("user_id") or 0),
        str(execution.get("execution_id") or ""),
        str(validation.get("validation_id") or ""),
    )


def _dispatch(
    execution_module: Any,
    integration_runtime: Any,
    execution: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> Dict[str, Any]:
    count = _attempt_count(
        execution_module,
        int(execution.get("user_id") or 0),
        str(execution.get("execution_id") or ""),
    )
    if count >= integration_repair_max_attempts():
        _set_execution_blocker(
            execution_module,
            execution,
            "velia_factory_integration_repairs_exhausted",
            {"attempts": count, "validation_id": str(validation.get("validation_id") or "")},
        )
        return execution_module.get_execution(
            int(execution.get("user_id") or 0), str(execution.get("execution_id") or "")
        )
    try:
        target = select_repair_target(execution, validation)
    except SoftwareFactoryError as exc:
        _set_execution_blocker(
            execution_module,
            execution,
            exc.code,
            {"validation_id": str(validation.get("validation_id") or ""), "detail": exc.detail},
        )
        return execution_module.get_execution(
            int(execution.get("user_id") or 0), str(execution.get("execution_id") or "")
        )

    repair = _insert_repair(execution_module, execution, validation, target, count + 1)
    if str(repair.get("status") or "") != "dispatching":
        return repair
    try:
        result = repair_adapter.repair_existing_run(
            int(execution.get("user_id") or 0),
            str(target.get("autopilot_run_id") or ""),
            evidence=target.get("evidence") or {},
            scope_roots=target.get("scope_roots") or [],
            repair_key=f"{execution.get('execution_id')}:{validation.get('validation_id')}:{target.get('workspace_task_id')}",
        )
        repair_result = result.get("repair") if isinstance(result.get("repair"), Mapping) else {}
        _set_repair(str(repair.get("repair_id") or ""), "waiting_ci", result=repair_result)
        _set_execution_blocker(
            execution_module,
            execution,
            "velia_factory_integration_repair_waiting_ci",
            {
                "repair_id": str(repair.get("repair_id") or ""),
                "attempt": count + 1,
                "workspace_task_id": str(target.get("workspace_task_id") or ""),
                "project_id": str(target.get("project_id") or ""),
                "autopilot_run_id": str(target.get("autopilot_run_id") or ""),
                "pull_request_number": int(repair_result.get("pull_request_number") or 0),
                "commit_sha": str(repair_result.get("commit_sha") or ""),
            },
        )
    except Exception as exc:
        code = str(getattr(exc, "code", "velia_factory_integration_repair_dispatch_failed"))[:120]
        _set_repair(str(repair.get("repair_id") or ""), "blocked", error_code=code)
        _set_execution_blocker(
            execution_module,
            execution,
            code,
            {"repair_id": str(repair.get("repair_id") or ""), "detail": str(getattr(exc, "detail", ""))[:500]},
        )
        logger.exception(
            "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_DISPATCH_FAILED execution_id=%s code=%s",
            execution.get("execution_id"),
            code,
        )
    return execution_module.get_execution(
        int(execution.get("user_id") or 0), str(execution.get("execution_id") or "")
    )


def process_execution(
    execution_module: Any,
    integration_runtime: Any,
    user_id: int,
    execution_id: str,
) -> Dict[str, Any]:
    if not integration_repair_enabled():
        raise SoftwareFactoryError("velia_factory_integration_repair_disabled", status=503)
    execution_module._require_live(int(user_id))
    ensure_integration_repair_tables(execution_module)
    execution = execution_module.get_execution(int(user_id), str(execution_id))
    if bool(execution.get("stop_requested")):
        return execution_module.request_stop(int(user_id), str(execution_id))

    validation = integration_runtime.latest_validation(
        execution_module, int(user_id), str(execution_id)
    )
    report = validation.get("report") if isinstance(validation.get("report"), Mapping) else {}
    validation_status = str(report.get("status") or validation.get("status") or "")
    if validation_status == "passed":
        finalizer = getattr(integration_runtime, "finalize_validation_pass", None)
        if not callable(finalizer):
            raise SoftwareFactoryError("velia_factory_integration_repair_finalizer_missing", status=500)
        finalizer(execution_module, int(user_id), str(execution_id), str(validation.get("validation_id") or ""))
        return execution_module.get_execution(int(user_id), str(execution_id))
    if validation_status == "blocked":
        _set_execution_blocker(
            execution_module,
            execution,
            "velia_factory_workspace_integration_validation_blocked",
            {"validation_id": str(validation.get("validation_id") or ""), "issues": list(report.get("issues") or [])[:20]},
        )
        return execution_module.get_execution(int(user_id), str(execution_id))
    if validation_status != "failed":
        raise SoftwareFactoryError("velia_factory_integration_repair_validation_missing", status=409)

    existing = _repair_for_validation(
        execution_module, int(user_id), str(execution_id), str(validation.get("validation_id") or "")
    )
    if not existing:
        return _dispatch(execution_module, integration_runtime, execution, validation)
    if str(existing.get("status") or "") == "blocked":
        return execution
    if str(existing.get("status") or "") == "succeeded":
        return execution

    run_id = str(existing.get("autopilot_run_id") or "")
    if not run_id:
        _set_repair(str(existing.get("repair_id") or ""), "blocked", error_code="velia_factory_integration_repair_run_missing")
        _set_execution_blocker(
            execution_module,
            execution,
            "velia_factory_integration_repair_run_missing",
            {"repair_id": str(existing.get("repair_id") or "")},
        )
        return execution_module.get_execution(int(user_id), str(execution_id))

    run = autopilot.get_run(int(user_id), run_id)
    run_status = str(run.get("status") or "")
    if run_status in {"waiting_ci", "repairing"}:
        return execution
    if run_status in {"blocked", "failed", "cancelled"}:
        code = str(run.get("error_code") or "velia_factory_integration_repair_autopilot_blocked")
        _set_repair(str(existing.get("repair_id") or ""), "blocked", error_code=code)
        _set_execution_blocker(
            execution_module,
            execution,
            code,
            {"repair_id": str(existing.get("repair_id") or ""), "autopilot_run_id": run_id},
        )
        return execution_module.get_execution(int(user_id), str(execution_id))
    if run_status != "ready_for_review":
        return execution

    _set_repair(str(existing.get("repair_id") or ""), "revalidating")
    refreshed = execution_module.get_execution(int(user_id), str(execution_id))
    next_validation = integration_runtime.validate_and_store(execution_module, refreshed)
    next_report = next_validation.get("report") if isinstance(next_validation.get("report"), Mapping) else {}
    next_status = str(next_report.get("status") or next_validation.get("status") or "blocked")
    _set_repair(
        str(existing.get("repair_id") or ""),
        "succeeded" if next_status in {"passed", "failed"} else "blocked",
        result={
            **dict(existing.get("result") or {}),
            "revalidation_id": str(next_validation.get("validation_id") or ""),
            "revalidation_status": next_status,
        },
        error_code=("" if next_status in {"passed", "failed"} else "velia_factory_workspace_integration_validation_blocked"),
    )
    if next_status == "passed":
        finalizer = getattr(integration_runtime, "finalize_validation_pass", None)
        if not callable(finalizer):
            raise SoftwareFactoryError("velia_factory_integration_repair_finalizer_missing", status=500)
        finalizer(
            execution_module,
            int(user_id),
            str(execution_id),
            str(next_validation.get("validation_id") or ""),
        )
        return execution_module.get_execution(int(user_id), str(execution_id))
    if next_status == "blocked":
        _set_execution_blocker(
            execution_module,
            execution,
            "velia_factory_workspace_integration_validation_blocked",
            {"validation_id": str(next_validation.get("validation_id") or ""), "issues": list(next_report.get("issues") or [])[:20]},
        )
        return execution_module.get_execution(int(user_id), str(execution_id))

    refreshed = execution_module.get_execution(int(user_id), str(execution_id))
    return _dispatch(execution_module, integration_runtime, refreshed, next_validation)
