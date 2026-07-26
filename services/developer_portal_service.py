import json
import os
import threading
from typing import Any, Dict, Iterable, List, Optional

from db.database import get_connection
from services.developer_api_billing_service import (
    ensure_api_billing_tables,
    list_api_credit_ledger,
    list_api_products,
)
from services.developer_api_service import (
    AVAILABLE_SCOPES,
    generate_api_key,
    get_usage_summary,
    normalize_scopes,
)

SELF_SERVICE_SCOPES = {
    "account:read",
    "usage:read",
    "analysis:run",
    "analysis:read",
    "opportunities:read",
    "markets:read",
}
DEFAULT_SELF_SERVICE_SCOPES = ["account:read", "usage:read"]

_PORTAL_TABLES_READY = False
_PORTAL_TABLES_LOCK = threading.Lock()


class DeveloperPortalError(ValueError):
    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code = str(code)
        self.details = details


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _rows_to_dicts(cursor, rows) -> List[Dict[str, Any]]:
    return [item for item in (_row_to_dict(cursor, row) for row in rows or []) if item]


def _safe_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(os.getenv(name, default)).strip())
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def max_projects_per_user() -> int:
    return _safe_env_int("DEVELOPER_PORTAL_MAX_PROJECTS", 3, 1, 20)


def max_keys_per_project() -> int:
    return _safe_env_int("DEVELOPER_PORTAL_MAX_KEYS_PER_PROJECT", 5, 1, 50)


def ensure_developer_portal_tables() -> None:
    global _PORTAL_TABLES_READY
    if _PORTAL_TABLES_READY:
        return
    with _PORTAL_TABLES_LOCK:
        if _PORTAL_TABLES_READY:
            return
        ensure_api_billing_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_client_owners (
                    user_id BIGINT NOT NULL,
                    client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'owner',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, client_id),
                    UNIQUE (client_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_client_owners_user ON api_client_owners(user_id)"
            )
            conn.commit()
            _PORTAL_TABLES_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def normalize_self_service_scopes(scopes: Optional[Iterable[str]]) -> List[str]:
    requested = DEFAULT_SELF_SERVICE_SCOPES if scopes is None else list(scopes)
    normalized = [
        scope
        for scope in normalize_scopes(requested)
        if scope in SELF_SERVICE_SCOPES and scope in AVAILABLE_SCOPES
    ]
    if not normalized:
        raise DeveloperPortalError("at_least_one_scope_required")
    return normalized


def _clean_project_name(value: str) -> str:
    name = " ".join(str(value or "").strip().split())[:120]
    if len(name) < 2:
        raise DeveloperPortalError("project_name_required")
    return name


def _clean_key_name(value: str) -> str:
    return " ".join(str(value or "default").strip().split())[:80] or "default"


def _lock_user_project_scope(cursor, user_id: int) -> None:
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (int(user_id),))


def _owned_client(cursor, user_id: int, client_id: int, *, for_update: bool = False) -> Optional[Dict[str, Any]]:
    suffix = " FOR UPDATE OF c" if for_update else ""
    cursor.execute(
        f"""
        SELECT c.*, o.role
        FROM api_clients c
        JOIN api_client_owners o ON o.client_id=c.id
        WHERE o.user_id=%s AND c.id=%s
        {suffix}
        """,
        (int(user_id), int(client_id)),
    )
    return _row_to_dict(cursor, cursor.fetchone())


