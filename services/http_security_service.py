import asyncio
import hashlib
import hmac
import json
import os
import uuid
from typing import Any, Optional, Set
from urllib.parse import urlparse

from aiohttp import web

from db.database import get_user_by_session
from services.velia_admin_user_display_service import apply_admin_users_display_fallback


def _json_response(payload: dict, status: int) -> web.Response:
    return web.Response(
        text=json.dumps(payload, ensure_ascii=False),
        status=status,
        content_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _origin_from_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def allowed_cors_origins() -> Set[str]:
    result: Set[str] = set()
    for raw in str(os.getenv("CORS_ALLOWED_ORIGINS", "") or "").split(","):
        origin = _origin_from_url(raw)
        if origin:
            result.add(origin)
    for env_name in ("WEB_APP_BASE_URL", "WEBAPP_URL"):
        origin = _origin_from_url(os.getenv(env_name, ""))
        if origin:
            result.add(origin)
    if str(os.getenv("CORS_ALLOW_LOCALHOST", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        result.update({
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        })
    return result


def _is_same_origin(request: web.Request, origin: str) -> bool:
    parsed = urlparse(str(origin or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    request_host = str(request.host or "").lower()
    return parsed.netloc.lower() == request_host


def _origin_allowed(request: web.Request, origin: str, configured: Set[str]) -> bool:
    return bool(origin and (origin in configured or _is_same_origin(request, origin)))


def _is_api_path(path: str) -> bool:
    normalized = str(path or "")
    return (
        normalized.startswith("/api/")
        or normalized.startswith("/app-api/")
        or normalized.startswith("/mobile-api/")
    )


def _admin_cookie_signature(secret: str) -> str:
    """Compatibility-only deterministic hash helper.

    Historical Developer API tests import this private helper to assert that a
    secret is transformed before storage. VELIA Control Center auth does not
    call it, does not accept the historical cookie, and does not read a shared
    admin secret from environment variables.
    """
    return hmac.new(
        str(secret or "").encode("utf-8"),
        b"deepalpha-admin-session-v1",
        hashlib.sha256,
    ).hexdigest()


def _protect_legacy_user_api(request: web.Request) -> Optional[web.Response]:
    if not request.path.startswith("/api/user/"):
        return None
    token = str(request.cookies.get("deepalpha_session", "") or "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "error": "unauthorized"}, 401)
    requested = str(request.match_info.get("user_id", "") or "")
    if requested != str(current.get("user_id", "")):
        return _json_response({"ok": False, "error": "forbidden"}, 403)
    return None


def _is_detailed_admin_audit_path(path: str) -> bool:
    normalized = str(path or "")
    return normalized.startswith("/admin/users/") and "/actions/" in normalized


def _admin_mutation_requires_origin(path: str) -> bool:
    """Return whether an admin mutation must pass the browser Origin gate.

    `/admin/login` consumes a high-entropy, owner-issued, one-time Telegram OTP
    and has no ambient authenticated authority yet. Railway may present an
    internal Host to aiohttp behind its reverse proxy while the browser sends
    the public custom-domain Origin, so applying the generic Origin gate here
    can reject a legitimate owner login before the OTP handler executes.

    Every authenticated Control Center mutation remains origin-gated and also
    requires the session-bound CSRF token in `admin_routes._guard`.
    """
    normalized = str(path or "")
    return normalized.startswith("/admin") and normalized != "/admin/login"


async def _record_generic_admin_mutation(request: web.Request, response: web.StreamResponse) -> None:
    if request.method not in {"POST", "PATCH", "PUT", "DELETE"}:
        return
    if request.path in {"/admin/login", "/admin/logout"}:
        return
    if _is_detailed_admin_audit_path(request.path):
        return
    session = request.get("velia_admin_session") or {}
    admin_user_id = int(session.get("admin_user_id") or 0)
    if admin_user_id <= 0:
        return
    try:
        from services.velia_admin_security_service import record_admin_audit

        await asyncio.to_thread(
            record_admin_audit,
            admin_user_id=admin_user_id,
            action="admin.http_mutation",
            target_type="admin_route",
            target_id=str(request.path)[:160],
            request_id=str(request.get("velia_request_id") or "")[:160],
            before=None,
            after={"http_status": int(getattr(response, "status", 0) or 0)},
            success=int(getattr(response, "status", 500) or 500) < 400,
            error_code="" if int(getattr(response, "status", 500) or 500) < 400 else f"http_{int(getattr(response, 'status', 500) or 500)}",
            source="web",
            ip=request.remote or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
    except Exception:
        # The mutation handler remains the source of truth. Never leak audit
        # storage failures or request bodies into the browser response/logs.
        pass


@web.middleware
async def deepalpha_security_middleware(request: web.Request, handler):
    if request.path.startswith("/admin"):
        request["velia_request_id"] = str(request.headers.get("X-Request-ID", "") or "").strip()[:160] or uuid.uuid4().hex
        if "key" in request.query:
            return web.Response(
                text="Legacy admin URL secrets are disabled",
                status=400,
                headers={"Cache-Control": "no-store"},
            )

    origin = str(request.headers.get("Origin", "") or "").strip()
    configured_origins = allowed_cors_origins()
    origin_allowed = _origin_allowed(request, origin, configured_origins) if origin else False
    if origin and _is_api_path(request.path) and not origin_allowed:
        return _json_response({"ok": False, "error": "origin_not_allowed"}, 403)
    if (
        request.method in {"POST", "PATCH", "PUT", "DELETE"}
        and _admin_mutation_requires_origin(request.path)
        and origin
        and not origin_allowed
    ):
        return web.Response(text="Forbidden", status=403)

    protected = _protect_legacy_user_api(request)
    if protected is not None:
        response = protected
    else:
        response = await handler(request)

    response = apply_admin_users_display_fallback(request, response)

    if request.path.startswith("/admin"):
        await _record_generic_admin_mutation(request, response)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.path.startswith("/admin"):
        response.headers["X-Request-ID"] = str(request.get("velia_request_id") or "")
    if (
        request.path.startswith("/admin")
        or request.path.startswith("/api/v1/")
        or request.path.startswith("/app-api/")
        or request.path.startswith("/mobile-api/")
        or request.path == "/developer"
    ):
        response.headers["Cache-Control"] = "no-store"

    response.headers.pop("Access-Control-Allow-Origin", None)
    if origin_allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Idempotency-Key, X-Idempotency-Key, "
            "X-Request-ID, X-DeepAlpha-Portal, X-VELIA-CSRF"
        )
        response.headers["Access-Control-Max-Age"] = "600"
    return response


def install_http_security(app: web.Application, admin_routes_module: Any) -> None:
    if app.get("deepalpha_http_security_installed"):
        return
    if not bool(getattr(admin_routes_module, "CONTROL_CENTER_AUTH_V2", False)):
        raise RuntimeError("VELIA Control Center identity auth is required")
    # Stage 1 owns /admin/login and /admin/logout inside admin_routes.py.
    # Do not monkeypatch the guard/key and do not expose legacy shared-secret auth.
    app.middlewares.append(deepalpha_security_middleware)
    app["deepalpha_http_security_installed"] = True
