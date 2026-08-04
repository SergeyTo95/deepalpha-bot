import asyncio
import html
import logging
import os
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

from aiohttp import web

from services import velia_developer_agent_service as agent_service
from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service


logger = logging.getLogger(__name__)
_PREFIX = "/mobile-api/v1/developer"
_CONNECTION_ERRORS: Dict[int, tuple[str, float]] = {}
_CONNECTION_ERRORS_LOCK = threading.Lock()


def _remember_connection_error(user_id: int, code: str) -> None:
    normalized_user_id = int(user_id or 0)
    normalized_code = str(code or "github_connection_failed")[:120]
    if normalized_user_id <= 0:
        return
    with _CONNECTION_ERRORS_LOCK:
        _CONNECTION_ERRORS[normalized_user_id] = (normalized_code, time.time())


def _clear_connection_error(user_id: int) -> None:
    with _CONNECTION_ERRORS_LOCK:
        _CONNECTION_ERRORS.pop(int(user_id or 0), None)


def _recent_connection_error(user_id: int) -> str:
    ttl = _env_int("VELIA_DEVELOPER_CONNECTION_ERROR_TTL_SECONDS", 900, 60, 3600)
    with _CONNECTION_ERRORS_LOCK:
        item = _CONNECTION_ERRORS.get(int(user_id or 0))
        if not item:
            return ""
        code, created_at = item
        if time.time() - created_at > ttl:
            _CONNECTION_ERRORS.pop(int(user_id or 0), None)
            return ""
        return code


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 1000) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _safe_redirect_url() -> str:
    value = str(
        os.getenv("VELIA_DEVELOPER_CALLBACK_REDIRECT", "velia://developer/github-connected")
        or "velia://developer/github-connected"
    ).strip()
    if value.startswith("velia://") or value.startswith("https://"):
        return value
    return "velia://developer/github-connected"


def _github_oauth_url(state: str) -> str:
    return "https://github.com/login/oauth/authorize?" + urlencode(
        {
            "client_id": github_service.github_client_id(),
            "state": str(state),
            "prompt": "select_account",
        }
    )


def _github_install_url(state: str) -> str:
    return (
        f"https://github.com/apps/{quote(github_service.github_app_slug(), safe='')}"
        f"/installations/new?{urlencode({'state': str(state)})}"
    )


def _error_response(routes_module: Any, exc: Exception) -> web.Response:
    if isinstance(exc, project_service.DeveloperProjectError):
        return routes_module._json_response({"ok": False, "error": exc.code}, status=exc.status)
    if isinstance(exc, github_service.DeveloperGithubError):
        payload = {"ok": False, "error": exc.code}
        return routes_module._json_response(payload, status=exc.status)
    if isinstance(exc, agent_service.DeveloperAgentError):
        return routes_module._json_response({"ok": False, "error": exc.code}, status=exc.status)
    logger.exception("VELIA_DEVELOPER_UNEXPECTED_ERROR")
    return routes_module._json_response({"ok": False, "error": "developer_internal_error"}, status=500)


def _require_feature(routes_module: Any, request: web.Request) -> Optional[web.Response]:
    if not routes_module._mobile_api_available():
        return routes_module._disabled_response()
    if not project_service.developer_enabled():
        return routes_module._json_response({"ok": False, "error": "velia_developer_disabled"}, status=503)
    if not github_service.github_app_configured():
        return routes_module._json_response({"ok": False, "error": "github_app_not_configured"}, status=503)
    return None


