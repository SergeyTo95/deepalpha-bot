import hashlib
import json
import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection


_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_MAX_REORDER_CONVERSATIONS = 100
_MAX_MERGE_SOURCES = 5
_MAX_MERGED_MESSAGES = 500
_MAX_SHARE_MESSAGES = 500
_MAX_SHARE_CHARACTERS = 300_000
_DEFAULT_SHARE_TTL_DAYS = 90
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,160}$")


class ConversationUxError(Exception):
    def __init__(self, code: str, *, status: int = 400):
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _dict_cursor(conn):
    cursor_factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=cursor_factory) if cursor_factory else conn.cursor()


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
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


def _normalize_ids(raw_ids: Sequence[Any], *, minimum: int, maximum: int) -> List[str]:
    result: List[str] = []
    seen = set()
    for raw in raw_ids or []:
        value = str(raw or "").strip()
        if not value or len(value) > 120:
            raise ConversationUxError("invalid_conversation_id")
        if value in seen:
            raise ConversationUxError("duplicate_conversation_id")
        seen.add(value)
        result.append(value)
    if len(result) < minimum:
        raise ConversationUxError("not_enough_conversations")
    if len(result) > maximum:
        raise ConversationUxError("too_many_conversations")
    return result


def ensure_velia_conversation_ux_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_conversation_order (
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, conversation_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_conversation_order_user_position "
                "ON velia_conversation_order(user_id, position ASC)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_conversation_merges (
                    merge_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    target_conversation_id TEXT NOT NULL,
                    source_conversation_ids JSONB NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_conversation_merges_user_created "
                "ON velia_conversation_merges(user_id, created_at DESC)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_conversation_shares (
                    share_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    snapshot_json JSONB NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMP NULL,
                    revoked_at TIMESTAMP NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_conversation_shares_owner_created "
                "ON velia_conversation_shares(user_id, created_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def list_conversations_ordered(
    user_id: int,
    *,
    include_archived: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    ensure_velia_conversation_ux_tables()
    maximum = min(_MAX_REORDER_CONVERSATIONS, max(1, int(limit or 50)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        archived_clause = "" if include_archived else "AND c.is_archived=FALSE"
        cursor.execute(
            f"""
            SELECT c.conversation_id, c.user_id, c.title, c.title_source,
                   c.is_pinned, c.is_archived, c.created_at, c.updated_at,
                   o.position
            FROM velia_conversations AS c
            LEFT JOIN velia_conversation_order AS o
              ON o.user_id=c.user_id AND o.conversation_id=c.conversation_id
            WHERE c.user_id=%s AND c.deleted_at IS NULL {archived_clause}
            ORDER BY
              (o.position IS NULL) DESC,
              CASE WHEN o.position IS NULL THEN c.is_pinned ELSE FALSE END DESC,
              CASE WHEN o.position IS NULL THEN c.updated_at END DESC,
              o.position ASC,
              c.updated_at DESC
            LIMIT %s
            """,
            (int(user_id), maximum),
        )
        return [_serialize_conversation(row) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def reorder_conversations(user_id: int, conversation_ids: Sequence[Any]) -> List[Dict[str, Any]]:
    ensure_velia_conversation_ux_tables()
    ordered_ids = _normalize_ids(
        conversation_ids,
        minimum=1,
        maximum=_MAX_REORDER_CONVERSATIONS,
    )
    uid = int(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT conversation_id
            FROM velia_conversations
            WHERE user_id=%s AND deleted_at IS NULL AND is_archived=FALSE
            ORDER BY conversation_id ASC
            FOR UPDATE
            """,
            (uid,),
        )
        active_ids = [str(_row_value(row, "conversation_id", 0, "")) for row in cursor.fetchall() or []]
        if len(active_ids) > _MAX_REORDER_CONVERSATIONS:
            raise ConversationUxError("too_many_active_conversations", status=409)
        if len(active_ids) != len(ordered_ids) or set(active_ids) != set(ordered_ids):
            raise ConversationUxError("conversation_order_stale", status=409)

        now = _utcnow()
        cursor.execute("DELETE FROM velia_conversation_order WHERE user_id=%s", (uid,))
        for position, conversation_id in enumerate(ordered_ids):
            cursor.execute(
                """
                INSERT INTO velia_conversation_order (
                    user_id, conversation_id, position, updated_at
                ) VALUES (%s, %s, %s, %s)
                """,
                (uid, conversation_id, position, now),
            )
        conn.commit()
    except ConversationUxError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return list_conversations_ordered(uid, include_archived=False, limit=_MAX_REORDER_CONVERSATIONS)


def _normalize_merge_title(value: Any, source_titles: Sequence[str]) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip())[:120]
    if normalized:
        return normalized
    first = next((title.strip() for title in source_titles if title and title.strip()), "")
    if first:
        return f"Объединено: {first}"[:120]
    return "Объединённый чат"


def merge_conversations(
    user_id: int,
    source_conversation_ids: Sequence[Any],
    *,
    title: str = "",
) -> Dict[str, Any]:
    ensure_velia_conversation_ux_tables()
    source_ids = _normalize_ids(source_conversation_ids, minimum=2, maximum=_MAX_MERGE_SOURCES)
    uid = int(user_id)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        placeholders = ",".join(["%s"] * len(source_ids))
        cursor.execute(
            f"""
            SELECT conversation_id, title
            FROM velia_conversations
            WHERE user_id=%s AND deleted_at IS NULL AND is_archived=FALSE
              AND conversation_id IN ({placeholders})
            FOR UPDATE
            """,
            tuple([uid] + source_ids),
        )
        rows = cursor.fetchall() or []
        title_by_id = {
            str(_row_value(row, "conversation_id", 0, "")): str(_row_value(row, "title", 1, ""))
            for row in rows
        }
        if set(title_by_id) != set(source_ids):
            raise ConversationUxError("conversation_not_found", status=404)

        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM velia_messages
            WHERE user_id=%s AND deleted_at IS NULL AND status='completed'
              AND role IN ('user','assistant')
              AND conversation_id IN ({placeholders})
            """,
            tuple([uid] + source_ids),
        )
        count_row = cursor.fetchone()
        message_count = int(_row_value(count_row, "count", 0, 0) or 0)
        if message_count > _MAX_MERGED_MESSAGES:
            raise ConversationUxError("merged_conversation_too_large", status=413)

        target_id = str(uuid.uuid4())
        now = _utcnow()
        source_titles = [title_by_id[source_id] for source_id in source_ids]
        target_title = _normalize_merge_title(title, source_titles)
        cursor.execute(
            """
            INSERT INTO velia_conversations (
                conversation_id, user_id, title, title_source,
                is_pinned, is_archived, created_at, updated_at
            ) VALUES (%s, %s, %s, 'manual', FALSE, FALSE, %s, %s)
            """,
            (target_id, uid, target_title, now, now),
        )

        copied = 0
        for source_id in source_ids:
            cursor.execute(
                """
                SELECT role, content, created_at, message_id
                FROM velia_messages
                WHERE user_id=%s AND conversation_id=%s
                  AND deleted_at IS NULL AND status='completed'
                  AND role IN ('user','assistant')
                ORDER BY created_at ASC,
                         CASE WHEN role='user' THEN 0 ELSE 1 END ASC,
                         message_id ASC
                """,
                (uid, source_id),
            )
            for row in cursor.fetchall() or []:
                role = str(_row_value(row, "role", 0, ""))
                content = str(_row_value(row, "content", 1, ""))
                created_at = now + timedelta(microseconds=copied + 1)
                cursor.execute(
                    """
                    INSERT INTO velia_messages (
                        message_id, conversation_id, user_id, role, content,
                        status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        target_id,
                        uid,
                        role,
                        content,
                        created_at,
                        created_at,
                    ),
                )
                copied += 1

        final_updated_at = now + timedelta(microseconds=max(1, copied + 1))
        cursor.execute(
            "UPDATE velia_conversations SET updated_at=%s WHERE conversation_id=%s AND user_id=%s",
            (final_updated_at, target_id, uid),
        )
        cursor.execute(
            """
            INSERT INTO velia_conversation_merges (
                merge_id, user_id, target_conversation_id,
                source_conversation_ids, created_at
            ) VALUES (%s, %s, %s, %s::jsonb, %s)
            """,
            (
                str(uuid.uuid4()),
                uid,
                target_id,
                json.dumps(source_ids, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        return {
            "id": target_id,
            "title": target_title,
            "title_source": "manual",
            "is_pinned": False,
            "is_archived": False,
            "created_at": _iso(now),
            "updated_at": _iso(final_updated_at),
            "merged_message_count": copied,
            "source_conversation_ids": source_ids,
        }
    except ConversationUxError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def create_share_snapshot(user_id: int, conversation_id: str) -> Dict[str, Any]:
    ensure_velia_conversation_ux_tables()
    uid = int(user_id)
    normalized_id = str(conversation_id or "").strip()
    if not normalized_id or len(normalized_id) > 120:
        raise ConversationUxError("invalid_conversation_id")

    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT conversation_id, title
            FROM velia_conversations
            WHERE user_id=%s AND conversation_id=%s AND deleted_at IS NULL
            LIMIT 1
            """,
            (uid, normalized_id),
        )
        conversation = cursor.fetchone()
        if not conversation:
            raise ConversationUxError("conversation_not_found", status=404)
        title = str(_row_value(conversation, "title", 1, ""))

        cursor.execute(
            """
            SELECT role, content, created_at, message_id
            FROM velia_messages
            WHERE user_id=%s AND conversation_id=%s
              AND deleted_at IS NULL AND status='completed'
              AND role IN ('user','assistant')
            ORDER BY created_at ASC,
                     CASE WHEN role='user' THEN 0 ELSE 1 END ASC,
                     message_id ASC
            LIMIT %s
            """,
            (uid, normalized_id, _MAX_SHARE_MESSAGES + 1),
        )
        rows = cursor.fetchall() or []
        if len(rows) > _MAX_SHARE_MESSAGES:
            raise ConversationUxError("conversation_too_large_to_share", status=413)

        messages: List[Dict[str, Any]] = []
        total_characters = 0
        for row in rows:
            role = str(_row_value(row, "role", 0, ""))
            content = str(_row_value(row, "content", 1, ""))
            total_characters += len(content)
            if total_characters > _MAX_SHARE_CHARACTERS:
                raise ConversationUxError("conversation_too_large_to_share", status=413)
            messages.append({"role": role, "content": content})

        token = secrets.token_urlsafe(32)
        share_id = str(uuid.uuid4())
        created_at = _utcnow()
        expires_at = created_at + timedelta(days=_DEFAULT_SHARE_TTL_DAYS)
        snapshot = {
            "schema_version": 1,
            "title": title,
            "messages": messages,
            "created_at": _iso(created_at),
        }
        cursor.execute(
            """
            INSERT INTO velia_conversation_shares (
                share_id, token_hash, user_id, conversation_id, title,
                snapshot_json, created_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                share_id,
                _token_hash(token),
                uid,
                normalized_id,
                title,
                json.dumps(snapshot, ensure_ascii=False),
                created_at,
                expires_at,
            ),
        )
        conn.commit()
        return {
            "id": share_id,
            "token": token,
            "conversation_id": normalized_id,
            "title": title,
            "created_at": _iso(created_at),
            "expires_at": _iso(expires_at),
            "message_count": len(messages),
        }
    except ConversationUxError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_public_share(token: str) -> Optional[Dict[str, Any]]:
    ensure_velia_conversation_ux_tables()
    normalized = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(normalized):
        return None
    now = _utcnow()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT share_id, title, snapshot_json, created_at, expires_at
            FROM velia_conversation_shares
            WHERE token_hash=%s AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at>%s)
            LIMIT 1
            """,
            (_token_hash(normalized), now),
        )
        row = cursor.fetchone()
        if not row:
            return None
        snapshot = _row_value(row, "snapshot_json", 2, {})
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        if not isinstance(snapshot, dict):
            return None
        messages = snapshot.get("messages")
        if not isinstance(messages, list):
            return None
        safe_messages = []
        for item in messages[:_MAX_SHARE_MESSAGES]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            safe_messages.append({
                "role": role,
                "content": str(item.get("content") or "")[:20_000],
            })
        return {
            "id": str(_row_value(row, "share_id", 0, "")),
            "title": str(snapshot.get("title") or _row_value(row, "title", 1, ""))[:120],
            "messages": safe_messages,
            "created_at": str(snapshot.get("created_at") or _iso(_row_value(row, "created_at", 3)) or ""),
            "expires_at": _iso(_row_value(row, "expires_at", 4)),
        }
    finally:
        cursor.close()
        conn.close()


def install_conversation_ordering(chat_service_module: Any, mobile_routes_module: Any) -> None:
    if getattr(chat_service_module, "_velia_conversation_ux_ordering_installed", False):
        return
    chat_service_module.list_conversations = list_conversations_ordered
    mobile_routes_module.list_conversations = list_conversations_ordered
    chat_service_module._velia_conversation_ux_ordering_installed = True
