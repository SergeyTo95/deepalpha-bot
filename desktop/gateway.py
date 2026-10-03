"""Isolated Desktop and optional Web preview using VELIA device authentication.

No database, Telegram bot or payment workers are used here. The opt-in Web
adapter uses its own encrypted, HttpOnly cookie; authority cookies are ignored.
Only a fixed identity authority receives account tokens; model keys stay here.
"""
from collections import deque
from dataclasses import dataclass
import json
import os
import re
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar, TCPConnector, web
from velia_desktop_routes import AuthenticationUnavailable, setup_velia_desktop_routes
from desktop.web_routes import prepare_web_payload, public_web_stream, setup_web_routes

MAX_AUTH_BODY = 16 * 1024
MAX_AUTH_RESPONSE = 64 * 1024
CLIENT = web.AppKey("identity_client", ClientSession)
READY = web.AppKey("identity_ready", bool)
ACCESS = re.compile(r"va_[A-Za-z0-9_-]{16,256}\Z")
REFRESH = re.compile(r"vr_[A-Za-z0-9_-]{16,256}\Z")


def https_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Identity and browser addresses must be HTTPS origins")
    return value.rstrip("/")


@dataclass(frozen=True)
class GatewayConfig:
    auth_origin: str
    browser_origin: str

    @classmethod
    def from_env(cls):
        return cls(https_origin(os.environ["VELIA_DESKTOP_AUTH_ORIGIN"]),
                   https_origin(os.environ["VELIA_DESKTOP_BROWSER_ORIGIN"]))


def json_response(data, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store",
        "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"})


def allowed(user_id):
    return str(user_id) in {s.strip() for s in
        os.getenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "").split(",") if s.strip()}


def valid_session(data):
    return (isinstance(data, dict) and data.get("ok") is True
            and isinstance(data.get("access_token"), str) and ACCESS.fullmatch(data["access_token"])
            and isinstance(data.get("refresh_token"), str) and REFRESH.fullmatch(data["refresh_token"])
            and type(data.get("access_expires_in")) is int
            and 1 <= data["access_expires_in"] <= 86400)


