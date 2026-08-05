from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_scheduler_service as scheduler

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/agent/schedules"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _json_error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(exc, scheduler.AgentScheduleError):
        return routes_module._json_response(
            {"ok": False, "error": exc.code, "detail": exc.detail},
            status=exc.status,
        )
    logger.exception("VELIA_AGENT_SCHEDULE_ROUTE_FAILED")
    return routes_module._json_response(
        {"ok": False, "error": "velia_agent_schedule_internal_error"},
        status=500,
    )


def _require_available(routes_module: Any) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not scheduler.scheduler_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_agent_scheduler_disabled"},
            status=503,
        )
    return None


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise scheduler.AgentScheduleError("velia_agent_schedule_json_invalid") from exc
    if not isinstance(value, dict):
        raise scheduler.AgentScheduleError("velia_agent_schedule_json_invalid")
    return value


def setup_velia_agent_scheduler_routes(app: web.Application, routes_module: Any) -> None:
    if app.get("velia_agent_scheduler_routes_installed"):
        scheduler.install_agent_scheduler(app)
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        return routes_module._json_response(
            {
                "ok": True,
                "enabled": scheduler.scheduler_enabled(),
                "max_schedules": scheduler._env_int(
                    "VELIA_AGENT_MAX_SCHEDULES_PER_USER",
                    20,
                    1,
                    100,
                ),
                "supported_kinds": ["daily", "weekly", "interval_hours"],
                "writes_require_approval": True,
            }
        )

    async def list_all(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(scheduler.list_schedules, int(auth["user_id"]))
            return routes_module._json_response({"ok": True, "schedules": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            item = await asyncio.to_thread(
                scheduler.create_schedule,
                int(auth["user_id"]),
                str(payload.get("instruction") or ""),
                str(payload.get("timezone") or ""),
                payload.get("schedule"),
                payload.get("actions"),
            )
            return routes_module._json_response({"ok": True, "schedule": item}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_one(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                scheduler.get_schedule,
                int(auth["user_id"]),
                request.match_info["schedule_id"],
            )
            return routes_module._json_response({"ok": True, "schedule": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def toggle(request: web.Request, enabled: bool) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                scheduler.set_schedule_enabled,
                int(auth["user_id"]),
                request.match_info["schedule_id"],
                enabled,
            )
            return routes_module._json_response({"ok": True, "schedule": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def enable(request: web.Request) -> web.Response:
        return await toggle(request, True)

    async def disable(request: web.Request) -> web.Response:
        return await toggle(request, False)

    async def delete(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            await asyncio.to_thread(
                scheduler.delete_schedule,
                int(auth["user_id"]),
                request.match_info["schedule_id"],
            )
            return routes_module._json_response({"ok": True})
        except Exception as exc:
            return _json_error(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_get(_PREFIX, list_all)
    app.router.add_post(_PREFIX, create)
    app.router.add_get(f"{_PREFIX}/{{schedule_id}}", get_one)
    app.router.add_post(f"{_PREFIX}/{{schedule_id}}/enable", enable)
    app.router.add_post(f"{_PREFIX}/{{schedule_id}}/disable", disable)
    app.router.add_delete(f"{_PREFIX}/{{schedule_id}}", delete)
    app["velia_agent_scheduler_routes_installed"] = True
    scheduler.install_agent_scheduler(app)
