from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_coding_autopilot_review_service as review_service
from services import velia_agent_coding_autopilot_review_store as review_store
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
    if not review_service.review_loop_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_coding_autopilot_review_disabled"}, status=503
        )
    return None


def setup_velia_coding_autopilot_review_routes(
    app: web.Application,
    routes_module: Any,
) -> None:
    # Disabled features must not initialize schema or patch the worker.
    if review_service.review_loop_enabled():
        review_service.install_review_loop()
    if app.get("velia_coding_autopilot_review_routes_installed"):
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
            {"ok": True, **review_service.review_status()}
        )

    async def actions(request: web.Request) -> web.Response:
        blocked = _blocked(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response(
                {"ok": False, "error": "unauthorized"}, status=401
            )
        try:
            items = await asyncio.to_thread(
                review_store.list_review_actions,
                int(auth["user_id"]),
                request.match_info["run_id"],
            )
            return routes_module._json_response({"ok": True, "reviews": items})
        except Exception as exc:
            code = str(getattr(exc, "code", "velia_coding_autopilot_review_failed"))
            status_code = int(getattr(exc, "status", 500))
            if status_code >= 500:
                logger.exception("VELIA_AUTOPILOT_REVIEW_ROUTE_FAILED")
            return routes_module._json_response(
                {
                    "ok": False,
                    "error": code,
                    "detail": str(getattr(exc, "detail", ""))[:500],
                },
                status=status_code,
            )

    app.router.add_get(f"{_PREFIX}/review/status", status)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/reviews", actions)
    app["velia_coding_autopilot_review_routes_installed"] = True