def _auth(routes_module: Any, request: web.Request) -> Optional[Dict[str, Any]]:
    return routes_module._require_mobile_auth(request)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def setup_velia_developer_routes(app: web.Application, routes_module: Any) -> None:
    if app.get("velia_developer_routes_installed"):
        return

    async def status(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        installations = []
        projects = []
        if project_service.developer_enabled():
            try:
                installations, projects = await asyncio.gather(
                    asyncio.to_thread(project_service.list_installations, int(auth["user_id"])),
                    asyncio.to_thread(project_service.list_projects, int(auth["user_id"])),
                )
            except Exception:
                logger.exception("VELIA_DEVELOPER_STATUS_STORAGE_FAILED")
        return routes_module._json_response(
            {
                "ok": True,
                "enabled": project_service.developer_enabled(),
                "github_configured": github_service.github_app_configured(),
                "github_app_slug": github_service.github_app_slug(),
                "read_only": True,
                "installation_count": len(installations),
                "project_count": len(projects),
                "tools": ["list_tree", "search_code", "read_file"],
            }
        )

    async def install_url(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            await asyncio.to_thread(project_service.ensure_developer_tables)
            await asyncio.to_thread(github_service.validate_github_app_configuration)
            _clear_connection_error(int(auth["user_id"]))
            state = project_service.create_install_state(int(auth["user_id"]))
            return routes_module._json_response(
                {
                    "ok": True,
                    "url": _github_oauth_url(state),
                    "expires_in": 600,
                    "flow": "oauth_first",
                }
            )
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def github_callback(request: web.Request) -> web.StreamResponse:
        state = str(request.query.get("state") or "")
        installation_id = _int(request.query.get("installation_id"))
        code = str(request.query.get("code") or "")
        callback_user_id = 0
        try:
            payload = project_service.verify_install_state(state)
            callback_user_id = int(payload["user_id"])
            details_list = await asyncio.to_thread(
                github_service.authorize_user_installations,
                code,
                installation_id,
            )
            for details in details_list:
                await asyncio.to_thread(
                    project_service.record_installation,
                    callback_user_id,
                    details,
                )
            _clear_connection_error(callback_user_id)
            primary_installation_id = int(details_list[0]["installation_id"])
            separator = "&" if "?" in _safe_redirect_url() else "?"
            location = _safe_redirect_url() + separator + urlencode(
                {
                    "connected": "true",
                    "installation_id": primary_installation_id,
                    "installation_count": len(details_list),
                }
            )
            raise web.HTTPFound(location=location)
        except web.HTTPException:
            raise
        except github_service.DeveloperGithubError as exc:
            if exc.code == "github_installation_not_found":
                raise web.HTTPFound(location=_github_install_url(state))
            _remember_connection_error(callback_user_id, exc.code)
            logger.warning("VELIA_DEVELOPER_GITHUB_CALLBACK_FAILED user_id=%s code=%s detail=%s", callback_user_id, exc.code, exc.detail)
            code = exc.code
            redirect = _safe_redirect_url()
            separator = "&" if "?" in redirect else "?"
            location = redirect + separator + urlencode({"connected": "false", "error": str(code)})
            raise web.HTTPFound(location=location)
        except Exception as exc:
            code = getattr(exc, "code", "github_connection_failed")
            _remember_connection_error(callback_user_id, str(code))
            logger.exception("VELIA_DEVELOPER_GITHUB_CALLBACK_UNEXPECTED user_id=%s code=%s", callback_user_id, code)
            redirect = _safe_redirect_url()
            separator = "&" if "?" in redirect else "?"
            location = redirect + separator + urlencode({"connected": "false", "error": str(code)})
            try:
                raise web.HTTPFound(location=location)
            except web.HTTPFound:
                raise

    async def installations(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            user_id = int(auth["user_id"])
            items = await asyncio.to_thread(project_service.list_installations, user_id)
            if not items:
                connection_error = _recent_connection_error(user_id)
                if connection_error:
                    return routes_module._json_response(
                        {"ok": False, "error": connection_error, "installations": []},
                        status=409,
                    )
            return routes_module._json_response({"ok": True, "installations": items})
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def repositories(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        installation_id = _int(request.query.get("installation_id"))
        try:
            await asyncio.to_thread(project_service.get_installation, int(auth["user_id"]), installation_id)
            items = await asyncio.to_thread(github_service.list_installation_repositories, installation_id)
            return routes_module._json_response({"ok": True, "repositories": items})
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def repository_branches(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        installation_id = _int(request.query.get("installation_id"))
        repository_id = _int(request.match_info.get("repository_id"))
        try:
            await asyncio.to_thread(project_service.get_installation, int(auth["user_id"]), installation_id)
            repositories_list = await asyncio.to_thread(github_service.list_installation_repositories, installation_id)
            repository = next(
                (item for item in repositories_list if int(item.get("id") or 0) == repository_id),
                None,
            )
            if not repository:
                raise project_service.DeveloperProjectError("repository_not_accessible", status=404)
            metadata = await asyncio.to_thread(
                github_service.repository_metadata,
                installation_id,
                repository_id,
                str(repository["full_name"]),
            )
            items = await asyncio.to_thread(
                github_service.list_branches,
                installation_id,
                repository_id,
                str(metadata["full_name"]),
            )
            return routes_module._json_response(
                {
                    "ok": True,
                    "default_branch": str(metadata["default_branch"]),
                    "branches": items,
                }
            )
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def list_projects(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            items = await asyncio.to_thread(project_service.list_projects, int(auth["user_id"]))
            return routes_module._json_response({"ok": True, "projects": items})
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def create_project(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await routes_module._read_json(request)
        if data is None:
            return routes_module._json_response({"ok": False, "error": "invalid_json"}, status=400)
        installation_id = _int(data.get("installation_id"))
        repository_id = _int(data.get("repository_id"))
        branch = str(data.get("branch") or "").strip()
        try:
            current_projects = await asyncio.to_thread(project_service.list_projects, int(auth["user_id"]))
            if len(current_projects) >= _env_int("VELIA_DEVELOPER_MAX_PROJECTS_PER_USER", 20, 1, 100):
                existing_ids = {int(item.get("repository_id") or 0) for item in current_projects}
                if repository_id not in existing_ids:
                    raise project_service.DeveloperProjectError("developer_project_limit_reached", status=409)
            await asyncio.to_thread(project_service.get_installation, int(auth["user_id"]), installation_id)
            repositories_list = await asyncio.to_thread(github_service.list_installation_repositories, installation_id)
            repository = next((item for item in repositories_list if int(item.get("id") or 0) == repository_id), None)
            if not repository:
                raise project_service.DeveloperProjectError("repository_not_accessible", status=404)
            metadata = await asyncio.to_thread(
                github_service.repository_metadata,
                installation_id,
                repository_id,
                str(repository["full_name"]),
            )
            selected_branch = github_service.validate_branch(branch or str(metadata["default_branch"]))
            branches = await asyncio.to_thread(
                github_service.list_branches,
                installation_id,
                repository_id,
                str(metadata["full_name"]),
            )
            if selected_branch not in {str(item.get("name") or "") for item in branches}:
                raise project_service.DeveloperProjectError("developer_branch_not_found", status=404)
            project = await asyncio.to_thread(
                project_service.create_project,
                int(auth["user_id"]),
                installation_id,
                metadata,
                selected_branch,
            )
            return routes_module._json_response({"ok": True, "project": project}, status=201)
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def remove_project(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            await asyncio.to_thread(project_service.delete_project, int(auth["user_id"]), str(request.match_info["project_id"]))
            return routes_module._json_response({"ok": True})
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def project_branches(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            project = await asyncio.to_thread(project_service.get_project, int(auth["user_id"]), str(request.match_info["project_id"]))
            items = await asyncio.to_thread(
                github_service.list_branches,
                project["installation_id"],
                project["repository_id"],
                project["repository_full_name"],
            )
            return routes_module._json_response({"ok": True, "branches": items})
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def project_tree(request: web.Request) -> web.Response:
        return await _project_tool_request(request, "tree")

    async def project_file(request: web.Request) -> web.Response:
        return await _project_tool_request(request, "file")

    async def project_search(request: web.Request) -> web.Response:
        return await _project_tool_request(request, "search")

    async def _project_tool_request(request: web.Request, operation: str) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            project = await asyncio.to_thread(project_service.get_project, int(auth["user_id"]), str(request.match_info["project_id"]))
            common = (
                project["installation_id"],
                project["repository_id"],
                project["repository_full_name"],
                project["selected_branch"],
            )
            if operation == "tree":
                result = await asyncio.to_thread(github_service.list_tree, *common, prefix=str(request.query.get("prefix") or ""))
                return routes_module._json_response({"ok": True, **result})
            if operation == "file":
                result = await asyncio.to_thread(
                    github_service.read_file,
                    *common,
                    str(request.query.get("path") or ""),
                    start_line=_int(request.query.get("start_line"), 1),
                    end_line=_int(request.query.get("end_line"), 240),
                )
                return routes_module._json_response({"ok": True, "file": result})
            query = str(request.query.get("q") or "")
            result = await asyncio.to_thread(
                github_service.search_code,
                common[0],
                common[1],
                common[2],
                query,
                branch=common[3],
                default_branch=str(project.get("default_branch") or common[3]),
            )
            return routes_module._json_response({"ok": True, "matches": result})
        except Exception as exc:
            return _error_response(routes_module, exc)

    async def ask_project(request: web.Request) -> web.Response:
        blocked = _require_feature(routes_module, request)
        if blocked:
            return blocked
        auth = _auth(routes_module, request)
        if not auth:
            return routes_module._json_response({"ok": False, "error": "unauthorized"}, status=401)
        data = await routes_module._read_json(request)
        if data is None:
            return routes_module._json_response({"ok": False, "error": "invalid_json"}, status=400)
        question = str(data.get("question") or "").strip()
        run_id = ""
        try:
            project = await asyncio.to_thread(project_service.get_project, int(auth["user_id"]), str(request.match_info["project_id"]))
            run_id = await asyncio.to_thread(project_service.start_run, int(auth["user_id"]), project["id"], question)
            result = await asyncio.to_thread(
                agent_service.run_developer_agent,
                user_id=int(auth["user_id"]),
                project=project,
                question=question,
                run_id=run_id,
            )
            await asyncio.to_thread(
                project_service.finish_run,
                run_id,
                ok=True,
                answer=str(result.get("answer") or ""),
                tool_calls=int(result.get("tool_calls") or 0),
                estimated_cost_usd=float(result.get("estimated_cost_usd") or 0.0),
            )
            return routes_module._json_response({"ok": True, "run_id": run_id, **result})
        except asyncio.CancelledError:
            if run_id:
                try:
                    await asyncio.shield(
                        asyncio.to_thread(
                            project_service.finish_run,
                            run_id,
                            ok=False,
                            error_code="developer_run_cancelled",
                        )
                    )
                except Exception:
                    logger.exception("VELIA_DEVELOPER_CANCEL_FINALIZE_FAILED run_id=%s", run_id)
            raise
        except Exception as exc:
            if run_id:
                try:
                    await asyncio.to_thread(
                        project_service.finish_run,
                        run_id,
                        ok=False,
                        error_code=str(getattr(exc, "code", "developer_failed")),
                    )
                except Exception:
                    logger.exception("VELIA_DEVELOPER_RUN_FINALIZE_FAILED run_id=%s", run_id)
            return _error_response(routes_module, exc)

    app.router.add_get(f"{_PREFIX}/status", status)
    app.router.add_post(f"{_PREFIX}/github/install-url", install_url)
    app.router.add_get(f"{_PREFIX}/github/callback", github_callback)
    app.router.add_get(f"{_PREFIX}/installations", installations)
    app.router.add_get(f"{_PREFIX}/repositories", repositories)
    app.router.add_get(
        f"{_PREFIX}/repositories/{repository_id}/branches",
        repository_branches,
    )
    app.router.add_get(f"{_PREFIX}/projects", list_projects)
    app.router.add_post(f"{_PREFIX}/projects", create_project)
    app.router.add_delete(f"{_PREFIX}/projects/{{project_id}}", remove_project)
    app.router.add_get(f"{_PREFIX}/projects/{{project_id}}/branches", project_branches)
    app.router.add_get(f"{_PREFIX}/projects/{{project_id}}/tree", project_tree)
    app.router.add_get(f"{_PREFIX}/projects/{{project_id}}/file", project_file)
    app.router.add_get(f"{_PREFIX}/projects/{{project_id}}/search", project_search)
    app.router.add_post(f"{_PREFIX}/projects/{{project_id}}/ask", ask_project)
    app["velia_developer_routes_installed"] = True
    logger.info("VELIA_DEVELOPER_ROUTES_INSTALLED")
