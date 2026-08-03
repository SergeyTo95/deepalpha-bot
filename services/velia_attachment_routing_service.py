import logging
from typing import Optional

from db.database import get_connection


logger = logging.getLogger(__name__)


def request_message_has_attachments(
    request_id: Optional[str],
    user_id: int,
) -> bool:
    """Fail closed for deterministic or paid action routing.

    A missing request id represents a legacy/internal call without a persisted
    mobile user turn. A database failure is treated as attachment-backed so
    deterministic acknowledgements and paid image actions are bypassed rather
    than silently ignoring a possible file.
    """
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        return False

    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM velia_messages m
                JOIN velia_message_attachments ma
                  ON ma.message_id=m.message_id
                WHERE m.request_id=%s
                  AND m.user_id=%s
                  AND m.role='user'
                  AND m.status='completed'
                  AND m.deleted_at IS NULL
            )
            """,
            (normalized_request_id, int(user_id)),
        )
        row = cursor.fetchone()
        if isinstance(row, dict):
            value = next(iter(row.values()), False)
        else:
            value = row[0] if row else False
        return bool(value)
    except Exception as exc:
        logger.warning(
            "VELIA_ATTACHMENT_ROUTING_LOOKUP_FAILED request_id=%s user_id=%s error=%s",
            normalized_request_id,
            int(user_id),
            exc.__class__.__name__,
        )
        return True
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
