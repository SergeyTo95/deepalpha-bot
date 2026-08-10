from datetime import datetime
from typing import Any, Dict, List, Mapping, Sequence

from db.database import get_connection
from services.velia_conversation_ux_service import (
    ConversationUxError,
    _dict_cursor,
    _serialize_conversation,
    ensure_velia_conversation_ux_tables,
)


_MAX_REORDER_SUBSET = 100


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _normalize_ids(raw_ids: Sequence[Any]) -> List[str]:
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
    if not result:
        raise ConversationUxError("not_enough_conversations")
    if len(result) > _MAX_REORDER_SUBSET:
        raise ConversationUxError("too_many_conversations")
    return result


def list_conversations_ordered_stable(
    user_id: int,
    *,
    include_archived: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Canonical mobile ordering used by both listing and drag persistence.

    `conversation_id ASC` is the final deterministic tie-breaker. Without it,
    equal timestamps can produce different page membership between the list API
    and the reorder transaction, making a visually successful drag appear to
    revert after refresh.
    """

    ensure_velia_conversation_ux_tables()
    maximum = min(_MAX_REORDER_SUBSET, max(1, int(limit or 50)))
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
              c.updated_at DESC,
              c.conversation_id ASC
            LIMIT %s
            """,
            (int(user_id), maximum),
        )
        return [_serialize_conversation(row) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def merge_partial_conversation_order(
    current_order: Sequence[str],
    submitted_order: Sequence[str],
) -> List[str]:
    """Reorder only submitted slots while preserving every non-submitted item.

    Mobile intentionally paginates the conversation list. Requiring the client to
    send every active conversation makes drag persistence fail as soon as a user
    owns more conversations than the current page. This helper replaces only the
    slots occupied by submitted ids and leaves hidden/non-submitted ids in their
    relative positions.
    """

    current = [str(value) for value in current_order]
    submitted = [str(value) for value in submitted_order]
    if not submitted or len(set(submitted)) != len(submitted):
        raise ValueError("invalid_submitted_order")
    submitted_set = set(submitted)
    slot_indexes = [index for index, value in enumerate(current) if value in submitted_set]
    if len(slot_indexes) != len(submitted):
        raise ValueError("submitted_order_not_subset")

    result = list(current)
    for index, conversation_id in zip(slot_indexes, submitted):
        result[index] = conversation_id
    return result


def reorder_visible_conversations(user_id: int, conversation_ids: Sequence[Any]):
    ensure_velia_conversation_ux_tables()
    submitted_order = _normalize_ids(conversation_ids)
    uid = int(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Lock stable conversation rows first so concurrent create/archive/delete
        # cannot invalidate the ordering snapshot while we rewrite positions.
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
        active_ids = {
            str(_row_value(row, "conversation_id", 0, ""))
            for row in cursor.fetchall() or []
        }
        if any(conversation_id not in active_ids for conversation_id in submitted_order):
            raise ConversationUxError("conversation_order_stale", status=409)

        cursor.execute(
            """
            SELECT c.conversation_id
            FROM velia_conversations AS c
            LEFT JOIN velia_conversation_order AS o
              ON o.user_id=c.user_id AND o.conversation_id=c.conversation_id
            WHERE c.user_id=%s AND c.deleted_at IS NULL AND c.is_archived=FALSE
            ORDER BY
              (o.position IS NULL) DESC,
              CASE WHEN o.position IS NULL THEN c.is_pinned ELSE FALSE END DESC,
              CASE WHEN o.position IS NULL THEN c.updated_at END DESC,
              o.position ASC,
              c.updated_at DESC,
              c.conversation_id ASC
            """,
            (uid,),
        )
        current_order = [
            str(_row_value(row, "conversation_id", 0, ""))
            for row in cursor.fetchall() or []
        ]
        try:
            final_order = merge_partial_conversation_order(current_order, submitted_order)
        except ValueError as error:
            raise ConversationUxError("conversation_order_stale", status=409) from error

        now = datetime.utcnow()
        cursor.execute("DELETE FROM velia_conversation_order WHERE user_id=%s", (uid,))
        for position, conversation_id in enumerate(final_order):
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

    return list_conversations_ordered_stable(
        uid,
        include_archived=False,
        limit=_MAX_REORDER_SUBSET,
    )
