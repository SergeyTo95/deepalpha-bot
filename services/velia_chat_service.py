import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection
from services.velia_llm_service import generate_velia_chat_result, public_generation_metadata


_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_DEFAULT_TITLE_VALUES = {"new chat", "новый чат", "new conversation", "новый диалог"}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int = 0, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return max(minimum, default)


def _dict_cursor(conn):
    cursor_factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=cursor_factory) if cursor_factory else conn.cursor()


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def _allowed_beta_user_ids() -> set[int]:
    result: set[int] = set()
    for part in str(os.getenv("VELIA_CHAT_BETA_USER_IDS", "") or "").split(","):
        try:
            result.add(int(part.strip()))
        except (TypeError, ValueError):
            continue
    return result


def is_velia_chat_enabled_for_user(user_id: int) -> bool:
    if not _env_bool("VELIA_CHAT_ENABLED", False):
        return False
    allowlist = _allowed_beta_user_ids()
    return not allowlist or int(user_id) in allowlist


def is_debug_usage_enabled_for_user(user_id: int) -> bool:
    if not _env_bool("VELIA_MOBILE_DEBUG_USAGE", False):
        return False
    raw = str(os.getenv("VELIA_MOBILE_DEBUG_USER_IDS", "") or "").strip()
    if not raw:
        return True
    allowed = set()
    for part in raw.split(","):
        try:
            allowed.add(int(part.strip()))
        except (TypeError, ValueError):
            continue
    return int(user_id) in allowed


