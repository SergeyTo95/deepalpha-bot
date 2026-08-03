import hashlib
import logging
import re
import uuid
from typing import Any, Dict, Optional

from db.database import get_connection
from services import velia_attachment_service as attachment_service
from services import velia_attachment_upload_service as upload_service


logger = logging.getLogger(__name__)
_UPLOAD_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_UPLOAD_NAMESPACE = uuid.UUID("ac230ade-85b7-4c0b-95ee-8a4ea5350ef4")
_IDEMPOTENCY_LOCK_NAMESPACE = 1_904_202_609


def normalize_upload_idempotency_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise attachment_service.AttachmentError(
            "attachment_idempotency_key_required",
            status=400,
        )
    if not _UPLOAD_KEY_RE.fullmatch(key):
        raise attachment_service.AttachmentError(
            "invalid_attachment_idempotency_key",
            status=400,
        )
    return key


def _attachment_id(user_id: int, conversation_id: str, key: str) -> str:
    return str(
        uuid.uuid5(
            _UPLOAD_NAMESPACE,
            f"{int(user_id)}:{str(conversation_id)}:{str(key)}",
        )
    )


def _idempotency_lock_key(attachment_id: str) -> int:
    value = int(uuid.UUID(str(attachment_id)).int & 0x7FFFFFFF)
    return _IDEMPOTENCY_LOCK_NAMESPACE * 1_000_000_000 + value


def _serialize_existing(row: Any) -> Dict[str, Any]:
    return attachment_service._serialize_attachment(tuple(row[:11]))


