import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from db.database import get_connection


ADMIN_SESSION_COOKIE = "velia_admin_session"
ADMIN_CSRF_COOKIE = "velia_admin_csrf"
ADMIN_LOGIN_TTL_SECONDS = 5 * 60
ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60
_ADMIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_REDACT_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "private_key",
    "seed",
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _hash_ip(value: str) -> str:
    return _sha256(value) if value else ""


def configured_admin_id() -> int:
    try:
        value = int(str(os.getenv("ADMIN_ID", "0") or "0").strip())
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def is_admin_user(user_id: int) -> bool:
    admin_id = configured_admin_id()
    try:
        candidate = int(user_id)
    except (TypeError, ValueError):
        return False
    return admin_id > 0 and candidate == admin_id


def ensure_velia_admin_tables() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_admin_login_codes (
                code_hash TEXT PRIMARY KEY,
                admin_user_id BIGINT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL,
                consumed_at TIMESTAMP NULL,
                source TEXT NOT NULL DEFAULT 'telegram'
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_admin_login_codes_admin "
            "ON velia_admin_login_codes(admin_user_id, created_at DESC)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_admin_sessions (
                session_token_hash TEXT PRIMARY KEY,
                admin_user_id BIGINT NOT NULL,
                csrf_token_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL,
                last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                revoked_at TIMESTAMP NULL,
                user_agent TEXT,
                ip_hash TEXT
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_admin_sessions_admin "
            "ON velia_admin_sessions(admin_user_id, expires_at DESC)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_admin_audit_log (
                id BIGSERIAL PRIMARY KEY,
                admin_user_id BIGINT NULL,
                timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                request_id TEXT,
                before_json TEXT,
                after_json TEXT,
                success BOOLEAN NOT NULL DEFAULT TRUE,
                error_code TEXT,
                source TEXT NOT NULL DEFAULT 'web',
                ip_hash TEXT,
                user_agent TEXT
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_admin_audit_time "
            "ON velia_admin_audit_log(timestamp DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_admin_audit_actor "
            "ON velia_admin_audit_log(admin_user_id, timestamp DESC)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def normalize_login_code(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def format_login_code(value: str) -> str:
    normalized = normalize_login_code(value)
    return "-".join(normalized[i : i + 4] for i in range(0, len(normalized), 4))


def _new_login_code() -> str:
    return "".join(secrets.choice(_ADMIN_CODE_ALPHABET) for _ in range(16))


def create_admin_login_code(admin_user_id: int) -> Dict[str, Any]:
    if not is_admin_user(admin_user_id):
        return {"ok": False, "error": "not_admin"}
    ensure_velia_admin_tables()
    now = _utcnow()
    expires_at = now + timedelta(seconds=ADMIN_LOGIN_TTL_SECONDS)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM velia_admin_login_codes "
            "WHERE expires_at < %s OR (consumed_at IS NOT NULL AND consumed_at < %s)",
            (now, now - timedelta(days=1)),
        )
        cursor.execute(
            "UPDATE velia_admin_login_codes SET consumed_at=%s "
            "WHERE admin_user_id=%s AND consumed_at IS NULL",
            (now, int(admin_user_id)),
        )
        for _ in range(8):
            raw_code = _new_login_code()
            cursor.execute(
                """
                INSERT INTO velia_admin_login_codes (
                    code_hash, admin_user_id, created_at, expires_at, source
                ) VALUES (%s, %s, %s, %s, 'telegram')
                ON CONFLICT (code_hash) DO NOTHING
                """,
                (_sha256(raw_code), int(admin_user_id), now, expires_at),
            )
            if cursor.rowcount == 1:
                conn.commit()
                return {
                    "ok": True,
                    "login_code": format_login_code(raw_code),
                    "expires_in": ADMIN_LOGIN_TTL_SECONDS,
                    "expires_at": expires_at.isoformat() + "Z",
                }
        conn.rollback()
        return {"ok": False, "error": "code_generation_failed"}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def consume_admin_login_code(
    raw_code: str,
    *,
    user_agent: str = "",
    ip: str = "",
) -> Dict[str, Any]:
    normalized = normalize_login_code(raw_code)
    if len(normalized) != 16:
        return {"ok": False, "error": "invalid_code"}
    ensure_velia_admin_tables()
    now = _utcnow()
    session_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(seconds=ADMIN_SESSION_TTL_SECONDS)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT admin_user_id, expires_at, consumed_at
            FROM velia_admin_login_codes
            WHERE code_hash=%s
            FOR UPDATE
            """,
            (_sha256(normalized),),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "invalid_code"}
        admin_user_id = int(row[0] or 0)
        code_expires_at = row[1]
        consumed_at = row[2]
        if not is_admin_user(admin_user_id):
            conn.rollback()
            return {"ok": False, "error": "not_admin"}
        if consumed_at is not None:
            conn.rollback()
            return {"ok": False, "error": "code_used"}
        if code_expires_at is None or code_expires_at <= now:
            conn.rollback()
            return {"ok": False, "error": "code_expired"}
        cursor.execute(
            "UPDATE velia_admin_login_codes SET consumed_at=%s WHERE code_hash=%s",
            (now, _sha256(normalized)),
        )
        cursor.execute(
            """
            INSERT INTO velia_admin_sessions (
                session_token_hash, admin_user_id, csrf_token_hash,
                created_at, expires_at, last_seen_at, user_agent, ip_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                _sha256(session_token),
                admin_user_id,
                _sha256(csrf_token),
                now,
                expires_at,
                now,
                str(user_agent or "")[:512],
                _hash_ip(ip),
            ),
        )
        conn.commit()
        return {
            "ok": True,
            "admin_user_id": admin_user_id,
            "session_token": session_token,
            "csrf_token": csrf_token,
            "expires_at": expires_at.isoformat() + "Z",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_admin_session(raw_session_token: str) -> Optional[Dict[str, Any]]:
    if not raw_session_token:
        return None
    ensure_velia_admin_tables()
    now = _utcnow()
    token_hash = _sha256(raw_session_token)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT admin_user_id, csrf_token_hash, created_at, expires_at,
                   last_seen_at, user_agent, ip_hash
            FROM velia_admin_sessions
            WHERE session_token_hash=%s AND revoked_at IS NULL
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        admin_user_id = int(row[0] or 0)
        if not is_admin_user(admin_user_id) or row[3] is None or row[3] <= now:
            cursor.execute(
                "UPDATE velia_admin_sessions SET revoked_at=%s WHERE session_token_hash=%s",
                (now, token_hash),
            )
            conn.commit()
            return None
        cursor.execute(
            "UPDATE velia_admin_sessions SET last_seen_at=%s WHERE session_token_hash=%s",
            (now, token_hash),
        )
        conn.commit()
        return {
            "admin_user_id": admin_user_id,
            "csrf_token_hash": str(row[1] or ""),
            "created_at": row[2],
            "expires_at": row[3],
            "last_seen_at": now,
            "user_agent": str(row[5] or ""),
            "ip_hash": str(row[6] or ""),
        }
    finally:
        cursor.close()
        conn.close()


def verify_admin_csrf(session: Dict[str, Any], raw_csrf_token: str) -> bool:
    expected = str((session or {}).get("csrf_token_hash") or "")
    supplied = _sha256(str(raw_csrf_token or ""))
    return bool(expected) and hmac.compare_digest(expected, supplied)


def revoke_admin_session(raw_session_token: str) -> bool:
    if not raw_session_token:
        return False
    ensure_velia_admin_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_admin_sessions SET revoked_at=%s "
            "WHERE session_token_hash=%s AND revoked_at IS NULL",
            (_utcnow(), _sha256(raw_session_token)),
        )
        changed = bool(cursor.rowcount)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in _REDACT_MARKERS):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _sanitize(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in list(value)[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= 2000 else text[:2000] + "…"


def insert_admin_audit(
    cursor: Any,
    *,
    admin_user_id: Optional[int],
    action: str,
    target_type: str = "",
    target_id: str = "",
    request_id: str = "",
    before: Any = None,
    after: Any = None,
    success: bool = True,
    error_code: str = "",
    source: str = "web",
    ip: str = "",
    user_agent: str = "",
) -> None:
    cursor.execute(
        """
        INSERT INTO velia_admin_audit_log (
            admin_user_id, timestamp, action, target_type, target_id,
            request_id, before_json, after_json, success, error_code,
            source, ip_hash, user_agent
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            int(admin_user_id) if admin_user_id else None,
            _utcnow(),
            str(action or "unknown")[:160],
            str(target_type or "")[:80] or None,
            str(target_id or "")[:160] or None,
            str(request_id or "")[:160] or None,
            json.dumps(_sanitize(before), ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(_sanitize(after), ensure_ascii=False, default=str) if after is not None else None,
            bool(success),
            str(error_code or "")[:160] or None,
            str(source or "web")[:40],
            _hash_ip(ip),
            str(user_agent or "")[:512],
        ),
    )


def record_admin_audit(**kwargs: Any) -> None:
    ensure_velia_admin_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        insert_admin_audit(cursor, **kwargs)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_admin_audit(limit: int = 100) -> list[Dict[str, Any]]:
    ensure_velia_admin_tables()
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, admin_user_id, timestamp, action, target_type, target_id,
                   request_id, before_json, after_json, success, error_code,
                   source, ip_hash, user_agent
            FROM velia_admin_audit_log
            ORDER BY id DESC
            LIMIT %s
            """,
            (safe_limit,),
        )
        rows = cursor.fetchall() or []
        keys = [
            "id", "admin_user_id", "timestamp", "action", "target_type", "target_id",
            "request_id", "before_json", "after_json", "success", "error_code",
            "source", "ip_hash", "user_agent",
        ]
        return [dict(zip(keys, row)) if not isinstance(row, dict) else dict(row) for row in rows]
    finally:
        cursor.close()
        conn.close()
