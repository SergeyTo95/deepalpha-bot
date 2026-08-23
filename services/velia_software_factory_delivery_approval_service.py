from __future__ import annotations

import os
import threading
import uuid
from typing import Any, Dict, Mapping

from db.database import get_connection
from services import velia_software_factory_delivery_gate_service as delivery
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_644
_DECISIONS = {"approved", "rejected", "revoked"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def approval_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED", False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": approval_enabled(),
        "mode": "record_only",
        "append_only": True,
        "candidate_revalidation_required": True,
        "exact_fingerprint_required": True,
        "user_decision_supported": True,
        "execution_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
    }


def _utcnow():
    return delivery._utcnow()


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


def ensure_approval_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    delivery.ensure_delivery_tables(execution_module)
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_delivery_approval_events (
                    sequence_id BIGSERIAL UNIQUE NOT NULL,
                    decision_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES velia_software_factory_delivery_candidates(candidate_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (decision IN ('approved','rejected','revoked'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_delivery_approval_events "
                "ON velia_software_factory_delivery_approval_events(user_id,candidate_id,sequence_id DESC)"
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
    if not approval_enabled():
        raise SoftwareFactoryError("velia_factory_delivery_approval_disabled", status=503)
    if not delivery.delivery_gate_enabled():
        raise SoftwareFactoryError("velia_factory_delivery_gate_disabled", status=503)
    if not rollout.intake_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)


def _event_row(row: Any) -> Dict[str, Any]:
    return {
        "sequence_id": int(_value(row, "sequence_id", 0, 0) or 0),
        "decision_id": str(_value(row, "decision_id", 1, "")),
        "candidate_id": str(_value(row, "candidate_id", 2, "")),
        "user_id": int(_value(row, "user_id", 3, 0) or 0),
        "source_id": str(_value(row, "source_id", 4, "")),
        "source_fingerprint": str(_value(row, "source_fingerprint", 5, "")),
        "decision": str(_value(row, "decision", 6, "")),
        "note": str(_value(row, "note", 7, "") or ""),
        "created_at": str(_value(row, "created_at", 8, "") or ""),
    }


def latest_decision(execution_module: Any, user_id: int, candidate_id: str) -> Dict[str, Any]:
    ensure_approval_tables(execution_module)
    delivery.get_candidate(execution_module, int(user_id), str(candidate_id))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT sequence_id,decision_id,candidate_id,user_id,source_id,source_fingerprint,decision,note,created_at "
            "FROM velia_software_factory_delivery_approval_events "
            "WHERE candidate_id=%s AND user_id=%s ORDER BY sequence_id DESC LIMIT 1",
            (str(candidate_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return {
                "candidate_id": str(candidate_id),
                "state": "none",
                "approved": False,
                "merge_supported": False,
                "deployment_supported": False,
            }
        result = _event_row(row)
        result["state"] = result["decision"]
        result["approved"] = result["decision"] == "approved"
        result["merge_supported"] = False
        result["deployment_supported"] = False
        return result
    finally:
        cursor.close()
        conn.close()


def _fresh_candidate(execution_module: Any, user_id: int, candidate: Mapping[str, Any]) -> Dict[str, Any]:
    if str(candidate.get("source_type") or "") != "workspace_execution":
        raise SoftwareFactoryError("velia_factory_delivery_candidate_source_invalid", status=409)
    source_id = str(candidate.get("source_id") or "")
    if not source_id:
        raise SoftwareFactoryError("velia_factory_delivery_candidate_source_invalid", status=409)
    fresh = delivery.evaluate_workspace_candidate(
        execution_module,
        int(user_id),
        source_id,
        persist=False,
    )
    expected = str(candidate.get("source_fingerprint") or "")
    actual = str(fresh.get("source_fingerprint") or "")
    if not expected or not actual or expected != actual:
        raise SoftwareFactoryError(
            "velia_factory_delivery_candidate_stale",
            detail=f"expected={expected[:16]} actual={actual[:16]}",
            status=409,
        )
    return fresh


def _insert_event(
    execution_module: Any,
    user_id: int,
    candidate: Mapping[str, Any],
    decision: str,
    note: str,
) -> Dict[str, Any]:
    ensure_approval_tables(execution_module)
    current = latest_decision(execution_module, int(user_id), str(candidate.get("candidate_id") or ""))
    if str(current.get("decision") or "") == decision and str(current.get("source_fingerprint") or "") == str(candidate.get("source_fingerprint") or ""):
        return current
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_delivery_approval_events (
                decision_id,candidate_id,user_id,source_id,source_fingerprint,decision,note,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING sequence_id,decision_id,candidate_id,user_id,source_id,source_fingerprint,decision,note,created_at
            """,
            (
                str(uuid.uuid4()),
                str(candidate.get("candidate_id") or ""),
                int(user_id),
                str(candidate.get("source_id") or ""),
                str(candidate.get("source_fingerprint") or ""),
                decision,
                str(note or "").replace("\x00", "")[:1000],
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        result = _event_row(row)
        result["state"] = result["decision"]
        result["approved"] = result["decision"] == "approved"
        result["merge_supported"] = False
        result["deployment_supported"] = False
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def record_decision(
    execution_module: Any,
    user_id: int,
    candidate_id: str,
    decision: str,
    *,
    note: str = "",
) -> Dict[str, Any]:
    _require_user(int(user_id))
    normalized = str(decision or "").strip().lower()
    if normalized not in _DECISIONS:
        raise SoftwareFactoryError("velia_factory_delivery_decision_invalid", status=400)
    candidate = delivery.get_candidate(execution_module, int(user_id), str(candidate_id))
    if normalized == "approved":
        if str(candidate.get("status") or "") != "eligible" or not bool(candidate.get("release_eligible")):
            raise SoftwareFactoryError("velia_factory_delivery_candidate_not_eligible", status=409)
        fresh = _fresh_candidate(execution_module, int(user_id), candidate)
        if str(fresh.get("status") or "") != "eligible" or not bool(fresh.get("release_eligible")):
            raise SoftwareFactoryError("velia_factory_delivery_candidate_not_eligible", status=409)
    elif normalized == "revoked":
        current = latest_decision(execution_module, int(user_id), str(candidate_id))
        if str(current.get("decision") or "") != "approved":
            raise SoftwareFactoryError("velia_factory_delivery_approval_not_active", status=409)
    return _insert_event(execution_module, int(user_id), candidate, normalized, str(note or ""))


def require_current_approval(execution_module: Any, user_id: int, candidate_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    candidate = delivery.get_candidate(execution_module, int(user_id), str(candidate_id))
    current = latest_decision(execution_module, int(user_id), str(candidate_id))
    if str(current.get("decision") or "") != "approved":
        raise SoftwareFactoryError("velia_factory_delivery_approval_required", status=409)
    if str(current.get("source_fingerprint") or "") != str(candidate.get("source_fingerprint") or ""):
        raise SoftwareFactoryError("velia_factory_delivery_approval_fingerprint_mismatch", status=409)
    fresh = _fresh_candidate(execution_module, int(user_id), candidate)
    if str(fresh.get("status") or "") != "eligible" or not bool(fresh.get("release_eligible")):
        raise SoftwareFactoryError("velia_factory_delivery_candidate_not_eligible", status=409)
    return {
        "ok": True,
        "candidate_id": str(candidate_id),
        "source_id": str(candidate.get("source_id") or ""),
        "source_fingerprint": str(candidate.get("source_fingerprint") or ""),
        "approval": current,
        "current": True,
        "release_eligible": True,
        "merge_supported": False,
        "deployment_supported": False,
    }
