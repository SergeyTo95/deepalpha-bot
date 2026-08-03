import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from services import gemini_gateway
from services.velia_attachment_privacy_service import (
    delete_attachment,
    scrub_legacy_failed_attachment_payloads,
)
from services.velia_attachment_public_contract import public_attachment
from services.velia_attachment_service import (
    AttachmentError,
    get_attachment,
)
from services.velia_attachment_upload_service import create_attachment_with_reservation
from services.velia_chat_service import get_conversation
from services.velia_mobile_auth_service import authenticate_access_token


logger = logging.getLogger(__name__)
_ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}
_PRIVACY_SCRUB_COMPLETE = False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _json_response(data: Dict[str, Any], status: int = 200) -> web.Response:
    response = web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str),
        status=status,
        content_type="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _bearer_token(request: web.Request) -> str:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


def _require_mobile_auth(request: web.Request) -> Optional[Dict[str, Any]]:
    token = _bearer_token(request)
    return authenticate_access_token(token) if token else None


def _attachment_api_unavailable_error() -> str:
    # The global mobile kill switch always takes precedence over the
    # feature-specific switch, matching every other mobile API surface.
    if not _env_bool("VELIA_MOBILE_API_ENABLED", False):
        return "velia_mobile_api_disabled"
    if not _env_bool("VELIA_FILE_ANALYST_ENABLED", False):
        return "velia_file_analyst_disabled"
    return ""


def _unavailable_response(error: str) -> web.Response:
    return _json_response(
        {"ok": False, "error": str(error)},
        status=503,
    )


def _error_response(error: AttachmentError) -> web.Response:
    return _json_response(
        {"ok": False, "error": error.code},
        status=error.status,
    )


def _scrub_legacy_payloads_best_effort() -> None:
    global _PRIVACY_SCRUB_COMPLETE
    if _PRIVACY_SCRUB_COMPLETE:
        return
    try:
        scrub_legacy_failed_attachment_payloads()
        _PRIVACY_SCRUB_COMPLETE = True
    except Exception as exc:
        # Route registration precedes schema bootstrap. Retry on every file
        # request until cleanup succeeds rather than blocking the web process.
        logger.warning(
            "VELIA_ATTACHMENT_LEGACY_SCRUB_DEFERRED error=%s",
            exc.__class__.__name__,
        )


async def _read_single_upload(request: web.Request) -> Tuple[str, str, bytes]:
    max_bytes = _env_int(
        "VELIA_ATTACHMENTS_MAX_BYTES",
        15 * 1024 * 1024,
        64 * 1024,
        50 * 1024 * 1024,
    )
    multipart_overhead = _env_int(
        "VELIA_ATTACHMENTS_MULTIPART_OVERHEAD_BYTES",
        64 * 1024,
        8 * 1024,
        1024 * 1024,
    )
    if (
        request.content_length is not None
        and request.content_length > max_bytes + multipart_overhead
    ):
        raise AttachmentError("attachment_too_large", status=413)
    content_type = str(request.headers.get("Content-Type") or "").lower()
    if not content_type.startswith("multipart/form-data"):
        raise AttachmentError("multipart_required", status=415)

    try:
        reader = await request.multipart()
        file_field = await reader.next()
    except Exception as exc:
        raise AttachmentError("invalid_multipart") from exc
    if file_field is None:
        raise AttachmentError("attachment_file_required")
    if file_field.name != "file":
        raise AttachmentError("invalid_attachment_form")

    filename = str(file_field.filename or "attachment")
    mime_type = str(file_field.headers.get("Content-Type") or "")
    mime_type = mime_type.split(";", 1)[0].strip().lower()
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise AttachmentError("attachment_type_not_supported", status=415)

    chunks = bytearray()
    while True:
        chunk = await file_field.read_chunk(size=64 * 1024)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > max_bytes:
            raise AttachmentError("attachment_too_large", status=413)
    if not chunks:
        raise AttachmentError("empty_attachment")

    try:
        extra_field = await reader.next()
    except Exception as exc:
        raise AttachmentError("invalid_multipart") from exc
    if extra_field is not None:
        raise AttachmentError("invalid_attachment_form")
    return filename, mime_type, bytes(chunks)


