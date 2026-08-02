import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from services import kimi_gateway
from services.kimi_streaming_gateway import call_kimi_stream
from services.velia_chat_latency_runtime_patch import (
    _casual_intent,
    _env_bool,
    _is_casual_message,
    _stable_prompt_cache_key,
)
from services.velia_conversation_quality_patch import memory_note_ack
from services.velia_image_intent_service import detect_image_intent
from services.velia_llm_service import _env_int, resolve_velia_provider


logger = logging.getLogger(__name__)

_STREAM_CONTEXT = threading.local()


def _latest_request_user_message(request_id: str, user_id: int) -> str:
    try:
        from db.database import get_connection

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
                (str(request_id), int(user_id)),
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
            "VELIA_STREAM_MESSAGE_LOOKUP_FAILED request_id=%s user_id=%s error=%s",
            str(request_id),
            int(user_id),
            exc.__class__.__name__,
        )
        return ""


def _should_stream_message(message: str) -> bool:
    normalized = str(message or "").strip()
    if not normalized:
        return False
    if resolve_velia_provider() != "kimi":
        return False
    if detect_image_intent(normalized).requested:
        return False
    if memory_note_ack(normalized) is not None:
        return False
    casual = _casual_intent(normalized)
    if casual is not None and casual[1] not in {"context_ack", "capabilities"}:
        return False
    return True


def _reasoning_effort_for_message(message: str) -> str:
    default_effort = kimi_gateway.kimi_reasoning_effort()
    if not _env_bool("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", True):
        return default_effort
    return "low" if _is_casual_message(message) else default_effort


def run_streaming_send(
    send_message: Callable[..., Dict[str, Any]],
    *,
    user_id: int,
    conversation_id: str,
    content: str,
    idempotency_key: str,
    on_delta: Callable[[str], None],
    on_reset: Callable[[], None],
) -> Dict[str, Any]:
    previous_delta = getattr(_STREAM_CONTEXT, "on_delta", None)
    previous_reset = getattr(_STREAM_CONTEXT, "on_reset", None)
    _STREAM_CONTEXT.on_delta = on_delta
    _STREAM_CONTEXT.on_reset = on_reset
    try:
        return send_message(
            int(user_id),
            str(conversation_id),
            str(content),
            idempotency_key=str(idempotency_key),
        )
    finally:
        if previous_delta is None:
            try:
                delattr(_STREAM_CONTEXT, "on_delta")
            except AttributeError:
                pass
        else:
            _STREAM_CONTEXT.on_delta = previous_delta
        if previous_reset is None:
            try:
                delattr(_STREAM_CONTEXT, "on_reset")
            except AttributeError:
                pass
        else:
            _STREAM_CONTEXT.on_reset = previous_reset


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_chat_streaming_patch_installed", False):
        return

    original_generate = chat_module.generate_velia_chat_result

    def generate_with_optional_streaming(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        on_delta = getattr(_STREAM_CONTEXT, "on_delta", None)
        on_reset = getattr(_STREAM_CONTEXT, "on_reset", None)
        if not callable(on_delta):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        message = _latest_request_user_message(str(request_id or ""), int(user_id))
        if not _should_stream_message(message):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        started = time.monotonic()
        selected_reasoning = _reasoning_effort_for_message(message)
        result = call_kimi_stream(
            prompt=str(prompt),
            feature="velia_chat",
            on_delta=on_delta,
            on_reset=on_reset if callable(on_reset) else None,
            origin="velia_mobile_chat_stream",
            is_background=False,
            request_id=str(request_id or ""),
            cycle_id=str(conversation_id),
            max_tokens=_env_int("VELIA_CHAT_MAX_OUTPUT_TOKENS", 1536, 128, 8192),
            user_id=int(user_id),
            prompt_cache_key=_stable_prompt_cache_key(str(conversation_id)),
            reasoning_effort=selected_reasoning,
        )
        result["request_id"] = str(request_id or "")
        text = str(result.get("text") or "").strip()
        if text:
            result["ok"] = True
            logger.info(
                "VELIA_STREAM_GENERATION_COMPLETED request_id=%s user_id=%s conversation_id=%s first_delta_ms=%s duration_ms=%s",
                str(request_id or ""),
                int(user_id),
                str(conversation_id),
                result.get("first_delta_ms"),
                int((time.monotonic() - started) * 1000),
            )
            return result

        # Preserve the existing provider/fallback path when streaming could not
        # produce a final answer. Any already emitted partial text has been
        # reset by the streaming gateway before this fallback starts.
        if result.get("fallback_allowed"):
            fallback = original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
            if isinstance(fallback, dict):
                fallback["stream_fallback_used"] = True
                fallback["stream_failure_reason"] = str(result.get("reason") or "")
            logger.warning(
                "VELIA_STREAM_FALLBACK request_id=%s user_id=%s conversation_id=%s reason=%s duration_ms=%s",
                str(request_id or ""),
                int(user_id),
                str(conversation_id),
                str(result.get("reason") or ""),
                int((time.monotonic() - started) * 1000),
            )
            return fallback

        return result

    chat_module.generate_velia_chat_result = generate_with_optional_streaming
    chat_module._velia_chat_streaming_patch_installed = True
    logger.info("VELIA_CHAT_STREAMING_RUNTIME_PATCH_INSTALLED")
