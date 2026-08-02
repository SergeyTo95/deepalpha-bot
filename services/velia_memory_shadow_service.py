import json
import logging
import os
import socket
import time
import uuid
from typing import Any, Dict, Iterable, Optional, Set
from urllib.parse import urlsplit, urlunsplit

import requests

from db.database import get_connection


logger = logging.getLogger(__name__)
_TABLES_READY = False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = int(default)
    return max(int(minimum), min(value, int(maximum)))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
    except Exception:
        value = float(default)
    return max(float(minimum), min(value, float(maximum)))


def _row_to_dict(cursor, row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _parse_user_allowlist(value: Optional[str]) -> Set[int]:
    result: Set[int] = set()
    for raw in str(value or "").replace(";", ",").split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            user_id = int(item)
        except ValueError:
            continue
        if user_id > 0:
            result.add(user_id)
    return result


def shadow_capture_enabled() -> bool:
    return _env_bool("VELIA_MEMORY_SHADOW_ENABLED", False)


def shadow_capture_enabled_for_user(user_id: int) -> bool:
    if not shadow_capture_enabled():
        return False
    if _env_bool("VELIA_MEMORY_SHADOW_ALLOW_ALL", False):
        return True
    allowlist = _parse_user_allowlist(os.getenv("VELIA_MEMORY_SHADOW_USER_IDS"))
    return int(user_id) in allowlist


def shadow_worker_poll_seconds() -> float:
    return _env_float("VELIA_MEMORY_SHADOW_POLL_SECONDS", 1.0, 0.2, 30.0)


def shadow_worker_max_attempts() -> int:
    return _env_int("VELIA_MEMORY_SHADOW_MAX_ATTEMPTS", 8, 1, 20)


def shadow_worker_lease_seconds() -> int:
    read_timeout = _env_float("VELIA_MEMORY_READ_TIMEOUT_SECONDS", 8.0, 1.0, 60.0)
    return max(30, int(read_timeout) + 30)


def _normalized_message(value: Any) -> str:
    text = str(value or "").strip()
    maximum = _env_int("VELIA_MEMORY_MAX_MESSAGE_CHARS", 50000, 1000, 200000)
    if len(text) <= maximum:
        return text
    suffix = "\n\n[content truncated by Velyon Memory shadow capture]"
    return text[: max(0, maximum - len(suffix))].rstrip() + suffix


def ensure_velia_memory_shadow_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_memory_shadow_outbox (
                event_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                conversation_id TEXT NOT NULL,
                user_message_id TEXT NOT NULL,
                assistant_message_id TEXT NOT NULL,
                user_content TEXT NOT NULL,
                assistant_content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TIMESTAMP NOT NULL DEFAULT NOW(),
                locked_by TEXT,
                lease_until TIMESTAMP,
                response_status INTEGER,
                remote_trace_id TEXT,
                last_error TEXT,
                delivered_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (assistant_message_id),
                CHECK (status IN ('pending', 'retrying', 'delivering', 'succeeded', 'failed'))
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_velia_memory_shadow_ready
            ON velia_memory_shadow_outbox(status, next_attempt_at, created_at)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_velia_memory_shadow_user_created
            ON velia_memory_shadow_outbox(user_id, created_at DESC)
            """
        )
        conn.commit()
        _TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def enqueue_completed_turn(
    *,
    user_id: int,
    conversation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    user_content: str,
    assistant_content: str,
) -> Dict[str, Any]:
    if not shadow_capture_enabled_for_user(int(user_id)):
        return {"queued": False, "reason": "shadow_disabled"}

    normalized_user = _normalized_message(user_content)
    normalized_assistant = _normalized_message(assistant_content)
    if not normalized_user or not normalized_assistant:
        return {"queued": False, "reason": "empty_turn"}

    clean_conversation_id = str(conversation_id or "").strip()
    clean_user_message_id = str(user_message_id or "").strip()
    clean_assistant_message_id = str(assistant_message_id or "").strip()
    if not clean_conversation_id or not clean_user_message_id or not clean_assistant_message_id:
        return {"queued": False, "reason": "missing_identity"}

    ensure_velia_memory_shadow_tables()
    event_id = f"vmem_{uuid.uuid4().hex}"
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_memory_shadow_outbox (
                event_id, user_id, conversation_id,
                user_message_id, assistant_message_id,
                user_content, assistant_content
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (assistant_message_id) DO NOTHING
            RETURNING event_id
            """,
            (
                event_id,
                int(user_id),
                clean_conversation_id,
                clean_user_message_id,
                clean_assistant_message_id,
                normalized_user,
                normalized_assistant,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        if not row:
            return {"queued": False, "reason": "duplicate"}
        actual_event_id = row.get("event_id") if isinstance(row, dict) else row[0]
        logger.info(
            "VELIA_MEMORY_SHADOW_ENQUEUED event_id=%s user_id=%s conversation_id=%s",
            str(actual_event_id),
            int(user_id),
            clean_conversation_id,
        )
        return {"queued": True, "event_id": str(actual_event_id)}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def recover_stale_shadow_events() -> int:
    ensure_velia_memory_shadow_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_memory_shadow_outbox
            SET status='retrying', next_attempt_at=NOW(), locked_by=NULL,
                lease_until=NULL, last_error='memory_worker_recovered', updated_at=NOW()
            WHERE status='delivering' AND (lease_until IS NULL OR lease_until < NOW())
            """
        )
        count = int(cursor.rowcount or 0)
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def claim_next_shadow_event(worker_id: str) -> Optional[Dict[str, Any]]:
    ensure_velia_memory_shadow_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT event_id
            FROM velia_memory_shadow_outbox
            WHERE status IN ('pending', 'retrying')
              AND next_attempt_at <= NOW()
            ORDER BY next_attempt_at ASC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return None
        event_id = row.get("event_id") if isinstance(row, dict) else row[0]
        cursor.execute(
            """
            UPDATE velia_memory_shadow_outbox
            SET status='delivering', attempt_count=attempt_count+1,
                locked_by=%s,
                lease_until=NOW() + make_interval(secs => %s),
                updated_at=NOW()
            WHERE event_id=%s AND status IN ('pending', 'retrying')
            RETURNING *
            """,
            (str(worker_id)[:120], shadow_worker_lease_seconds(), str(event_id)),
        )
        event = _row_to_dict(cursor, cursor.fetchone())
        conn.commit()
        return event
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def retry_delay_seconds(attempt_count: int) -> int:
    schedule = [0, 5, 30, 120, 600, 1800, 7200, 21600, 43200]
    index = max(0, min(int(attempt_count), len(schedule) - 1))
    return schedule[index]


def _memory_endpoint() -> str:
    raw = str(os.getenv("VELIA_MEMORY_ENDPOINT") or "").strip()
    if not raw or len(raw) > 1000:
        raise ValueError("memory_endpoint_not_configured")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_memory_endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid_memory_endpoint")
    path = (parsed.path or "").rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def _memory_api_key() -> str:
    value = str(os.getenv("VELIA_MEMORY_API_KEY") or "").strip()
    if len(value) < 16:
        raise ValueError("memory_api_key_not_configured")
    return value


def _memory_service_id() -> str:
    value = str(os.getenv("VELIA_MEMORY_SERVICE_ID") or "velia").strip()
    if not value or len(value) > 120:
        raise ValueError("invalid_memory_service_id")
    return value


def _memory_team_id() -> str:
    value = str(os.getenv("VELIA_MEMORY_TEAM_ID") or "velia").strip()
    if not value or len(value) > 120:
        raise ValueError("invalid_memory_team_id")
    return value


def _memory_agent_id() -> str:
    value = str(os.getenv("VELIA_MEMORY_AGENT_ID") or "velia-main").strip()
    if not value or len(value) > 120:
        raise ValueError("invalid_memory_agent_id")
    return value


def build_shadow_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    user_id = int(event.get("user_id") or 0)
    conversation_id = str(event.get("conversation_id") or "").strip()
    user_content = str(event.get("user_content") or "").strip()
    assistant_content = str(event.get("assistant_content") or "").strip()
    if user_id <= 0 or not conversation_id or not user_content or not assistant_content:
        raise ValueError("invalid_memory_event")
    return {
        "team_id": _memory_team_id(),
        "agent_id": _memory_agent_id(),
        "user_id": str(user_id),
        "session_id": conversation_id,
        "messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ],
    }


def send_shadow_event(event: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = _memory_endpoint()
    payload = build_shadow_payload(event)
    url = f"{endpoint}/v3/conversation/add"
    headers = {
        "Authorization": f"Bearer {_memory_api_key()}",
        "x-tdai-service-id": _memory_service_id(),
        "Content-Type": "application/json",
        "User-Agent": "Velyon-Memory-Shadow/1.0",
        "X-Velyon-Memory-Event": str(event.get("event_id") or "")[:120],
    }
    connect_timeout = _env_float("VELIA_MEMORY_CONNECT_TIMEOUT_SECONDS", 2.0, 0.5, 20.0)
    read_timeout = _env_float("VELIA_MEMORY_READ_TIMEOUT_SECONDS", 8.0, 1.0, 60.0)
    verify_tls = _env_bool("VELIA_MEMORY_TLS_VERIFY", True)
    started = time.monotonic()
    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=(connect_timeout, read_timeout),
            verify=verify_tls,
        )
    except requests.RequestException as exc:
        return {
            "success": False,
            "retryable": True,
            "response_status": None,
            "remote_trace_id": "",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error": f"memory_transport_{exc.__class__.__name__}"[:240],
        }

    status = int(response.status_code)
    trace_id = str(
        response.headers.get("x-trace-id")
        or response.headers.get("x-qcloud-transaction-id")
        or ""
    )[:240]
    retryable = status >= 500 or status in {408, 425, 429}
    error = None
    success = 200 <= status < 300
    if success:
        try:
            envelope = response.json()
        except ValueError:
            success = False
            retryable = True
            error = "memory_invalid_json"
        else:
            if not isinstance(envelope, dict) or envelope.get("code") != 0:
                success = False
                code = envelope.get("code") if isinstance(envelope, dict) else "unknown"
                error = f"memory_remote_code_{code}"[:240]
                retryable = False
    else:
        error = f"memory_http_{status}"

    return {
        "success": success,
        "retryable": retryable,
        "response_status": status,
        "remote_trace_id": trace_id,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "error": error,
    }


def record_shadow_result(event: Dict[str, Any], result: Dict[str, Any]) -> None:
    event_id = str(event.get("event_id") or "")
    attempt_count = int(event.get("attempt_count") or 0)
    success = bool(result.get("success"))
    retryable = bool(result.get("retryable"))
    terminal = not retryable or attempt_count >= shadow_worker_max_attempts()
    delay = retry_delay_seconds(attempt_count)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if success:
            cursor.execute(
                """
                UPDATE velia_memory_shadow_outbox
                SET status='succeeded', response_status=%s, remote_trace_id=%s,
                    last_error=NULL, delivered_at=NOW(), locked_by=NULL,
                    lease_until=NULL, updated_at=NOW()
                WHERE event_id=%s
                """,
                (
                    result.get("response_status"),
                    str(result.get("remote_trace_id") or "")[:240] or None,
                    event_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE velia_memory_shadow_outbox
                SET status=%s,
                    next_attempt_at=CASE WHEN %s THEN next_attempt_at
                                         ELSE NOW() + make_interval(secs => %s) END,
                    response_status=%s, remote_trace_id=%s, last_error=%s,
                    locked_by=NULL, lease_until=NULL, updated_at=NOW()
                WHERE event_id=%s
                """,
                (
                    "failed" if terminal else "retrying",
                    terminal,
                    delay,
                    result.get("response_status"),
                    str(result.get("remote_trace_id") or "")[:240] or None,
                    str(result.get("error") or "memory_delivery_failed")[:500],
                    event_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def process_shadow_event(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = send_shadow_event(event)
    except ValueError as exc:
        result = {
            "success": False,
            "retryable": False,
            "response_status": None,
            "remote_trace_id": "",
            "duration_ms": 0,
            "error": str(exc)[:240],
        }
    except Exception as exc:
        logger.exception(
            "VELIA_MEMORY_SHADOW_DELIVERY_EXCEPTION event_id=%s",
            str(event.get("event_id") or ""),
        )
        result = {
            "success": False,
            "retryable": True,
            "response_status": None,
            "remote_trace_id": "",
            "duration_ms": 0,
            "error": f"memory_delivery_{exc.__class__.__name__}"[:240],
        }

    record_shadow_result(event, result)
    logger.info(
        "VELIA_MEMORY_SHADOW_DELIVERY event_id=%s user_id=%s success=%s status=%s attempt=%s duration_ms=%s error=%s",
        str(event.get("event_id") or ""),
        int(event.get("user_id") or 0),
        bool(result.get("success")),
        result.get("response_status"),
        int(event.get("attempt_count") or 0),
        int(result.get("duration_ms") or 0),
        str(result.get("error") or "")[:120],
    )
    return result


def shadow_queue_snapshot() -> Dict[str, int]:
    ensure_velia_memory_shadow_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT status, COUNT(*)
            FROM velia_memory_shadow_outbox
            GROUP BY status
            """
        )
        result = {"pending": 0, "retrying": 0, "delivering": 0, "succeeded": 0, "failed": 0}
        for row in cursor.fetchall() or []:
            if isinstance(row, dict):
                status = str(row.get("status") or "")
                count = int(row.get("count") or 0)
            else:
                status = str(row[0] or "")
                count = int(row[1] or 0)
            if status in result:
                result[status] = count
        return result
    finally:
        cursor.close()
        conn.close()


def run_shadow_worker_forever(worker_id: Optional[str] = None) -> None:
    ensure_velia_memory_shadow_tables()
    resolved_worker_id = str(
        worker_id or f"velia-memory-shadow:{socket.gethostname()}:{os.getpid()}"
    )[:120]
    recovered = recover_stale_shadow_events()
    logger.info(
        "VELIA_MEMORY_SHADOW_WORKER_STARTED worker_id=%s recovered=%s",
        resolved_worker_id,
        recovered,
    )
    last_snapshot = 0.0
    while True:
        event = claim_next_shadow_event(resolved_worker_id)
        if event:
            process_shadow_event(event)
            continue
        now = time.monotonic()
        if now - last_snapshot >= 300:
            logger.info(
                "VELIA_MEMORY_SHADOW_QUEUE worker_id=%s snapshot=%s",
                resolved_worker_id,
                json.dumps(shadow_queue_snapshot(), sort_keys=True),
            )
            last_snapshot = now
        time.sleep(shadow_worker_poll_seconds())
