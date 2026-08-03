import os
from collections import defaultdict
from typing import Any, Dict, Iterable, List

from db.database import get_connection


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        value = row[index]
    except (IndexError, TypeError):
        return default
    return default if value is None else value


def public_attachment_metadata_for_messages(
    user_id: int,
    message_ids: Iterable[str],
) -> Dict[str, List[Dict[str, Any]]]:
    normalized_ids = [str(value) for value in message_ids if str(value or "").strip()]
    if not normalized_ids:
        return {}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT ma.message_id, a.attachment_id, a.original_name, a.mime_type,
                   a.kind, a.byte_size, a.width, a.height,
                   a.extraction_status, ma.position
            FROM velia_message_attachments ma
            JOIN velia_attachments a ON a.attachment_id=ma.attachment_id
            JOIN velia_messages m ON m.message_id=ma.message_id
            WHERE m.user_id=%s
              AND ma.message_id = ANY(%s)
              AND a.user_id=%s
              AND a.deleted_at IS NULL
              AND a.extraction_status='ready'
            ORDER BY ma.message_id ASC, ma.position ASC
            """,
            (int(user_id), normalized_ids, int(user_id)),
        )
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in cursor.fetchall() or []:
            message_id = str(_row_value(row, "message_id", 0, ""))
            if not message_id:
                continue
            grouped[message_id].append(
                {
                    "id": str(_row_value(row, "attachment_id", 1, "")),
                    "name": str(_row_value(row, "original_name", 2, "")),
                    "mime_type": str(_row_value(row, "mime_type", 3, "")),
                    "kind": str(_row_value(row, "kind", 4, "")),
                    "byte_size": int(_row_value(row, "byte_size", 5, 0) or 0),
                    "width": int(_row_value(row, "width", 6, 0) or 0) or None,
                    "height": int(_row_value(row, "height", 7, 0) or 0) or None,
                    "status": str(_row_value(row, "extraction_status", 8, "ready")),
                }
            )
        return dict(grouped)
    finally:
        cursor.close()
        conn.close()


def attachment_prompt_context(
    user_id: int,
    conversation_id: str,
) -> str:
    # This limit is a window of recent conversation turns, not a count of
    # attachment-bearing messages. Consequently old files age out naturally
    # as the user continues the conversation with ordinary text turns.
    max_recent_turns = _env_int(
        "VELIA_ATTACHMENT_CONTEXT_MESSAGES",
        8,
        1,
        24,
    )
    max_chars = _env_int(
        "VELIA_ATTACHMENT_CONTEXT_CHARS",
        40_000,
        2_000,
        120_000,
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            WITH recent_messages AS (
                SELECT message_id, role, content, created_at
                FROM velia_messages
                WHERE conversation_id=%s AND user_id=%s
                  AND role IN ('user', 'assistant')
                  AND status='completed'
                  AND deleted_at IS NULL
                ORDER BY created_at DESC, message_id DESC
                LIMIT %s
            ),
            recent_user_messages AS (
                SELECT message_id, content, created_at
                FROM recent_messages
                WHERE role='user'
            )
            SELECT rum.message_id, rum.content, a.original_name, a.mime_type,
                   a.extracted_text, ma.position, rum.created_at
            FROM recent_user_messages rum
            JOIN velia_message_attachments ma ON ma.message_id=rum.message_id
            JOIN velia_attachments a ON a.attachment_id=ma.attachment_id
            WHERE a.user_id=%s AND a.deleted_at IS NULL
              AND a.extraction_status='ready'
            ORDER BY rum.created_at ASC, rum.message_id ASC, ma.position ASC
            """,
            (
                str(conversation_id),
                int(user_id),
                max_recent_turns,
                int(user_id),
            ),
        )
        rows = cursor.fetchall() or []
    finally:
        cursor.close()
        conn.close()

    blocks: List[str] = []
    current_message_id = ""
    for row in rows:
        message_id = str(_row_value(row, "message_id", 0, ""))
        user_content = str(_row_value(row, "content", 1, "") or "").strip()
        filename = str(_row_value(row, "original_name", 2, "attachment"))
        mime_type = str(_row_value(row, "mime_type", 3, ""))
        extracted_text = str(_row_value(row, "extracted_text", 4, "") or "").strip()
        if not message_id or not extracted_text:
            continue
        if message_id != current_message_id:
            current_message_id = message_id
            blocks.append(
                "ASSOCIATED_USER_MESSAGE:\n" + (user_content or "[attachment-only message]")
            )
        safe_name = filename.replace('"', "").replace("\n", " ")
        blocks.append(
            f'[BEGIN_ATTACHMENT name="{safe_name}" mime="{mime_type}"]\n'
            + extracted_text
            + "\n[END_ATTACHMENT]"
        )

    if not blocks:
        return ""
    context = "\n\n".join(blocks)
    if len(context) > max_chars:
        context = context[-max_chars:]
        context = "[Older attachment context truncated]\n" + context
    return context
