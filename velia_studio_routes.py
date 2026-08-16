import asyncio
import json
from typing import Any, Dict, Optional

from aiohttp import web

from services.velia_mobile_auth_service import authenticate_access_token
from services.velia_studio_generation_service import generate_studio_turn
from services.velia_studio_recovery_service import generation_for_client_request
from services.velia_studio_service import (
    StudioError,
    _ensure_schema,
    create_reference_asset,
    create_session,
    get_reference_content,
    get_session,
    list_messages,
    list_sessions,
    studio_enabled,
    verify_reference_signature,
)
from services.velia_studio_upload_quota import assert_studio_upload_capacity
from services.velia_studio_video_duration_client import studio_video_duration_options

MAX_JSON_BYTES = 96 * 1024
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
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
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def _require_auth(request: web.Request) -> Optional[Dict[str, Any]]:
    token = _bearer_token(request)
    return authenticate_access_token(token) if token else None


async def _read_json(request: web.Request) -> Optional[Dict[str, Any]]:
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return None
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _error(exc: StudioError) -> web.Response:
    return _json_response({"ok": False, "error": exc.code}, status=exc.status)


def setup_velia_studio_routes(app: web.Application) -> None:
    async def resume_pending_video_jobs(_app: web.Application) -> None:
        if studio_enabled():
            await asyncio.to_thread(_ensure_schema)

    app.on_startup.append(resume_pending_video_jobs)

    async def status(request: web.Request) -> web.Response:
        if not _require_auth(request):
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        return _json_response({
            "ok": True,
            "enabled": studio_enabled(),
            "modes": ["image", "video"],
            "image": {"max_references": 4, "reference_editing": True},
            "video": {
                "draft": True,
                # Compatibility default for older APKs. New clients must use
                # duration_options_seconds and send duration_seconds explicitly.
                "duration_seconds": 5,
                "duration_options_seconds": list(studio_video_duration_options()),
                "resolution": "640x368",
                "max_references": 1,
                "eta_supported": True,
            },
        })

    async def sessions_list(request: web.Request) -> web.Response:
        auth = _require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not studio_enabled():
            return _json_response({"ok": False, "error": "studio_disabled"}, status=503)
        try:
            sessions = list_sessions(
                int(auth["user_id"]),
                mode=str(request.query.get("mode") or "").strip() or None,
                limit=int(request.query.get("limit") or 100),
            )
        except (StudioError, ValueError) as exc:
            return _error(exc) if isinstance(exc, StudioError) else _json_response({"ok": False, "error": "invalid_limit"}, status=400)
        return _json_response({"ok": True, "sessions": sessions})

    async def sessions_create(request: web.Request) -> web.Response:
        auth = _require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not studio_enabled():
            return _json_response({"ok": False, "error": "studio_disabled"}, status=503)
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        try:
            session = create_session(int(auth["user_id"]), str(data.get("mode") or ""), str(data.get("title") or ""))
        except StudioError as exc:
            return _error(exc)
        return _json_response({"ok": True, "session": session}, status=201)

    async def session_get(request: web.Request) -> web.Response:
        auth = _require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not studio_enabled():
            return _json_response({"ok": False, "error": "studio_disabled"}, status=503)
        session = get_session(int(auth["user_id"]), str(request.match_info.get("session_id") or ""))
        if not session:
            return _json_response({"ok": False, "error": "studio_session_not_found"}, status=404)
        return _json_response({"ok": True, "session": session})

    async def messages(request: web.Request) -> web.Response:
        auth = _require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not studio_enabled():
            return _json_response({"ok": False, "error": "studio_disabled"}, status=503)
        try:
            values = list_messages(
                int(auth["user_id"]),
                str(request.match_info.get("session_id") or ""),
                limit=int(request.query.get("limit") or 200),
            )
        except StudioError as exc:
            return _error(exc)
        except ValueError:
            return _json_response({"ok": False, "error": "invalid_limit"}, status=400)
        return _json_response({"ok": True, "messages": values})

    async def recover(request: web.Request) -> web.Response:
        auth = _require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not studio_enabled():
            return _json_response({"ok": False, "error": "studio_disabled"}, status=503)
        client_request_id = str(request.query.get("client_request_id") or "").strip()
        if not client_request_id or len(client_request_id) > 200:
            return _json_response({"ok": False, "error": "studio_invalid_idempotency_key"}, status=400)
        generation = await asyncio.to_thread(
            generation_for_client_request,
            int(auth["user_id"]),
            str(request.match_info.get("session_id") or ""),
            client_request_id,
        )
        return _json_response({"ok": True, "generation": generation})

    async def asset_upload(request: web.Request) -> web.Response:
        auth = _require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not studio_enabled():
            return _json_response({"ok": False, "error": "studio_disabled"}, status=503)
        if not request.content_type.startswith("multipart/"):
            return _json_response({"ok": False, "error": "studio_multipart_required"}, status=415)
        try:
            reader = await request.multipart()
            part = await reader.next()
            while part is not None and part.name != "file":
                part = await reader.next()
            if part is None:
                return _json_response({"ok": False, "error": "studio_reference_file_required"}, status=400)
            buffer = bytearray()
            while True:
                chunk = await part.read_chunk(size=64 * 1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > MAX_UPLOAD_BYTES:
                    return _json_response({"ok": False, "error": "studio_reference_too_large"}, status=413)
            await asyncio.to_thread(
                assert_studio_upload_capacity,
                int(auth["user_id"]),
                len(buffer),
            )
            asset = await asyncio.to_thread(
                create_reference_asset,
                int(auth["user_id"]),
                str(request.match_info.get("session_id") or ""),
                filename=str(part.filename or "reference"),
                mime_type=str(part.headers.get("Content-Type") or "application/octet-stream"),
                content=bytes(buffer),
            )
        except StudioError as exc:
            return _error(exc)
        except Exception:
            return _json_response({"ok": False, "error": "studio_reference_upload_failed"}, status=500)
        return _json_response({"ok": True, "asset": asset}, status=201)

    async def generate(request: web.Request) -> web.Response:
        auth = _require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not studio_enabled():
            return _json_response({"ok": False, "error": "studio_disabled"}, status=503)
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        key = str(request.headers.get("Idempotency-Key") or data.get("idempotency_key") or "").strip()
        try:
            duration_seconds = int(data.get("duration_seconds", 5) or 5)
        except (TypeError, ValueError):
            return _json_response({"ok": False, "error": "studio_video_duration_not_supported"}, status=400)
        if duration_seconds not in studio_video_duration_options():
            return _json_response({"ok": False, "error": "studio_video_duration_not_supported"}, status=400)
        try:
            result = await asyncio.to_thread(
                generate_studio_turn,
                user_id=int(auth["user_id"]),
                session_id=str(request.match_info.get("session_id") or ""),
                prompt=str(data.get("prompt") or ""),
                client_request_id=key,
                reference_asset_ids=data.get("reference_asset_ids"),
                duration_seconds=duration_seconds,
            )
        except StudioError as exc:
            return _error(exc)
        except Exception:
            return _json_response({"ok": False, "error": "studio_generation_failed"}, status=500)
        generation = result.get("generation") if isinstance(result, dict) else None
        response_status = (
            200
            if result.get("duplicate")
            else 202
            if isinstance(generation, dict) and generation.get("status") == "pending"
            else 201
        )
        return _json_response({"ok": True, **result}, status=response_status)

    async def asset_content(request: web.Request) -> web.Response:
        asset_id = str(request.match_info.get("asset_id") or "").strip()
        try:
            user_id = int(request.query.get("user_id") or 0)
            expires_at = int(request.query.get("expires") or 0)
        except (TypeError, ValueError):
            raise web.HTTPForbidden(text="Studio asset access is unavailable")
        signature = str(request.query.get("signature") or "")
        if not asset_id or user_id <= 0 or not verify_reference_signature(asset_id, user_id, expires_at, signature):
            raise web.HTTPForbidden(text="Studio asset access is unavailable")
        asset = get_reference_content(asset_id, user_id)
        if not asset:
            raise web.HTTPNotFound(text="Studio asset not found")
        return web.Response(
            body=asset["bytes"],
            content_type=asset["mime_type"],
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    app.router.add_get("/mobile-api/v1/studio/status", status)
    app.router.add_get("/mobile-api/v1/studio/sessions", sessions_list)
    app.router.add_post("/mobile-api/v1/studio/sessions", sessions_create)
    app.router.add_get("/mobile-api/v1/studio/sessions/{session_id}", session_get)
    app.router.add_get("/mobile-api/v1/studio/sessions/{session_id}/messages", messages)
    app.router.add_get("/mobile-api/v1/studio/sessions/{session_id}/recover", recover)
    app.router.add_post("/mobile-api/v1/studio/sessions/{session_id}/assets", asset_upload)
    app.router.add_post("/mobile-api/v1/studio/sessions/{session_id}/generate", generate)
    app.router.add_get("/api/mobile/studio/assets/{asset_id}/content", asset_content)
