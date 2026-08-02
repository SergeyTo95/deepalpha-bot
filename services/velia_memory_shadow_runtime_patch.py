import logging
from typing import Any, Dict

from services.velia_memory_shadow_service import enqueue_completed_turn


logger = logging.getLogger(__name__)


def install(velia_chat_service_module: Any, velia_mobile_routes_module: Any) -> None:
    if getattr(velia_chat_service_module, "_velia_memory_shadow_patch_installed", False):
        return

    original_send_message = velia_mobile_routes_module.send_message

    def send_message_with_shadow_capture(
        user_id: int,
        conversation_id: str,
        content: str,
        *,
        idempotency_key: str,
        attachment_ids: Any = None,
    ) -> Dict[str, Any]:
        result = original_send_message(
            user_id,
            conversation_id,
            content,
            idempotency_key=idempotency_key,
            attachment_ids=attachment_ids,
        )
        if not result.get("ok") or result.get("duplicate"):
            return result

        assistant = result.get("assistant_message")
        if not isinstance(assistant, dict):
            return result
        assistant_message_id = str(assistant.get("id") or "").strip()
        user_message_id = str(assistant.get("reply_to_message_id") or "").strip()
        assistant_content = str(assistant.get("content") or "").strip()
        if not assistant_message_id or not user_message_id or not assistant_content:
            return result

        try:
            enqueue_completed_turn(
                user_id=int(user_id),
                conversation_id=str(conversation_id),
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                # Keep Memory Shadow text-only. Attachment bytes and extracted
                # document content stay inside the private attachment subsystem.
                user_content=str(content or ""),
                assistant_content=assistant_content,
            )
        except Exception as exc:
            # Shadow memory is strictly fail-open. A queue or database failure
            # must never change a successful chat response.
            logger.warning(
                "VELIA_MEMORY_SHADOW_ENQUEUE_SKIPPED user_id=%s conversation_id=%s error=%s",
                int(user_id),
                str(conversation_id),
                exc.__class__.__name__,
            )
        return result

    velia_chat_service_module.send_message = send_message_with_shadow_capture
    velia_mobile_routes_module.send_message = send_message_with_shadow_capture
    velia_chat_service_module._velia_memory_shadow_patch_installed = True
    logger.info("VELIA_MEMORY_SHADOW_RUNTIME_PATCH_INSTALLED")
