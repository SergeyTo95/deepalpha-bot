import logging
from typing import Any, Dict, Optional

from db.database import get_connection
from services.velia_attachment_routing_service import request_message_has_attachments
from services.velia_image_intent_service import (
    detect_image_intent,
    last_user_message_from_chat_prompt,
)
from services.velia_images_queue_runtime_patch import install as install_queue_runtime
from services.velia_images_service import (
    image_metadata_for_request,
    generate_and_store_image,
)
from services.velia_media_worker_runtime_patch import install as install_media_worker_runtime

logger = logging.getLogger(__name__)


def _persisted_request_user_message(request_id: str, user_id: int) -> str:
    """Read only the real submitted user text used by deterministic routers."""
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        return ""
    try:
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
    except Exception as exc:
        logger.warning(
            "VELIA_IMAGE_INTENT_MESSAGE_LOOKUP_FAILED request_id=%s user_id=%s error=%s",
            normalized_request_id,
            int(user_id),
            exc.__class__.__name__,
        )
        return ""


def _image_intent_source_message(
    prompt: str,
    *,
    user_id: int,
    request_id: Optional[str],
) -> str:
    # A persisted request id is the trust boundary: deterministic action
    # routing must inspect only the actual submitted message, never extracted
    # attachment text appended to the LLM prompt. Prompt parsing remains only
    # as a compatibility fallback for legacy/internal calls without a request.
    if str(request_id or "").strip():
        return _persisted_request_user_message(str(request_id), int(user_id))
    return last_user_message_from_chat_prompt(prompt)


def install(velia_chat_service_module: Any) -> None:
    if getattr(velia_chat_service_module, "_velia_images_patch_installed", False):
        return

    install_queue_runtime()
    # Self-hosted mode intentionally replaces the legacy Fal/Reve and BFL
    # submitters after their compatibility patches are installed. The legacy
    # implementations remain in code for an explicit env rollback only.
    install_media_worker_runtime()
    original_generate = velia_chat_service_module.generate_velia_chat_result
    original_serialize = velia_chat_service_module._serialize_message

    def generate_with_velia_images(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Reference-image generation is not implemented yet. Attachment-backed
        # requests must stay in the normal File Analyst path so the attached
        # image/document is actually analyzed rather than silently ignored by
        # a paid text-to-image call. The lookup fails closed on database errors.
        if request_message_has_attachments(request_id, int(user_id)):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        latest_message = _image_intent_source_message(
            prompt,
            user_id=int(user_id),
            request_id=request_id,
        )
        intent = detect_image_intent(latest_message)
        if not intent.requested:
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        logger.info(
            "VELIA_IMAGE_INTENT_MATCHED user_id=%s conversation_id=%s request_id=%s prompt_chars=%s",
            int(user_id),
            str(conversation_id),
            str(request_id or ""),
            len(intent.prompt),
        )
        result = generate_and_store_image(
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=str(request_id or ""),
            original_message=latest_message,
            prompt=intent.prompt,
        )
        return {
            "ok": True,
            "text": str(result.get("text") or ""),
            "request_id": str(request_id or ""),
            "provider": "velyon_images",
            "model": "quality",
            "finish_reason": "image_created" if result.get("image_created") else "image_not_created",
            "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
            "usage": {},
        }

    def serialize_with_velia_image(row: Any, *, debug_usage: bool = False) -> Dict[str, Any]:
        serialized = original_serialize(row, debug_usage=debug_usage)
        if serialized.get("role") != "assistant" or serialized.get("status") != "completed":
            return serialized
        provider = str(
            velia_chat_service_module._row_value(row, "provider", 9, "") or ""
        )
        if provider != "velyon_images":
            serialized["type"] = "text"
            return serialized

        request_id = str(serialized.get("request_id") or "")
        user_id = velia_chat_service_module._row_value(row, "user_id", 2, 0)
        try:
            image = image_metadata_for_request(request_id, int(user_id or 0))
        except Exception:
            image = None
        if image:
            serialized["type"] = "image"
            serialized["image"] = image
        else:
            serialized["type"] = "text"
        return serialized

    velia_chat_service_module.generate_velia_chat_result = generate_with_velia_images
    velia_chat_service_module._serialize_message = serialize_with_velia_image
    velia_chat_service_module._velia_images_patch_installed = True
    logger.info("VELIA_IMAGES_RUNTIME_PATCH_INSTALLED")
