from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_project_service as project_service
from services import velia_software_factory_lead_service as factory
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/developer/factory"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _json_error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(exc, (SoftwareFactoryError, autopilot.CodingAutopilotError, project_service.DeveloperProjectError)):
        return routes_module._json_response(
            {
                "ok": False,
                "error": str(getattr(exc, "code", "velia_factory_failed")),
                "detail": str(getattr(exc, "detail", "")),
            },
            status=int(getattr(exc, "status", 400)),
        )
    logger.exception("VELIA_SOFTWARE_FACTORY_ROUTE_FAILED")
    return routes_module._json_response({"ok": False, "error": "velia_factory_internal_error"}, status=500)


def _require_available(routes_module: Any, *, execution: bool = False) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not project_service.developer_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_developer_disabled"}, status=503)
    if not factory.software_factory_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_software_factory_disabled"}, status=503)
    if execution and not autopilot.autopilot_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_coding_autopilot_disabled"}, status=503)
    return None


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise SoftwareFactoryError("velia_factory_json_invalid") from exc
    if not isinstance(value, dict):
        raise SoftwareFactoryError("velia_factory_json_invalid")
    return value


def setup_velia_software_factory_routes(app: web.Application, routes_module: Any) -> None:
    if app.get("velia_software_factory_routes_installed"):
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
                "enabled": factory.software_factory_enabled(),
                "developer_enabled": project_service.developer_enabled(),
                "autopilot_enabled": autopilot.autopilot_enabled(),
                "stage": 1,
                "pipeline": [
                    "project_spec",
                    "project_brain",
                    "state_machine",
                    "event_log",
                    "task_dag",
                    "clarifier",
                    "lead",
                ],
                "execution_owner": "coding_autopilot",
                "completion_scope": "review_ready",
            }
        )

    async def create(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(factory.create_run, int(auth["user_id"]), await _body(request))
            return routes_module._json_response({"ok": True, "run": item}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(factory.get_run, int(auth["user_id"]), request.match_info["run_id"])
            return routes_module._json_response({"ok": True, "run": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def events(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            try:
                limit = min(500, max(1, int(request.query.get("limit") or 200)))
            except (TypeError, ValueError):
                limit = 200
            items = await asyncio.to_thread(
                factory.list_events,
                int(auth["user_id"]),
                request.match_info["run_id"],
                limit,
            )
            return routes_module._json_response({"ok": True, "events": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def clarify(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else payload
            item = await asyncio.to_thread(
                factory.answer_clarifications,
                int(auth["user_id"]),
                request.match_info["run_id"],
                answers,
            )
            return routes_module._json_response({"ok": True, "run": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def advance(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module, execution=True)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(factory.advance_run, int(auth["user_id"]), request.match_info["run_id"])
            return routes_module._json_response({"ok": True, "run": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_post(f"{_PREFIX}/runs", create)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}", get)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/events", events)
    app.router.add_post(f"{_PREFIX}/runs/{{run_id}}/clarifications", clarify)
    app.router.add_post(f"{_PREFIX}/runs/{{run_id}}/advance", advance)
    app["velia_software_factory_routes_installed"] = True
    logger.info("VELIA_SOFTWARE_FACTORY_ROUTES_INSTALLED")
