from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from typing import Any, Dict, List, Mapping

from db.database import get_connection
from services import velia_agent_coding_autopilot_merge_policy_service as merge_policy
from services import velia_developer_github_write_service as write_service
from services import velia_developer_project_service as project_service
from services import velia_software_factory_delivery_approval_service as approval
from services import velia_software_factory_release_merge_github_service as release_github
from services import velia_software_factory_release_preflight_service as preflight
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_677
_TERMINAL = {"completed", "blocked", "partial_release", "cancelled", "failed"}
_ACTIVE = {"created", "running"}
_ITEM_TERMINAL = {"merged", "failed", "skipped"}
_CONFIRMED_RECONCILE_FAILURES = {
    "velia_factory_release_head_sha_stale",
    "velia_factory_release_pr_closed_without_merge",
    "velia_factory_release_repository_identity_changed",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def execution_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED", False)


def merge_method() -> str:
    value = str(os.getenv("VELIA_SOFTWARE_FACTORY_RELEASE_MERGE_METHOD", "merge") or "merge").strip().lower()
    return value if value in {"merge", "squash", "rebase"} else "merge"


def public_status() -> Dict[str, Any]:
    enabled = execution_enabled()
    return {
        "available": True,
        "enabled": enabled,
        "mode": "controlled_merge",
        "current_preflight_required_before_start": True,
        "approval_event_must_remain_active": True,
        "per_pr_exact_head_revalidation": True,
        "per_pr_merge_policy_revalidation": True,
        "uncertain_merge_reconciliation": True,
        "stop_after_first_failure": True,
        "cross_repository_atomic_merge": False,
        "partial_release_state": True,
        "rollback_supported": False,
        "execution_supported": enabled,
        "merge_supported": enabled,
        "deployment_supported": False,
    }


def _utcnow():
    return preflight._utcnow()


def _json(value: Any, limit: int = 160000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _dict_cursor(conn):
    try:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        return conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _lock_key(value: str) -> int:
    raw = hashlib.sha256(str(value).encode("utf-8")).digest()[:8]
    number = int.from_bytes(raw, "big", signed=False)
    return number - (1 << 64) if number >= (1 << 63) else number


def ensure_execution_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    preflight.ensure_preflight_tables(execution_module)
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_executions (
                    execution_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL REFERENCES velia_software_factory_release_preflight_plans(plan_id) ON DELETE RESTRICT,
                    candidate_id TEXT NOT NULL,
                    user_id BIGINT NOT NULL,
                    plan_fingerprint TEXT NOT NULL,
                    approval_sequence_id BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created',
                    merged_count INTEGER NOT NULL DEFAULT 0,
                    stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    blocker_code TEXT NOT NULL DEFAULT '',
                    blocker_detail TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('created','running','completed','blocked','partial_release','cancelled','failed')),
                    UNIQUE(user_id,plan_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_execution_items (
                    execution_id TEXT NOT NULL REFERENCES velia_software_factory_release_executions(execution_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    repository_full_name TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    pull_request_number INTEGER NOT NULL,
                    expected_head_sha TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    merge_commit_sha TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_detail TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(execution_id,position),
                    UNIQUE(execution_id,project_id),
                    CHECK (status IN ('pending','merging','merged','failed','skipped'))
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_execution_events (
                    sequence_id BIGSERIAL PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES velia_software_factory_release_executions(execution_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
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


def _require_user(user_id: int) -> None:
    if not execution_enabled():
        raise SoftwareFactoryError("velia_factory_release_execution_disabled", status=503)
    if not preflight.preflight_enabled():
        raise SoftwareFactoryError("velia_factory_release_preflight_disabled", status=503)
    if not approval.approval_enabled():
        raise SoftwareFactoryError("velia_factory_delivery_approval_disabled", status=503)
    if not merge_policy.merge_policy_enabled():
        raise SoftwareFactoryError("velia_factory_release_merge_policy_disabled", status=503)
    if not write_service.write_enabled():
        raise SoftwareFactoryError("velia_factory_release_github_write_disabled", status=503)
    if not rollout.live_execution_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_release_live_rollout_required", status=403)


def _event(execution_id: str, user_id: int, kind: str, payload: Mapping[str, Any] | None = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO velia_software_factory_release_execution_events "
            "(execution_id,user_id,kind,payload_json,created_at) VALUES (%s,%s,%s,%s,%s)",
            (str(execution_id), int(user_id), str(kind)[:120], _json(dict(payload or {})), _utcnow()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _execution_row(row: Any) -> Dict[str, Any]:
    result = _loads(_value(row, "result_json", 11, "{}"), {})
    if not isinstance(result, dict):
        result = {}
    result.update(
        {
            "execution_id": str(_value(row, "execution_id", 0, "")),
            "plan_id": str(_value(row, "plan_id", 1, "")),
            "candidate_id": str(_value(row, "candidate_id", 2, "")),
            "user_id": int(_value(row, "user_id", 3, 0) or 0),
            "plan_fingerprint": str(_value(row, "plan_fingerprint", 4, "")),
            "approval_sequence_id": int(_value(row, "approval_sequence_id", 5, 0) or 0),
            "status": str(_value(row, "status", 6, "")),
            "merged_count": int(_value(row, "merged_count", 7, 0) or 0),
            "stop_requested": bool(_value(row, "stop_requested", 8, False)),
            "blocker_code": str(_value(row, "blocker_code", 9, "") or ""),
            "blocker_detail": str(_value(row, "blocker_detail", 10, "") or ""),
            "created_at": str(_value(row, "created_at", 12, "") or ""),
            "updated_at": str(_value(row, "updated_at", 13, "") or ""),
        }
    )
    result["deployment_supported"] = False
    return result


def _item_row(row: Any) -> Dict[str, Any]:
    return {
        "position": int(_value(row, "position", 0, 0) or 0),
        "project_id": str(_value(row, "project_id", 1, "")),
        "repository_full_name": str(_value(row, "repository_full_name", 2, "")),
        "run_id": str(_value(row, "run_id", 3, "")),
        "pull_request_number": int(_value(row, "pull_request_number", 4, 0) or 0),
        "expected_head_sha": str(_value(row, "expected_head_sha", 5, "")),
        "status": str(_value(row, "status", 6, "")),
        "merge_commit_sha": str(_value(row, "merge_commit_sha", 7, "") or ""),
        "error_code": str(_value(row, "error_code", 8, "") or ""),
        "error_detail": str(_value(row, "error_detail", 9, "") or ""),
    }


def _items(execution_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT position,project_id,repository_full_name,run_id,pull_request_number,expected_head_sha,"
            "status,merge_commit_sha,error_code,error_detail FROM velia_software_factory_release_execution_items "
            "WHERE execution_id=%s ORDER BY position ASC",
            (str(execution_id),),
        )
        return [_item_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def get_execution(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
    ensure_execution_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT execution_id,plan_id,candidate_id,user_id,plan_fingerprint,approval_sequence_id,status,"
            "merged_count,stop_requested,blocker_code,blocker_detail,result_json,created_at,updated_at "
            "FROM velia_software_factory_release_executions WHERE execution_id=%s AND user_id=%s",
            (str(execution_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_release_execution_not_found", status=404)
        result = _execution_row(row)
    finally:
        cursor.close()
        conn.close()
    result["items"] = _items(str(execution_id))
    result["merge_supported"] = execution_enabled()
    return result


def _set_execution(
    execution_id: str,
    user_id: int,
    *,
    status: str | None = None,
    merged_count: int | None = None,
    blocker_code: str | None = None,
    blocker_detail: str | None = None,
    result: Mapping[str, Any] | None = None,
) -> None:
    fields = ["updated_at=%s"]
    values: List[Any] = [_utcnow()]
    if status is not None:
        fields.append("status=%s")
        values.append(str(status))
    if merged_count is not None:
        fields.append("merged_count=%s")
        values.append(int(merged_count))
    if blocker_code is not None:
        fields.append("blocker_code=%s")
        values.append(str(blocker_code)[:160])
    if blocker_detail is not None:
        fields.append("blocker_detail=%s")
        values.append(str(blocker_detail or "")[:1000])
    if result is not None:
        fields.append("result_json=%s")
        values.append(_json(dict(result)))
    values.extend([str(execution_id), int(user_id)])
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE velia_software_factory_release_executions SET {','.join(fields)} WHERE execution_id=%s AND user_id=%s",
            tuple(values),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _set_item(
    execution_id: str,
    position: int,
    *,
    status: str,
    merge_commit_sha: str = "",
    error_code: str = "",
    error_detail: str = "",
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_release_execution_items SET status=%s,merge_commit_sha=%s,"
            "error_code=%s,error_detail=%s,updated_at=%s WHERE execution_id=%s AND position=%s",
            (
                str(status),
                str(merge_commit_sha or "")[:80],
                str(error_code or "")[:160],
                str(error_detail or "")[:1000],
                _utcnow(),
                str(execution_id),
                int(position),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_execution(execution_module: Any, user_id: int, plan_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    ensure_execution_tables(execution_module)
    validated = preflight.validate_plan(execution_module, int(user_id), str(plan_id))
    plan = preflight.get_plan(execution_module, int(user_id), str(plan_id))
    if str(plan.get("status") or "") != "prepared":
        raise SoftwareFactoryError("velia_factory_release_preflight_not_prepared", status=409)
    if str(validated.get("plan_fingerprint") or "") != str(plan.get("plan_fingerprint") or ""):
        raise SoftwareFactoryError("velia_factory_release_preflight_stale", status=409)
    repositories = [dict(item) for item in plan.get("repositories") or [] if isinstance(item, Mapping)]
    if not repositories:
        raise SoftwareFactoryError("velia_factory_release_repositories_missing", status=409)

    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_key(str(plan_id)),))
        cursor.execute(
            "SELECT execution_id FROM velia_software_factory_release_executions WHERE user_id=%s AND plan_id=%s",
            (int(user_id), str(plan_id)),
        )
        existing = cursor.fetchone()
        if existing:
            existing_id = str(_value(existing, "execution_id", 0, ""))
            conn.commit()
            cursor.close()
            conn.close()
            return get_execution(execution_module, int(user_id), existing_id)
        execution_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_release_executions (
                execution_id,plan_id,candidate_id,user_id,plan_fingerprint,approval_sequence_id,status,
                merged_count,stop_requested,blocker_code,blocker_detail,result_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,'created',0,FALSE,'','','{}',%s,%s)
            """,
            (
                execution_id,
                str(plan_id),
                str(plan.get("candidate_id") or ""),
                int(user_id),
                str(plan.get("plan_fingerprint") or ""),
                int(plan.get("approval_sequence_id") or 0),
                _utcnow(),
                _utcnow(),
            ),
        )
        for item in repositories:
            cursor.execute(
                """
                INSERT INTO velia_software_factory_release_execution_items (
                    execution_id,position,project_id,repository_full_name,run_id,pull_request_number,
                    expected_head_sha,status,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s)
                """,
                (
                    execution_id,
                    int(item.get("order") or 0),
                    str(item.get("project_id") or ""),
                    str(item.get("repository_full_name") or ""),
                    str(item.get("run_id") or ""),
                    int(item.get("pull_request_number") or 0),
                    str(item.get("head_sha") or ""),
                    _utcnow(),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if not cursor.closed:
            cursor.close()
        if not conn.closed:
            conn.close()
    _event(execution_id, int(user_id), "release_execution.created", {"plan_id": str(plan_id)})
    return get_execution(execution_module, int(user_id), execution_id)


def _approval_still_active(execution_module: Any, execution: Mapping[str, Any]) -> None:
    current = approval.latest_decision(
        execution_module,
        int(execution.get("user_id") or 0),
        str(execution.get("candidate_id") or ""),
    )
    if str(current.get("decision") or "") != "approved":
        raise SoftwareFactoryError("velia_factory_release_approval_no_longer_active", status=409)
    if int(current.get("sequence_id") or 0) != int(execution.get("approval_sequence_id") or 0):
        raise SoftwareFactoryError("velia_factory_release_approval_event_changed", status=409)


def _validate_item_policy(user_id: int, item: Mapping[str, Any]) -> Dict[str, Any]:
    policy = merge_policy.evaluate_merge_policy(int(user_id), str(item.get("run_id") or ""))
    gates = policy.get("gates") if isinstance(policy.get("gates"), Mapping) else {}
    pull = gates.get("pull_request") if isinstance(gates.get("pull_request"), Mapping) else {}
    attempt = gates.get("ci_attempt") if isinstance(gates.get("ci_attempt"), Mapping) else {}
    if not bool(policy.get("would_allow_merge")) or str(policy.get("recommendation") or "") != "eligible":
        raise SoftwareFactoryError("velia_factory_release_merge_policy_not_eligible", status=409)
    if int(pull.get("number") or 0) != int(item.get("pull_request_number") or 0):
        raise SoftwareFactoryError("velia_factory_release_pr_changed", status=409)
    expected = str(item.get("expected_head_sha") or "").lower()
    if str(pull.get("head_sha") or "").lower() != expected:
        raise SoftwareFactoryError("velia_factory_release_head_sha_stale", status=409)
    if str(attempt.get("status") or "").lower() != "success" or str(attempt.get("head_sha") or "").lower() != expected:
        raise SoftwareFactoryError("velia_factory_release_exact_head_ci_stale", status=409)
    return policy


def _project_for_item(user_id: int, item: Mapping[str, Any]) -> Dict[str, Any]:
    project = project_service.get_project(int(user_id), str(item.get("project_id") or ""))
    actual = str(project.get("repository_full_name") or "").strip().casefold()
    expected = str(item.get("repository_full_name") or "").strip().casefold()
    if not actual or actual != expected:
        raise SoftwareFactoryError("velia_factory_release_repository_identity_changed", status=409)
    return project


def _reconcile_merging_item(user_id: int, execution_id: str, item: Mapping[str, Any]) -> bool:
    project = _project_for_item(int(user_id), item)
    state = release_github.pull_state(project, int(item.get("pull_request_number") or 0))
    expected = str(item.get("expected_head_sha") or "").lower()
    if str(state.get("head_sha") or "").lower() != expected:
        raise SoftwareFactoryError("velia_factory_release_head_sha_stale", status=409)
    if state.get("merged") is True:
        _set_item(
            str(execution_id),
            int(item.get("position") or 0),
            status="merged",
            merge_commit_sha=str(state.get("merge_commit_sha") or ""),
        )
        return True
    if str(state.get("state") or "") == "open":
        _set_item(str(execution_id), int(item.get("position") or 0), status="pending")
        return False
    raise SoftwareFactoryError("velia_factory_release_pr_closed_without_merge", status=409)


def _terminal_after_stop(merged_count: int) -> str:
    return "partial_release" if int(merged_count) > 0 else "cancelled"


def _record_uncertain(
    execution_id: str,
    user_id: int,
    merged_count: int,
    detail: str,
) -> Dict[str, Any]:
    _set_execution(
        str(execution_id),
        int(user_id),
        status="running",
        merged_count=int(merged_count),
        blocker_code="velia_factory_release_merge_outcome_uncertain",
        blocker_detail=str(detail or "")[:1000],
        result={
            "reconciliation_required": True,
            "safe_to_retry_execution": True,
            "deployment_started": False,
        },
    )
    _event(
        str(execution_id),
        int(user_id),
        "release_item.merge_outcome_uncertain",
        {"merged_count": int(merged_count), "detail": str(detail or "")[:500]},
    )
    return get_execution(None, int(user_id), str(execution_id))


def execute_release(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    ensure_execution_tables(execution_module)
    lock_conn = get_connection()
    lock_cursor = lock_conn.cursor()
    locked = False
    try:
        lock_cursor.execute("SELECT pg_try_advisory_lock(%s)", (_lock_key(str(execution_id)),))
        row = lock_cursor.fetchone()
        locked = bool(row and row[0])
        if not locked:
            raise SoftwareFactoryError("velia_factory_release_execution_busy", status=409)

        current = get_execution(execution_module, int(user_id), str(execution_id))
        if str(current.get("status") or "") in _TERMINAL:
            return current
        if str(current.get("status") or "") == "created":
            validated = preflight.validate_plan(execution_module, int(user_id), str(current.get("plan_id") or ""))
            if str(validated.get("plan_fingerprint") or "") != str(current.get("plan_fingerprint") or ""):
                raise SoftwareFactoryError("velia_factory_release_preflight_stale", status=409)
            _approval_still_active(execution_module, current)
            _set_execution(str(execution_id), int(user_id), status="running", blocker_code="", blocker_detail="")
            _event(str(execution_id), int(user_id), "release_execution.started", {"plan_id": current.get("plan_id")})

        items = _items(str(execution_id))
        merged_count = sum(1 for item in items if str(item.get("status") or "") == "merged")
        for item in items:
            status = str(item.get("status") or "")
            if status == "merged":
                continue
            if status == "merging":
                try:
                    if _reconcile_merging_item(int(user_id), str(execution_id), item):
                        merged_count += 1
                        _set_execution(str(execution_id), int(user_id), merged_count=merged_count, blocker_code="", blocker_detail="")
                        _event(str(execution_id), int(user_id), "release_item.reconciled_merged", item)
                        continue
                    status = "pending"
                except Exception as reconcile_exc:
                    code = str(getattr(reconcile_exc, "code", reconcile_exc.__class__.__name__))[:160]
                    if code not in _CONFIRMED_RECONCILE_FAILURES:
                        return _record_uncertain(
                            str(execution_id),
                            int(user_id),
                            merged_count,
                            str(getattr(reconcile_exc, "detail", str(reconcile_exc)) or ""),
                        )
                    raise
            if status in _ITEM_TERMINAL:
                break

            current = get_execution(execution_module, int(user_id), str(execution_id))
            if bool(current.get("stop_requested")):
                terminal = _terminal_after_stop(merged_count)
                _set_execution(str(execution_id), int(user_id), status=terminal, merged_count=merged_count)
                _event(str(execution_id), int(user_id), "release_execution.stopped", {"merged_count": merged_count})
                return get_execution(execution_module, int(user_id), str(execution_id))

            merge_started = False
            try:
                plan = preflight.get_plan(execution_module, int(user_id), str(current.get("plan_id") or ""))
                if str(plan.get("status") or "") != "prepared" or str(plan.get("plan_fingerprint") or "") != str(current.get("plan_fingerprint") or ""):
                    raise SoftwareFactoryError("velia_factory_release_preflight_no_longer_active", status=409)
                _approval_still_active(execution_module, current)
                _validate_item_policy(int(user_id), item)
                project = _project_for_item(int(user_id), item)
                _set_item(str(execution_id), int(item.get("position") or 0), status="merging")
                merge_started = True
                _event(str(execution_id), int(user_id), "release_item.merge_started", item)
                result = release_github.merge_exact_head(
                    project,
                    pull_number=int(item.get("pull_request_number") or 0),
                    expected_head_sha=str(item.get("expected_head_sha") or ""),
                    merge_method=merge_method(),
                )
                _set_item(
                    str(execution_id),
                    int(item.get("position") or 0),
                    status="merged",
                    merge_commit_sha=str(result.get("merge_commit_sha") or ""),
                )
                merged_count += 1
                _set_execution(str(execution_id), int(user_id), merged_count=merged_count, blocker_code="", blocker_detail="")
                _event(str(execution_id), int(user_id), "release_item.merged", result)
            except Exception as exc:
                if merge_started:
                    try:
                        if _reconcile_merging_item(int(user_id), str(execution_id), item):
                            merged_count += 1
                            _set_execution(str(execution_id), int(user_id), merged_count=merged_count, blocker_code="", blocker_detail="")
                            _event(
                                str(execution_id),
                                int(user_id),
                                "release_item.reconciled_after_error",
                                {"position": item.get("position"), "merged_count": merged_count},
                            )
                            continue
                    except Exception as reconcile_exc:
                        reconcile_code = str(getattr(reconcile_exc, "code", reconcile_exc.__class__.__name__))[:160]
                        if reconcile_code not in _CONFIRMED_RECONCILE_FAILURES:
                            return _record_uncertain(
                                str(execution_id),
                                int(user_id),
                                merged_count,
                                str(getattr(reconcile_exc, "detail", str(reconcile_exc)) or ""),
                            )
                code = str(getattr(exc, "code", exc.__class__.__name__))[:160]
                detail = str(getattr(exc, "detail", str(exc)) or "")[:1000]
                _set_item(
                    str(execution_id),
                    int(item.get("position") or 0),
                    status="failed",
                    error_code=code,
                    error_detail=detail,
                )
                terminal = "partial_release" if merged_count > 0 else "blocked"
                _set_execution(
                    str(execution_id),
                    int(user_id),
                    status=terminal,
                    merged_count=merged_count,
                    blocker_code=code,
                    blocker_detail=detail,
                    result={
                        "recovery_required": bool(merged_count > 0),
                        "recovery_policy": "stop_after_first_failure_and_require_explicit_recovery_plan",
                        "deployment_started": False,
                    },
                )
                _event(
                    str(execution_id),
                    int(user_id),
                    "release_execution.blocked" if not merged_count else "release_execution.partial_release",
                    {"code": code, "detail": detail, "merged_count": merged_count},
                )
                return get_execution(execution_module, int(user_id), str(execution_id))

        final_items = _items(str(execution_id))
        if final_items and all(str(item.get("status") or "") == "merged" for item in final_items):
            _set_execution(
                str(execution_id),
                int(user_id),
                status="completed",
                merged_count=len(final_items),
                blocker_code="",
                blocker_detail="",
                result={"release_completed": True, "deployment_started": False},
            )
            _event(str(execution_id), int(user_id), "release_execution.completed", {"merged_count": len(final_items)})
        return get_execution(execution_module, int(user_id), str(execution_id))
    finally:
        if locked:
            try:
                lock_cursor.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(str(execution_id)),))
                lock_conn.commit()
            except Exception:
                lock_conn.rollback()
        lock_cursor.close()
        lock_conn.close()


def request_stop(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
    ensure_execution_tables(execution_module)
    current = get_execution(execution_module, int(user_id), str(execution_id))
    if str(current.get("status") or "") in _TERMINAL:
        return current
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_release_executions SET stop_requested=TRUE,updated_at=%s "
            "WHERE execution_id=%s AND user_id=%s AND status IN ('created','running')",
            (_utcnow(), str(execution_id), int(user_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    _event(str(execution_id), int(user_id), "release_execution.stop_requested", {})
    return get_execution(execution_module, int(user_id), str(execution_id))
