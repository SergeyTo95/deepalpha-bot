from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_coding_autopilot_ci_classifier as ci_classifier
from services import velia_agent_coding_autopilot_ci_log_service as ci_logs
from services import velia_agent_coding_autopilot_ci_reliability_patch as ci_reliability
from services import velia_agent_coding_autopilot_ci_repair_evidence_patch as ci_evidence
from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_project_service as project_service
from services import velia_developer_repowise_context_patch as repowise_patch
from services import velia_developer_repowise_context_service as repowise_context

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
    return None


def _error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(
        exc,
        (
            ci_service.CodingAutopilotCIError,
            autopilot.CodingAutopilotError,
        ),
    ):
        return routes_module._json_response(
            {
                "ok": False,
                "error": str(getattr(exc, "code", "velia_coding_autopilot_ci_failed")),
                "detail": str(getattr(exc, "detail", "")),
            },
            status=int(getattr(exc, "status", 400)),
        )
    logger.exception("VELIA_CODING_AUTOPILOT_CI_ROUTE_FAILED")
    return routes_module._json_response(
        {"ok": False, "error": "velia_coding_autopilot_ci_internal_error"},
        status=500,
    )


def setup_velia_coding_autopilot_ci_routes(
    app: web.Application,
    routes_module: Any,
) -> None:
    # Disabled features must be completely inert: route registration cannot
    # require DATABASE_URL or mutate schema. Enabling CI requires a redeploy.
    if repowise_context.context_enabled():
        repowise_patch.install()
    if ci_service.ci_watch_enabled():
        ci_classifier.install()
        ci_reliability.install()
        if ci_logs.logs_enabled():
            ci_logs.install()
        ci_evidence.install()
        ci_service.install_ci_repair_loop()
    if app.get("velia_coding_autopilot_ci_routes_installed"):
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response(
                {"ok": False, "error": "unauthorized"}, status=401
            )
        repowise_status = repowise_context.status()
        return routes_module._json_response(
            {
                "ok": True,
                "ci_watch_enabled": ci_service.ci_watch_enabled(),
                "ci_repair_enabled": ci_service.ci_repair_enabled(),
                "ci_logs_enabled": ci_logs.logs_enabled(),
                "repowise_context_enabled": bool(repowise_status["enabled"]),
                "repowise_context_configured": bool(repowise_status["configured"]),
                "repowise_context_read_only": True,
                "repowise_context_fail_open": True,
                "repowise_context_exact_sha_required": True,
                "structured_infrastructure_classifier": True,
                "strong_evidence_required": True,
                "literal_evidence_guard": True,
                "max_repairs": ci_service._env_int(
                    "VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS", 2, 0, 2
                ),
                "draft_pr_only": True,
                "auto_merge": False,
                "deployment": False,
                "approved_plan_files_only": True,
                "infrastructure_failures_change_code": False,
                "db_lease_claims": True,
                "bounded_log_bytes": ci_logs._env_int(
                    "VELIA_DEVELOPER_AUTOPILOT_CI_LOG_MAX_BYTES",
                    131072,
                    8192,
                    262144,
                ),
            }
        )

    async def attempts(request: web.Request) -> web.Response:
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
                ci_service.list_ci_attempts,
                int(auth["user_id"]),
                request.match_info["run_id"],
            )
            return routes_module._json_response(
                {"ok": True, "attempts": items}
            )
        except Exception as exc:
            return _error(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/ci/status", status)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/ci", attempts)
    app["velia_coding_autopilot_ci_routes_installed"] = True
