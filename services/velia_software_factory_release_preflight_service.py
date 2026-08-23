from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from db.database import get_connection
from services import velia_software_factory_delivery_approval_service as approval
from services import velia_software_factory_delivery_gate_service as delivery
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_655
_PREPARED = "prepared"
_TERMINAL = {"stale", "cancelled"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def preflight_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED", False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": preflight_enabled(),
        "mode": "preflight_only",
        "current_approval_required": True,
        "exact_candidate_fingerprint_required": True,
        "exact_head_sha_required": True,
        "deterministic_repository_order": True,
        "cross_repository_atomic_merge": False,
        "partial_merge_recovery_required": True,
        "execution_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
    }


def _utcnow():
    return delivery._utcnow()


def _json(value: Any, limit: int = 120000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


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


def _lock_key(candidate_id: str) -> int:
    raw = hashlib.sha256(str(candidate_id).encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=False)
    return value - (1 << 64) if value >= (1 << 63) else value


def ensure_preflight_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    approval.ensure_approval_tables(execution_module)
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_preflight_plans (
                    plan_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES velia_software_factory_delivery_candidates(candidate_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    approval_sequence_id BIGINT NOT NULL,
                    plan_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'prepared',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('prepared','stale','cancelled'))
                )
                """
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_factory_release_preflight_fingerprint "
                "ON velia_software_factory_release_preflight_plans(user_id,candidate_id,plan_fingerprint)"
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_factory_release_preflight_active "
                "ON velia_software_factory_release_preflight_plans(user_id,candidate_id) WHERE status='prepared'"
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
    if not preflight_enabled():
        raise SoftwareFactoryError("velia_factory_release_preflight_disabled", status=503)
    if not delivery.delivery_gate_enabled():
        raise SoftwareFactoryError("velia_factory_delivery_gate_disabled", status=503)
    if not approval.approval_enabled():
        raise SoftwareFactoryError("velia_factory_delivery_approval_disabled", status=503)
    if not rollout.intake_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)


def _plan_row(row: Any) -> Dict[str, Any]:
    plan = _loads(_value(row, "plan_json", 8, "{}"), {})
    result = dict(plan) if isinstance(plan, dict) else {}
    result.update(
        {
            "plan_id": str(_value(row, "plan_id", 0, "")),
            "candidate_id": str(_value(row, "candidate_id", 1, "")),
            "user_id": int(_value(row, "user_id", 2, 0) or 0),
            "source_id": str(_value(row, "source_id", 3, "")),
            "source_fingerprint": str(_value(row, "source_fingerprint", 4, "")),
            "approval_sequence_id": int(_value(row, "approval_sequence_id", 5, 0) or 0),
            "plan_fingerprint": str(_value(row, "plan_fingerprint", 6, "")),
            "status": str(_value(row, "status", 7, result.get("status") or "prepared")),
            "created_at": str(_value(row, "created_at", 9, "") or ""),
            "updated_at": str(_value(row, "updated_at", 10, "") or ""),
        }
    )
    result["execution_supported"] = False
    result["merge_supported"] = False
    result["deployment_supported"] = False
    return result


def get_plan(execution_module: Any, user_id: int, plan_id: str) -> Dict[str, Any]:
    ensure_preflight_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT plan_id,candidate_id,user_id,source_id,source_fingerprint,approval_sequence_id,"
            "plan_fingerprint,status,plan_json,created_at,updated_at "
            "FROM velia_software_factory_release_preflight_plans WHERE plan_id=%s AND user_id=%s",
            (str(plan_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_release_preflight_not_found", status=404)
        return _plan_row(row)
    finally:
        cursor.close()
        conn.close()


def list_plans(execution_module: Any, user_id: int, candidate_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    ensure_preflight_tables(execution_module)
    delivery.get_candidate(execution_module, int(user_id), str(candidate_id))
    safe_limit = min(50, max(1, int(limit or 20)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT plan_id FROM velia_software_factory_release_preflight_plans "
            "WHERE user_id=%s AND candidate_id=%s ORDER BY created_at DESC LIMIT %s",
            (int(user_id), str(candidate_id), safe_limit),
        )
        ids = [str(row[0]) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()
    return [get_plan(execution_module, int(user_id), item) for item in ids]


def _project_dependencies(execution: Mapping[str, Any]) -> Tuple[Dict[str, set], Dict[str, set]]:
    tasks = [dict(item) for item in (execution.get("plan") or {}).get("tasks") or [] if isinstance(item, Mapping)]
    by_id = {str(item.get("id") or ""): item for item in tasks if str(item.get("id") or "")}
    outgoing: Dict[str, set] = {}
    incoming: Dict[str, set] = {}
    for task in tasks:
        consumer = str(task.get("project_id") or "")
        if not consumer:
            continue
        outgoing.setdefault(consumer, set())
        incoming.setdefault(consumer, set())
        for dep_id in task.get("depends_on") or []:
            provider = str((by_id.get(str(dep_id)) or {}).get("project_id") or "")
            if not provider or provider == consumer:
                continue
            outgoing.setdefault(provider, set()).add(consumer)
            incoming.setdefault(consumer, set()).add(provider)
            outgoing.setdefault(consumer, set())
            incoming.setdefault(provider, set())
    return outgoing, incoming


def _repository_order(execution: Mapping[str, Any], repositories: Sequence[Mapping[str, Any]]) -> List[str]:
    repo_projects = {str(item.get("project_id") or "") for item in repositories if str(item.get("project_id") or "")}
    if not repo_projects:
        raise SoftwareFactoryError("velia_factory_release_repositories_missing", status=409)
    outgoing, incoming = _project_dependencies(execution)
    for project_id in repo_projects:
        outgoing.setdefault(project_id, set())
        incoming.setdefault(project_id, set())
    # Restrict graph to repositories participating in this exact candidate.
    scoped_out = {project: {item for item in outgoing.get(project, set()) if item in repo_projects} for project in repo_projects}
    scoped_in = {project: {item for item in incoming.get(project, set()) if item in repo_projects} for project in repo_projects}
    ready = sorted(project for project in repo_projects if not scoped_in[project])
    ordered: List[str] = []
    while ready:
        project = ready.pop(0)
        ordered.append(project)
        for consumer in sorted(scoped_out[project]):
            scoped_in[consumer].discard(project)
            if not scoped_in[consumer] and consumer not in ordered and consumer not in ready:
                ready.append(consumer)
                ready.sort()
    if len(ordered) != len(repo_projects):
        raise SoftwareFactoryError("velia_factory_release_repository_dependency_cycle", status=409)
    return ordered


def _build_plan_snapshot(
    execution_module: Any,
    user_id: int,
    candidate: Mapping[str, Any],
    approval_evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    if str(candidate.get("status") or "") != "eligible" or not bool(candidate.get("release_eligible")):
        raise SoftwareFactoryError("velia_factory_delivery_candidate_not_eligible", status=409)
    source_id = str(candidate.get("source_id") or "")
    execution = execution_module.get_execution(int(user_id), source_id)
    if str(execution.get("status") or "") != "review_ready":
        raise SoftwareFactoryError("velia_factory_delivery_execution_not_review_ready", status=409)
    repositories = [dict(item) for item in candidate.get("repositories") or [] if isinstance(item, Mapping)]
    order = _repository_order(execution, repositories)
    by_project = {str(item.get("project_id") or ""): item for item in repositories}
    ordered_items: List[Dict[str, Any]] = []
    seen_targets = set()
    for index, project_id in enumerate(order, start=1):
        item = by_project.get(project_id) or {}
        pr = int(item.get("pull_request_number") or 0)
        head_sha = str(item.get("head_sha") or "")
        repository = str(item.get("repository_full_name") or "")
        run_id = str(item.get("run_id") or "")
        if pr <= 0 or len(head_sha) < 7 or not repository or not run_id or not bool(item.get("eligible")):
            raise SoftwareFactoryError("velia_factory_release_repository_evidence_incomplete", detail=project_id, status=409)
        target = (repository.casefold(), pr)
        if target in seen_targets:
            raise SoftwareFactoryError("velia_factory_release_duplicate_pull_request", detail=f"{repository}#{pr}", status=409)
        seen_targets.add(target)
        ordered_items.append(
            {
                "order": index,
                "project_id": project_id,
                "repository_full_name": repository,
                "run_id": run_id,
                "pull_request_number": pr,
                "head_sha": head_sha,
                "ci_attempt": int(item.get("ci_attempt") or 0),
                "ci_status": str(item.get("ci_status") or ""),
                "policy_recommendation": str(item.get("policy_recommendation") or ""),
            }
        )
    approval_sequence_id = int((approval_evidence.get("approval") or {}).get("sequence_id") or 0)
    if approval_sequence_id <= 0:
        raise SoftwareFactoryError("velia_factory_delivery_approval_evidence_missing", status=409)
    snapshot = {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "source_id": source_id,
        "source_fingerprint": str(candidate.get("source_fingerprint") or ""),
        "approval_sequence_id": approval_sequence_id,
        "repositories": ordered_items,
        "repository_count": len(ordered_items),
        "merge_order_policy": "workspace_dependency_topological_then_project_id",
        "cross_repository_atomic_merge": False,
        "partial_merge_recovery_required": len(ordered_items) > 1,
        "recovery_policy": "stop_after_first_failure_and_require_explicit_recovery_plan",
        "status": _PREPARED,
        "execution_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
        "prepared_at": _utcnow().isoformat() + "Z",
    }
    snapshot["plan_fingerprint"] = _fingerprint(
        {
            "candidate_id": snapshot["candidate_id"],
            "source_fingerprint": snapshot["source_fingerprint"],
            "approval_sequence_id": approval_sequence_id,
            "repositories": ordered_items,
            "recovery_policy": snapshot["recovery_policy"],
        }
    )
    return snapshot


def _fresh_snapshot(execution_module: Any, user_id: int, candidate_id: str) -> Dict[str, Any]:
    approval_evidence = approval.require_current_approval(execution_module, int(user_id), str(candidate_id))
    candidate = delivery.get_candidate(execution_module, int(user_id), str(candidate_id))
    fresh = delivery.evaluate_workspace_candidate(
        execution_module,
        int(user_id),
        str(candidate.get("source_id") or ""),
        persist=False,
    )
    if str(fresh.get("source_fingerprint") or "") != str(candidate.get("source_fingerprint") or ""):
        raise SoftwareFactoryError("velia_factory_delivery_candidate_stale", status=409)
    fresh_candidate = {**dict(fresh), "candidate_id": str(candidate_id)}
    return _build_plan_snapshot(execution_module, int(user_id), fresh_candidate, approval_evidence)


def _mark_status(plan_id: str, user_id: int, status: str) -> None:
    if status not in _TERMINAL:
        raise SoftwareFactoryError("velia_factory_release_preflight_status_invalid")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_release_preflight_plans SET status=%s,updated_at=%s "
            "WHERE plan_id=%s AND user_id=%s AND status='prepared'",
            (status, _utcnow(), str(plan_id), int(user_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def validate_plan(execution_module: Any, user_id: int, plan_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    current = get_plan(execution_module, int(user_id), str(plan_id))
    if str(current.get("status") or "") != _PREPARED:
        raise SoftwareFactoryError("velia_factory_release_preflight_not_prepared", detail=str(current.get("status") or ""), status=409)
    try:
        fresh = _fresh_snapshot(execution_module, int(user_id), str(current.get("candidate_id") or ""))
    except Exception:
        _mark_status(str(plan_id), int(user_id), "stale")
        raise
    if str(fresh.get("plan_fingerprint") or "") != str(current.get("plan_fingerprint") or ""):
        _mark_status(str(plan_id), int(user_id), "stale")
        raise SoftwareFactoryError("velia_factory_release_preflight_stale", status=409)
    return {
        "ok": True,
        "plan_id": str(plan_id),
        "candidate_id": str(current.get("candidate_id") or ""),
        "plan_fingerprint": str(current.get("plan_fingerprint") or ""),
        "current": True,
        "repository_count": int(current.get("repository_count") or 0),
        "repositories": list(current.get("repositories") or []),
        "execution_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
    }


def prepare_plan(execution_module: Any, user_id: int, candidate_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    ensure_preflight_tables(execution_module)
    snapshot = _fresh_snapshot(execution_module, int(user_id), str(candidate_id))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_key(str(candidate_id)),))
        cursor.execute(
            "SELECT plan_id FROM velia_software_factory_release_preflight_plans "
            "WHERE user_id=%s AND candidate_id=%s AND status='prepared' LIMIT 1",
            (int(user_id), str(candidate_id)),
        )
        active = cursor.fetchone()
        if active:
            active_id = str(_value(active, "plan_id", 0, ""))
            conn.commit()
            existing = get_plan(execution_module, int(user_id), active_id)
            if str(existing.get("plan_fingerprint") or "") == str(snapshot.get("plan_fingerprint") or ""):
                return existing
            _mark_status(active_id, int(user_id), "stale")
            conn = get_connection()
            cursor = _dict_cursor(conn)
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_key(str(candidate_id)),))
        plan_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_release_preflight_plans (
                plan_id,candidate_id,user_id,source_id,source_fingerprint,approval_sequence_id,
                plan_fingerprint,status,plan_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'prepared',%s,%s,%s)
            ON CONFLICT (user_id,candidate_id,plan_fingerprint) DO UPDATE SET
                updated_at=velia_software_factory_release_preflight_plans.updated_at
            RETURNING plan_id,candidate_id,user_id,source_id,source_fingerprint,approval_sequence_id,
                plan_fingerprint,status,plan_json,created_at,updated_at
            """,
            (
                plan_id,
                str(candidate_id),
                int(user_id),
                str(snapshot.get("source_id") or ""),
                str(snapshot.get("source_fingerprint") or ""),
                int(snapshot.get("approval_sequence_id") or 0),
                str(snapshot.get("plan_fingerprint") or ""),
                _json(snapshot),
                _utcnow(),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _plan_row(row)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


def cancel_plan(execution_module: Any, user_id: int, plan_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    current = get_plan(execution_module, int(user_id), str(plan_id))
    if str(current.get("status") or "") != _PREPARED:
        return current
    _mark_status(str(plan_id), int(user_id), "cancelled")
    return get_plan(execution_module, int(user_id), str(plan_id))
