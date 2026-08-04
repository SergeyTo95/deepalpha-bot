from pathlib import Path


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


github_path = Path("services/velia_developer_github_service.py")
github = github_path.read_text(encoding="utf-8")
github = replace_once(
    github,
    '''def github_app_configured() -> bool:\n    return bool(\n        github_app_id()\n        and github_app_slug()\n        and github_client_id()\n        and os.getenv("VELIA_GITHUB_APP_CLIENT_SECRET")\n        and os.getenv("VELIA_GITHUB_APP_PRIVATE_KEY")\n    )\n\n\ndef _app_jwt''',
    '''def github_app_configured() -> bool:\n    return bool(\n        github_app_id()\n        and github_app_slug()\n        and github_client_id()\n        and os.getenv("VELIA_GITHUB_APP_CLIENT_SECRET")\n        and os.getenv("VELIA_GITHUB_APP_PRIVATE_KEY")\n    )\n\n\ndef validate_github_app_configuration() -> Dict[str, Any]:\n    if not github_app_configured():\n        raise DeveloperGithubError("github_app_not_configured", status=503)\n    data = _request("GET", "/app", token=_app_jwt())\n    if not isinstance(data, dict):\n        raise DeveloperGithubError("github_app_validation_failed", status=503)\n    configured_id = github_app_id()\n    actual_id = str(data.get("id") or "")\n    if actual_id != configured_id:\n        raise DeveloperGithubError("github_app_id_mismatch", status=503)\n    configured_slug = github_app_slug().lower()\n    actual_slug = str(data.get("slug") or "").strip().lower()\n    if actual_slug != configured_slug:\n        raise DeveloperGithubError("github_app_slug_mismatch", status=503)\n    return {"id": actual_id, "slug": actual_slug}\n\n\ndef _app_jwt''',
    label="github validation function",
)
github_path.write_text(github, encoding="utf-8")

routes_path = Path("services/velia_developer_routes.py")
routes = routes_path.read_text(encoding="utf-8")
routes = replace_once(
    routes,
    '''import logging\nimport os\nfrom typing import Any, Dict, Optional''',
    '''import logging\nimport os\nimport threading\nimport time\nfrom typing import Any, Dict, Optional''',
    label="route imports",
)
routes = replace_once(
    routes,
    '''logger = logging.getLogger(__name__)\n_PREFIX = "/mobile-api/v1/developer"\n\n\ndef _env_int''',
    '''logger = logging.getLogger(__name__)\n_PREFIX = "/mobile-api/v1/developer"\n_CONNECTION_ERRORS: Dict[int, tuple[str, float]] = {}\n_CONNECTION_ERRORS_LOCK = threading.Lock()\n\n\ndef _remember_connection_error(user_id: int, code: str) -> None:\n    normalized_user_id = int(user_id or 0)\n    normalized_code = str(code or "github_connection_failed")[:120]\n    if normalized_user_id <= 0:\n        return\n    with _CONNECTION_ERRORS_LOCK:\n        _CONNECTION_ERRORS[normalized_user_id] = (normalized_code, time.time())\n\n\ndef _clear_connection_error(user_id: int) -> None:\n    with _CONNECTION_ERRORS_LOCK:\n        _CONNECTION_ERRORS.pop(int(user_id or 0), None)\n\n\ndef _recent_connection_error(user_id: int) -> str:\n    ttl = _env_int("VELIA_DEVELOPER_CONNECTION_ERROR_TTL_SECONDS", 900, 60, 3600)\n    with _CONNECTION_ERRORS_LOCK:\n        item = _CONNECTION_ERRORS.get(int(user_id or 0))\n        if not item:\n            return ""\n        code, created_at = item\n        if time.time() - created_at > ttl:\n            _CONNECTION_ERRORS.pop(int(user_id or 0), None)\n            return ""\n        return code\n\n\ndef _env_int''',
    label="connection error helpers",
)
routes = replace_once(
    routes,
    '''        try:\n            state = project_service.create_install_state(int(auth["user_id"]))\n            return routes_module._json_response(''',
    '''        try:\n            await asyncio.to_thread(project_service.ensure_developer_tables)\n            await asyncio.to_thread(github_service.validate_github_app_configuration)\n            _clear_connection_error(int(auth["user_id"]))\n            state = project_service.create_install_state(int(auth["user_id"]))\n            return routes_module._json_response(''',
    label="install preflight",
)
routes = replace_once(
    routes,
    '''    async def github_callback(request: web.Request) -> web.StreamResponse:\n        state = str(request.query.get("state") or "")\n        installation_id = _int(request.query.get("installation_id"))\n        code = str(request.query.get("code") or "")\n        try:\n            payload = project_service.verify_install_state(state)''',
    '''    async def github_callback(request: web.Request) -> web.StreamResponse:\n        state = str(request.query.get("state") or "")\n        installation_id = _int(request.query.get("installation_id"))\n        code = str(request.query.get("code") or "")\n        callback_user_id = 0\n        try:\n            payload = project_service.verify_install_state(state)\n            callback_user_id = int(payload["user_id"])''',
    label="callback user capture",
)
routes = replace_once(
    routes,
    '''                    project_service.record_installation,\n                    int(payload["user_id"]),\n                    details,\n                )\n            primary_installation_id''',
    '''                    project_service.record_installation,\n                    callback_user_id,\n                    details,\n                )\n            _clear_connection_error(callback_user_id)\n            primary_installation_id''',
    label="callback success clear",
)
routes = replace_once(
    routes,
    '''        except github_service.DeveloperGithubError as exc:\n            if exc.code == "github_installation_not_found":\n                raise web.HTTPFound(location=_github_install_url(state))\n            code = exc.code''',
    '''        except github_service.DeveloperGithubError as exc:\n            if exc.code == "github_installation_not_found":\n                raise web.HTTPFound(location=_github_install_url(state))\n            _remember_connection_error(callback_user_id, exc.code)\n            logger.warning("VELIA_DEVELOPER_GITHUB_CALLBACK_FAILED user_id=%s code=%s detail=%s", callback_user_id, exc.code, exc.detail)\n            code = exc.code''',
    label="callback github error",
)
routes = replace_once(
    routes,
    '''        except Exception as exc:\n            code = getattr(exc, "code", "github_connection_failed")\n            redirect = _safe_redirect_url()''',
    '''        except Exception as exc:\n            code = getattr(exc, "code", "github_connection_failed")\n            _remember_connection_error(callback_user_id, str(code))\n            logger.exception("VELIA_DEVELOPER_GITHUB_CALLBACK_UNEXPECTED user_id=%s code=%s", callback_user_id, code)\n            redirect = _safe_redirect_url()''',
    label="callback unexpected error",
)
routes = replace_once(
    routes,
    '''            items = await asyncio.to_thread(project_service.list_installations, int(auth["user_id"]))\n            return routes_module._json_response({"ok": True, "installations": items})''',
    '''            user_id = int(auth["user_id"])\n            items = await asyncio.to_thread(project_service.list_installations, user_id)\n            if not items:\n                connection_error = _recent_connection_error(user_id)\n                if connection_error:\n                    return routes_module._json_response(\n                        {"ok": False, "error": connection_error, "installations": []},\n                        status=409,\n                    )\n            return routes_module._json_response({"ok": True, "installations": items})''',
    label="installation diagnostics",
)
routes_path.write_text(routes, encoding="utf-8")

