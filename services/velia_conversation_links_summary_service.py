from typing import Any, Dict, List, Mapping

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection
from services.velia_conversation_links_service import ensure_velia_conversation_links_table


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


def list_conversation_link_summaries(user_id: int) -> List[Dict[str, Any]]:
    """Return one compact summary per active target conversation.

    The count includes only currently active source conversations. A deleted or
    archived source therefore never leaves a stale badge in the mobile UI.
    """

    ensure_velia_conversation_links_table()
    uid = int(user_id)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT l.target_conversation_id, COUNT(*) AS linked_source_count
            FROM velia_conversation_links AS l
            JOIN velia_conversations AS target
              ON target.user_id=l.user_id
             AND target.conversation_id=l.target_conversation_id
            JOIN velia_conversations AS source
              ON source.user_id=l.user_id
             AND source.conversation_id=l.source_conversation_id
            WHERE l.user_id=%s
              AND target.deleted_at IS NULL AND target.is_archived=FALSE
              AND source.deleted_at IS NULL AND source.is_archived=FALSE
            GROUP BY l.target_conversation_id
            ORDER BY l.target_conversation_id ASC
            """,
            (uid,),
        )
        result: List[Dict[str, Any]] = []
        for row in cursor.fetchall() or []:
            source_count = int(_row_value(row, "linked_source_count", 1, 0) or 0)
            if source_count <= 0:
                continue
            result.append(
                {
                    "id": str(_row_value(row, "target_conversation_id", 0, "")),
                    "linked_source_count": source_count,
                    "linked_group_size": source_count + 1,
                }
            )
        return result
    finally:
        cursor.close()
        conn.close()
