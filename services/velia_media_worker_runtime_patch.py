from __future__ import annotations

import contextvars
import hashlib
import logging
import os
from typing import Any, Dict, Optional

import services.velia_images_service as image_service
import services.velia_videos_service as video_service
from services.velia_media_worker_client import MediaWorkerError, generate_image, generate_video


logger = logging.getLogger(__name__)

_REQUEST_CONTEXT: contextvars.ContextVar[Dict[str, str]] = contextvars.ContextVar(
    "velia_media_worker_request_context",
    default={},
)
_INSTALLED = False
_ORIGINAL_IMAGE_GENERATE_AND_STORE = image_service.generate_and_store_image
_ORIGINAL_VIDEO_GENERATE_AND_STORE = video_service.generate_and_store_video


def _provider() -> str:
    # Rollout is explicit. Production switches to the worker only when Railway
    # sets VELIA_MEDIA_PROVIDER=self_hosted; otherwise the existing providers
    # keep their current behavior.
    return str(os.getenv("VELIA_MEDIA_PROVIDER", "legacy") or "legacy").strip().lower()


def _auth_token_diagnostics() -> tuple[int, str]:
    # Match the client normalization exactly, but never log the credential.
    # A short SHA-256 prefix is sufficient to compare independently computed
    # fingerprints between Railway and the worker host without exposing the
    # bearer token itself.
    token = str(os.getenv("VELIA_MEDIA_WORKER_AUTH_TOKEN", "") or "").strip()
    if not token:
        return 0, "missing"
    return len(token), hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _request_id_for(kind: str) -> str:
    context = _REQUEST_CONTEXT.get()
    request_id = str(context.get("request_id") or "").strip()
    context_kind = str(context.get("kind") or "").strip()
    if not request_id or context_kind != kind:
        raise MediaWorkerError("media_worker_request_context_missing")
    return request_id


def _image_submit_and_wait(prompt: str) -> Dict[str, Any]:
    request_id = _request_id_for("image")
    logger.info(
        "VELIA_MEDIA_WORKER_IMAGE_SUBMIT request_id=%s provider=self_hosted",
        request_id,
    )
    try:
        result = generate_image(prompt=prompt, request_id=request_id)
    except MediaWorkerError as exc:
        logger.error(
            "VELIA_MEDIA_WORKER_IMAGE_FAILED request_id=%s code=%s http_status=%s",
            request_id,
            exc.code,
            exc.http_status if exc.http_status is not None else "",
        )
        raise
    except Exception as exc:
        logger.exception(
            "VELIA_MEDIA_WORKER_IMAGE_FAILED_UNEXPECTED request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        raise
    result["estimated_cost_usd"] = 0.0
    logger.info(
        "VELIA_MEDIA_WORKER_IMAGE_COMPLETED request_id=%s artifact_id=%s sha256=%s",
        request_id,
        str(result.get("artifact_id") or "")[:80],
        str(result.get("sha256") or "")[:16],
    )
    return result


def _video_submit_and_wait(
    *,
    mode: str,
    prompt: str,
    attachment: Optional[video_service.RequestImageAttachment],
) -> Dict[str, Any]:
    request_id = _request_id_for("video")
    if mode != "t2v" or attachment is not None:
        # Stage 1 Wan worker is deliberately T2V-only. Do not silently route
        # i2v back to the paid legacy provider while self-hosted mode is on.
        raise video_service.VideoGenerationError("video_mode_not_supported")
    logger.info(
        "VELIA_MEDIA_WORKER_VIDEO_SUBMIT request_id=%s provider=self_hosted mode=t2v",
        request_id,
    )
    try:
        result = generate_video(prompt=prompt, request_id=request_id)
    except MediaWorkerError as exc:
        logger.error(
            "VELIA_MEDIA_WORKER_VIDEO_FAILED request_id=%s code=%s http_status=%s",
            request_id,
            exc.code,
            exc.http_status if exc.http_status is not None else "",
        )
        raise video_service.VideoGenerationError(
            exc.code,
            http_status=exc.http_status,
        ) from exc
    result["estimated_cost_usd"] = 0.0
    logger.info(
        "VELIA_MEDIA_WORKER_VIDEO_COMPLETED request_id=%s artifact_id=%s sha256=%s",
        request_id,
        str(result.get("artifact_id") or "")[:80],
        str(result.get("sha256") or "")[:16],
    )
    return result


def _generate_and_store_image(
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
    original_message: str,
    prompt: str,
) -> Dict[str, Any]:
    token = _REQUEST_CONTEXT.set(
        {
            "kind": "image",
            "request_id": str(request_id or ""),
        }
    )
    try:
        return _ORIGINAL_IMAGE_GENERATE_AND_STORE(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            original_message=original_message,
            prompt=prompt,
        )
    finally:
        _REQUEST_CONTEXT.reset(token)


def _generate_and_store_video(
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
    original_message: str,
    requested_mode: str,
    prompt: str,
) -> Dict[str, Any]:
    token = _REQUEST_CONTEXT.set(
        {
            "kind": "video",
            "request_id": str(request_id or ""),
        }
    )
    try:
        return _ORIGINAL_VIDEO_GENERATE_AND_STORE(
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            original_message=original_message,
            requested_mode=requested_mode,
            prompt=prompt,
        )
    finally:
        _REQUEST_CONTEXT.reset(token)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    provider = _provider()
    if provider not in {"self_hosted", "self-hosted", "velia_worker", "worker"}:
        logger.info(
            "VELIA_MEDIA_WORKER_RUNTIME_SKIPPED provider=%s legacy_provider_active=true",
            provider or "legacy",
        )
        return

    # Import here to avoid a module cycle while velia_images_runtime_patch is
    # itself importing this provider patch.
    import services.velia_images_runtime_patch as image_runtime
    import services.velia_videos_runtime_patch as video_runtime

    # The image runtime installs its legacy queue compatibility patch first.
    # Replacing the provider function here makes the self-hosted worker the only
    # active media provider while leaving legacy code available for an explicit
    # rollback via VELIA_MEDIA_PROVIDER=legacy.
    image_service._submit_and_wait = _image_submit_and_wait
    video_service._submit_and_wait = _video_submit_and_wait
    image_service.generate_and_store_image = _generate_and_store_image
    video_service.generate_and_store_video = _generate_and_store_video
    image_runtime.generate_and_store_image = _generate_and_store_image
    video_runtime.generate_and_store_video = _generate_and_store_video

    _INSTALLED = True
    token_len, token_fingerprint = _auth_token_diagnostics()
    logger.info(
        "VELIA_MEDIA_WORKER_RUNTIME_INSTALLED provider=self_hosted legacy_fallback=false auth_token_len=%d auth_token_sha256_prefix=%s",
        token_len,
        token_fingerprint,
    )
