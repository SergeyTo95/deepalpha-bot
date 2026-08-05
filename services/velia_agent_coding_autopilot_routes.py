from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import velia_agent_coding_autopilot_policy_service as policy_service
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_coding_service as coding_service
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service
from services import velia_developer_project_service as project_service

logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/developer/autopilot"


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _json_error(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(
        exc,
        (
            autopilot.CodingAutopilotError,
            policy_service.CodingAutopilotPolicyError,
            coding_service.DeveloperCodingError,
            project_service.DeveloperProjectError,
            github_service.DeveloperGithubError,
            write_service.DeveloperWriteError,
        ),
    ):
        return routes_module._json_response(
            {
                "ok": False,
                "error": str(getattr(exc, "code", "velia_coding_autopilot_failed")),
                "detail": str(getattr(exc, "detail", "")),
            },
            status=int(getattr(exc, "status", 400)),
        )
    logger.exception("VELIA_CODING_AUTOPILOT_ROUTE_FAILED")
    return routes_module._json_response(
        {"ok": False, "error": "velia_coding_autopilot_internal_error"},
        status=500,
    )


def _require_available(routes_module: Any) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not project_service.developer_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_developer_disabled"},
            status=503,
        )
    if not coding_service.coding_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_developer_coding_disabled"},
            status=503,
        )
    if not autopilot.autopilot_enabled():
        return routes_module._json_response(
            {"ok": False, "error": "velia_coding_autopilot_disabled"},
            status=503,
        )
    if not github_service.github_app_configured():
        return routes_module._json_response(
            {"ok": False, "error": "github_app_not_configured"},
            status=503,
        )
    return None


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise autopilot.CodingAutopilotError("velia_coding_autopilot_json_invalid") from exc
    if not isinstance(value, dict):
        raise autopilot.CodingAutopilotError("velia_coding_autopilot_json_invalid")
    return value


def _limit(request: web.Request, default: int = 100) -> int:
    try:
        return min(200, max(1, int(request.query.get("limit") or default)))
    except (TypeError, ValueError):
        return default


def setup_velia_coding_autopilot_routes(app: web.Application, routes_module: Any) -> None:
    if app.get("velia_coding_autopilot_routes_installed"):
        autopilot.install_coding_autopilot(app)
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        enabled = autopilot.autopilot_enabled()
        worker = autopilot.worker_enabled()
        coding = coding_service.coding_enabled()
        write = write_service.write_enabled()
        return routes_module._json_response(
            {
                "ok": True,
                "enabled": enabled,
                "worker_enabled": worker,
                "coding_enabled": coding,
                "write_enabled": write,
                "worker_ready": enabled and worker and coding and write,
                "mode": "draft_pr_only",
                "auto_merge": False,
                "deployment": False,
                "ci_repair": False,
                "one_active_run_per_repository": True,
                "missions_start_paused": True,
                "protected_paths": True,
            }
        )

    async def list_missions(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(autopilot.list_missions, int(auth["user_id"]))
            return routes_module._json_response({"ok": True, "missions": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def create_mission(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            item = await asyncio.to_thread(
                autopilot.create_mission,
                int(auth["user_id"]),
                str(payload.get("project_id") or ""),
                str(payload.get("name") or ""),
                allowed_paths=payload.get("allowed_paths"),
                blocked_paths=payload.get("blocked_paths"),
                max_steps=payload.get("max_steps", 4),
                max_files=payload.get("max_files", 8),
            )
            return routes_module._json_response({"ok": True, "mission": item}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_mission(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                autopilot.get_mission,
                int(auth["user_id"]),
                request.match_info["mission_id"],
            )
            return routes_module._json_response({"ok": True, "mission": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def mission_status(request: web.Request, value: str) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                autopilot.set_mission_status,
                int(auth["user_id"]),
                request.match_info["mission_id"],
                value,
            )
            return routes_module._json_response({"ok": True, "mission": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def activate_mission(request: web.Request) -> web.Response:
        return await mission_status(request, "active")

    async def pause_mission(request: web.Request) -> web.Response:
        return await mission_status(request, "paused")

    async def list_tasks(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(
                autopilot.list_tasks,
                int(auth["user_id"]),
                request.match_info["mission_id"],
                limit=_limit(request),
            )
            return routes_module._json_response({"ok": True, "tasks": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def enqueue_task(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await _body(request)
            item = await asyncio.to_thread(
                autopilot.enqueue_task,
                int(auth["user_id"]),
                request.match_info["mission_id"],
                str(payload.get("instruction") or ""),
                priority=payload.get("priority", 0),
                client_request_id=str(payload.get("client_request_id") or ""),
            )
            return routes_module._json_response({"ok": True, "task": item}, status=201)
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_task(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                autopilot.get_task,
                int(auth["user_id"]),
                request.match_info["task_id"],
            )
            return routes_module._json_response({"ok": True, "task": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def cancel_task(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                autopilot.cancel_task,
                int(auth["user_id"]),
                request.match_info["task_id"],
            )
            return routes_module._json_response({"ok": True, "task": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def list_runs(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(
                autopilot.list_runs,
                int(auth["user_id"]),
                request.match_info["mission_id"],
                limit=_limit(request),
            )
            return routes_module._json_response({"ok": True, "runs": items})
        except Exception as exc:
            return _json_error(routes_module, exc)

    async def get_run(request: web.Request) -> web.Response:
        blocked = _require_available(routes_module)
        if blocked is not None:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            item = await asyncio.to_thread(
                autopilot.get_run,
                int(auth["user_id"]),
                request.match_info["run_id"],
            )
            return routes_module._json_response({"ok": True, "run": item})
        except Exception as exc:
            return _json_error(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_get(f"{_PREFIX}/missions", list_missions)
    app.router.add_post(f"{_PREFIX}/missions", create_mission)
    app.router.add_get(f"{_PREFIX}/missions/{{mission_id}}", get_mission)
    app.router.add_post(f"{_PREFIX}/missions/{{mission_id}}/activate", activate_mission)
    app.router.add_post(f"{_PREFIX}/missions/{{mission_id}}/pause", pause_mission)
    app.router.add_get(f"{_PREFIX}/missions/{{mission_id}}/tasks", list_tasks)
    app.router.add_post(f"{_PREFIX}/missions/{{mission_id}}/tasks", enqueue_task)
    app.router.add_get(f"{_PREFIX}/missions/{{mission_id}}/runs", list_runs)
    app.router.add_get(f"{_PREFIX}/tasks/{{task_id}}", get_task)
    app.router.add_post(f"{_PREFIX}/tasks/{{task_id}}/cancel", cancel_task)
    app.router.add_get(f"{_PREFIX}/runs/{{run_id}}", get_run)
    app["velia_coding_autopilot_routes_installed"] = True
    autopilot.install_coding_autopilot(app)
