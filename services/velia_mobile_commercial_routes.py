from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from aiohttp import web

from services.velia_mobile_commercial_service import (
    commercial_state_for_user,
    mobile_catalog,
    verify_google_play_purchase,
)


MAX_JSON_BYTES = 16 * 1024


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


async def _read_json(request: web.Request) -> Dict[str, Any] | None:
    if request.content_length is not None and request.content_length > MAX_JSON_BYTES:
        return None
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def setup_velia_mobile_commercial_routes(app: web.Application, mobile_routes_module: Any) -> None:
    require_auth = getattr(mobile_routes_module, "_require_mobile_auth", None)
    api_available = getattr(mobile_routes_module, "_mobile_api_available", None)
    if not callable(require_auth) or not callable(api_available):
        raise RuntimeError("VELIA mobile auth boundary unavailable")

    async def handle_catalog(request: web.Request) -> web.Response:
        if not api_available():
            return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
        auth = require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        return _json_response(mobile_catalog())

    async def handle_account(request: web.Request) -> web.Response:
        if not api_available():
            return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
        auth = require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        result = await asyncio.to_thread(commercial_state_for_user, int(auth["user_id"]))
        return _json_response(result, status=200 if result.get("ok") else 404)

    async def handle_google_play_verify(request: web.Request) -> web.Response:
        if not api_available():
            return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
        auth = require_auth(request)
        if not auth:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await _read_json(request)
        if data is None:
            return _json_response({"ok": False, "error": "invalid_json"}, status=400)
        result = await asyncio.to_thread(
            verify_google_play_purchase,
            int(auth["user_id"]),
            str(data.get("product_id") or ""),
            str(data.get("purchase_token") or ""),
        )
        if result.get("ok"):
            return _json_response(result)
        error = str(result.get("error") or "verification_failed")
        if error in {"google_play_billing_not_ready"}:
            status = 503
        elif error in {"purchase_claimed_by_another_user", "account_mismatch"}:
            status = 409
        elif error.startswith("google_") or error.startswith("google_play_http_"):
            status = 502
        else:
            status = 400
        return _json_response(result, status=status)

    app.router.add_get("/mobile-api/v1/economy/catalog", handle_catalog)
    app.router.add_get("/mobile-api/v1/economy/me", handle_account)
    app.router.add_post("/mobile-api/v1/economy/google-play/verify", handle_google_play_verify)
