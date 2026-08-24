from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Mapping

from db.database import get_connection
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_712
_ALLOWED_STATES = {"ready", "planning", "executing"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def live_pilot_guard_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", False)


def _utcnow() -> datetime:
    return datetime.utcnow()


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


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def _row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "grant_id": str(_value(row, "grant_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "run_id": str(_value(row, "run_id", 2, "")),
        "project_id": str(_value(row, "project_id", 3, "")),
        "repository_full_name": str(_value(row, "repository_full_name", 4, "")),
        "spec_fingerprint": str(_value(row, "spec_fingerprint", 5, "")),
        "status": str(_value(row, "status", 6, "")),
        "factory_task_id": str(_value(row, "factory_task_id", 7, "")),
        "client_request_id": str(_value(row, "client_request_id", 8, "")),
        "autopilot_task_id": str(_value(row, "autopilot_task_id", 9, "")),
        "approval_source": str(_value(row, "approval_source", 10, "")),
        "expires_at": _iso(_value(row, "expires_at", 11)),
        "claimed_at": _iso(_value(row, "claimed_at", 12)),
        "consumed_at": _iso(_value(row, "consumed_at", 13)),
        "created_at": _iso(_value(row, "created_at", 14)),
        "updated_at": _iso(_value(row, "updated_at", 15)),
        "max_dispatches": 1,
    }


_COLUMNS = (
    "grant_id,user_id,run_id,project_id,repository_full_name,spec_fingerprint,status,"
    "factory_task_id,client_request_id,autopilot_task_id,approval_source,expires_at,"
    "claimed_at,consumed_at,created_at,updated_at"
)


def ensure_live_pilot_guard_tables() -> None:
    global _SCHEMA_READY
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_live_pilot_grants (
                    grant_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    run_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    repository_full_name TEXT NOT NULL,
                    spec_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    factory_task_id TEXT NOT NULL DEFAULT '',
                    client_request_id TEXT NOT NULL DEFAULT '',
                    autopilot_task_id TEXT NOT NULL DEFAULT '',
                    approval_source TEXT NOT NULL DEFAULT 'explicit_admin',
                    expires_at TIMESTAMP NOT NULL,
                    claimed_at TIMESTAMP NULL,
                    consumed_at TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('pending','claimed','consumed','revoked','expired')),
                    UNIQUE(user_id, run_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_live_pilot_grants_status "
                "ON velia_software_factory_live_pilot_grants(status,expires_at)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": live_pilot_guard_enabled(),
        "mode": "one_shot_dispatch_grant",
        "max_dispatches_per_run": 1,
        "grant_required_before_autopilot_enqueue": True,
        "repository_write_supported_by_guard": False,
        "merge_supported_by_guard": False,
        "deployment_supported_by_guard": False,
    }


def _run_identity(run: Mapping[str, Any], project: Mapping[str, Any]) -> Dict[str, str]:
    repository = str(project.get("repository_full_name") or "").strip()
    project_id = str(run.get("project_id") or "").strip()
    run_id = str(run.get("run_id") or "").strip()
    fingerprint = str(run.get("spec_fingerprint") or "").strip()
    if not run_id or not project_id or not repository or not fingerprint:
        raise SoftwareFactoryError("velia_factory_live_pilot_identity_incomplete", status=409)
    if str(project.get("id") or project.get("project_id") or "").strip() not in {"", project_id}:
        raise SoftwareFactoryError("velia_factory_live_pilot_project_mismatch", status=409)
    return {
        "run_id": run_id,
        "project_id": project_id,
        "repository_full_name": repository,
        "spec_fingerprint": fingerprint,
    }


def _has_dispatched_work(run: Mapping[str, Any]) -> bool:
    for raw in run.get("dag") or []:
        if isinstance(raw, Mapping) and str(raw.get("external_ref") or "").strip():
            return True
    return False


def issue_grant(
    user_id: int,
    run: Mapping[str, Any],
    project: Mapping[str, Any],
    *,
    approval_source: str = "explicit_admin",
    ttl_seconds: int = 600,
) -> Dict[str, Any]:
    if not live_pilot_guard_enabled():
        raise SoftwareFactoryError("velia_factory_live_pilot_guard_disabled", status=503)
    state = str(run.get("state") or "")
    if state not in _ALLOWED_STATES:
        raise SoftwareFactoryError("velia_factory_live_pilot_run_not_dispatchable", detail=state, status=409)
    spec = run.get("spec") if isinstance(run.get("spec"), Mapping) else {}
    if not list(spec.get("allowed_paths") or []):
        raise SoftwareFactoryError("velia_factory_live_pilot_write_scope_required", status=409)
    if _has_dispatched_work(run):
        raise SoftwareFactoryError("velia_factory_live_pilot_work_already_dispatched", status=409)

    identity = _run_identity(run, project)
    safe_ttl = min(1800, max(60, int(ttl_seconds or 600)))
    now = _utcnow()
    expires_at = now + timedelta(seconds=safe_ttl)
    source = str(approval_source or "explicit_admin").strip()[:120] or "explicit_admin"
    ensure_live_pilot_guard_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM velia_software_factory_live_pilot_grants "
            "WHERE user_id=%s AND run_id=%s FOR UPDATE",
            (int(user_id), identity["run_id"]),
        )
        existing = _row(cursor.fetchone())
        if existing:
            if (
                existing["project_id"] != identity["project_id"]
                or existing["repository_full_name"].casefold() != identity["repository_full_name"].casefold()
                or existing["spec_fingerprint"] != identity["spec_fingerprint"]
            ):
                raise SoftwareFactoryError("velia_factory_live_pilot_grant_identity_mismatch", status=409)
            conn.commit()
            return existing
        cursor.execute(
            f"""
            INSERT INTO velia_software_factory_live_pilot_grants (
                grant_id,user_id,run_id,project_id,repository_full_name,spec_fingerprint,status,
                approval_source,expires_at,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)
            RETURNING {_COLUMNS}
            """,
            (
                str(uuid.uuid4()),
                int(user_id),
                identity["run_id"],
                identity["project_id"],
                identity["repository_full_name"],
                identity["spec_fingerprint"],
                source,
                expires_at,
                now,
                now,
            ),
        )
        result = _row(cursor.fetchone())
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_grant(user_id: int, run_id: str) -> Dict[str, Any]:
    ensure_live_pilot_guard_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM velia_software_factory_live_pilot_grants "
            "WHERE user_id=%s AND run_id=%s",
            (int(user_id), str(run_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_live_pilot_grant_not_found", status=404)
        return _row(row)
    finally:
        cursor.close()
        conn.close()


def claim_dispatch(
    user_id: int,
    run: Mapping[str, Any],
    project: Mapping[str, Any],
    *,
    factory_task_id: str,
    client_request_id: str,
) -> Dict[str, Any]:
    if not live_pilot_guard_enabled():
        return {"enabled": False, "status": "disabled", "max_dispatches": 1}
    identity = _run_identity(run, project)
    task_id = str(factory_task_id or "").strip()[:160]
    request_id = str(client_request_id or "").strip()[:160]
    if not task_id or not request_id:
        raise SoftwareFactoryError("velia_factory_live_pilot_dispatch_identity_required", status=409)

    ensure_live_pilot_guard_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM velia_software_factory_live_pilot_grants "
            "WHERE user_id=%s AND run_id=%s FOR UPDATE",
            (int(user_id), identity["run_id"]),
        )
        grant = _row(cursor.fetchone())
        if not grant:
            raise SoftwareFactoryError("velia_factory_live_pilot_grant_required", status=403)
        if (
            grant["project_id"] != identity["project_id"]
            or grant["repository_full_name"].casefold() != identity["repository_full_name"].casefold()
            or grant["spec_fingerprint"] != identity["spec_fingerprint"]
        ):
            raise SoftwareFactoryError("velia_factory_live_pilot_grant_identity_mismatch", status=409)

        now = _utcnow()
        if grant["status"] == "pending":
            cursor.execute("SELECT expires_at FROM velia_software_factory_live_pilot_grants WHERE grant_id=%s", (grant["grant_id"],))
            raw_expiry = _value(cursor.fetchone(), "expires_at", 0)
            if isinstance(raw_expiry, datetime) and raw_expiry <= now:
                cursor.execute(
                    "UPDATE velia_software_factory_live_pilot_grants SET status='expired',updated_at=%s WHERE grant_id=%s",
                    (now, grant["grant_id"]),
                )
                conn.commit()
                raise SoftwareFactoryError("velia_factory_live_pilot_grant_expired", status=409)
            cursor.execute(
                f"""
                UPDATE velia_software_factory_live_pilot_grants
                SET status='claimed',factory_task_id=%s,client_request_id=%s,claimed_at=%s,updated_at=%s
                WHERE grant_id=%s AND status='pending'
                RETURNING {_COLUMNS}
                """,
                (task_id, request_id, now, now, grant["grant_id"]),
            )
            grant = _row(cursor.fetchone())
            if not grant:
                raise SoftwareFactoryError("velia_factory_live_pilot_grant_state_conflict", status=409)
            conn.commit()
            return grant

        same_dispatch = grant["factory_task_id"] == task_id and grant["client_request_id"] == request_id
        if grant["status"] in {"claimed", "consumed"} and same_dispatch:
            conn.commit()
            return grant
        if grant["status"] in {"claimed", "consumed"}:
            raise SoftwareFactoryError("velia_factory_live_pilot_dispatch_budget_exhausted", status=409)
        raise SoftwareFactoryError(
            "velia_factory_live_pilot_grant_unavailable",
            detail=grant["status"],
            status=409,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def confirm_dispatch(
    user_id: int,
    run_id: str,
    *,
    factory_task_id: str,
    client_request_id: str,
    autopilot_task_id: str,
) -> Dict[str, Any]:
    if not live_pilot_guard_enabled():
        return {"enabled": False, "status": "disabled", "max_dispatches": 1}
    task_id = str(factory_task_id or "").strip()[:160]
    request_id = str(client_request_id or "").strip()[:160]
    external_id = str(autopilot_task_id or "").strip()[:160]
    if not task_id or not request_id or not external_id:
        raise SoftwareFactoryError("velia_factory_live_pilot_confirmation_required", status=409)
    ensure_live_pilot_guard_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_COLUMNS} FROM velia_software_factory_live_pilot_grants "
            "WHERE user_id=%s AND run_id=%s FOR UPDATE",
            (int(user_id), str(run_id)),
        )
        grant = _row(cursor.fetchone())
        if not grant:
            raise SoftwareFactoryError("velia_factory_live_pilot_grant_not_found", status=404)
        if grant["factory_task_id"] != task_id or grant["client_request_id"] != request_id:
            raise SoftwareFactoryError("velia_factory_live_pilot_dispatch_identity_mismatch", status=409)
        if grant["status"] == "consumed":
            if grant["autopilot_task_id"] != external_id:
                raise SoftwareFactoryError("velia_factory_live_pilot_autopilot_task_mismatch", status=409)
            conn.commit()
            return grant
        if grant["status"] != "claimed":
            raise SoftwareFactoryError("velia_factory_live_pilot_grant_not_claimed", detail=grant["status"], status=409)
        now = _utcnow()
        cursor.execute(
            f"""
            UPDATE velia_software_factory_live_pilot_grants
            SET status='consumed',autopilot_task_id=%s,consumed_at=%s,updated_at=%s
            WHERE grant_id=%s AND status='claimed'
            RETURNING {_COLUMNS}
            """,
            (external_id, now, now, grant["grant_id"]),
        )
        result = _row(cursor.fetchone())
        if not result:
            raise SoftwareFactoryError("velia_factory_live_pilot_grant_state_conflict", status=409)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def revoke_pending_grant(user_id: int, run_id: str) -> Dict[str, Any]:
    grant = get_grant(int(user_id), str(run_id))
    if grant["status"] != "pending":
        raise SoftwareFactoryError("velia_factory_live_pilot_grant_not_revocable", detail=grant["status"], status=409)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"UPDATE velia_software_factory_live_pilot_grants SET status='revoked',updated_at=%s "
            f"WHERE user_id=%s AND run_id=%s AND status='pending' RETURNING {_COLUMNS}",
            (_utcnow(), int(user_id), str(run_id)),
        )
        result = _row(cursor.fetchone())
        if not result:
            raise SoftwareFactoryError("velia_factory_live_pilot_grant_state_conflict", status=409)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
