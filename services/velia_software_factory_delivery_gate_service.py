from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from db.database import get_connection
from services import velia_agent_coding_autopilot_merge_policy_service as merge_policy
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_project_service as project_service
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_633
_SOURCE_WORKSPACE = "workspace_execution"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def delivery_gate_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED", False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": delivery_gate_enabled(),
        "mode": "read_only_candidate",
        "sources": [_SOURCE_WORKSPACE],
        "merge_policy_required": True,
        "integration_validation_required": True,
        "user_approval_required": True,
        "execution_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
        "rollback_supported": False,
    }


def _utcnow() -> datetime:
    return datetime.utcnow()


def _json(value: Any, limit: int = 160000) -> str:
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


def ensure_delivery_tables(execution_module: Any) -> None:
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_delivery_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (source_type IN ('workspace_execution')),
                    CHECK (status IN ('eligible','blocked')),
                    UNIQUE(user_id,source_type,source_id,source_fingerprint)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_delivery_candidates "
                "ON velia_software_factory_delivery_candidates(user_id,source_type,source_id,created_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _require_user(user_id: int) -> None:
    if not delivery_gate_enabled():
        raise SoftwareFactoryError("velia_factory_delivery_gate_disabled", status=503)
    if not rollout.intake_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)
    if not merge_policy.merge_policy_enabled():
        raise SoftwareFactoryError("velia_factory_delivery_merge_policy_disabled", status=503)


def _integration_evidence(execution: Mapping[str, Any]) -> Dict[str, Any]:
    if not bool(execution.get("integration_validator_enabled")):
        raise SoftwareFactoryError("velia_factory_delivery_integration_validator_required", status=503)
    validation = execution.get("integration_validation") if isinstance(execution.get("integration_validation"), Mapping) else {}
    if str(validation.get("status") or "") != "passed":
        raise SoftwareFactoryError(
            "velia_factory_delivery_integration_validation_not_passed",
            detail=str(validation.get("status") or "missing"),
            status=409,
        )
    report = validation.get("report") if isinstance(validation.get("report"), Mapping) else {}
    report_status = str(report.get("status") or validation.get("status") or "")
    if report_status != "passed":
        raise SoftwareFactoryError(
            "velia_factory_delivery_integration_validation_not_passed",
            detail=report_status or "missing",
            status=409,
        )
    return {
        "validation_id": str(validation.get("validation_id") or ""),
        "status": "passed",
        "contract_fingerprint": str(validation.get("contract_fingerprint") or report.get("contract_fingerprint") or ""),
        "created_at": str(validation.get("created_at") or ""),
    }