def create_user_api_project(user_id: int, name: str) -> Dict[str, Any]:
    ensure_developer_portal_tables()
    uid = int(user_id)
    if uid <= 0:
        raise DeveloperPortalError("unauthorized")
    clean_name = _clean_project_name(name)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _lock_user_project_scope(cursor, uid)
        cursor.execute("SELECT COUNT(*) FROM api_client_owners WHERE user_id=%s", (uid,))
        row = cursor.fetchone()
        current_count = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
        project_limit = max_projects_per_user()
        if current_count >= project_limit:
            raise DeveloperPortalError("project_limit_reached", limit=project_limit)

        cursor.execute(
            """
            INSERT INTO api_clients (
                name, status, daily_request_limit, monthly_request_limit,
                rate_limit_per_minute, credit_balance, metadata_json
            ) VALUES (%s, 'active', %s, %s, %s, 0, %s)
            RETURNING *
            """,
            (
                clean_name,
                _safe_env_int("DEVELOPER_PORTAL_DEFAULT_DAILY_LIMIT", 100, 1, 1000000),
                _safe_env_int("DEVELOPER_PORTAL_DEFAULT_MONTHLY_LIMIT", 2000, 1, 20000000),
                _safe_env_int("DEVELOPER_PORTAL_DEFAULT_RATE_LIMIT", 30, 1, 10000),
                json.dumps({"source": "self_service", "owner_user_id": uid}),
            ),
        )
        client = _row_to_dict(cursor, cursor.fetchone()) or {}
        client_id = int(client.get("id") or 0)
        cursor.execute(
            "INSERT INTO api_client_owners (user_id, client_id, role) VALUES (%s, %s, 'owner')",
            (uid, client_id),
        )
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'portal.project.create', 'api_client', %s, %s)
            """,
            (
                f"user:{uid}",
                str(client_id),
                json.dumps({"name": clean_name, "source": "developer_portal"}),
            ),
        )
        conn.commit()
        return client
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_user_api_projects(user_id: int) -> List[Dict[str, Any]]:
    ensure_developer_portal_tables()
    uid = int(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT c.*, o.role,
                   (SELECT COUNT(*) FROM api_keys k WHERE k.client_id=c.id AND k.status='active') AS active_keys,
                   (SELECT COUNT(*) FROM api_usage u WHERE u.client_id=c.id AND u.created_at >= CURRENT_DATE) AS usage_today,
                   (SELECT COUNT(*) FROM api_usage u WHERE u.client_id=c.id AND u.created_at >= date_trunc('month', NOW())) AS usage_month
            FROM api_client_owners o
            JOIN api_clients c ON c.id=o.client_id
            WHERE o.user_id=%s
            ORDER BY c.id DESC
            """,
            (uid,),
        )
        return _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def list_user_api_keys(user_id: int, client_id: Optional[int] = None) -> List[Dict[str, Any]]:
    ensure_developer_portal_tables()
    uid = int(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        params: List[Any] = [uid]
        client_filter = ""
        if client_id is not None:
            client_filter = " AND k.client_id=%s"
            params.append(int(client_id))
        cursor.execute(
            f"""
            SELECT k.id, k.client_id, k.name, k.environment, k.key_prefix,
                   k.scopes, k.status, k.created_at, k.last_used_at, k.revoked_at
            FROM api_keys k
            JOIN api_client_owners o ON o.client_id=k.client_id
            WHERE o.user_id=%s {client_filter}
            ORDER BY k.id DESC
            """,
            tuple(params),
        )
        keys = _rows_to_dicts(cursor, cursor.fetchall())
        for item in keys:
            item["scopes"] = [scope for scope in str(item.get("scopes") or "").split(",") if scope]
        return keys
    finally:
        cursor.close()
        conn.close()


def issue_user_api_key(
    *,
    user_id: int,
    client_id: int,
    name: str,
    scopes: Optional[Iterable[str]],
) -> Dict[str, Any]:
    ensure_developer_portal_tables()
    uid = int(user_id)
    cid = int(client_id)
    scope_list = normalize_self_service_scopes(scopes)
    raw_key, key_prefix, key_hash = generate_api_key("test")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, uid, cid, for_update=True)
        if not client:
            raise DeveloperPortalError("project_not_found")
        if str(client.get("status") or "") != "active":
            raise DeveloperPortalError("project_not_active")
        cursor.execute(
            "SELECT COUNT(*) FROM api_keys WHERE client_id=%s AND status='active'",
            (cid,),
        )
        row = cursor.fetchone()
        active_count = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
        key_limit = max_keys_per_project()
        if active_count >= key_limit:
            raise DeveloperPortalError("key_limit_reached", limit=key_limit)
        cursor.execute(
            """
            INSERT INTO api_keys (
                client_id, name, environment, key_hash, key_prefix, scopes, status
            ) VALUES (%s, %s, 'test', %s, %s, %s, 'active')
            RETURNING id, client_id, name, environment, key_prefix, scopes, status, created_at
            """,
            (cid, _clean_key_name(name), key_hash, key_prefix, ",".join(scope_list)),
        )
        key = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'portal.key.issue', 'api_key', %s, %s)
            """,
            (
                f"user:{uid}",
                str(key.get("id") or ""),
                json.dumps({"client_id": cid, "scopes": scope_list, "environment": "test"}),
            ),
        )
        conn.commit()
        return {**key, "raw_key": raw_key, "scopes": scope_list}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def revoke_user_api_key(*, user_id: int, key_id: int) -> bool:
    ensure_developer_portal_tables()
    uid = int(user_id)
    kid = int(key_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_keys k
            SET status='revoked', revoked_at=NOW()
            FROM api_client_owners o
            WHERE k.id=%s AND o.client_id=k.client_id AND o.user_id=%s AND k.status='active'
            RETURNING k.id, k.client_id
            """,
            (kid, uid),
        )
        changed = _row_to_dict(cursor, cursor.fetchone())
        if changed:
            cursor.execute(
                """
                INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
                VALUES (%s, 'portal.key.revoke', 'api_key', %s, %s)
                """,
                (f"user:{uid}", str(kid), json.dumps({"client_id": changed.get("client_id")})),
            )
        conn.commit()
        return bool(changed)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def rotate_user_api_key(*, user_id: int, key_id: int) -> Dict[str, Any]:
    ensure_developer_portal_tables()
    uid = int(user_id)
    kid = int(key_id)
    raw_key, key_prefix, key_hash = generate_api_key("test")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT k.*
            FROM api_keys k
            JOIN api_client_owners o ON o.client_id=k.client_id
            WHERE k.id=%s AND o.user_id=%s
            FOR UPDATE OF k
            """,
            (kid, uid),
        )
        existing = _row_to_dict(cursor, cursor.fetchone())
        if not existing:
            raise DeveloperPortalError("key_not_found")
        if str(existing.get("status") or "") != "active":
            raise DeveloperPortalError("key_not_active")
        cid = int(existing.get("client_id") or 0)
        cursor.execute(
            "UPDATE api_keys SET status='revoked', revoked_at=NOW() WHERE id=%s",
            (kid,),
        )
        cursor.execute(
            """
            INSERT INTO api_keys (
                client_id, name, environment, key_hash, key_prefix, scopes, status
            ) VALUES (%s, %s, 'test', %s, %s, %s, 'active')
            RETURNING id, client_id, name, environment, key_prefix, scopes, status, created_at
            """,
            (
                cid,
                _clean_key_name(str(existing.get("name") or "default")),
                key_hash,
                key_prefix,
                str(existing.get("scopes") or ""),
            ),
        )
        replacement = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'portal.key.rotate', 'api_key', %s, %s)
            """,
            (
                f"user:{uid}",
                str(replacement.get("id") or ""),
                json.dumps({"client_id": cid, "replaced_key_id": kid}),
            ),
        )
        conn.commit()
        return {
            **replacement,
            "raw_key": raw_key,
            "scopes": [scope for scope in str(replacement.get("scopes") or "").split(",") if scope],
            "replaced_key_id": kid,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_user_developer_overview(user_id: int) -> Dict[str, Any]:
    uid = int(user_id)
    projects = list_user_api_projects(uid)
    keys = list_user_api_keys(uid)
    keys_by_project: Dict[int, List[Dict[str, Any]]] = {}
    for key in keys:
        keys_by_project.setdefault(int(key.get("client_id") or 0), []).append(key)

    for project in projects:
        client_id = int(project.get("id") or 0)
        project["keys"] = keys_by_project.get(client_id, [])
        project["usage"] = get_usage_summary(client_id=client_id, key_id=None)
        project["recent_ledger"] = list_api_credit_ledger(client_id=client_id, limit=20)

    return {
        "projects": projects,
        "products": list_api_products(),
        "available_scopes": sorted(SELF_SERVICE_SCOPES),
        "default_scopes": list(DEFAULT_SELF_SERVICE_SCOPES),
        "limits": {
            "projects_per_user": max_projects_per_user(),
            "keys_per_project": max_keys_per_project(),
        },
        "analysis_endpoints_enabled": False,
        "live_keys_enabled": False,
    }
