from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from aiohttp import web

from services import velia_agent_connector_crypto_service as crypto
from services import velia_agent_google_calendar_service as calendar

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/agent/connectors/google-calendar"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _json_error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(exc, (calendar.GoogleCalendarError, crypto.ConnectorCryptoError)):
        return routes_module._json_response(
            {"ok": False, "error": exc.code, "detail": getattr(exc, "detail", "")},
            status=int(getattr(exc, "status", 400)),
        )
    logger.exception("VELIA_GOOGLE_CALENDAR_ROUTE_FAILED")
    return routes_module._json_response(
        {"ok": False, "error": "velia_google_calendar_internal_error"},
        status=500,
    )


def _app_redirect(status: str, error: str = "") -> str:
    base = str(
        os.getenv("VELIA_GOOGLE_CALENDAR_APP_REDIRECT")
        or "velia://agent/google-calendar-connected"
    ).strip()
    if not (base.startswith("velia://") or base.startswith("https://")):
        base = "velia://agent/google-calendar-connected"
    query = {"status": str(status)}
    if error:
        query["error"] = str(error)[:120]
    separator = "&" if "?" in base else "?"
    return base + separator + urlencode(query)


def setup_velia_google_calendar_routes(app: web.Application, routes_module: Any) -> None:
    if app.get("velia_google_calendar_routes_installed"):
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            value = await asyncio.to_thread(calendar.connection_status, int(auth["user_id"]))
            return routes_module._json_response({"ok": True, **value})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def connect(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            value = await asyncio.to_thread(calendar.create_authorization_url, int(auth["user_id"]))
            return routes_module._json_response({"ok": True, **value})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def callback(request: web.Request) -> web.StreamResponse:
        provider_error = str(request.query.get("error") or "").strip()
        if provider_error:
            raise web.HTTPFound(_app_redirect("error", "authorization_denied"))
        state = str(request.query.get("state") or "")
        code = str(request.query.get("code") or "")
        try:
            await asyncio.to_thread(calendar.connect_with_code, state, code)
            raise web.HTTPFound(_app_redirect("success"))
        except web.HTTPException:
            raise
        except Exception as exc:
            code_value = str(getattr(exc, "code", "connection_failed"))[:120]
            logger.warning("VELIA_GOOGLE_CALENDAR_CALLBACK_FAILED code=%s", code_value)
            raise web.HTTPFound(_app_redirect("error", code_value))

    async def disconnect(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            await asyncio.to_thread(calendar.disconnect, int(auth["user_id"]))
            return routes_module._json_response({"ok": True, "connected": False})
        except Exception as exc:
            return _json_error(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_get(f"{_PREFIX}/connect", connect)
    app.router.add_get(f"{_PREFIX}/callback", callback)
    app.router.add_delete(f"{_PREFIX}", disconnect)
    app["velia_google_calendar_routes_installed"] = True
