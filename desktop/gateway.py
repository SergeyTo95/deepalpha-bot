"""Isolated Desktop and optional Web preview using VELIA device authentication.

Account data stays with the fixed identity authority. Optional guest access
uses the existing preview database only for persistent quota counters. The Web
adapter uses encrypted, HttpOnly cookies; authority cookies are ignored.
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
from desktop.admin_proxy import setup_owner_admin_proxy

MAX_AUTH_BODY = 16 * 1024
MAX_AUTH_RESPONSE = 64 * 1024
MAX_AGENT_RESPONSE = 1024 * 1024
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


def internal_agent_origin(value):
    parsed = urlsplit(value)
    private_http = parsed.scheme == "http" and (
        parsed.hostname == "127.0.0.1" or parsed.hostname == "localhost"
        or (parsed.hostname or "").endswith(".railway.internal")
    )
    if ((parsed.scheme != "https" and not private_http) or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Browser Agent must use HTTPS or a Railway-private HTTP origin")
    return value.rstrip("/")


@dataclass(frozen=True)
class GatewayConfig:
    auth_origin: str
    browser_origin: str
    admin_origin: str = ""
    agent_origin: str = ""

    @classmethod
    def from_env(cls):
        auth_origin = https_origin(os.environ["VELIA_DESKTOP_AUTH_ORIGIN"])
        admin_raw = str(os.getenv("VELIA_DESKTOP_ADMIN_ORIGIN", "") or "").strip()
        agent_raw = str(os.getenv("VELIA_AGENT_CORE_BROWSER_ORIGIN", "") or "").strip()
        return cls(
            auth_origin,
            https_origin(os.environ["VELIA_DESKTOP_BROWSER_ORIGIN"]),
            https_origin(admin_raw) if admin_raw else auth_origin,
            internal_agent_origin(agent_raw) if agent_raw else "",
        )


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


def create_app(config=None, *, check_identity=True, web_origin=None, guest_store=None, web_search=None):
    config = config or GatewayConfig.from_env()
    app = web.Application(client_max_size=MAX_AUTH_BODY)
    exchanges = deque()
    if web_search is not None or os.getenv("VELIA_WEB_SEARCH_ENABLED") == "true":
        import asyncio
        from desktop.guest_store import GuestStore
        from desktop.web_search import WebSearch
        store = guest_store or GuestStore(os.environ["VELIA_WEB_GUEST_DATABASE_URL"])
        web_search = web_search or WebSearch(store=store)
        async def search_lifecycle(application):
            await asyncio.to_thread(web_search.store.initialize_search)
            yield
        app.cleanup_ctx.append(search_lifecycle)

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

    async def browser_agent_run(user_id, prompt):
        if os.getenv("VELIA_BROWSER_AGENT_ENABLED", "").lower() not in {"true", "1", "yes", "on"}:
            return 503, {"ok": False, "error": "browser_agent_disabled"}
        secret = str(os.getenv("VELIA_AGENT_CORE_INTERNAL_KEY", "") or "").strip()
        if not config.agent_origin or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
            return 503, {"ok": False, "error": "browser_agent_unavailable"}
        headers = {
            "Authorization": "Bearer " + secret,
            "User-Agent": "VELIA-Web-Browser-Agent/0.1",
            "X-Velia-User": str(user_id),
        }
        try:
            async with app[CLIENT].post(
                    config.agent_origin + "/v1/run",
                    json={"prompt": prompt},
                    headers=headers,
                    allow_redirects=False,
                    timeout=ClientTimeout(total=460, sock_read=450)) as response:
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_AGENT_RESPONSE:
                        return 502, {"ok": False, "error": "browser_agent_invalid_response"}
                try:
                    result = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    return 502, {"ok": False, "error": "browser_agent_invalid_response"}
                if not isinstance(result, dict):
                    return 502, {"ok": False, "error": "browser_agent_invalid_response"}
                if response.status == 200 and result.get("ok") is True:
                    return 200, {
                        "ok": True,
                        "text": str(result.get("text") or "")[:128000],
                        "model": "velia-flash",
                        "session_id": str(result.get("session_id") or "")[:160],
                        "tool_count": int(result.get("tool_count") or 0),
                    }
                code = str(result.get("error") or "browser_agent_failed")
                if response.status == 429:
                    return 429, {"ok": False, "error": "browser_agent_busy"}
                if response.status == 504:
                    return 504, {"ok": False, "error": "browser_agent_timeout"}
                if response.status == 400:
                    return 400, {"ok": False, "error": "invalid_browser_task"}
                return 503, {"ok": False, "error": "browser_agent_unavailable"}
        except (ClientError, TimeoutError, OSError):
            return 503, {"ok": False, "error": "browser_agent_unavailable"}

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
    async def enrich_web(request, payload):
        from desktop.web_search import SEARCH, PREPARED_REPLY, SearchUnavailable
        if request.get(SEARCH):
            if web_search is None:
                raise SearchUnavailable()
            payload = await web_search.enrich(request, payload)
            if request.get(PREPARED_REPLY):
                from desktop.web_routes import prepared_web_response
                return await prepared_web_response(request, request[PREPARED_REPLY])
            return payload
        return payload
    def prepare_browser_payload(request, data):
        return prepare_web_payload(request, data, search_enabled=bool(web_search and web_search.available))
    handlers = setup_velia_desktop_routes(app, authenticate, prepare_payload=prepare_browser_payload,
        filter_stream=public_web_stream, authorize_model=authorize_model, enrich_payload=enrich_web)
    if os.getenv("VELIA_WEB_ENABLED", "").lower() in {"true", "1"}:
        origin = web_origin or https_origin(os.environ["VELIA_WEB_ORIGIN"])
        setup_web_routes(app, origin=origin, upstream=upstream, authenticate=authenticate,
            allowed=allowed, valid_session=valid_session, json_response=json_response, handlers=handlers,
            account_balance=account_balance, authorize_model=authorize_model, upstream_stream=upstream_stream,
            web_search=web_search, browser_agent_run=browser_agent_run)
        if os.getenv("VELIA_WEB_GUEST_ENABLED") == "true":
            from desktop.guest_routes import setup_guest_routes
            setup_guest_routes(app, origin=origin, handlers=handlers, json_response=json_response,
                store=guest_store, web_search=web_search)
    else:
        app.router.add_get("/", pairing_page)
    if os.getenv("VELIA_DESKTOP_ADMIN_PROXY_ENABLED", "").lower() in {"true", "1", "yes", "on"}:
        setup_owner_admin_proxy(app, upstream_origin=(config.admin_origin or config.auth_origin), client_key=CLIENT)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8080")), access_log=None,
                handler_cancellation=True)
