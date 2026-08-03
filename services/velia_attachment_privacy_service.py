import logging
from typing import Any

from db.database import get_connection
from services import velia_attachment_service as attachment_service


logger = logging.getLogger(__name__)


def scrub_legacy_failed_attachment_payloads() -> int:
    """Scrub failed rows created by older deployments.

    New upload reservations never persist original bytes. Running this during
    route setup makes legacy cleanup retry on every web-process restart.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_attachments
            SET content_bytes=%s,
                extracted_text='',
                sha256='',
                original_name='failed attachment',
                width=NULL,
                height=NULL,
                deleted_at=COALESCE(deleted_at, %s)
            WHERE extraction_status='failed'
              AND (
                  OCTET_LENGTH(content_bytes) > 0
                  OR extracted_text <> ''
                  OR sha256 <> ''
                  OR deleted_at IS NULL
              )
            """,
            (b"", attachment_service._utcnow()),
        )
        changed = int(cursor.rowcount or 0)
        conn.commit()
        if changed:
            logger.info(
                "VELIA_ATTACHMENT_LEGACY_FAILED_PAYLOADS_SCRUBBED count=%s",
                changed,
            )
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def delete_attachment(user_id: int, attachment_id: str) -> bool:
    """Delete an unused attachment and scrub all private contents atomically."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT attachment_id
            FROM velia_attachments
            WHERE attachment_id=%s AND user_id=%s AND deleted_at IS NULL
            FOR UPDATE
            """,
            (str(attachment_id), int(user_id)),
        )
        if not cursor.fetchone():
            conn.rollback()
            return False

        cursor.execute(
            """
            SELECT 1
            FROM velia_message_attachments ma
            WHERE ma.attachment_id=%s
            LIMIT 1
            """,
            (str(attachment_id),),
        )
        if cursor.fetchone():
            raise attachment_service.AttachmentError(
                "attachment_in_use",
                status=409,
            )

        cursor.execute(
            """
            UPDATE velia_attachments
            SET content_bytes=%s,
                extracted_text='',
                sha256='',
                original_name='deleted attachment',
                width=NULL,
                height=NULL,
                deleted_at=%s
            WHERE attachment_id=%s AND user_id=%s AND deleted_at IS NULL
            """,
            (
                b"",
                attachment_service._utcnow(),
                str(attachment_id),
                int(user_id),
            ),
        )
        changed = bool(cursor.rowcount)
        conn.commit()
        return changed
    except attachment_service.AttachmentError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


# Keep direct service callers on the same secure implementation. The mobile
# route also imports this function explicitly so import order cannot restore
# the legacy soft-delete implementation.
attachment_service.delete_attachment = delete_attachment
