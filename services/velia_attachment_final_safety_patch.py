import io
from typing import Any, List

from db.database import get_connection
from services import velia_attachment_service as attachment_service


def _safe_extract_pdf(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise attachment_service.AttachmentError(
            "pdf_parser_unavailable",
            status=503,
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if bool(getattr(reader, "is_encrypted", False)):
            raise attachment_service.AttachmentError(
                "encrypted_pdf_not_supported",
                status=415,
            )
        # Materialize the page tree inside the protected parser boundary.
        # Some malformed PDFs initialize PdfReader successfully and fail only
        # while resolving reader.pages or enumerating indirect page objects.
        pages = list(reader.pages)
    except attachment_service.AttachmentError:
        raise
    except Exception as exc:
        raise attachment_service.AttachmentError(
            "invalid_pdf",
            status=415,
        ) from exc

    max_pages = attachment_service._env_int(
        "VELIA_ATTACHMENTS_PDF_MAX_PAGES",
        200,
        1,
        1000,
    )
    if len(pages) > max_pages:
        raise attachment_service.AttachmentError(
            "pdf_too_many_pages",
            status=413,
        )

    chunks: List[str] = []
    for page in pages:
        try:
            page_text = str(page.extract_text() or "").strip()
        except Exception:
            page_text = ""
        if page_text:
            chunks.append(page_text)
    text = attachment_service._normalize_text("\n\n".join(chunks))
    if not text:
        raise attachment_service.AttachmentError(
            "document_has_no_readable_text",
            status=422,
        )
    return text


def delete_conversation(user_id: int, conversation_id: str) -> bool:
    """Soft-delete a conversation and scrub every linked attachment atomically."""
    conn = get_connection()
    cursor = conn.cursor()
    now = attachment_service._utcnow()
    try:
        cursor.execute(
            """
            SELECT conversation_id
            FROM velia_conversations
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            FOR UPDATE
            """,
            (str(conversation_id), int(user_id)),
        )
        if not cursor.fetchone():
            conn.rollback()
            return False

        cursor.execute(
            """
            SELECT attachment_id
            FROM velia_attachments
            WHERE conversation_id=%s AND user_id=%s
            FOR UPDATE
            """,
            (str(conversation_id), int(user_id)),
        )
        attachment_ids = [
            str(row.get("attachment_id") if isinstance(row, dict) else row[0])
            for row in cursor.fetchall() or []
        ]

        if attachment_ids:
            cursor.execute(
                """
                DELETE FROM velia_message_attachments
                WHERE attachment_id = ANY(%s)
                """,
                (attachment_ids,),
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
                    deleted_at=COALESCE(deleted_at, %s)
                WHERE conversation_id=%s AND user_id=%s
                """,
                (
                    b"",
                    now,
                    str(conversation_id),
                    int(user_id),
                ),
            )

        cursor.execute(
            """
            UPDATE velia_conversations
            SET deleted_at=%s, updated_at=%s
            WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
            """,
            (now, now, str(conversation_id), int(user_id)),
        )
        changed = bool(cursor.rowcount)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def install(chat_module: Any, routes_module: Any) -> None:
    attachment_service._extract_pdf = _safe_extract_pdf
    chat_module.delete_conversation = delete_conversation
    routes_module.delete_conversation = delete_conversation