def _run_bindings(execution: Mapping[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    tasks = [dict(item) for item in execution.get("tasks") or [] if isinstance(item, Mapping)]
    blockers: List[Dict[str, str]] = []
    bindings: List[Dict[str, str]] = []
    seen: Dict[str, str] = {}
    for task in tasks:
        task_id = str(task.get("workspace_task_id") or "")
        project_id = str(task.get("project_id") or "")
        status = str(task.get("status") or "")
        result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
        run_id = str(result.get("autopilot_run_id") or "")
        if status != "ready_for_review":
            blockers.append({"code": "delivery_task_not_review_ready", "task_id": task_id, "detail": status})
            continue
        if not run_id:
            blockers.append({"code": "delivery_autopilot_run_missing", "task_id": task_id, "detail": project_id})
            continue
        previous = seen.get(run_id)
        if previous and previous != project_id:
            blockers.append({"code": "delivery_run_project_conflict", "task_id": task_id, "detail": run_id})
            continue
        seen[run_id] = project_id
    for run_id, project_id in sorted(seen.items(), key=lambda item: (item[1], item[0])):
        bindings.append({"run_id": run_id, "project_id": project_id})
    if not bindings:
        blockers.append({"code": "delivery_autopilot_runs_missing", "task_id": "", "detail": ""})
    return bindings, blockers


def _reason_codes(result: Mapping[str, Any]) -> List[str]:
    return [
        str(item.get("code") or "")[:120]
        for item in result.get("reasons") or []
        if isinstance(item, Mapping) and str(item.get("code") or "")
    ][:40]


def _evaluate_binding(user_id: int, binding: Mapping[str, str]) -> Dict[str, Any]:
    run_id = str(binding.get("run_id") or "")
    project_id = str(binding.get("project_id") or "")
    try:
        run = autopilot.get_run(int(user_id), run_id)
        if str(run.get("project_id") or "") != project_id:
            raise SoftwareFactoryError("velia_factory_delivery_run_project_mismatch", detail=run_id, status=409)
        project = project_service.get_project(int(user_id), project_id)
        policy = merge_policy.evaluate_merge_policy(int(user_id), run_id)
        gates = policy.get("gates") if isinstance(policy.get("gates"), Mapping) else {}
        pull = gates.get("pull_request") if isinstance(gates.get("pull_request"), Mapping) else {}
        attempt = gates.get("ci_attempt") if isinstance(gates.get("ci_attempt"), Mapping) else {}
        head_sha = str(pull.get("head_sha") or gates.get("branch_head") or "")[:80]
        eligible = bool(policy.get("would_allow_merge")) and str(policy.get("recommendation") or "") == "eligible"
        return {
            "project_id": project_id,
            "repository_full_name": str(project.get("repository_full_name") or ""),
            "run_id": run_id,
            "pull_request_number": int(pull.get("number") or run.get("pull_request_number") or 0),
            "pull_request_url": str(run.get("pull_request_url") or ""),
            "head_sha": head_sha,
            "ci_attempt": int(attempt.get("attempt_number") or 0),
            "ci_status": str(attempt.get("status") or ""),
            "policy_recommendation": str(policy.get("recommendation") or "not_ready"),
            "eligible": eligible,
            "reason_codes": _reason_codes(policy),
            "policy_mode": str(policy.get("mode") or "dry_run"),
            "merge_execution_supported": bool(policy.get("execution_supported")),
        }
    except Exception as exc:
        return {
            "project_id": project_id,
            "repository_full_name": "",
            "run_id": run_id,
            "pull_request_number": 0,
            "pull_request_url": "",
            "head_sha": "",
            "ci_attempt": 0,
            "ci_status": "",
            "policy_recommendation": "not_ready",
            "eligible": False,
            "reason_codes": [str(getattr(exc, "code", exc.__class__.__name__))[:120]],
            "policy_mode": "dry_run",
            "merge_execution_supported": False,
        }


def build_workspace_candidate_snapshot(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    execution = execution_module.get_execution(int(user_id), str(execution_id))
    if str(execution.get("status") or "") != "review_ready":
        raise SoftwareFactoryError(
            "velia_factory_delivery_execution_not_review_ready",
            detail=str(execution.get("status") or ""),
            status=409,
        )
    integration = _integration_evidence(execution)
    bindings, blockers = _run_bindings(execution)
    items = [_evaluate_binding(int(user_id), binding) for binding in bindings]
    for item in items:
        for code in item.get("reason_codes") or []:
            blockers.append(
                {
                    "code": str(code)[:120],
                    "task_id": "",
                    "detail": str(item.get("repository_full_name") or item.get("project_id") or "")[:300],
                }
            )
    all_eligible = bool(items) and not blockers and all(bool(item.get("eligible")) for item in items)
    snapshot = {
        "source_type": _SOURCE_WORKSPACE,
        "source_id": str(execution_id),
        "workspace_id": str(execution.get("workspace_id") or ""),
        "plan_fingerprint": str(execution.get("plan_fingerprint") or ""),
        "integration_validation": integration,
        "repositories": items,
        "blockers": blockers[:100],
        "status": "eligible" if all_eligible else "blocked",
        "release_eligible": all_eligible,
        "approval_required": True,
        "merge_supported": False,
        "deployment_supported": False,
        "evaluated_at": _utcnow().isoformat() + "Z",
    }
    fingerprint_input = {
        "source_id": snapshot["source_id"],
        "plan_fingerprint": snapshot["plan_fingerprint"],
        "integration_validation": integration,
        "repositories": [
            {
                "project_id": item.get("project_id"),
                "run_id": item.get("run_id"),
                "pr": item.get("pull_request_number"),
                "head_sha": item.get("head_sha"),
                "ci_attempt": item.get("ci_attempt"),
                "ci_status": item.get("ci_status"),
                "eligible": item.get("eligible"),
                "reason_codes": item.get("reason_codes"),
            }
            for item in items
        ],
        "blockers": blockers,
    }
    snapshot["source_fingerprint"] = _fingerprint(fingerprint_input)
    return snapshot


def _candidate_row(row: Any) -> Dict[str, Any]:
    snapshot = _loads(_value(row, "snapshot_json", 5, "{}"), {})
    result = dict(snapshot) if isinstance(snapshot, dict) else {}
    result.update(
        {
            "candidate_id": str(_value(row, "candidate_id", 0, "")),
            "user_id": int(_value(row, "user_id", 1, 0) or 0),
            "source_type": str(_value(row, "source_type", 2, "")),
            "source_id": str(_value(row, "source_id", 3, "")),
            "source_fingerprint": str(_value(row, "source_fingerprint", 4, "")),
            "status": str(_value(row, "status", 6, result.get("status") or "blocked")),
            "created_at": str(_value(row, "created_at", 7, "") or ""),
        }
    )
    return result


def persist_candidate(execution_module: Any, user_id: int, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    ensure_delivery_tables(execution_module)
    candidate_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_delivery_candidates (
                candidate_id,user_id,source_type,source_id,source_fingerprint,status,snapshot_json,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,source_type,source_id,source_fingerprint) DO UPDATE SET
                snapshot_json=EXCLUDED.snapshot_json
            RETURNING candidate_id,user_id,source_type,source_id,source_fingerprint,snapshot_json,status,created_at
            """,
            (
                candidate_id,
                int(user_id),
                str(snapshot.get("source_type") or _SOURCE_WORKSPACE),
                str(snapshot.get("source_id") or ""),
                str(snapshot.get("source_fingerprint") or ""),
                str(snapshot.get("status") or "blocked"),
                _json(dict(snapshot)),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _candidate_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def evaluate_workspace_candidate(execution_module: Any, user_id: int, execution_id: str, *, persist: bool = True) -> Dict[str, Any]:
    snapshot = build_workspace_candidate_snapshot(execution_module, int(user_id), str(execution_id))
    if not persist:
        return snapshot
    return persist_candidate(execution_module, int(user_id), snapshot)


def get_candidate(execution_module: Any, user_id: int, candidate_id: str) -> Dict[str, Any]:
    ensure_delivery_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT candidate_id,user_id,source_type,source_id,source_fingerprint,snapshot_json,status,created_at "
            "FROM velia_software_factory_delivery_candidates WHERE candidate_id=%s AND user_id=%s",
            (str(candidate_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_delivery_candidate_not_found", status=404)
        return _candidate_row(row)
    finally:
        cursor.close()
        conn.close()


def list_candidates(execution_module: Any, user_id: int, source_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    ensure_delivery_tables(execution_module)
    safe_limit = min(50, max(1, int(limit or 20)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT candidate_id FROM velia_software_factory_delivery_candidates "
            "WHERE user_id=%s AND source_type=%s AND source_id=%s ORDER BY created_at DESC LIMIT %s",
            (int(user_id), _SOURCE_WORKSPACE, str(source_id), safe_limit),
        )
        ids = [str(row[0]) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()
    return [get_candidate(execution_module, int(user_id), candidate_id) for candidate_id in ids]
