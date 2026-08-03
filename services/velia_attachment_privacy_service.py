import logging
import os
import time
from typing import Any

from db.database import get_connection
from services import velia_attachment_service as attachment_service


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


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


def _delete_attachment_once(user_id: int, attachment_id: str) -> bool:
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


def delete_attachment(user_id: int, attachment_id: str) -> bool:
    """Delete and scrub an attachment, retrying transient storage failures.

    The same function is used by explicit DELETE requests and by disconnect
    cleanup. Retrying here guarantees that an undelivered ready upload is not
    left permanently orphaned after one short PostgreSQL interruption.
    """
    attempts = _env_int(
        "VELIA_ATTACHMENTS_DELETE_ATTEMPTS",
        3,
        1,
        10,
    )
    base_delay_ms = _env_int(
        "VELIA_ATTACHMENTS_DELETE_RETRY_DELAY_MS",
        100,
        0,
        5_000,
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _delete_attachment_once(int(user_id), str(attachment_id))
        except attachment_service.AttachmentError:
            # Domain errors such as attachment_in_use are deterministic and
            # must be returned immediately rather than retried.
            raise
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            if base_delay_ms > 0:
                time.sleep((base_delay_ms * attempt) / 1000.0)

    logger.error(
        "VELIA_ATTACHMENT_DELETE_RETRIES_EXHAUSTED attachment_id=%s user_id=%s attempts=%s error=%s",
        str(attachment_id),
        int(user_id),
        attempts,
        last_error.__class__.__name__ if last_error else "unknown",
    )
    if last_error is not None:
        raise last_error
    return False


# Keep direct service callers on the same secure implementation. The mobile
# route also imports this function explicitly so import order cannot restore
# the legacy soft-delete implementation.
attachment_service.delete_attachment = delete_attachment
