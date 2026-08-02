import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection


PERSISTENT_REFRESH_EXPIRES_AT = datetime(9999, 12, 31, 23, 59, 59)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return max(minimum, default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _resolve_refresh_expiry(
    now: datetime,
    current_expires_at: Optional[datetime] = None,
) -> datetime:
    if _env_bool("VELIA_MOBILE_PERSISTENT_SESSIONS", True):
        return PERSISTENT_REFRESH_EXPIRES_AT

    refresh_ttl_days = _env_int("VELIA_MOBILE_REFRESH_TTL_DAYS", 30, 1)
    configured_expiry = now + timedelta(days=refresh_ttl_days)
    if current_expires_at is None:
        return configured_expiry
    return min(current_expires_at, configured_expiry)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _hash_optional(value: str) -> str:
    value = str(value or "").strip()
    return _hash_secret(value) if value else ""


def normalize_pairing_code(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def format_pairing_code(value: str) -> str:
    normalized = normalize_pairing_code(value)
    return "-".join(normalized[index:index + 4] for index in range(0, len(normalized), 4))


def _new_pairing_code() -> str:
    # 16 base32-like characters keep manual entry practical while retaining
    # substantially more entropy than a six-digit verification code.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))


def _new_access_token() -> str:
    return "va_" + secrets.token_urlsafe(48)


def _new_refresh_token() -> str:
    return "vr_" + secrets.token_urlsafe(64)


def ensure_velia_mobile_auth_tables() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_mobile_pairing_codes (
                code_hash TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL,
                consumed_at TIMESTAMP NULL,
                created_user_agent TEXT,
                created_ip_hash TEXT
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_pairing_user_created "
            "ON velia_mobile_pairing_codes(user_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_pairing_expires "
            "ON velia_mobile_pairing_codes(expires_at)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_mobile_sessions (
                session_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                access_token_hash TEXT NOT NULL UNIQUE,
                refresh_token_hash TEXT NOT NULL UNIQUE,
                device_id_hash TEXT,
                device_name TEXT,
                refresh_generation INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                access_expires_at TIMESTAMP NOT NULL,
                refresh_expires_at TIMESTAMP NOT NULL,
                last_seen_at TIMESTAMP NULL,
                revoked_at TIMESTAMP NULL,
                user_agent TEXT,
                ip_hash TEXT
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_mobile_sessions_user "
            "ON velia_mobile_sessions(user_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_mobile_sessions_refresh_expiry "
            "ON velia_mobile_sessions(refresh_expires_at)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_mobile_refresh_history (
                refresh_token_hash TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                generation INTEGER NOT NULL,
                replaced_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_refresh_history_session "
            "ON velia_mobile_refresh_history(session_id, replaced_at DESC)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_pairing_code(
    user_id: int,
    *,
    user_agent: str = "",
    ip: str = "",
) -> Dict[str, Any]:
    ttl_seconds = _env_int("VELIA_MOBILE_PAIRING_TTL_SECONDS", 600, 60)
    now = _utcnow()
    expires_at = now + timedelta(seconds=ttl_seconds)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM velia_mobile_pairing_codes "
            "WHERE expires_at < %s OR (consumed_at IS NOT NULL AND consumed_at < %s)",
            (now, now - timedelta(days=1)),
        )
        cursor.execute(
            "UPDATE velia_mobile_pairing_codes SET consumed_at=%s "
            "WHERE user_id=%s AND consumed_at IS NULL",
            (now, int(user_id)),
        )

        for _ in range(5):
            raw_code = _new_pairing_code()
            try:
                cursor.execute(
                    """
                    INSERT INTO velia_mobile_pairing_codes (
                        code_hash, user_id, created_at, expires_at,
                        created_user_agent, created_ip_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        _hash_secret(raw_code),
                        int(user_id),
                        now,
                        expires_at,
                        str(user_agent or "")[:500],
                        _hash_optional(ip),
                    ),
                )
                conn.commit()
                return {
                    "ok": True,
                    "pairing_code": format_pairing_code(raw_code),
                    "expires_at": expires_at.isoformat() + "Z",
                    "expires_in": ttl_seconds,
                }
            except Exception as exc:
                conn.rollback()
                if exc.__class__.__name__ not in {"UniqueViolation", "IntegrityError"}:
                    raise
        return {"ok": False, "error": "pairing_code_generation_failed"}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _session_payload(
    *,
    session_id: str,
    user_id: int,
    access_token: str,
    refresh_token: str,
    access_expires_at: datetime,
    refresh_expires_at: datetime,
    refresh_generation: int,
) -> Dict[str, Any]:
    now = _utcnow()
    return {
        "ok": True,
        "token_type": "Bearer",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_expires_in": max(0, int((access_expires_at - now).total_seconds())),
        "refresh_expires_in": max(0, int((refresh_expires_at - now).total_seconds())),
        "access_expires_at": access_expires_at.isoformat() + "Z",
        "refresh_expires_at": refresh_expires_at.isoformat() + "Z",
        "session_id": session_id,
        "user_id": int(user_id),
        "refresh_generation": int(refresh_generation),
    }


def exchange_pairing_code(
    pairing_code: str,
    *,
    device_id: str,
    device_name: str = "",
    user_agent: str = "",
    ip: str = "",
) -> Dict[str, Any]:
    normalized_code = normalize_pairing_code(pairing_code)
    if len(normalized_code) != 16:
        return {"ok": False, "error": "invalid_pairing_code"}
    normalized_device_id = str(device_id or "").strip()
    if len(normalized_device_id) < 8 or len(normalized_device_id) > 256:
        return {"ok": False, "error": "invalid_device_id"}

    access_ttl = _env_int("VELIA_MOBILE_ACCESS_TTL_SECONDS", 900, 60)
    now = _utcnow()
    access_expires_at = now + timedelta(seconds=access_ttl)
    refresh_expires_at = _resolve_refresh_expiry(now)
    raw_access_token = _new_access_token()
    raw_refresh_token = _new_refresh_token()
    session_id = str(uuid.uuid4())

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT user_id, expires_at, consumed_at
            FROM velia_mobile_pairing_codes
            WHERE code_hash=%s
            FOR UPDATE
            """,
            (_hash_secret(normalized_code),),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "invalid_pairing_code"}

        user_id = int(row[0] if not isinstance(row, dict) else row["user_id"])
        expires_at = row[1] if not isinstance(row, dict) else row["expires_at"]
        consumed_at = row[2] if not isinstance(row, dict) else row["consumed_at"]
        if consumed_at is not None:
            conn.rollback()
            return {"ok": False, "error": "pairing_code_used"}
        if expires_at is None or expires_at <= now:
            conn.rollback()
            return {"ok": False, "error": "pairing_code_expired"}

        cursor.execute(
            "UPDATE velia_mobile_pairing_codes SET consumed_at=%s WHERE code_hash=%s",
            (now, _hash_secret(normalized_code)),
        )
        cursor.execute(
            """
            INSERT INTO velia_mobile_sessions (
                session_id, user_id, access_token_hash, refresh_token_hash,
                device_id_hash, device_name, refresh_generation,
                created_at, updated_at, access_expires_at, refresh_expires_at,
                last_seen_at, user_agent, ip_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                user_id,
                _hash_secret(raw_access_token),
                _hash_secret(raw_refresh_token),
                _hash_secret(normalized_device_id),
                str(device_name or "")[:200],
                now,
                now,
                access_expires_at,
                refresh_expires_at,
                now,
                str(user_agent or "")[:500],
                _hash_optional(ip),
            ),
        )
        conn.commit()
        return _session_payload(
            session_id=session_id,
            user_id=user_id,
            access_token=raw_access_token,
            refresh_token=raw_refresh_token,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
            refresh_generation=0,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def authenticate_access_token(raw_access_token: str) -> Optional[Dict[str, Any]]:
    if not raw_access_token or not str(raw_access_token).startswith("va_"):
        return None
    now = _utcnow()
    conn = get_connection()
    cursor_factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    cursor = conn.cursor(cursor_factory=cursor_factory) if cursor_factory else conn.cursor()
    try:
        cursor.execute(
            """
            SELECT session_id, user_id, device_name, refresh_generation,
                   access_expires_at, refresh_expires_at
            FROM velia_mobile_sessions
            WHERE access_token_hash=%s
              AND revoked_at IS NULL
              AND access_expires_at>%s
              AND refresh_expires_at>%s
            LIMIT 1
            """,
            (_hash_secret(raw_access_token), now, now),
        )
        row = cursor.fetchone()
        if not row:
            return None
        session_id = row["session_id"] if isinstance(row, dict) else row[0]
        user_id = int(row["user_id"] if isinstance(row, dict) else row[1])
        device_name = row.get("device_name", "") if isinstance(row, dict) else row[2]
        generation = int(row.get("refresh_generation", 0) if isinstance(row, dict) else row[3])
        cursor.execute(
            "UPDATE velia_mobile_sessions SET last_seen_at=%s WHERE session_id=%s",
            (now, session_id),
        )
        conn.commit()
        return {
            "session_id": str(session_id),
            "user_id": user_id,
            "device_name": str(device_name or ""),
            "refresh_generation": generation,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def rotate_refresh_token(
    raw_refresh_token: str,
    *,
    device_id: str,
    user_agent: str = "",
    ip: str = "",
) -> Dict[str, Any]:
    if not raw_refresh_token or not str(raw_refresh_token).startswith("vr_"):
        return {"ok": False, "error": "invalid_refresh_token"}
    normalized_device_id = str(device_id or "").strip()
    if len(normalized_device_id) < 8 or len(normalized_device_id) > 256:
        return {"ok": False, "error": "invalid_device_id"}

    access_ttl = _env_int("VELIA_MOBILE_ACCESS_TTL_SECONDS", 900, 60)
    now = _utcnow()
    token_hash = _hash_secret(raw_refresh_token)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT session_id, user_id FROM velia_mobile_refresh_history "
            "WHERE refresh_token_hash=%s LIMIT 1",
            (token_hash,),
        )
        replay = cursor.fetchone()
        if replay:
            replay_session_id = replay[0] if not isinstance(replay, dict) else replay["session_id"]
            cursor.execute(
                "UPDATE velia_mobile_sessions SET revoked_at=%s, updated_at=%s "
                "WHERE session_id=%s AND revoked_at IS NULL",
                (now, now, replay_session_id),
            )
            conn.commit()
            return {"ok": False, "error": "refresh_token_reused", "session_revoked": True}

        cursor.execute(
            """
            SELECT session_id, user_id, device_id_hash, refresh_generation,
                   refresh_expires_at, revoked_at
            FROM velia_mobile_sessions
            WHERE refresh_token_hash=%s
            FOR UPDATE
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "invalid_refresh_token"}

        session_id = row[0] if not isinstance(row, dict) else row["session_id"]
        user_id = int(row[1] if not isinstance(row, dict) else row["user_id"])
        expected_device_hash = row[2] if not isinstance(row, dict) else row["device_id_hash"]
        generation = int(row[3] if not isinstance(row, dict) else row["refresh_generation"])
        refresh_expires_at = row[4] if not isinstance(row, dict) else row["refresh_expires_at"]
        revoked_at = row[5] if not isinstance(row, dict) else row["revoked_at"]

        if revoked_at is not None:
            conn.rollback()
            return {"ok": False, "error": "session_revoked"}
        if refresh_expires_at is None or refresh_expires_at <= now:
            cursor.execute(
                "UPDATE velia_mobile_sessions SET revoked_at=%s, updated_at=%s WHERE session_id=%s",
                (now, now, session_id),
            )
            conn.commit()
            return {"ok": False, "error": "refresh_token_expired"}
        if expected_device_hash and expected_device_hash != _hash_secret(normalized_device_id):
            cursor.execute(
                "UPDATE velia_mobile_sessions SET revoked_at=%s, updated_at=%s WHERE session_id=%s",
                (now, now, session_id),
            )
            conn.commit()
            return {"ok": False, "error": "device_mismatch", "session_revoked": True}

        new_access_token = _new_access_token()
        new_refresh_token = _new_refresh_token()
        new_generation = generation + 1
        new_access_expires_at = now + timedelta(seconds=access_ttl)
        new_refresh_expires_at = _resolve_refresh_expiry(now, refresh_expires_at)
        cursor.execute(
            """
            INSERT INTO velia_mobile_refresh_history (
                refresh_token_hash, session_id, user_id, generation,
                replaced_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (token_hash, session_id, user_id, generation, now, refresh_expires_at),
        )
        cursor.execute(
            """
            UPDATE velia_mobile_sessions
            SET access_token_hash=%s,
                refresh_token_hash=%s,
                refresh_generation=%s,
                access_expires_at=%s,
                refresh_expires_at=%s,
                updated_at=%s,
                last_seen_at=%s,
                user_agent=%s,
                ip_hash=%s
            WHERE session_id=%s
            """,
            (
                _hash_secret(new_access_token),
                _hash_secret(new_refresh_token),
                new_generation,
                new_access_expires_at,
                new_refresh_expires_at,
                now,
                now,
                str(user_agent or "")[:500],
                _hash_optional(ip),
                session_id,
            ),
        )
        conn.commit()
        return _session_payload(
            session_id=str(session_id),
            user_id=user_id,
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            access_expires_at=new_access_expires_at,
            refresh_expires_at=new_refresh_expires_at,
            refresh_generation=new_generation,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def revoke_access_token(raw_access_token: str) -> bool:
    if not raw_access_token:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = _utcnow()
        cursor.execute(
            "UPDATE velia_mobile_sessions SET revoked_at=%s, updated_at=%s "
            "WHERE access_token_hash=%s AND revoked_at IS NULL",
            (now, now, _hash_secret(raw_access_token)),
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


def revoke_all_user_sessions(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = _utcnow()
        cursor.execute(
            "UPDATE velia_mobile_sessions SET revoked_at=%s, updated_at=%s "
            "WHERE user_id=%s AND revoked_at IS NULL",
            (now, now, int(user_id)),
        )
        changed = int(cursor.rowcount or 0)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
