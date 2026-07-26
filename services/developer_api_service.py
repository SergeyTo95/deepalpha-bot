import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from db.database import get_connection

DEFAULT_SCOPES: Set[str] = {"account:read", "usage:read"}
AVAILABLE_SCOPES: Set[str] = {
    "account:read",
    "usage:read",
    "analysis:run",
    "analysis:read",
    "opportunities:read",
    "markets:read",
    "webhooks:manage",
}

_TABLES_READY = False
_TABLES_LOCK = threading.Lock()
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: Dict[int, deque] = defaultdict(deque)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _rows_to_dicts(cursor, rows) -> List[Dict[str, Any]]:
    return [item for item in (_row_to_dict(cursor, row) for row in rows or []) if item]


def _normalize_environment(environment: str) -> str:
    return "live" if str(environment or "").strip().lower() == "live" else "test"


def normalize_scopes(scopes: Optional[Iterable[str]]) -> List[str]:
    result = []
    for raw in scopes or DEFAULT_SCOPES:
        scope = str(raw or "").strip().lower()
        if scope in AVAILABLE_SCOPES and scope not in result:
            result.append(scope)
    return result or sorted(DEFAULT_SCOPES)


def parse_scopes(value: Any) -> Set[str]:
    if isinstance(value, (list, tuple, set)):
        return set(normalize_scopes(value))
    return set(normalize_scopes(str(value or "").split(",")))


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(str(raw_key or "").encode("utf-8")).hexdigest()


def generate_api_key(environment: str = "test") -> Tuple[str, str, str]:
    env = _normalize_environment(environment)
    raw_key = f"da_{env}_{secrets.token_urlsafe(32)}"
    key_prefix = raw_key[:18]
    return raw_key, key_prefix, hash_api_key(raw_key)


