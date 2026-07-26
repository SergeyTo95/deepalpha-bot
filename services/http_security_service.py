import hashlib
import hmac
import json
import os
from typing import Any, Optional, Set
from urllib.parse import urlencode, urlparse

from aiohttp import web

from db.database import get_user_by_session

ADMIN_COOKIE_NAME = "deepalpha_admin_session"
ADMIN_COOKIE_MAX_AGE = 12 * 60 * 60


def _json_response(payload: dict, status: int) -> web.Response:
    return web.Response(
        text=json.dumps(payload, ensure_ascii=False),
        status=status,
        content_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _secure_cookie_enabled() -> bool:
    explicit = str(os.getenv("COOKIE_SECURE", "") or "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    environment = str(os.getenv("RAILWAY_ENVIRONMENT_NAME", "") or "").strip().lower()
    return environment in {"production", "prod"}


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
    return normalized.startswith("/api/") or normalized.startswith("/app-api/")


def _admin_secret() -> str:
    return str(os.getenv("ADMIN_SECRET_KEY", "") or "")


def _admin_cookie_signature(secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), b"deepalpha-admin-session-v1", hashlib.sha256).hexdigest()


def _valid_admin_cookie(request: web.Request) -> bool:
    secret = _admin_secret()
    supplied = str(request.cookies.get(ADMIN_COOKIE_NAME, "") or "")
    expected = _admin_cookie_signature(secret) if secret else ""
    return bool(secret and supplied and hmac.compare_digest(supplied, expected))


def _set_admin_cookie(response: web.StreamResponse) -> None:
    secret = _admin_secret()
    if not secret:
        return
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        _admin_cookie_signature(secret),
        max_age=ADMIN_COOKIE_MAX_AGE,
        httponly=True,
        secure=_secure_cookie_enabled(),
        samesite="Strict",
        path="/admin",
    )


def _strip_admin_key_redirect(request: web.Request) -> web.HTTPFound:
    query = [(key, value) for key, value in request.query.items() if key != "key"]
    target = request.path
    if query:
        target = f"{target}?{urlencode(query)}"
    response = web.HTTPFound(target)
    _set_admin_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


def _secure_admin_guard_factory():
    async def secure_admin_guard(request: web.Request):
        if not _admin_secret():
            return web.Response(text="Admin is not configured", status=403)
        if _valid_admin_cookie(request):
            return None
        return web.HTTPFound("/admin/login")
    return secure_admin_guard


def _secure_admin_key(_request: web.Request) -> str:
    return ""


async def handle_admin_login(request: web.Request) -> web.Response:
    secret = _admin_secret()
    if not secret:
        return web.Response(text="Admin is not configured", status=403)
    if request.method == "POST":
        form = await request.post()
        supplied = str(form.get("secret", "") or "")
        if hmac.compare_digest(supplied, secret):
            response = web.HTTPFound("/admin")
            _set_admin_cookie(response)
            response.headers["Cache-Control"] = "no-store"
            return response
        error = "Invalid secret"
    else:
        error = ""
    body = f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
    <meta name='referrer' content='no-referrer'><title>DeepAlpha Admin Login</title></head>
    <body style='margin:0;background:#070b17;color:#e5e7eb;font:14px Arial;display:grid;place-items:center;min-height:100vh'>
    <form method='post' action='/admin/login' style='width:min(92vw,380px);padding:22px;background:#0f172a;border:1px solid #1f2937;border-radius:14px'>
    <h2>DeepAlpha Admin</h2>{f"<p style='color:#f87171'>{error}</p>" if error else ''}
    <input type='password' name='secret' autocomplete='current-password' placeholder='Admin secret' required style='width:100%;padding:11px;border-radius:8px;border:1px solid #334155;background:#0b1220;color:#fff'>
    <button style='width:100%;margin-top:12px;padding:11px;border:0;border-radius:8px;background:#1d4ed8;color:#fff'>Sign in</button>
    </form></body></html>"""
    return web.Response(text=body, content_type="text/html", headers={"Cache-Control": "no-store"})


async def handle_admin_logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/admin/login")
    response.del_cookie(ADMIN_COOKIE_NAME, path="/admin")
    response.headers["Cache-Control"] = "no-store"
    return response


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


@web.middleware
async def deepalpha_security_middleware(request: web.Request, handler):
    if request.path.startswith("/admin") and request.path not in {"/admin/login", "/admin/logout"}:
        query_key = str(request.query.get("key", "") or "")
        secret = _admin_secret()
        if query_key and secret and hmac.compare_digest(query_key, secret) and not _valid_admin_cookie(request):
            return _strip_admin_key_redirect(request)

    origin = str(request.headers.get("Origin", "") or "").strip()
    configured_origins = allowed_cors_origins()
    origin_allowed = _origin_allowed(request, origin, configured_origins) if origin else False
    if origin and _is_api_path(request.path) and not origin_allowed:
        return _json_response({"ok": False, "error": "origin_not_allowed"}, 403)
    if request.method == "POST" and request.path.startswith("/admin") and origin and not origin_allowed:
        return web.Response(text="Forbidden", status=403)

    protected = _protect_legacy_user_api(request)
    if protected is not None:
        response = protected
    else:
        response = await handler(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if (
        request.path.startswith("/admin")
        or request.path.startswith("/api/v1/")
        or request.path.startswith("/app-api/")
        or request.path == "/developer"
    ):
        response.headers["Cache-Control"] = "no-store"

    response.headers.pop("Access-Control-Allow-Origin", None)
    if origin_allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Idempotency-Key, X-Idempotency-Key, "
            "X-Request-ID, X-DeepAlpha-Portal"
        )
        response.headers["Access-Control-Max-Age"] = "600"
    return response


def install_http_security(app: web.Application, admin_routes_module: Any) -> None:
    if app.get("deepalpha_http_security_installed"):
        return
    admin_routes_module._guard = _secure_admin_guard_factory()
    admin_routes_module._key = _secure_admin_key
    app.middlewares.append(deepalpha_security_middleware)
    app.router.add_get("/admin/login", handle_admin_login)
    app.router.add_post("/admin/login", handle_admin_login)
    app.router.add_get("/admin/logout", handle_admin_logout)
    app["deepalpha_http_security_installed"] = True