def ensure_velia_chat_tables() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_conversations (
                conversation_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                title TEXT NOT NULL,
                title_source TEXT NOT NULL DEFAULT 'default',
                is_pinned BOOLEAN NOT NULL DEFAULT FALSE,
                is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMP NULL
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_conversations_user_updated "
            "ON velia_conversations(user_id, updated_at DESC)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES velia_conversations(conversation_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'completed',
                idempotency_key TEXT NULL,
                reply_to_message_id TEXT NULL,
                request_id TEXT NULL,
                provider TEXT NULL,
                model TEXT NULL,
                prompt_tokens INTEGER NULL,
                completion_tokens INTEGER NULL,
                total_tokens INTEGER NULL,
                cached_input_tokens INTEGER NULL,
                reasoning_tokens INTEGER NULL,
                estimated_cost_usd NUMERIC(18, 8) NULL,
                latency_ms INTEGER NULL,
                finish_reason TEXT NULL,
                error_code TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMP NULL,
                CHECK (role IN ('user', 'assistant', 'system')),
                CHECK (status IN ('pending', 'completed', 'error'))
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_messages_conversation_created "
            "ON velia_messages(conversation_id, created_at ASC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_messages_user_created "
            "ON velia_messages(user_id, created_at DESC)"
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_messages_idempotency
            ON velia_messages(conversation_id, idempotency_key)
            WHERE role='user' AND idempotency_key IS NOT NULL
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def generate_conversation_title(message: str, fallback: str = "New chat") -> str:
    normalized = re.sub(r"\s+", " ", str(message or "").strip())
    if not normalized:
        return fallback
    words = normalized.split(" ")[:6]
    title = " ".join(words).strip(" .,!?:;—–-")
    if not title:
        return fallback
    title = title[:80].rstrip()
    return title[:1].upper() + title[1:]


def _serialize_conversation(row: Any) -> Dict[str, Any]:
    return {
        "id": str(_row_value(row, "conversation_id", 0, "")),
        "title": str(_row_value(row, "title", 2, "")),
        "title_source": str(_row_value(row, "title_source", 3, "default")),
        "is_pinned": bool(_row_value(row, "is_pinned", 4, False)),
        "is_archived": bool(_row_value(row, "is_archived", 5, False)),
        "created_at": _iso(_row_value(row, "created_at", 6)),
        "updated_at": _iso(_row_value(row, "updated_at", 7)),
    }


def _serialize_message(row: Any, *, debug_usage: bool = False) -> Dict[str, Any]:
    result = {
        "id": str(_row_value(row, "message_id", 0, "")),
        "conversation_id": str(_row_value(row, "conversation_id", 1, "")),
        "role": str(_row_value(row, "role", 3, "")),
        "content": str(_row_value(row, "content", 4, "")),
        "status": str(_row_value(row, "status", 5, "completed")),
        "reply_to_message_id": _row_value(row, "reply_to_message_id", 7),
        "request_id": _row_value(row, "request_id", 8),
        "error_code": _row_value(row, "error_code", 18),
        "created_at": _iso(_row_value(row, "created_at", 19)),
        "updated_at": _iso(_row_value(row, "updated_at", 20)),
    }
    if debug_usage and result["role"] == "assistant":
        result["usage"] = {
            "prompt_tokens": int(_row_value(row, "prompt_tokens", 11, 0) or 0),
            "completion_tokens": int(_row_value(row, "completion_tokens", 12, 0) or 0),
            "total_tokens": int(_row_value(row, "total_tokens", 13, 0) or 0),
            "cached_input_tokens": int(_row_value(row, "cached_input_tokens", 14, 0) or 0),
            "reasoning_tokens": int(_row_value(row, "reasoning_tokens", 15, 0) or 0),
            "estimated_cost_usd": float(_row_value(row, "estimated_cost_usd", 16, 0.0) or 0.0),
            "latency_ms": int(_row_value(row, "latency_ms", 17, 0) or 0),
        }
    return result


def create_conversation(user_id: int, title: str = "") -> Dict[str, Any]:
    now = _utcnow()
    conversation_id = str(uuid.uuid4())
    normalized_title = re.sub(r"\s+", " ", str(title or "").strip())[:120]
    if not normalized_title:
        normalized_title = "New chat"
    title_source = "manual" if title else "default"
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            INSERT INTO velia_conversations (
                conversation_id, user_id, title, title_source,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING conversation_id, user_id, title, title_source,
                      is_pinned, is_archived, created_at, updated_at
            """,
            (conversation_id, int(user_id), normalized_title, title_source, now, now),
        )
        row = cursor.fetchone()
        conn.commit()
        return _serialize_conversation(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_conversations(
    user_id: int,
    *,
    include_archived: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    limit = min(100, max(1, int(limit or 50)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        archived_clause = "" if include_archived else "AND is_archived=FALSE"
        cursor.execute(
            f"""
            SELECT conversation_id, user_id, title, title_source,
                   is_pinned, is_archived, created_at, updated_at
            FROM velia_conversations
            WHERE user_id=%s AND deleted_at IS NULL {archived_clause}
            ORDER BY is_pinned DESC, updated_at DESC
            LIMIT %s
            """,
            (int(user_id), limit),
        )
        return [_serialize_conversation(row) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def get_conversation(user_id: int, conversation_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT conversation_id, user_id, title, title_source,
                   is_pinned, is_archived, created_at, updated_at
            FROM velia_conversations
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            LIMIT 1
            """,
            (str(conversation_id), int(user_id)),
        )
        row = cursor.fetchone()
        return _serialize_conversation(row) if row else None
    finally:
        cursor.close()
        conn.close()


def update_conversation(
    user_id: int,
    conversation_id: str,
    *,
    title: Optional[str] = None,
    is_pinned: Optional[bool] = None,
    is_archived: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    fields: List[str] = []
    values: List[Any] = []
    if title is not None:
        normalized = re.sub(r"\s+", " ", str(title).strip())[:120]
        if not normalized:
            raise ValueError("invalid_title")
        fields.extend(["title=%s", "title_source='manual'"])
        values.append(normalized)
    if is_pinned is not None:
        fields.append("is_pinned=%s")
        values.append(bool(is_pinned))
    if is_archived is not None:
        fields.append("is_archived=%s")
        values.append(bool(is_archived))
    if not fields:
        return get_conversation(user_id, conversation_id)
    fields.append("updated_at=%s")
    values.append(_utcnow())
    values.extend([str(conversation_id), int(user_id)])

    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"""
            UPDATE velia_conversations
            SET {', '.join(fields)}
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            RETURNING conversation_id, user_id, title, title_source,
                      is_pinned, is_archived, created_at, updated_at
            """,
            tuple(values),
        )
        row = cursor.fetchone()
        conn.commit()
        return _serialize_conversation(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def delete_conversation(user_id: int, conversation_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = _utcnow()
        cursor.execute(
            """
            UPDATE velia_conversations
            SET deleted_at=%s, updated_at=%s
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            """,
            (now, now, str(conversation_id), int(user_id)),
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


def list_messages(
    user_id: int,
    conversation_id: str,
    *,
    limit: int = 100,
) -> Optional[List[Dict[str, Any]]]:
    limit = min(200, max(1, int(limit or 100)))
    debug_usage = is_debug_usage_enabled_for_user(user_id)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT 1 FROM velia_conversations "
            "WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL",
            (str(conversation_id), int(user_id)),
        )
        if not cursor.fetchone():
            return None
        cursor.execute(
            """
            SELECT message_id, conversation_id, user_id, role, content, status,
                   idempotency_key, reply_to_message_id, request_id, provider, model,
                   prompt_tokens, completion_tokens, total_tokens,
                   cached_input_tokens, reasoning_tokens, estimated_cost_usd,
                   latency_ms, error_code, created_at, updated_at
            FROM velia_messages
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (str(conversation_id), int(user_id), limit),
        )
        return [
            _serialize_message(row, debug_usage=debug_usage)
            for row in cursor.fetchall() or []
        ]
    finally:
        cursor.close()
        conn.close()


def _daily_usage_snapshot(user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT COALESCE(SUM(estimated_cost_usd), 0), COUNT(*)
            FROM velia_messages
            WHERE role='assistant' AND status='completed'
              AND created_at>=CURRENT_DATE
            """
        )
        global_row = cursor.fetchone() or (0, 0)
        cursor.execute(
            """
            SELECT COALESCE(SUM(estimated_cost_usd), 0), COUNT(*)
            FROM velia_messages
            WHERE user_id=%s AND role='assistant' AND status='completed'
              AND created_at>=CURRENT_DATE
            """,
            (int(user_id),),
        )
        user_row = cursor.fetchone() or (0, 0)
        return {
            "global_cost_usd": float(_row_value(global_row, "cost", 0, 0.0) or 0.0),
            "global_messages": int(_row_value(global_row, "count", 1, 0) or 0),
            "user_cost_usd": float(_row_value(user_row, "cost", 0, 0.0) or 0.0),
            "user_messages": int(_row_value(user_row, "count", 1, 0) or 0),
        }
    finally:
        cursor.close()
        conn.close()


def get_usage_summary(user_id: int) -> Dict[str, Any]:
    snapshot = _daily_usage_snapshot(user_id)
    return {
        **snapshot,
        "per_user_daily_cost_limit_usd": _env_float(
            "VELIA_CHAT_PER_USER_DAILY_COST_USD_LIMIT", 2.0
        ),
        "global_daily_cost_limit_usd": _env_float(
            "VELIA_CHAT_DAILY_COST_USD_LIMIT", 10.0
        ),
        "per_user_daily_message_limit": _env_int(
            "VELIA_CHAT_MAX_MESSAGES_PER_USER_DAY", 100, 1, 10000
        ),
    }


def _budget_error(user_id: int) -> Optional[str]:
    snapshot = _daily_usage_snapshot(user_id)
    per_user_limit = _env_float("VELIA_CHAT_PER_USER_DAILY_COST_USD_LIMIT", 2.0)
    global_limit = _env_float("VELIA_CHAT_DAILY_COST_USD_LIMIT", 10.0)
    reserve = _env_float("VELIA_CHAT_REQUEST_COST_RESERVE_USD", 0.25)
    message_limit = _env_int("VELIA_CHAT_MAX_MESSAGES_PER_USER_DAY", 100, 1, 10000)
    if snapshot["user_messages"] >= message_limit:
        return "daily_message_limit_exceeded"
    if per_user_limit > 0 and snapshot["user_cost_usd"] + reserve > per_user_limit:
        return "daily_user_cost_limit_exceeded"
    if global_limit > 0 and snapshot["global_cost_usd"] + reserve > global_limit:
        return "daily_global_cost_limit_exceeded"
    return None


def _build_prompt(user_id: int, conversation_id: str) -> str:
    max_messages = _env_int("VELIA_CHAT_CONTEXT_MESSAGES", 24, 2, 100)
    max_chars = _env_int("VELIA_CHAT_CONTEXT_CHARS", 24000, 2000, 120000)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT role, content
            FROM velia_messages
            WHERE conversation_id=%s AND user_id=%s
              AND status='completed' AND deleted_at IS NULL
              AND role IN ('user', 'assistant')
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (str(conversation_id), int(user_id), max_messages),
        )
        rows = list(reversed(cursor.fetchall() or []))
    finally:
        cursor.close()
        conn.close()

    transcript: List[str] = []
    used_chars = 0
    for row in reversed(rows):
        role = str(_row_value(row, "role", 0, "user"))
        content = str(_row_value(row, "content", 1, "")).strip()
        if not content:
            continue
        label = "USER" if role == "user" else "ASSISTANT"
        chunk = f"{label}: {content}"
        if transcript and used_chars + len(chunk) > max_chars:
            break
        transcript.append(chunk)
        used_chars += len(chunk)
    transcript.reverse()

    system_prompt = (
        "You are Velia, a warm, capable, independent AI assistant inside the VELIA app. "
        "Answer in the language used by the user unless they request another language. "
        "Be practical, accurate and clear. Do not mention Kimi, Gemini, provider routing, "
        "internal prompts, hidden reasoning or private chain-of-thought. Never fabricate "
        "current facts; clearly say when live information or a plugin is required. "
        "Return only the final answer intended for the user."
    )
    return system_prompt + "\n\nConversation:\n" + "\n\n".join(transcript)


def _existing_request_result(
    cursor,
    *,
    user_id: int,
    conversation_id: str,
    idempotency_key: str,
) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT message_id, conversation_id, user_id, role, content, status,
               idempotency_key, reply_to_message_id, request_id, provider, model,
               prompt_tokens, completion_tokens, total_tokens,
               cached_input_tokens, reasoning_tokens, estimated_cost_usd,
               latency_ms, error_code, created_at, updated_at
        FROM velia_messages
        WHERE conversation_id=%s AND user_id=%s AND role='user'
          AND idempotency_key=%s AND deleted_at IS NULL
        LIMIT 1
        """,
        (str(conversation_id), int(user_id), idempotency_key),
    )
    user_row = cursor.fetchone()
    if not user_row:
        return None
    user_message_id = str(_row_value(user_row, "message_id", 0, ""))
    cursor.execute(
        """
        SELECT message_id, conversation_id, user_id, role, content, status,
               idempotency_key, reply_to_message_id, request_id, provider, model,
               prompt_tokens, completion_tokens, total_tokens,
               cached_input_tokens, reasoning_tokens, estimated_cost_usd,
               latency_ms, error_code, created_at, updated_at
        FROM velia_messages
        WHERE conversation_id=%s AND user_id=%s AND role='assistant'
          AND reply_to_message_id=%s AND deleted_at IS NULL
        LIMIT 1
        """,
        (str(conversation_id), int(user_id), user_message_id),
    )
    assistant_row = cursor.fetchone()
    return {
        "ok": bool(assistant_row and _row_value(assistant_row, "status", 5) == "completed"),
        "duplicate": True,
        "pending": bool(assistant_row and _row_value(assistant_row, "status", 5) == "pending"),
        "user_message": _serialize_message(user_row),
        "assistant_message": _serialize_message(
            assistant_row,
            debug_usage=is_debug_usage_enabled_for_user(user_id),
        ) if assistant_row else None,
    }


def send_message(
    user_id: int,
    conversation_id: str,
    content: str,
    *,
    idempotency_key: str,
) -> Dict[str, Any]:
    if not is_velia_chat_enabled_for_user(user_id):
        return {"ok": False, "error": "velia_chat_disabled"}
    normalized_content = str(content or "").strip()
    max_input_chars = _env_int("VELIA_CHAT_MAX_INPUT_CHARS", 12000, 100, 50000)
    if not normalized_content:
        return {"ok": False, "error": "empty_message"}
    if len(normalized_content) > max_input_chars:
        return {"ok": False, "error": "message_too_long", "max_chars": max_input_chars}
    if not _IDEMPOTENCY_RE.match(str(idempotency_key or "")):
        return {"ok": False, "error": "invalid_idempotency_key"}

    budget_error = _budget_error(user_id)
    if budget_error:
        return {"ok": False, "error": budget_error}

    now = _utcnow()
    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT conversation_id, title, title_source
            FROM velia_conversations
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            FOR UPDATE
            """,
            (str(conversation_id), int(user_id)),
        )
        conversation = cursor.fetchone()
        if not conversation:
            conn.rollback()
            return {"ok": False, "error": "conversation_not_found"}

        existing = _existing_request_result(
            cursor,
            user_id=user_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            conn.rollback()
            return existing

        cursor.execute(
            """
            SELECT 1 FROM velia_messages
            WHERE user_id=%s AND role='assistant' AND status='pending'
              AND deleted_at IS NULL
            LIMIT 1
            """,
            (int(user_id),),
        )
        if cursor.fetchone():
            conn.rollback()
            return {"ok": False, "error": "generation_in_progress"}

        cursor.execute(
            """
            INSERT INTO velia_messages (
                message_id, conversation_id, user_id, role, content, status,
                idempotency_key, request_id, created_at, updated_at
            ) VALUES (%s, %s, %s, 'user', %s, 'completed', %s, %s, %s, %s)
            """,
            (
                user_message_id,
                str(conversation_id),
                int(user_id),
                normalized_content,
                idempotency_key,
                request_id,
                now,
                now,
            ),
        )
        cursor.execute(
            """
            INSERT INTO velia_messages (
                message_id, conversation_id, user_id, role, content, status,
                reply_to_message_id, request_id, created_at, updated_at
            ) VALUES (%s, %s, %s, 'assistant', '', 'pending', %s, %s, %s, %s)
            """,
            (
                assistant_message_id,
                str(conversation_id),
                int(user_id),
                user_message_id,
                request_id,
                now,
                now,
            ),
        )

        current_title = str(_row_value(conversation, "title", 1, ""))
        title_source = str(_row_value(conversation, "title_source", 2, "default"))
        if title_source == "default" or current_title.strip().lower() in _DEFAULT_TITLE_VALUES:
            cursor.execute(
                """
                UPDATE velia_conversations
                SET title=%s, title_source='auto', updated_at=%s
                WHERE conversation_id=%s AND user_id=%s
                """,
                (
                    generate_conversation_title(normalized_content),
                    now,
                    str(conversation_id),
                    int(user_id),
                ),
            )
        else:
            cursor.execute(
                "UPDATE velia_conversations SET updated_at=%s "
                "WHERE conversation_id=%s AND user_id=%s",
                (now, str(conversation_id), int(user_id)),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
            retry_cursor = _dict_cursor(conn)
            try:
                existing = _existing_request_result(
                    retry_cursor,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    idempotency_key=idempotency_key,
                )
                if existing:
                    return existing
            finally:
                retry_cursor.close()
        raise
    finally:
        cursor.close()
        conn.close()

    prompt = _build_prompt(user_id, conversation_id)
    started = time.monotonic()
    try:
        generation = generate_velia_chat_result(
            prompt,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=request_id,
        )
    except Exception:
        generation = {
            "ok": False,
            "text": "",
            "reason": "generation_exception",
            "request_id": request_id,
        }
    latency_ms = int((time.monotonic() - started) * 1000)
    text = str(generation.get("text") or "").strip()
    usage = generation.get("usage") if isinstance(generation.get("usage"), dict) else {}

    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        if text:
            cursor.execute(
                """
                UPDATE velia_messages
                SET content=%s, status='completed', provider=%s, model=%s,
                    prompt_tokens=%s, completion_tokens=%s, total_tokens=%s,
                    cached_input_tokens=%s, reasoning_tokens=%s,
                    estimated_cost_usd=%s, latency_ms=%s, finish_reason=%s,
                    error_code=NULL, updated_at=%s
                WHERE message_id=%s AND user_id=%s AND status='pending'
                RETURNING message_id, conversation_id, user_id, role, content, status,
                          idempotency_key, reply_to_message_id, request_id, provider, model,
                          prompt_tokens, completion_tokens, total_tokens,
                          cached_input_tokens, reasoning_tokens, estimated_cost_usd,
                          latency_ms, error_code, created_at, updated_at
                """,
                (
                    text,
                    str(generation.get("provider") or ""),
                    str(generation.get("model") or ""),
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                    usage.get("cached_input_tokens"),
                    usage.get("reasoning_tokens"),
                    float(generation.get("estimated_cost_usd") or 0.0),
                    latency_ms,
                    str(generation.get("finish_reason") or ""),
                    _utcnow(),
                    assistant_message_id,
                    int(user_id),
                ),
            )
            assistant_row = cursor.fetchone()
            conn.commit()
            return {
                "ok": True,
                "duplicate": False,
                "pending": False,
                "assistant_message": _serialize_message(
                    assistant_row,
                    debug_usage=is_debug_usage_enabled_for_user(user_id),
                ),
                "generation": public_generation_metadata(
                    generation,
                    debug_usage=is_debug_usage_enabled_for_user(user_id),
                ),
            }

        error_code = str(generation.get("reason") or "generation_failed")[:120]
        cursor.execute(
            """
            UPDATE velia_messages
            SET status='error', error_code=%s, latency_ms=%s,
                estimated_cost_usd=%s, updated_at=%s
            WHERE message_id=%s AND user_id=%s AND status='pending'
            RETURNING message_id, conversation_id, user_id, role, content, status,
                      idempotency_key, reply_to_message_id, request_id, provider, model,
                      prompt_tokens, completion_tokens, total_tokens,
                      cached_input_tokens, reasoning_tokens, estimated_cost_usd,
                      latency_ms, error_code, created_at, updated_at
            """,
            (
                error_code,
                latency_ms,
                float(generation.get("estimated_cost_usd") or 0.0),
                _utcnow(),
                assistant_message_id,
                int(user_id),
            ),
        )
        assistant_row = cursor.fetchone()
        conn.commit()
        return {
            "ok": False,
            "error": error_code,
            "assistant_message": _serialize_message(
                assistant_row,
                debug_usage=is_debug_usage_enabled_for_user(user_id),
            ),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