def ensure_developer_api_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    with _TABLES_LOCK:
        if _TABLES_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_clients (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    daily_request_limit INTEGER NOT NULL DEFAULT 1000,
                    monthly_request_limit INTEGER NOT NULL DEFAULT 20000,
                    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
                    credit_balance INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    id BIGSERIAL PRIMARY KEY,
                    client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                    name TEXT NOT NULL DEFAULT 'default',
                    environment TEXT NOT NULL DEFAULT 'test',
                    key_hash TEXT NOT NULL UNIQUE,
                    key_prefix TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_used_at TIMESTAMP,
                    revoked_at TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_usage (
                    id BIGSERIAL PRIMARY KEY,
                    client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                    key_id BIGINT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    method TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    units INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_jobs (
                    job_id TEXT PRIMARY KEY,
                    client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                    key_id BIGINT REFERENCES api_keys(id) ON DELETE SET NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    idempotency_key TEXT,
                    request_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    units_reserved INTEGER NOT NULL DEFAULT 0,
                    units_charged INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(client_id, idempotency_key)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_webhooks (
                    id BIGSERIAL PRIMARY KEY,
                    client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    secret_hash TEXT NOT NULL,
                    events TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    metadata_json TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_client ON api_keys(client_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_status ON api_keys(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_key_created ON api_usage(key_id, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_client_created ON api_usage(client_id, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_jobs_client_created ON api_jobs(client_id, created_at)")
            conn.commit()
            _TABLES_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def create_api_client(
    name: str,
    daily_request_limit: int = 1000,
    monthly_request_limit: int = 20000,
    rate_limit_per_minute: int = 60,
    credit_balance: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_developer_api_tables()
    clean_name = str(name or "").strip()[:120]
    if not clean_name:
        raise ValueError("name_required")
    daily = max(1, min(int(daily_request_limit), 1_000_000))
    monthly = max(daily, min(int(monthly_request_limit), 20_000_000))
    per_minute = max(1, min(int(rate_limit_per_minute), 10_000))
    credits = max(0, int(credit_balance))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_clients (
                name, daily_request_limit, monthly_request_limit,
                rate_limit_per_minute, credit_balance, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (clean_name, daily, monthly, per_minute, credits, json.dumps(metadata or {})),
        )
        row = _row_to_dict(cursor, cursor.fetchone()) or {}
        conn.commit()
        write_api_audit("admin", "client.create", "api_client", row.get("id"), {"name": clean_name})
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_api_clients(limit: int = 200) -> List[Dict[str, Any]]:
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT c.*,
                   (SELECT COUNT(*) FROM api_keys k WHERE k.client_id=c.id AND k.status='active') AS active_keys,
                   (SELECT COUNT(*) FROM api_usage u WHERE u.client_id=c.id AND u.created_at >= CURRENT_DATE) AS usage_today,
                   (SELECT COUNT(*) FROM api_usage u WHERE u.client_id=c.id AND u.created_at >= date_trunc('month', NOW())) AS usage_month
            FROM api_clients c
            ORDER BY c.id DESC
            LIMIT %s
            """,
            (max(1, min(int(limit), 1000)),),
        )
        return _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def get_api_client(client_id: int) -> Optional[Dict[str, Any]]:
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_clients WHERE id=%s", (int(client_id),))
        return _row_to_dict(cursor, cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def issue_api_key(
    client_id: int,
    name: str = "default",
    environment: str = "test",
    scopes: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    ensure_developer_api_tables()
    client = get_api_client(client_id)
    if not client or str(client.get("status")) != "active":
        raise ValueError("client_not_active")
    raw_key, key_prefix, key_hash = generate_api_key(environment)
    scope_list = normalize_scopes(scopes)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_keys (client_id, name, environment, key_hash, key_prefix, scopes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, client_id, name, environment, key_prefix, scopes, status, created_at
            """,
            (
                int(client_id),
                str(name or "default").strip()[:80] or "default",
                _normalize_environment(environment),
                key_hash,
                key_prefix,
                ",".join(scope_list),
            ),
        )
        row = _row_to_dict(cursor, cursor.fetchone()) or {}
        conn.commit()
        write_api_audit("admin", "key.issue", "api_key", row.get("id"), {"client_id": int(client_id), "scopes": scope_list})
        return {**row, "raw_key": raw_key, "scopes": scope_list}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_api_keys(client_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if client_id is None:
            cursor.execute(
                "SELECT id, client_id, name, environment, key_prefix, scopes, status, created_at, last_used_at, revoked_at FROM api_keys ORDER BY id DESC LIMIT %s",
                (max(1, min(int(limit), 2000)),),
            )
        else:
            cursor.execute(
                "SELECT id, client_id, name, environment, key_prefix, scopes, status, created_at, last_used_at, revoked_at FROM api_keys WHERE client_id=%s ORDER BY id DESC LIMIT %s",
                (int(client_id), max(1, min(int(limit), 2000))),
            )
        rows = _rows_to_dicts(cursor, cursor.fetchall())
        for row in rows:
            row["scopes"] = sorted(parse_scopes(row.get("scopes")))
        return rows
    finally:
        cursor.close()
        conn.close()


def revoke_api_key(key_id: int) -> bool:
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE api_keys SET status='revoked', revoked_at=NOW() WHERE id=%s AND status='active'",
            (int(key_id),),
        )
        changed = cursor.rowcount > 0
        conn.commit()
        if changed:
            write_api_audit("admin", "key.revoke", "api_key", int(key_id), {})
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def authenticate_api_key(raw_key: str) -> Optional[Dict[str, Any]]:
    raw = str(raw_key or "").strip()
    if not (raw.startswith("da_test_") or raw.startswith("da_live_")) or len(raw) < 40:
        return None
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT k.id AS key_id, k.client_id, k.name AS key_name, k.environment,
                   k.key_prefix, k.scopes, k.status AS key_status,
                   c.name AS client_name, c.status AS client_status,
                   c.daily_request_limit, c.monthly_request_limit,
                   c.rate_limit_per_minute, c.credit_balance
            FROM api_keys k
            JOIN api_clients c ON c.id=k.client_id
            WHERE k.key_hash=%s
            LIMIT 1
            """,
            (hash_api_key(raw),),
        )
        auth = _row_to_dict(cursor, cursor.fetchone())
        if not auth or auth.get("key_status") != "active" or auth.get("client_status") != "active":
            return None
        auth["scopes"] = parse_scopes(auth.get("scopes"))
        cursor.execute("UPDATE api_keys SET last_used_at=NOW() WHERE id=%s", (int(auth["key_id"]),))
        conn.commit()
        return auth
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def enforce_api_limits(auth: Dict[str, Any]) -> Dict[str, Any]:
    key_id = int(auth.get("key_id") or 0)
    per_minute = max(1, int(auth.get("rate_limit_per_minute") or 60))
    now = time.time()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS[key_id]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= per_minute:
            retry_after = max(1, int(60 - (now - bucket[0])))
            return {"ok": False, "error": "rate_limit_exceeded", "retry_after": retry_after}
        bucket.append(now)

    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE) AS daily_count,
                COUNT(*) FILTER (WHERE created_at >= date_trunc('month', NOW())) AS monthly_count
            FROM api_usage WHERE key_id=%s
            """,
            (key_id,),
        )
        counts = _row_to_dict(cursor, cursor.fetchone()) or {}
    finally:
        cursor.close()
        conn.close()
    daily_count = int(counts.get("daily_count") or 0)
    monthly_count = int(counts.get("monthly_count") or 0)
    daily_limit = max(1, int(auth.get("daily_request_limit") or 1000))
    monthly_limit = max(1, int(auth.get("monthly_request_limit") or 20000))
    if daily_count >= daily_limit:
        return {"ok": False, "error": "daily_limit_exceeded"}
    if monthly_count >= monthly_limit:
        return {"ok": False, "error": "monthly_limit_exceeded"}
    return {
        "ok": True,
        "daily_used": daily_count,
        "daily_limit": daily_limit,
        "monthly_used": monthly_count,
        "monthly_limit": monthly_limit,
    }


def record_api_usage(
    auth: Dict[str, Any],
    request_id: str,
    endpoint: str,
    method: str,
    status_code: int,
    units: int = 0,
    latency_ms: int = 0,
) -> None:
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_usage (
                client_id, key_id, request_id, endpoint, method,
                status_code, units, latency_ms
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(auth.get("client_id") or 0),
                int(auth.get("key_id") or 0),
                str(request_id or "")[:100],
                str(endpoint or "")[:300],
                str(method or "GET")[:10],
                int(status_code),
                max(0, int(units)),
                max(0, int(latency_ms)),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_usage_summary(client_id: int, key_id: Optional[int] = None) -> Dict[str, Any]:
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        params: List[Any] = [int(client_id)]
        key_filter = ""
        if key_id is not None:
            key_filter = " AND key_id=%s"
            params.append(int(key_id))
        cursor.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE created_at >= CURRENT_DATE) AS requests_today,
                COUNT(*) FILTER (WHERE created_at >= date_trunc('month', NOW())) AS requests_month,
                COALESCE(SUM(units) FILTER (WHERE created_at >= CURRENT_DATE), 0) AS units_today,
                COALESCE(SUM(units) FILTER (WHERE created_at >= date_trunc('month', NOW())), 0) AS units_month,
                COALESCE(AVG(latency_ms) FILTER (WHERE created_at >= CURRENT_DATE), 0) AS average_latency_ms_today
            FROM api_usage
            WHERE client_id=%s {key_filter}
            """,
            tuple(params),
        )
        summary = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            f"""
            SELECT request_id, endpoint, method, status_code, units, latency_ms, created_at
            FROM api_usage
            WHERE client_id=%s {key_filter}
            ORDER BY id DESC LIMIT 20
            """,
            tuple(params),
        )
        summary["recent_requests"] = _rows_to_dicts(cursor, cursor.fetchall())
        return summary
    finally:
        cursor.close()
        conn.close()


def write_api_audit(
    actor: str,
    action: str,
    target_type: Optional[str],
    target_id: Any,
    metadata: Optional[Dict[str, Any]],
) -> None:
    ensure_developer_api_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json) VALUES (%s, %s, %s, %s, %s)",
            (
                str(actor or "system")[:100],
                str(action or "unknown")[:120],
                str(target_type or "")[:80],
                str(target_id or "")[:120],
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
