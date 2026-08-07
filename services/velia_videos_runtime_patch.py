import logging
from typing import Any, Dict, Optional

from db.database import get_connection
from services.velia_video_intent_service import (
    detect_video_intent,
    last_user_message_from_chat_prompt,
)
from services.velia_videos_service import (
    generate_and_store_video,
    video_metadata_for_request,
)


logger = logging.getLogger(__name__)


def _persisted_request_user_message(request_id: str, user_id: int) -> str:
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
            "VELIA_VIDEO_INTENT_MESSAGE_LOOKUP_FAILED request_id=%s user_id=%s error=%s",
            normalized_request_id,
            int(user_id),
            exc.__class__.__name__,
        )
        return ""


def _video_intent_source_message(
    prompt: str,
    *,
    user_id: int,
    request_id: Optional[str],
) -> str:
    if str(request_id or "").strip():
        return _persisted_request_user_message(str(request_id), int(user_id))
    return last_user_message_from_chat_prompt(prompt)


def install(velia_chat_service_module: Any) -> None:
    if getattr(velia_chat_service_module, "_velia_videos_patch_installed", False):
        return

    original_generate = velia_chat_service_module.generate_velia_chat_result
    original_serialize = velia_chat_service_module._serialize_message

    def generate_with_velia_videos(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        latest_message = _video_intent_source_message(
            prompt,
            user_id=int(user_id),
            request_id=request_id,
        )
        intent = detect_video_intent(latest_message)
        if not intent.requested:
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        logger.info(
            "VELIA_VIDEO_INTENT_MATCHED user_id=%s conversation_id=%s request_id=%s mode=%s prompt_chars=%s",
            int(user_id),
            str(conversation_id),
            str(request_id or ""),
            intent.mode,
            len(intent.prompt),
        )
        result = generate_and_store_video(
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=str(request_id or ""),
            original_message=latest_message,
            requested_mode=intent.mode,
            prompt=intent.prompt,
        )
        return {
            "ok": True,
            "text": str(result.get("text") or ""),
            "request_id": str(request_id or ""),
            "provider": "velyon_videos",
            "model": "draft_hd",
            "finish_reason": (
                "video_created" if result.get("video_created") else "video_not_created"
            ),
            "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
            "usage": {},
        }

    def serialize_with_velia_video(
        row: Any,
        *,
        debug_usage: bool = False,
    ) -> Dict[str, Any]:
        serialized = original_serialize(row, debug_usage=debug_usage)
        if (
            serialized.get("role") != "assistant"
            or serialized.get("status") != "completed"
        ):
            return serialized

        provider = str(
            velia_chat_service_module._row_value(row, "provider", 9, "") or ""
        )
        if provider != "velyon_videos":
            return serialized

        request_id = str(serialized.get("request_id") or "")
        user_id = velia_chat_service_module._row_value(row, "user_id", 2, 0)
        try:
            video = video_metadata_for_request(request_id, int(user_id or 0))
        except Exception as exc:
            logger.warning(
                "VELIA_VIDEO_METADATA_LOOKUP_FAILED request_id=%s user_id=%s error=%s",
                request_id,
                int(user_id or 0),
                exc.__class__.__name__,
            )
            video = None

        serialized.pop("image", None)
        if video:
            serialized["type"] = "video"
            serialized["video"] = video
        else:
            serialized["type"] = "text"
        return serialized

    velia_chat_service_module.generate_velia_chat_result = generate_with_velia_videos
    velia_chat_service_module._serialize_message = serialize_with_velia_video
    velia_chat_service_module._velia_videos_patch_installed = True
    logger.info("VELIA_VIDEOS_RUNTIME_PATCH_INSTALLED")
