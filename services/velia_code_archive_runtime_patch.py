import logging
import re
from typing import Any, Dict, Optional

from db.database import get_connection
from services import velia_code_archive_service as archive_service


logger = logging.getLogger(__name__)


def _persisted_request_user_message(request_id: str, user_id: int) -> str:
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        return ""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT content
            FROM velia_messages
            WHERE request_id=%s AND user_id=%s AND role='user'
              AND status='completed' AND deleted_at IS NULL
            ORDER BY created_at DESC, message_id DESC
            LIMIT 1
            """,
            (normalized_request_id, int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return ""
        if isinstance(row, dict):
            return str(row.get("content") or "").strip()
        return str(row[0] or "").strip()
    finally:
        cursor.close()
        conn.close()


def _source_message(prompt: str, *, user_id: int, request_id: Optional[str]) -> str:
    if str(request_id or "").strip():
        try:
            return _persisted_request_user_message(str(request_id), int(user_id))
        except Exception as exc:
            logger.warning(
                "VELIA_CODE_ARCHIVE_INTENT_LOOKUP_FAILED request_id=%s user_id=%s error=%s",
                str(request_id),
                int(user_id),
                exc.__class__.__name__,
            )
            return ""
    return str(prompt or "").strip()


def _text(message: str, *, success: bool, code: str = "", metadata: Optional[Dict[str, Any]] = None) -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    if success and metadata:
        count = int(metadata.get("file_count") or 0)
        filename = str(metadata.get("filename") or "VELIA-code.zip")
        return (
            f"Готово — собрала {count} файл(ов) в {filename}. Архив можно скачать ниже."
            if russian
            else f"Done — I packed {count} file(s) into {filename}. Download the archive below."
        )
    if code == "code_archive_completed_job_missing":
        return (
            "В этом чате пока нет завершённого Coding Agent результата, который можно упаковать в ZIP."
            if russian
            else "There is no completed Coding Agent result in this chat to package as ZIP yet."
        )
    if code in {"code_archive_no_files", "code_archive_no_final_files"}:
        return (
            "В последнем Coding Agent результате нет финальных файлов для архива."
            if russian
            else "The latest Coding Agent result has no final files to archive."
        )
    return (
        "Не удалось безопасно собрать ZIP из последнего Coding Agent результата."
        if russian
        else "I could not safely build a ZIP from the latest Coding Agent result."
    )


def install(velia_chat_service_module: Any) -> None:
    if getattr(velia_chat_service_module, "_velia_code_archive_patch_installed", False):
        return

    original_generate = velia_chat_service_module.generate_velia_chat_result
    original_serialize = velia_chat_service_module._serialize_message

    def generate_with_code_archive(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        message = _source_message(prompt, user_id=int(user_id), request_id=request_id)
        if not archive_service.is_code_archive_request(message):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return {
                "ok": True,
                "text": _text(message, success=False, code="code_archive_request_id_missing"),
                "provider": "velia_code_archive",
                "model": "code-archive-v1",
                "reason": "code_archive_request_id_missing",
                "request_id": "",
                "finish_reason": "stop",
                "usage": {},
                "estimated_cost_usd": 0.0,
            }

        try:
            metadata = archive_service.create_archive_for_latest_coding_job(
                user_id=int(user_id),
                conversation_id=str(conversation_id),
                request_id=normalized_request_id,
            )
        except archive_service.VeliaCodeArchiveError as exc:
            logger.info(
                "VELIA_CODE_ARCHIVE_NOT_CREATED user_id=%s conversation_id=%s request_id=%s code=%s",
                int(user_id),
                str(conversation_id),
                normalized_request_id,
                exc.code,
            )
            return {
                "ok": True,
                "text": _text(message, success=False, code=exc.code),
                "provider": "velia_code_archive",
                "model": "code-archive-v1",
                "reason": exc.code,
                "request_id": normalized_request_id,
                "finish_reason": "stop",
                "usage": {},
                "estimated_cost_usd": 0.0,
            }
        except Exception as exc:
            logger.exception(
                "VELIA_CODE_ARCHIVE_FAILED user_id=%s conversation_id=%s request_id=%s error=%s",
                int(user_id),
                str(conversation_id),
                normalized_request_id,
                exc.__class__.__name__,
            )
            return {
                "ok": True,
                "text": _text(message, success=False, code="code_archive_failed"),
                "provider": "velia_code_archive",
                "model": "code-archive-v1",
                "reason": "code_archive_failed",
                "request_id": normalized_request_id,
                "finish_reason": "stop",
                "usage": {},
                "estimated_cost_usd": 0.0,
            }

        logger.info(
            "VELIA_CODE_ARCHIVE_CREATED user_id=%s conversation_id=%s request_id=%s archive_id=%s files=%s bytes=%s",
            int(user_id),
            str(conversation_id),
            normalized_request_id,
            str(metadata.get("id") or ""),
            int(metadata.get("file_count") or 0),
            int(metadata.get("size_bytes") or 0),
        )
        return {
            "ok": True,
            "text": _text(message, success=True, metadata=metadata),
            "provider": "velia_code_archive",
            "model": "code-archive-v1",
            "reason": "code_archive_created",
            "request_id": normalized_request_id,
            "finish_reason": "archive_created",
            "usage": {},
            "estimated_cost_usd": 0.0,
        }

    def serialize_with_code_archive(row: Any, *, debug_usage: bool = False) -> Dict[str, Any]:
        serialized = original_serialize(row, debug_usage=debug_usage)
        if serialized.get("role") != "assistant" or serialized.get("status") != "completed":
            return serialized
        provider = str(velia_chat_service_module._row_value(row, "provider", 9, "") or "")
        if provider != "velia_code_archive":
            return serialized
        request_id = str(serialized.get("request_id") or "")
        user_id = int(velia_chat_service_module._row_value(row, "user_id", 2, 0) or 0)
        try:
            metadata = archive_service.archive_metadata_for_request(request_id, user_id)
        except Exception:
            metadata = None
        if metadata:
            serialized["type"] = "archive"
            serialized["archive"] = metadata
        else:
            serialized["type"] = "text"
            serialized.pop("archive", None)
        return serialized

    velia_chat_service_module.generate_velia_chat_result = generate_with_code_archive
    velia_chat_service_module._serialize_message = serialize_with_code_archive
    velia_chat_service_module._velia_code_archive_patch_installed = True
    logger.info("VELIA_CODE_ARCHIVE_RUNTIME_PATCH_INSTALLED")
