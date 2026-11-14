"""VELIA Guardian identity events and bounded authentication throttling.

No chat content, prompts, voice data, access tokens, raw IP addresses or cookies
are inspected or persisted here. All storage uses the existing PostgreSQL.
"""
from collections import deque
from datetime import datetime, timezone
import ipaddress
import logging
import os
import re
import threading
import time
import uuid

from db.database import get_connection

logger = logging.getLogger(__name__)

_EVENT_KINDS = {
    "web_login", "mobile_login", "refresh_replay", "device_mismatch",
    "session_revoked", "agent_access_warning",
}
_SEVERITIES = {"info", "warning", "critical"}
_AUTH_BUDGETS = {
    ("POST", "/api/auth/telegram"): (180, 60),
    ("GET", "/api/auth/google/start"): (180, 60),
    ("POST", "/mobile-api/v1/auth/exchange"): (120, 60),
    ("POST", "/mobile-api/v1/auth/refresh"): (300, 60),
    ("POST", "/mobile-api/v1/auth/qa-pairing"): (12, 60),
    ("POST", "/admin/login"): (30, 60),
}
_PROBES = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\.|$)|\.git(?:/|$)|wp-admin(?:/|$)|"
    r"wp-login\.php$|server-status$|actuator(?:/|$)|phpmyadmin(?:/|$)|"
    r"config\.json$|\.aws(?:/|$))"
)
_COUNTER_LOCK = threading.Lock()
_COUNTERS = {}
_COUNTER_LIMIT = 4096
_COUNTER_LAST_CLEAN = 0.0


def enabled():
    return os.getenv("VELIA_GUARDIAN_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }


def ensure_tables():
    """Called by the existing serialized schema bootstrap, not per request."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS velia_guardian_events (
                event_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                source TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                read_at TIMESTAMPTZ NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_velia_guardian_user_events
            ON velia_guardian_events(user_id, created_at DESC)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _safe_detail(value):
    # No raw IP, URLs, credential material or request body is accepted.
    # Only a short, human-facing device label is permitted.
    label = re.sub(r"[^\w .(),+\-]", " ", str(value or ""), flags=re.UNICODE)
    return re.sub(r"\s+", " ", label).strip()[:80]


def record(user_id, kind, *, source="web", detail="", severity="info"):
    """Best-effort auth event recording; failure cannot break a successful login."""
    if not enabled() or int(user_id or 0) <= 0 or kind not in _EVENT_KINDS:
        return False
    if severity not in _SEVERITIES:
        return False
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Anti-spam: a replayed stolen token must not flood the user's inbox.
        if kind in {"refresh_replay", "device_mismatch"}:
            cursor.execute("""
                SELECT 1 FROM velia_guardian_events
                WHERE user_id=%s AND kind=%s
                  AND created_at>NOW()-INTERVAL '15 minutes'
                LIMIT 1
            """, (int(user_id), kind))
            if cursor.fetchone():
                return False
        cursor.execute("""
            INSERT INTO velia_guardian_events
            (event_id, user_id, kind, severity, source, detail)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            str(uuid.uuid4()), int(user_id), kind, severity,
            str(source or "web")[:30], _safe_detail(detail),
        ))
        conn.commit()
        return True
    except Exception:
        if conn is not None:
            conn.rollback()
        logger.warning("VELIA_GUARDIAN_EVENT_STORE_UNAVAILABLE")
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def list_events(user_id, *, limit=50):
    count = min(100, max(1, int(limit)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT event_id, kind, severity, source, detail, created_at, read_at
            FROM velia_guardian_events WHERE user_id=%s
            ORDER BY created_at DESC, event_id DESC LIMIT %s
        """, (int(user_id), count))
        rows = cursor.fetchall()
        cursor.execute("""
            SELECT COUNT(*) FROM velia_guardian_events
            WHERE user_id=%s AND read_at IS NULL
        """, (int(user_id),))
        unread = int(cursor.fetchone()[0])
        return {
            "events": [
                {
                    "id": row[0], "kind": row[1], "severity": row[2],
                    "source": row[3], "detail": row[4],
                    "created_at": row[5].isoformat(),
                    "read": row[6] is not None,
                }
                for row in rows
            ],
            "unread": unread,
        }
    finally:
        cursor.close()
        conn.close()


def mark_read(user_id, event_id=None):
    if event_id and not re.fullmatch(r"[a-f0-9-]{36}", str(event_id)):
        raise ValueError("invalid_event_id")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if event_id:
            cursor.execute("""
                UPDATE velia_guardian_events SET read_at=NOW()
                WHERE user_id=%s AND event_id=%s AND read_at IS NULL
            """, (int(user_id), event_id))
        else:
            cursor.execute("""
                UPDATE velia_guardian_events SET read_at=NOW()
                WHERE user_id=%s AND read_at IS NULL
            """, (int(user_id),))
        count = int(cursor.rowcount or 0)
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _network_identity(request):
    # Never trust arbitrary X-Forwarded-For. Railway's direct peer is safest
    # unless a separately verified trusted-proxy integration is configured.
    raw = str(getattr(request, "remote", "") or "").strip()
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return "unknown"


def incoming_decision(request, *, timestamp=None):
    """Constant-time-ish bounded, process-local login guard. No DB/LLM calls.

    Anonymous probes have no reliable account identity, so never create a
    user-specific 'someone tried to log in' notification from these events.
    """
    if not enabled():
        return None
    path = str(request.path or "")
    if path.startswith("/mobile-api/v1/conversations/"):
        return None
    if _PROBES.search(path):
        return {"status": 404, "error": "not_found", "retry_after": 0}
    budget = _AUTH_BUDGETS.get((request.method, path))
    if budget is None:
        return None
    now = time.monotonic() if timestamp is None else float(timestamp)
    source = _network_identity(request)
    if source == "unknown":
        # Never impose a global shared budget when the peer address is missing.
        return None
    key = (source, request.method, path)
    global _COUNTER_LAST_CLEAN
    with _COUNTER_LOCK:
        if now - _COUNTER_LAST_CLEAN >= 60 or len(_COUNTERS) >= _COUNTER_LIMIT:
            stale = [k for k, entry in _COUNTERS.items() if now - entry[-1] > 120]
            for item in stale:
                _COUNTERS.pop(item, None)
            if len(_COUNTERS) >= _COUNTER_LIMIT:
                # Bounded memory and no false global lockout when exhausted.
                _COUNTERS.clear()
            _COUNTER_LAST_CLEAN = now
        hits = _COUNTERS.setdefault(key, deque())
        while hits and now - hits[0] >= budget[1]:
            hits.popleft()
        if len(hits) >= budget[0]:
            return {
                "status": 429, "error": "rate_limited",
                "retry_after": max(1, int(budget[1] - (now - hits[0]))),
            }
        hits.append(now)
    return None
