from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_job_service as jobs
from services import velia_agent_runtime_service as runtime
from services.velia_agent_protocol_service import AgentProtocolError

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/agent"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _json_error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(exc, (runtime.AgentRuntimeError, jobs.AgentJobError, AgentProtocolError)):
        return routes_module._json_response(
            {"ok": False, "error": exc.code, "detail": getattr(exc, "detail", "")},
            status=int(getattr(exc, "status", 400)),
        )
    logger.exception("VELIA_AGENT_ROUTE_FAILED")
    return routes_module._json_response({"ok": False, "error": "velia_agent_internal_error"}, status=500)


def _require_enabled(routes_module: Any) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not runtime.agent_core_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_agent_core_disabled"}, status=503)
    return None


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise runtime.AgentRuntimeError("velia_agent_json_invalid") from exc
    if not isinstance(value, dict):
        raise runtime.AgentRuntimeError("velia_agent_json_invalid")
    return value


def setup_velia_agent_routes(app: web.Application, routes_module: Any) -> None:
    if app.get("velia_agent_routes_installed"):
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        enabled = runtime.agent_core_enabled()
        return routes_module._json_response(
            {
                "ok": True,
                "enabled": enabled,
                "brand": "VELIA",
                "core": "Velyon Core",
                "approval_gated": True,
                "max_actions": 8,
                "tools": runtime.public_tools() if enabled else [],
            }
        )

    async def tools_list(request: web.Request) -> web.Response:
        blocked = _require_enabled(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        return routes_module._json_response({"ok": True, "tools": runtime.public_tools()})

    async def create_job(request: web.Request) -> web.Response:
        blocked = _require_enabled(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            job = await asyncio.to_thread(
                runtime.plan_job,
                int(auth["user_id"]),
                str(payload.get("goal") or ""),
                payload.get("actions"),
                mode=str(payload.get("mode") or "interactive"),
            )
            return routes_module._json_response({"ok": True, "job": job}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_job(request: web.Request) -> web.Response:
        blocked = _require_enabled(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            job = await asyncio.to_thread(jobs.get_job, int(auth["user_id"]), request.match_info["job_id"])
            return routes_module._json_response({"ok": True, "job": job})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def decide(request: web.Request, decision: str) -> web.Response:
        blocked = _require_enabled(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            handler = runtime.approve_action if decision == "approve" else runtime.reject_action
            job = await asyncio.to_thread(
                handler,
                int(auth["user_id"]),
                request.match_info["job_id"],
                request.match_info["action_id"],
            )
            return routes_module._json_response({"ok": True, "job": job})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def approve(request: web.Request) -> web.Response:
        return await decide(request, "approve")

    async def reject(request: web.Request) -> web.Response:
        return await decide(request, "reject")

    async def run_job(request: web.Request) -> web.Response:
        blocked = _require_enabled(routes_module)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            job = await asyncio.to_thread(runtime.execute_job, int(auth["user_id"]), request.match_info["job_id"])
            return routes_module._json_response({"ok": True, "job": job})
        except Exception as exc:
            return _json_error(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_get(f"{_PREFIX}/tools", tools_list)
    app.router.add_post(f"{_PREFIX}/jobs", create_job)
    app.router.add_get(f"{_PREFIX}/jobs/{{job_id}}", get_job)
    app.router.add_post(f"{_PREFIX}/jobs/{{job_id}}/actions/{{action_id}}/approve", approve)
    app.router.add_post(f"{_PREFIX}/jobs/{{job_id}}/actions/{{action_id}}/reject", reject)
    app.router.add_post(f"{_PREFIX}/jobs/{{job_id}}/run", run_job)
    app["velia_agent_routes_installed"] = True
