import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from aiohttp import web

from db.database import get_user, get_user_by_session
from services.developer_portal_service import (
    DeveloperPortalError,
    create_user_api_project,
    ensure_developer_portal_tables,
    get_user_developer_overview,
    issue_user_api_key,
    revoke_user_api_key,
    rotate_user_api_key,
)

logger = logging.getLogger(__name__)

_MUTATION_HEADER = "X-DeepAlpha-Portal"
_MAX_JSON_BYTES = 16 * 1024


def _json_default(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_response(payload: Dict[str, Any], status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(payload, ensure_ascii=False, default=_json_default),
        status=status,
        content_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _current_user(request: web.Request) -> Optional[Dict[str, Any]]:
    token = str(request.cookies.get("deepalpha_session", "") or "")
    current = get_user_by_session(token) if token else None
    if not current:
        return None
    try:
        user_id = int(current.get("user_id") or 0)
    except Exception:
        return None
    if user_id <= 0 or not get_user(user_id):
        return None
    return {**current, "user_id": user_id}


def _user_language(user_id: int) -> str:
    user = get_user(int(user_id)) or {}
    language = str(user.get("language") or "ru").lower()
    return "ru" if language.startswith("ru") else "en"


def _require_user(request: web.Request):
    current = _current_user(request)
    if not current:
        return None, _json_response({"ok": False, "error": "unauthorized"}, status=401)
    return current, None


def _require_mutation_request(request: web.Request) -> Optional[web.Response]:
    if str(request.headers.get(_MUTATION_HEADER, "") or "") != "1":
        return _json_response({"ok": False, "error": "portal_header_required"}, status=403)
    if request.content_length is not None and request.content_length > _MAX_JSON_BYTES:
        return _json_response({"ok": False, "error": "request_too_large"}, status=413)
    content_type = str(request.content_type or "").lower()
    if content_type != "application/json":
        return _json_response({"ok": False, "error": "json_required"}, status=415)
    return None


async def _read_json(request: web.Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise DeveloperPortalError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise DeveloperPortalError("invalid_json")
    return payload


def _error_status(code: str) -> int:
    if code == "unauthorized":
        return 401
    if code in {"project_not_found", "key_not_found"}:
        return 404
    if code in {
        "project_limit_reached",
        "key_limit_reached",
        "project_not_active",
        "key_not_active",
    }:
        return 409
    if code in {
        "project_name_required",
        "at_least_one_scope_required",
        "invalid_json",
    }:
        return 400
    return 400


def _portal_error(exc: DeveloperPortalError) -> web.Response:
    return _json_response(
        {"ok": False, "error": exc.code, "details": exc.details},
        status=_error_status(exc.code),
    )


async def handle_developer_portal_page(request: web.Request) -> web.Response:
    try:
        with open("webapp/developer.html", "r", encoding="utf-8") as file:
            content = file.read()
        return web.Response(
            text=content,
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
    except FileNotFoundError:
        return web.Response(text="Not found", status=404)


async def handle_developer_portal_overview(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    assert current is not None
    try:
        overview = get_user_developer_overview(int(current["user_id"]))
        return _json_response({
            "ok": True,
            "user": {
                "user_id": int(current["user_id"]),
                "provider": str(current.get("provider") or ""),
                "language": _user_language(int(current["user_id"])),
            },
            **overview,
        })
    except Exception:
        logger.exception("DEVELOPER_PORTAL_OVERVIEW_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_developer_portal_create_project(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        payload = await _read_json(request)
        project = create_user_api_project(
            user_id=int(current["user_id"]),
            name=str(payload.get("name") or ""),
        )
        return _json_response({"ok": True, "project": project}, status=201)
    except DeveloperPortalError as exc:
        return _portal_error(exc)
    except Exception:
        logger.exception("DEVELOPER_PORTAL_PROJECT_CREATE_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_developer_portal_issue_key(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        client_id = int(request.match_info.get("client_id") or 0)
        payload = await _read_json(request)
        scopes = payload.get("scopes")
        if scopes is not None and not isinstance(scopes, list):
            raise DeveloperPortalError("invalid_json")
        key = issue_user_api_key(
            user_id=int(current["user_id"]),
            client_id=client_id,
            name=str(payload.get("name") or "default"),
            scopes=scopes,
        )
        return _json_response({"ok": True, "key": key}, status=201)
    except (TypeError, ValueError):
        return _json_response({"ok": False, "error": "invalid_project_id"}, status=400)
    except DeveloperPortalError as exc:
        return _portal_error(exc)
    except Exception:
        logger.exception("DEVELOPER_PORTAL_KEY_ISSUE_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_developer_portal_revoke_key(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        key_id = int(request.match_info.get("key_id") or 0)
        changed = revoke_user_api_key(user_id=int(current["user_id"]), key_id=key_id)
        if not changed:
            return _json_response({"ok": False, "error": "key_not_found"}, status=404)
        return _json_response({"ok": True, "revoked": True})
    except (TypeError, ValueError):
        return _json_response({"ok": False, "error": "invalid_key_id"}, status=400)
    except Exception:
        logger.exception("DEVELOPER_PORTAL_KEY_REVOKE_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_developer_portal_rotate_key(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        key_id = int(request.match_info.get("key_id") or 0)
        replacement = rotate_user_api_key(user_id=int(current["user_id"]), key_id=key_id)
        return _json_response({"ok": True, "key": replacement}, status=201)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, DeveloperPortalError):
            return _portal_error(exc)
        return _json_response({"ok": False, "error": "invalid_key_id"}, status=400)
    except DeveloperPortalError as exc:
        return _portal_error(exc)
    except Exception:
        logger.exception("DEVELOPER_PORTAL_KEY_ROTATE_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_developer_portal_options(request: web.Request) -> web.Response:
    return web.Response(status=204)


def setup_developer_portal_routes(app: web.Application) -> None:
    if app.get("developer_portal_routes_installed"):
        return
    ensure_developer_portal_tables()
    app.router.add_get("/developer", handle_developer_portal_page)
    app.router.add_get("/app-api/v1/developer/overview", handle_developer_portal_overview)
    app.router.add_post("/app-api/v1/developer/projects", handle_developer_portal_create_project)
    app.router.add_post(
        "/app-api/v1/developer/projects/{client_id}/keys",
        handle_developer_portal_issue_key,
    )
    app.router.add_post(
        "/app-api/v1/developer/keys/{key_id}/revoke",
        handle_developer_portal_revoke_key,
    )
    app.router.add_post(
        "/app-api/v1/developer/keys/{key_id}/rotate",
        handle_developer_portal_rotate_key,
    )
    for path in (
        "/app-api/v1/developer/overview",
        "/app-api/v1/developer/projects",
        "/app-api/v1/developer/projects/{client_id}/keys",
        "/app-api/v1/developer/keys/{key_id}/revoke",
        "/app-api/v1/developer/keys/{key_id}/rotate",
    ):
        app.router.add_route("OPTIONS", path, handle_developer_portal_options)
    app["developer_portal_routes_installed"] = True