async def _cleanup_cancelled_upload(
    upload_task: "asyncio.Task[Dict[str, Any]]",
    *,
    user_id: int,
) -> None:
    """Wait for an offloaded upload and scrub it when its response was lost."""
    try:
        attachment = await asyncio.shield(upload_task)
    except asyncio.CancelledError:
        # A second cancellation should not cancel the shielded worker. Keep a
        # detached cleanup coroutine alive so the eventual row is still scrubbed.
        async def finish_cleanup() -> None:
            try:
                completed = await asyncio.shield(upload_task)
                attachment_id = str(completed.get("id") or "")
                if attachment_id:
                    await asyncio.to_thread(delete_attachment, int(user_id), attachment_id)
            except Exception:
                logger.exception(
                    "VELIA_ATTACHMENT_CANCELLED_UPLOAD_CLEANUP_FAILED user_id=%s",
                    int(user_id),
                )

        asyncio.create_task(finish_cleanup())
        return
    except Exception:
        # Failed uploads are already scrubbed by the reservation service.
        return

    attachment_id = str(attachment.get("id") or "")
    if not attachment_id:
        return
    try:
        await asyncio.to_thread(delete_attachment, int(user_id), attachment_id)
    except Exception:
        logger.exception(
            "VELIA_ATTACHMENT_CANCELLED_UPLOAD_CLEANUP_FAILED attachment_id=%s user_id=%s",
            attachment_id,
            int(user_id),
        )


async def _create_attachment_recoverably(
    *,
    user_id: int,
    conversation_id: str,
    filename: str,
    mime_type: str,
    content: bytes,
) -> Dict[str, Any]:
    # Shield the worker so cancellation of the HTTP handler cannot discard the
    # only future that knows the server-generated attachment UUID.
    upload_task = asyncio.create_task(
        asyncio.to_thread(
            create_attachment_with_reservation,
            int(user_id),
            str(conversation_id),
            filename=filename,
            mime_type=mime_type,
            content=content,
        )
    )
    try:
        return await asyncio.shield(upload_task)
    except asyncio.CancelledError as cancelled:
        await _cleanup_cancelled_upload(upload_task, user_id=int(user_id))
        raise cancelled


def setup_velia_mobile_attachment_routes(app: web.Application) -> None:
    gemini_gateway.FEATURE_FLAGS.setdefault(
        "velia_file_vision",
        "VELIA_FILE_VISION_GEMINI_ENABLED",
    )
    _scrub_legacy_payloads_best_effort()

    async def handle_attachment_create(request: web.Request) -> web.Response:
        unavailable_error = _attachment_api_unavailable_error()
        if unavailable_error:
            return _unavailable_response(unavailable_error)
        _scrub_legacy_payloads_best_effort()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        user_id = int(auth["user_id"])
        conversation_id = str(request.match_info.get("conversation_id") or "")
        conversation = await asyncio.to_thread(
            get_conversation,
            user_id,
            conversation_id,
        )
        if not conversation:
            return _json_response(
                {"ok": False, "error": "conversation_not_found"},
                status=404,
            )
        try:
            filename, mime_type, content = await _read_single_upload(request)
            attachment = await _create_attachment_recoverably(
                user_id=user_id,
                conversation_id=conversation_id,
                filename=filename,
                mime_type=mime_type,
                content=content,
            )
        except AttachmentError as exc:
            return _error_response(exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _json_response(
                {"ok": False, "error": "attachment_upload_failed"},
                status=500,
            )
        return _json_response(
            {"ok": True, "attachment": public_attachment(attachment)},
            status=201,
        )

    async def handle_attachment_get(request: web.Request) -> web.Response:
        unavailable_error = _attachment_api_unavailable_error()
        if unavailable_error:
            return _unavailable_response(unavailable_error)
        _scrub_legacy_payloads_best_effort()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        attachment = await asyncio.to_thread(
            get_attachment,
            int(auth["user_id"]),
            str(request.match_info.get("attachment_id") or ""),
        )
        if not attachment:
            return _json_response(
                {"ok": False, "error": "attachment_not_found"},
                status=404,
            )
        return _json_response(
            {"ok": True, "attachment": public_attachment(attachment)}
        )

    async def handle_attachment_delete(request: web.Request) -> web.Response:
        unavailable_error = _attachment_api_unavailable_error()
        if unavailable_error:
            return _unavailable_response(unavailable_error)
        _scrub_legacy_payloads_best_effort()
        auth = _require_mobile_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            deleted = await asyncio.to_thread(
                delete_attachment,
                int(auth["user_id"]),
                str(request.match_info.get("attachment_id") or ""),
            )
        except AttachmentError as exc:
            return _error_response(exc)
        if not deleted:
            return _json_response(
                {"ok": False, "error": "attachment_not_found"},
                status=404,
            )
        return _json_response({"ok": True})

    app.router.add_post(
        "/mobile-api/v1/conversations/{conversation_id}/attachments",
        handle_attachment_create,
    )
    app.router.add_get(
        "/mobile-api/v1/attachments/{attachment_id}",
        handle_attachment_get,
    )
    app.router.add_delete(
        "/mobile-api/v1/attachments/{attachment_id}",
        handle_attachment_delete,
    )