Path("tests/test_velia_developer_connection_diagnostics.py").write_text(
    '''import time\n\nfrom services import velia_developer_github_service as github\nfrom services import velia_developer_routes as routes\n\n\ndef test_app_validation_checks_id_and_slug(monkeypatch):\n    monkeypatch.setattr(github, "github_app_configured", lambda: True)\n    monkeypatch.setattr(github, "github_app_id", lambda: "4484249")\n    monkeypatch.setattr(github, "github_app_slug", lambda: "velia-developer-beta")\n    monkeypatch.setattr(github, "_app_jwt", lambda: "app-jwt")\n    monkeypatch.setattr(\n        github,\n        "_request",\n        lambda method, path, **kwargs: {"id": 4484249, "slug": "velia-developer-beta"},\n    )\n\n    assert github.validate_github_app_configuration() == {\n        "id": "4484249",\n        "slug": "velia-developer-beta",\n    }\n\n\ndef test_app_validation_rejects_wrong_app_id(monkeypatch):\n    monkeypatch.setattr(github, "github_app_configured", lambda: True)\n    monkeypatch.setattr(github, "github_app_id", lambda: "4484249")\n    monkeypatch.setattr(github, "github_app_slug", lambda: "velia-developer-beta")\n    monkeypatch.setattr(github, "_app_jwt", lambda: "app-jwt")\n    monkeypatch.setattr(github, "_request", lambda *args, **kwargs: {"id": 7, "slug": "velia-developer-beta"})\n\n    try:\n        github.validate_github_app_configuration()\n        assert False\n    except github.DeveloperGithubError as exc:\n        assert exc.code == "github_app_id_mismatch"\n        assert exc.status == 503\n\n\ndef test_recent_callback_error_is_user_scoped_and_expires(monkeypatch):\n    routes._CONNECTION_ERRORS.clear()\n    monkeypatch.setenv("VELIA_DEVELOPER_CONNECTION_ERROR_TTL_SECONDS", "60")\n    routes._remember_connection_error(101, "github_private_key_invalid")\n    routes._remember_connection_error(202, "github_oauth_failed")\n\n    assert routes._recent_connection_error(101) == "github_private_key_invalid"\n    assert routes._recent_connection_error(202) == "github_oauth_failed"\n    assert routes._recent_connection_error(303) == ""\n\n    routes._CONNECTION_ERRORS[101] = ("github_private_key_invalid", time.time() - 120)\n    assert routes._recent_connection_error(101) == ""\n\n\ndef test_success_clears_callback_error():\n    routes._CONNECTION_ERRORS.clear()\n    routes._remember_connection_error(101, "github_forbidden")\n    routes._clear_connection_error(101)\n    assert routes._recent_connection_error(101) == ""\n\n\ndef test_routes_run_preflight_and_surface_recent_error():\n    source = open("services/velia_developer_routes.py", encoding="utf-8").read()\n    install_url = source.split("async def install_url", 1)[1].split("async def github_callback", 1)[0]\n    installations = source.split("async def installations", 1)[1].split("async def repositories", 1)[0]\n    callback = source.split("async def github_callback", 1)[1].split("async def installations", 1)[0]\n\n    assert "validate_github_app_configuration" in install_url\n    assert "ensure_developer_tables" in install_url\n    assert "_recent_connection_error" in installations\n    assert "_remember_connection_error" in callback\n    assert "_clear_connection_error" in callback\n''',
    encoding="utf-8",
)