def create_app(config=None, *, check_identity=True, web_origin=None):
    config = config or GatewayConfig.from_env()
    app = web.Application(client_max_size=MAX_AUTH_BODY)
    exchanges = deque()

    async def upstream(method, path, *, token=None, data=None):
        headers = {"User-Agent": "VELIA-Desktop-Gateway/0.2"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            async with app[CLIENT].request(method, config.auth_origin + path, headers=headers,
                    json=data, allow_redirects=False) as response:
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    limit = 2 * 1024 * 1024 if path.startswith("/mobile-api/v1/conversations") else MAX_AUTH_RESPONSE
                    if len(body) > limit:
                        raise AuthenticationUnavailable()
                result = json.loads(body)
                if not isinstance(result, dict) or response.status not in {200, 201, 400, 401, 402, 403, 404, 409, 429, 502, 503}:
                    raise AuthenticationUnavailable()
                return response.status, result
        except (ClientError, TimeoutError, ValueError, OSError) as exc:
            raise AuthenticationUnavailable() from exc

    async def authenticate(token):
        if not isinstance(token, str) or not ACCESS.fullmatch(token):
            return None
        status, result = await upstream("GET", "/mobile-api/v1/me", token=token)
        if status in {401, 403}:
            return None
        user = result.get("user")
        if (status != 200 or result.get("ok") is not True or not isinstance(user, dict)
                or type(user.get("id")) is not int or user["id"] <= 0):
            raise AuthenticationUnavailable()
        return {"user_id": user["id"], "name": str(user.get("first_name") or "Аккаунт VELIA")[:120]}

    async def account_balance(token):
        status, result = await upstream("GET", "/mobile-api/v1/economy/me", token=token)
        credits = result.get("account", {}).get("credits") if isinstance(result.get("account"), dict) else None
        if status != 200 or result.get("ok") is not True or type(credits) is not int:
            raise AuthenticationUnavailable()
        return max(0, credits)

    async def authorize_model(token, model):
        if model == "velia-pro":
            if await account_balance(token) <= 0:
                return "pro_tokens_required", 402
        return None

    async def upstream_stream(path, *, token, data):
        timeout = ClientTimeout(total=360, sock_read=300)
        async with app[CLIENT].post(config.auth_origin + path, json=data,
                headers={"Authorization": "Bearer " + token, "User-Agent": "VELIA-Web/0.3"},
                timeout=timeout, allow_redirects=False) as response:
            if response.status != 200 or "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise AuthenticationUnavailable()
            async for chunk in response.content.iter_chunked(65536):
                yield chunk

    async def lifecycle(application):
        async with ClientSession(timeout=ClientTimeout(total=15, sock_read=10),
                                 connector=TCPConnector(limit=16), cookie_jar=DummyCookieJar()) as client:
            application[CLIENT] = client
            application[READY] = False
            if check_identity:
                status, result = await upstream("GET", "/mobile-api/v1/health")
                if status != 200 or result.get("ok") is not True or result.get("enabled") is not True:
                    raise RuntimeError("VELIA device authentication is unavailable")
            application[READY] = True
            yield
    app.cleanup_ctx.append(lifecycle)

    async def health(request):
        ready = (app[READY] and bool(os.getenv("KIMI_API_KEY", "").strip())
                 and os.getenv("VELIA_DESKTOP_API_ENABLED", "").lower() in {"true", "1"}
                 and bool(os.getenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "").strip()))
        return json_response({"ok": ready, "service": "velia-desktop-gateway"}, 200 if ready else 503)

    async def pairing_page(request):
        raise web.HTTPFound(location=config.browser_origin + "/mobile-connect",
                            headers={"Cache-Control": "no-store"})

    async def relay_session(request):
        if os.getenv("VELIA_DESKTOP_API_ENABLED", "").lower() not in {"true", "1"}:
            return json_response({"ok": False, "error": "desktop_api_disabled"}, 503)
        now = time.monotonic()
        while exchanges and exchanges[0] < now - 60:
            exchanges.popleft()
        if len(exchanges) >= 60:
            return json_response({"ok": False, "error": "auth_rate_limit"}, 429)
        exchanges.append(now)
        try:
            data = await request.json()
        except web.HTTPRequestEntityTooLarge:
            return json_response({"ok": False, "error": "request_too_large"}, 413)
        except (ValueError, UnicodeDecodeError):
            return json_response({"ok": False, "error": "invalid_json"}, 400)
        exchange = request.path.endswith("/exchange")
        fields = {"pairing_code", "device_id", "device_name"} if exchange else {"refresh_token", "device_id"}
        if not isinstance(data, dict) or set(data) - fields:
            return json_response({"ok": False, "error": "invalid_auth_request"}, 400)
        device = data.get("device_id")
        if not isinstance(device, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", device):
            return json_response({"ok": False, "error": "invalid_device_id"}, 400)
        if exchange:
            code, name = data.get("pairing_code"), data.get("device_name", "VELIA Desktop")
            if (not isinstance(code, str) or not re.fullmatch(r"[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{16}", code)
                    or not isinstance(name, str) or not 1 <= len(name) <= 120):
                return json_response({"ok": False, "error": "invalid_pairing_request"}, 400)
        elif not isinstance(data.get("refresh_token"), str) or not REFRESH.fullmatch(data["refresh_token"]):
            return json_response({"ok": False, "error": "invalid_refresh_token"}, 400)
        try:
            status, result = await upstream("POST", request.path, data=data)
            if status != 200 or result.get("ok") is not True:
                return json_response({"ok": False, "error": "pairing_failed" if exchange else "refresh_failed"}, 401)
            if not valid_session(result):
                raise AuthenticationUnavailable()
            identity = await authenticate(result["access_token"])
            if not identity or not allowed(identity["user_id"]):
                await upstream("POST", "/mobile-api/v1/auth/logout", token=result["access_token"])
                return json_response({"ok": False, "error": "preview_access_required"}, 403)
            # Return only the device-session protocol, never upstream cookies or headers.
            return json_response({key: result[key] for key in
                ("ok", "access_token", "refresh_token", "access_expires_in")})
        except AuthenticationUnavailable:
            return json_response({"ok": False, "error": "authentication_unavailable"}, 503)

    app.router.add_get("/health", health)
    app.router.add_get("/mobile-connect", pairing_page)
    app.router.add_post("/mobile-api/v1/auth/exchange", relay_session)
    app.router.add_post("/mobile-api/v1/auth/refresh", relay_session)
    handlers = setup_velia_desktop_routes(app, authenticate, prepare_payload=prepare_web_payload,
                                         filter_stream=public_web_stream, authorize_model=authorize_model)
    if os.getenv("VELIA_WEB_ENABLED", "").lower() in {"true", "1"}:
        origin = web_origin or https_origin(os.environ["VELIA_WEB_ORIGIN"])
        setup_web_routes(app, origin=origin, upstream=upstream, authenticate=authenticate,
            allowed=allowed, valid_session=valid_session, json_response=json_response, handlers=handlers,
            account_balance=account_balance, authorize_model=authorize_model, upstream_stream=upstream_stream)
    else:
        app.router.add_get("/", pairing_page)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8080")), access_log=None,
                handler_cancellation=True)
