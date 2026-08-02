import asyncio
import json
import os
from typing import Any, Dict, Optional

from aiohttp import web

from services.velia_mobile_auth_service import authenticate_access_token
from services.velia_user_profile_service import get_user_profile, update_user_profile


MAX_JSON_BYTES = 8 * 1024


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


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


def _token(request: web.Request) -> str:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


async def _auth(request: web.Request) -> Optional[Dict[str, Any]]:
    token = _token(request)
    if not token:
        return None
    return await asyncio.to_thread(authenticate_access_token, token)


async def _read_json(request: web.Request) -> Optional[Dict[str, Any]]:
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return None
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def setup_velia_profile_routes(app: web.Application) -> None:
    if app.get("velia_profile_routes_installed"):
        return

    async def handle_profile_get(request: web.Request) -> web.Response:
        if not _env_bool("VELIA_MOBILE_API_ENABLED", False):
            return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, 503)
        auth = await _auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, 401)
        profile = await asyncio.to_thread(get_user_profile, int(auth["user_id"]))
        return _json_response({"ok": True, "profile": profile})

    async def handle_profile_patch(request: web.Request) -> web.Response:
        if not _env_bool("VELIA_MOBILE_API_ENABLED", False):
            return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, 503)
        auth = await _auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, 401)
        payload = await _read_json(request)
        if payload is None:
            return _json_response({"ok": False, "error": "invalid_json"}, 400)

        has_name = "preferred_name" in payload
        has_about = "about_me" in payload
        if not has_name and not has_about:
            return _json_response({"ok": False, "error": "invalid_profile_update"}, 400)
        try:
            kwargs: Dict[str, Any] = {}
            if has_name:
                kwargs["preferred_name"] = payload.get("preferred_name")
            if has_about:
                kwargs["about_me"] = payload.get("about_me")
            profile = await asyncio.to_thread(
                update_user_profile,
                int(auth["user_id"]),
                **kwargs,
            )
        except ValueError as exc:
            return _json_response({"ok": False, "error": str(exc)}, 400)
        return _json_response({"ok": True, "profile": profile})

    app.router.add_get("/mobile-api/v1/profile", handle_profile_get)
    app.router.add_patch("/mobile-api/v1/profile", handle_profile_patch)
    app["velia_profile_routes_installed"] = True
