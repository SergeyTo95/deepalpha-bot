import hashlib
import os
import uuid
from typing import Any, Dict

from db.database import get_connection
from services import velia_attachment_service as attachment_service


_USER_QUOTA_LOCK_NAMESPACE = 1_904_202_608
_ALLOWED_DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _preflight_attachment(raw: bytes, declared_mime_type: str) -> Dict[str, Any]:
    mime_type = str(declared_mime_type or "").split(";", 1)[0].strip().lower()
    if not raw:
        raise attachment_service.AttachmentError("empty_attachment")
    if mime_type in attachment_service._ALLOWED_IMAGE_MIME_TYPES:
        _, width, height = attachment_service._verify_image(raw, mime_type)
        return {
            "kind": "image",
            "mime_type": mime_type,
            "width": width,
            "height": height,
        }
    if mime_type not in _ALLOWED_DOCUMENT_MIME_TYPES:
        raise attachment_service.AttachmentError(
            "attachment_type_not_supported",
            status=415,
        )
    if mime_type == "application/pdf" and not raw.startswith(b"%PDF-"):
        raise attachment_service.AttachmentError(
            "attachment_type_mismatch",
            status=415,
        )
    if (
        mime_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        and not raw.startswith(b"PK")
    ):
        raise attachment_service.AttachmentError(
            "attachment_type_mismatch",
            status=415,
        )
    return {
        "kind": "document",
        "mime_type": mime_type,
        "width": None,
        "height": None,
    }


def _quota_lock_key(user_id: int) -> int:
    return _USER_QUOTA_LOCK_NAMESPACE * 1_000_000_000 + int(user_id)


def _reserve_attachment(
    *,
    attachment_id: str,
    user_id: int,
    conversation_id: str,
    filename: str,
    raw: bytes,
    digest: str,
    preflight: Dict[str, Any],
    max_bytes: int,
) -> None:
    daily_count_limit = _env_int(
        "VELIA_ATTACHMENTS_DAILY_USER_LIMIT",
        20,
        1,
        500,
    )
    daily_bytes_limit = _env_int(
        "VELIA_ATTACHMENTS_DAILY_USER_BYTES",
        100 * 1024 * 1024,
        max_bytes,
        2 * 1024 * 1024 * 1024,
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_quota_lock_key(user_id),),
        )
        cursor.execute(
            "SELECT 1 FROM velia_conversations "
            "WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL",
            (str(conversation_id), int(user_id)),
        )
        if not cursor.fetchone():
            raise attachment_service.AttachmentError(
                "conversation_not_found",
                status=404,
            )
        # Deleted and failed attempts still count for the current day. This
        # prevents delete-and-retry loops from bypassing file/vision quotas.
        cursor.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(byte_size), 0)
            FROM velia_attachments
            WHERE user_id=%s AND created_at>=CURRENT_DATE
            """,
            (int(user_id),),
        )
        quota_row = cursor.fetchone() or (0, 0)
        current_count = int(quota_row[0] or 0)
        current_bytes = int(quota_row[1] or 0)
        if current_count >= daily_count_limit:
            raise attachment_service.AttachmentError(
                "attachment_daily_limit_exceeded",
                status=429,
            )
        if current_bytes + len(raw) > daily_bytes_limit:
            raise attachment_service.AttachmentError(
                "attachment_daily_bytes_exceeded",
                status=429,
            )
        cursor.execute(
            """
            INSERT INTO velia_attachments (
                attachment_id, user_id, conversation_id, original_name,
                mime_type, kind, byte_size, sha256, width, height,
                content_bytes, extracted_text, extraction_status, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, '', 'failed', %s
            )
            """,
            (
                str(attachment_id),
                int(user_id),
                str(conversation_id),
                str(filename),
                str(preflight["mime_type"]),
                str(preflight["kind"]),
                len(raw),
                str(digest),
                preflight.get("width"),
                preflight.get("height"),
                raw,
                attachment_service._utcnow(),
            ),
        )
        conn.commit()
    except attachment_service.AttachmentError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _scrub_failed_attachment(attachment_id: str, user_id: int) -> None:
    """Keep the quota ledger while deleting failed private file contents."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_attachments
            SET content_bytes=%s,
                extracted_text='',
                deleted_at=COALESCE(deleted_at, %s)
            WHERE attachment_id=%s AND user_id=%s
              AND extraction_status='failed'
            """,
            (
                b"",
                attachment_service._utcnow(),
                str(attachment_id),
                int(user_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def _complete_attachment(
    *,
    attachment_id: str,
    user_id: int,
    inspected: Dict[str, Any],
) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_attachments
            SET mime_type=%s,
                kind=%s,
                width=%s,
                height=%s,
                extracted_text=%s,
                extraction_status='ready'
            WHERE attachment_id=%s AND user_id=%s
              AND deleted_at IS NULL AND extraction_status='failed'
            RETURNING attachment_id, conversation_id, original_name, mime_type,
                      kind, byte_size, sha256, width, height,
                      extraction_status, created_at
            """,
            (
                str(inspected["mime_type"]),
                str(inspected["kind"]),
                inspected.get("width"),
                inspected.get("height"),
                str(inspected["extracted_text"]),
                str(attachment_id),
                int(user_id),
            ),
        )
        row = cursor.fetchone()
        if not row:
            raise attachment_service.AttachmentError(
                "attachment_reservation_lost",
                status=409,
            )
        conn.commit()
        return attachment_service._serialize_attachment(row)
    except attachment_service.AttachmentError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_attachment_with_reservation(
    user_id: int,
    conversation_id: str,
    *,
    filename: str,
    mime_type: str,
    content: bytes,
) -> Dict[str, Any]:
    max_bytes = _env_int(
        "VELIA_ATTACHMENTS_MAX_BYTES",
        15 * 1024 * 1024,
        64 * 1024,
        50 * 1024 * 1024,
    )
    raw = bytes(content or b"")
    if len(raw) > max_bytes:
        raise attachment_service.AttachmentError(
            "attachment_too_large",
            status=413,
        )

    preflight = _preflight_attachment(raw, mime_type)
    attachment_id = str(uuid.uuid4())
    normalized_name = attachment_service.sanitize_filename(filename)
    digest = hashlib.sha256(raw).hexdigest()

    _reserve_attachment(
        attachment_id=attachment_id,
        user_id=int(user_id),
        conversation_id=str(conversation_id),
        filename=normalized_name,
        raw=raw,
        digest=digest,
        preflight=preflight,
        max_bytes=max_bytes,
    )

    try:
        inspected = attachment_service.inspect_attachment(
            raw,
            str(preflight["mime_type"]),
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            attachment_id=attachment_id,
        )
        return _complete_attachment(
            attachment_id=attachment_id,
            user_id=int(user_id),
            inspected=inspected,
        )
    except Exception:
        _scrub_failed_attachment(attachment_id, int(user_id))
        raise
