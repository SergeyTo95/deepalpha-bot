import json
import os
from typing import Any, Dict, Optional

from aiohttp import web

from services.velia_mobile_auth_service import authenticate_access_token
from services.velia_plugin_service import get_user_plugins, update_user_plugins

MAX_JSON_BYTES = 16 * 1024


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


def _auth(request: web.Request) -> Optional[Dict[str, Any]]:
    authorization = str(request.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    return authenticate_access_token(token) if token else None


async def _read_json(request: web.Request) -> Optional[Dict[str, Any]]:
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return None
    try:
        payload = await request.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def setup_velia_plugin_routes(app: web.Application) -> None:
    if app.get("velia_plugin_routes_installed"):
        return

    async def handle_plugins_get(request: web.Request) -> web.Response:
        if not _env_bool("VELIA_MOBILE_API_ENABLED", False):
            return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, 503)
        auth = _auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, 401)
        plugins = get_user_plugins(int(auth["user_id"]))
        return _json_response({"ok": True, "plugins": plugins})

    async def handle_plugins_patch(request: web.Request) -> web.Response:
        if not _env_bool("VELIA_MOBILE_API_ENABLED", False):
            return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, 503)
        auth = _auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, 401)
        payload = await _read_json(request)
        if payload is None:
            return _json_response({"ok": False, "error": "invalid_json"}, 400)
        updates = payload.get("plugins") if isinstance(payload.get("plugins"), dict) else payload
        allowed_updates = {
            str(key): value
            for key, value in updates.items()
            if isinstance(key, str) and isinstance(value, bool)
        }
        if not allowed_updates:
            return _json_response({"ok": False, "error": "invalid_plugin_updates"}, 400)
        plugins = update_user_plugins(int(auth["user_id"]), allowed_updates)
        return _json_response({"ok": True, "plugins": plugins})

    app.router.add_get("/mobile-api/v1/plugins", handle_plugins_get)
    app.router.add_patch("/mobile-api/v1/plugins", handle_plugins_patch)
    app["velia_plugin_routes_installed"] = True