def _existing_or_reserve(
    *,
    attachment_id: str,
    user_id: int,
    conversation_id: str,
    filename: str,
    raw: bytes,
    digest: str,
    preflight: Dict[str, Any],
    max_bytes: int,
) -> Optional[Dict[str, Any]]:
    daily_count_limit = upload_service._env_int(
        "VELIA_ATTACHMENTS_DAILY_USER_LIMIT",
        20,
        1,
        500,
    )
    daily_bytes_limit = upload_service._env_int(
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
            (_idempotency_lock_key(attachment_id),),
        )
        cursor.execute(
            """
            SELECT attachment_id, conversation_id, original_name, mime_type,
                   kind, byte_size, sha256, width, height,
                   extraction_status, created_at, deleted_at
            FROM velia_attachments
            WHERE attachment_id=%s AND user_id=%s
            FOR UPDATE
            """,
            (str(attachment_id), int(user_id)),
        )
        existing = cursor.fetchone()
        if existing:
            existing_conversation_id = str(existing[1] or "")
            existing_size = int(existing[5] or 0)
            existing_digest = str(existing[6] or "")
            existing_status = str(existing[9] or "")
            existing_deleted_at = existing[11]
            if (
                existing_conversation_id != str(conversation_id)
                or existing_size != len(raw)
                or (existing_digest and existing_digest != str(digest))
            ):
                raise attachment_service.AttachmentError(
                    "idempotency_attachment_mismatch",
                    status=409,
                )
            if existing_status == "ready" and existing_deleted_at is None:
                conn.commit()
                return _serialize_existing(existing)
            if existing_status == "failed" and existing_deleted_at is None:
                # The first request has reserved the deterministic row and is
                # still inspecting it outside this short transaction. Do not
                # start a second worker that could race completion/cleanup.
                raise attachment_service.AttachmentError(
                    "attachment_upload_in_progress",
                    status=409,
                )

            # A failed or privacy-scrubbed attempt may be retried with the same
            # key and exact bytes without consuming a second quota entry.
            cursor.execute(
                """
                UPDATE velia_attachments
                SET conversation_id=%s,
                    original_name=%s,
                    mime_type=%s,
                    kind=%s,
                    byte_size=%s,
                    sha256=%s,
                    width=%s,
                    height=%s,
                    content_bytes=%s,
                    extracted_text='',
                    extraction_status='failed',
                    deleted_at=NULL,
                    created_at=%s
                WHERE attachment_id=%s AND user_id=%s
                """,
                (
                    str(conversation_id),
                    str(filename),
                    str(preflight["mime_type"]),
                    str(preflight["kind"]),
                    len(raw),
                    str(digest),
                    preflight.get("width"),
                    preflight.get("height"),
                    b"",
                    attachment_service._utcnow(),
                    str(attachment_id),
                    int(user_id),
                ),
            )
            conn.commit()
            return None

        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (upload_service._quota_lock_key(user_id),),
        )
        cursor.execute(
            """
            SELECT 1
            FROM velia_conversations
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            FOR UPDATE
            """,
            (str(conversation_id), int(user_id)),
        )
        if not cursor.fetchone():
            raise attachment_service.AttachmentError(
                "conversation_not_found",
                status=404,
            )
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
                b"",
                attachment_service._utcnow(),
            ),
        )
        conn.commit()
        return None
    except attachment_service.AttachmentError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def scrub_unlinked_known_attachment(
    attachment_id: str,
    user_id: int,
) -> bool:
    """Scrub a known reservation even after an ambiguous ready commit.

    The row is changed only while it is still unlinked, so a message that has
    already accepted the attachment can never be damaged by recovery cleanup.
    """
    attempts = upload_service._env_int(
        "VELIA_ATTACHMENTS_SCRUB_ATTEMPTS",
        3,
        1,
        10,
    )
    for attempt in range(1, attempts + 1):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT attachment_id
                FROM velia_attachments
                WHERE attachment_id=%s AND user_id=%s
                FOR UPDATE
                """,
                (str(attachment_id), int(user_id)),
            )
            if not cursor.fetchone():
                conn.rollback()
                return True
            cursor.execute(
                """
                SELECT 1
                FROM velia_message_attachments
                WHERE attachment_id=%s
                LIMIT 1
                """,
                (str(attachment_id),),
            )
            if cursor.fetchone():
                conn.rollback()
                return False
            cursor.execute(
                """
                UPDATE velia_attachments
                SET content_bytes=%s,
                    extracted_text='',
                    original_name='failed attachment',
                    width=NULL,
                    height=NULL,
                    extraction_status='failed',
                    deleted_at=COALESCE(deleted_at, %s)
                WHERE attachment_id=%s AND user_id=%s
                """,
                (
                    b"",
                    attachment_service._utcnow(),
                    str(attachment_id),
                    int(user_id),
                ),
            )
            conn.commit()
            return True
        except Exception as exc:
            conn.rollback()
            if attempt >= attempts:
                logger.error(
                    "VELIA_ATTACHMENT_KNOWN_ID_SCRUB_FAILED attachment_id=%s user_id=%s attempts=%s error=%s",
                    str(attachment_id),
                    int(user_id),
                    attempts,
                    exc.__class__.__name__,
                )
        finally:
            cursor.close()
            conn.close()
    return False


def create_attachment_idempotently(
    user_id: int,
    conversation_id: str,
    *,
    idempotency_key: str,
    filename: str,
    mime_type: str,
    content: bytes,
) -> Dict[str, Any]:
    key = normalize_upload_idempotency_key(idempotency_key)
    max_bytes = upload_service._env_int(
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
    preflight = upload_service._preflight_attachment(raw, mime_type)
    normalized_name = attachment_service.sanitize_filename(filename)
    digest = hashlib.sha256(raw).hexdigest()
    attachment_id = _attachment_id(user_id, conversation_id, key)

    existing = _existing_or_reserve(
        attachment_id=attachment_id,
        user_id=int(user_id),
        conversation_id=str(conversation_id),
        filename=normalized_name,
        raw=raw,
        digest=digest,
        preflight=preflight,
        max_bytes=max_bytes,
    )
    if existing is not None:
        return existing

    try:
        inspected = attachment_service.inspect_attachment(
            raw,
            str(preflight["mime_type"]),
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            attachment_id=attachment_id,
        )
        return upload_service._complete_attachment(
            attachment_id=attachment_id,
            user_id=int(user_id),
            inspected=inspected,
        )
    except Exception:
        scrub_unlinked_known_attachment(attachment_id, int(user_id))
        raise
