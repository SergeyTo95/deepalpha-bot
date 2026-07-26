import logging

from aiohttp import web

from developer_portal_routes import _json_response, _require_user
from services.developer_api_observability_service import (
    get_api_runtime_health,
    list_user_api_jobs,
)

logger = logging.getLogger(__name__)


def _safe_limit(value: str) -> int:
    try:
        parsed = int(str(value or "30").strip())
    except Exception:
        parsed = 30
    return max(1, min(parsed, 100))


async def handle_developer_portal_project_jobs(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    assert current is not None
    try:
        client_id = int(request.match_info.get("client_id") or 0)
    except (TypeError, ValueError):
        client_id = 0
    if client_id <= 0:
        return _json_response({"ok": False, "error": "invalid_project_id"}, status=400)

    try:
        history = list_user_api_jobs(
            user_id=int(current["user_id"]),
            client_id=client_id,
            limit=_safe_limit(request.query.get("limit", "30")),
        )
        if not history.get("project"):
            return _json_response({"ok": False, "error": "project_not_found"}, status=404)
        runtime = get_api_runtime_health(include_workers=False)
        return _json_response({
            "ok": True,
            **history,
            "runtime": {
                "status": runtime.get("status"),
                "worker_available": bool(runtime.get("worker_available")),
                "warnings": runtime.get("warnings") or [],
            },
        })
    except Exception:
        logger.exception(
            "DEVELOPER_PORTAL_JOB_HISTORY_FAILED user_id=%s client_id=%s",
            current.get("user_id"),
            client_id,
        )
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_developer_portal_jobs_options(request: web.Request) -> web.Response:
    return web.Response(status=204)


def setup_developer_portal_jobs_routes(app: web.Application) -> None:
    if app.get("developer_portal_jobs_routes_installed"):
        return
    path = "/app-api/v1/developer/projects/{client_id}/jobs"
    app.router.add_get(path, handle_developer_portal_project_jobs)
    app.router.add_route("OPTIONS", path, handle_developer_portal_jobs_options)
    app["developer_portal_jobs_routes_installed"] = True
