import asyncio
import json
import logging

from aiohttp import web

from services import velia_project_service as projects
from velia_mobile_routes import _json_response, _mobile_api_available, _require_mobile_auth
from services.velia_chat_service import is_velia_chat_enabled_for_user

MAX_BODY = 16 * 1024


async def _body(request):
    raw = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise projects.ProjectError("request_too_large", 413)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise projects.ProjectError("invalid_json")
    if not isinstance(data, dict):
        raise projects.ProjectError("invalid_json")
    return data


def setup_velia_project_routes(app):
    async def startup(_app):
        if not _mobile_api_available():
            return
        try:
            await asyncio.to_thread(projects.ensure_tables)
        except Exception:
            logging.getLogger(__name__).exception("VELIA_PROJECT_STORAGE_UNAVAILABLE")
            return
        # Startup runs after all existing chat extensions have been installed.
        from services import velia_chat_service
        from services.velia_project_runtime import install
        install(velia_chat_service)

    app.on_startup.append(startup)

    def guarded(handler):
        async def wrapped(request):
            if not _mobile_api_available():
                return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
            auth = await asyncio.to_thread(_require_mobile_auth, request)
            if not auth:
                return _json_response({"ok": False, "error": "unauthorized"}, status=401)
            expected_account = request.headers.get("X-Velia-Account")
            if expected_account is not None and expected_account != str(auth["user_id"]):
                return _json_response({"ok": False, "error": "account_changed"}, status=409)
            if not projects.ready():
                return _json_response({"ok": False, "error": "projects_unavailable"}, status=503)
            try:
                return await handler(request, int(auth["user_id"]))
            except projects.ProjectError as exc:
                return _json_response({"ok": False, "error": exc.code}, status=exc.status)
            except (TypeError, ValueError):
                return _json_response({"ok": False, "error": "invalid_request"}, status=400)
        return wrapped

    async def project_list(request, uid):
        return _json_response({"ok": True, "projects": await asyncio.to_thread(projects.list_projects, uid)})

    async def project_create(request, uid):
        data = await _body(request)
        project = await asyncio.to_thread(projects.create_project, uid, data.get("passport"), request.headers.get("Idempotency-Key"))
        return _json_response({"ok": True, "project": project}, status=201)

    async def project_get(request, uid):
        project = await asyncio.to_thread(projects.project_detail, uid, request.match_info["project_id"],
                    int(request.query.get("before_revision", 2147483647)))
        return _json_response({"ok": True, "project": project})

    async def project_update(request, uid):
        data = await _body(request)
        project = await asyncio.to_thread(projects.update_project, uid, request.match_info["project_id"],
                    data.get("passport"), data.get("expected_revision"))
        return _json_response({"ok": True, "project": project})

    async def resources_list(request, uid):
        data = await asyncio.to_thread(projects.list_resources, uid, request.query.get("project_id"),
                    request.query.get("kind"), int(request.query.get("offset", 0)))
        return _json_response({"ok": True, **data})

    async def resources_create(request, uid):
        if not is_velia_chat_enabled_for_user(uid):
            return _json_response({"ok": False, "error": "velia_chat_disabled"}, status=503)
        data = await _body(request)
        if data.get("kind") == "deepalpha":
            from services.velia_live_plugins_patch import _env_bool
            if not _env_bool("VELIA_LIVE_PLUGINS_ENABLED", True):
                return _json_response({"ok": False, "error": "research_disabled"}, status=503)
        resource = await asyncio.to_thread(projects.create_resource, uid, data, request.headers.get("Idempotency-Key"))
        return _json_response({"ok": True, "resource": resource}, status=201)

    async def evidence(request, uid):
        values = await asyncio.to_thread(projects.research_evidence, uid, request.match_info["resource_id"])
        return _json_response({"ok": True, "evidence": values})

    async def resource_assign(request, uid):
        data = await _body(request)
        if "project_id" not in data or "expected_project_id" not in data:
            raise projects.ProjectError("invalid_request")
        resource = await asyncio.to_thread(projects.assign_resource, uid, request.match_info["resource_id"],
                    data["project_id"], data["expected_project_id"])
        return _json_response({"ok": True, "resource": resource})

    prefix = "/mobile-api/v1"
    for method, path, handler in [
        ("GET", "/projects", project_list), ("POST", "/projects", project_create),
        ("GET", "/projects/{project_id}", project_get), ("PATCH", "/projects/{project_id}", project_update),
        ("GET", "/project-resources", resources_list), ("POST", "/project-resources", resources_create),
        ("PATCH", "/project-resources/{resource_id}", resource_assign),
        ("GET", "/deepalpha/{resource_id}/evidence", evidence),
    ]:
        app.router.add_route(method, prefix + path, guarded(handler))

    # Research Center is a separate capability surface but shares the same
    # authenticated mobile account and project-resource ownership boundary.
    from velia_research_routes import setup_velia_research_routes
    setup_velia_research_routes(app)

    # Medical Intelligence is a separate high-trust capability surface. Raw
    # studies are streamed to a self-hosted GPU worker and are never stored in
    # the Railway database.
    from velia_medical_routes import setup_velia_medical_routes
    setup_velia_medical_routes(app)
