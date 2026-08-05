from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_coding_autopilot_merge_policy_service as merge_policy
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_project_service as project_service

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/developer/autopilot"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _blocked(routes_module: Any) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not project_service.developer_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_developer_disabled"}, status=503
        )
    if not autopilot.autopilot_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_coding_autopilot_disabled"}, status=503
        )
    if not merge_policy.merge_policy_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_coding_autopilot_merge_policy_disabled"},
            status=503,
        )
    return None


def setup_velia_coding_autopilot_merge_policy_routes(
    app: web.Application,
    routes_module: Any,
) -> None:
    if app.get("velia_coding_autopilot_merge_policy_routes_installed"):
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response(
                {"ok": False, "error": "unauthorized"}, status=401
            )
        return routes_module._json_response(
            {"ok": True, **merge_policy.merge_policy_status()}
        )

    async def evaluate(request: web.Request) -> web.Response:
        blocked = _blocked(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response(
                {"ok": False, "error": "unauthorized"}, status=401
            )
        try:
            result = await asyncio.to_thread(
                merge_policy.evaluate_merge_policy,
                int(auth["user_id"]),
                request.match_info["run_id"],
            )
            return routes_module._json_response(result)
        except Exception as exc:
            code = str(
                getattr(exc, "code", "velia_coding_autopilot_merge_policy_failed")
            )[:120]
            status_code = int(getattr(exc, "status", 500))
            if status_code >= 500:
                logger.exception("VELIA_AUTOPILOT_MERGE_POLICY_ROUTE_FAILED")
            return routes_module._json_response(
                {
                    "ok": False,
                    "error": code,
                    "detail": str(getattr(exc, "detail", ""))[:500],
                },
                status=status_code,
            )

    app.router.add_get(f"{_PREFIX}/merge-policy/status", status)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/merge-policy", evaluate)
    app["velia_coding_autopilot_merge_policy_routes_installed"] = True
