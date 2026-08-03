import os
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List

from db.database import get_connection


_ATTACHMENT_FRAME_TOKEN_RE = re.compile(
    r"\[(?:BEGIN_ATTACHMENT\b[^\]\n]*|END_ATTACHMENT)\]",
    re.IGNORECASE,
)
_ATTACHMENT_TRUNCATED_MARKER = "\n[Attachment payload truncated]"


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


def _escape_attachment_payload(value: str) -> str:
    """Make frame delimiters impossible inside untrusted extracted content."""

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        return "⟦" + token[1:-1] + "⟧"

    return _ATTACHMENT_FRAME_TOKEN_RE.sub(replace_token, str(value or ""))


def _safe_attachment_header_value(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "/")
        .replace('"', "")
        .replace("[", "(")
        .replace("]", ")")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _framed_attachment(
    filename: str,
    mime_type: str,
    extracted_text: str,
    max_chars: int,
) -> str:
    """Return one complete frame that never exceeds the supplied budget."""
    safe_name = _safe_attachment_header_value(filename) or "attachment"
    safe_mime_type = _safe_attachment_header_value(mime_type)
    header = f'[BEGIN_ATTACHMENT name="{safe_name}" mime="{safe_mime_type}"]\n'
    footer = "\n[END_ATTACHMENT]"
    available_payload_chars = int(max_chars) - len(header) - len(footer)
    if available_payload_chars <= 0:
        return ""

    payload = _escape_attachment_payload(extracted_text).strip()
    if not payload:
        return ""
    if len(payload) > available_payload_chars:
        marker = _ATTACHMENT_TRUNCATED_MARKER
        if available_payload_chars <= len(marker):
            return ""
        payload = payload[: available_payload_chars - len(marker)] + marker
    return header + payload + footer


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

    message_groups: List[Dict[str, Any]] = []
    group_by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        message_id = str(_row_value(row, "message_id", 0, ""))
        extracted_text = str(_row_value(row, "extracted_text", 4, "") or "").strip()
        if not message_id or not extracted_text:
            continue
        group = group_by_id.get(message_id)
        if group is None:
            user_content = str(_row_value(row, "content", 1, "") or "").strip()
            # The same user turn already exists in the ordinary transcript. A
            # bounded copy here is only an association label for its files.
            associated_text = (user_content or "[attachment-only message]")[:1000]
            group = {
                "message_id": message_id,
                "associated": "ASSOCIATED_USER_MESSAGE:\n" + associated_text,
                "attachments": [],
            }
            group_by_id[message_id] = group
            message_groups.append(group)
        group["attachments"].append(
            {
                "filename": str(_row_value(row, "original_name", 2, "attachment")),
                "mime_type": str(_row_value(row, "mime_type", 3, "")),
                "extracted_text": extracted_text,
            }
        )

    if not message_groups:
        return ""

    # Spend the budget from newest to oldest. Every included document remains
    # inside a complete BEGIN/END frame; older groups or extra attachments are
    # omitted rather than slicing a previously framed context string.
    selected_newest_first: List[str] = []
    used_chars = 0
    omitted = False
    for group in reversed(message_groups):
        separator_chars = 2 if selected_newest_first else 0
        remaining = max_chars - used_chars - separator_chars
        associated = str(group["associated"])
        minimum_frame_budget = 128
        if remaining <= len(associated) + 2 + minimum_frame_budget:
            omitted = True
            continue

        frames: List[str] = []
        frame_used = len(associated) + 2
        for attachment in group["attachments"]:
            frame_separator = 2 if frames else 0
            frame_budget = remaining - frame_used - frame_separator
            if frame_budget < minimum_frame_budget:
                omitted = True
                break
            frame = _framed_attachment(
                str(attachment["filename"]),
                str(attachment["mime_type"]),
                str(attachment["extracted_text"]),
                frame_budget,
            )
            if not frame:
                omitted = True
                continue
            frames.append(frame)
            frame_used += frame_separator + len(frame)

        if not frames:
            omitted = True
            continue
        chunk = associated + "\n\n" + "\n\n".join(frames)
        selected_newest_first.append(chunk)
        used_chars += separator_chars + len(chunk)

    if not selected_newest_first:
        return ""
    context = "\n\n".join(reversed(selected_newest_first))
    if omitted:
        notice = "[Older or excess attachment context omitted]\n"
        if len(notice) + len(context) <= max_chars:
            context = notice + context
    return context
