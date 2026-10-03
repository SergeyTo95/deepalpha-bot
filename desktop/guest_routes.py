"""Guest Flash only, signed HttpOnly identity and durable server-side trial limit."""
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
from aiohttp import ClientError, ClientSession, ClientTimeout, web
from cryptography.fernet import Fernet, InvalidToken
from desktop.guest_store import GuestLimitReached, GuestStore, LIMIT
from desktop.web_routes import WEB_CHAT, prepare_web_payload, public_web_stream
from velia_desktop_routes import FlashContextTooLong, check_flash_context, flash_enabled, flash_endpoint, validate_payload, FLASH_ID

COOKIE = "__Host-velia-guest"
LIFETIME = 365 * 24 * 3600
GUEST = web.RequestKey("velia_guest_cookie", str)


def setup_guest_routes(app, *, origin, handlers, json_response, store=None, web_search=None):
    secret = os.environ["VELIA_WEB_SESSION_KEY"].encode()
    cipher = Fernet(secret)
    store = store or GuestStore(os.environ["VELIA_WEB_GUEST_DATABASE_URL"])

    async def lifecycle(application):
        await asyncio.to_thread(store.initialize)
        yield
    app.cleanup_ctx.append(lifecycle)

    def error(code, status):
        return json_response({"ok": False, "error": code}, status)

    def identity(request):
        value = request.cookies.get(COOKIE, "")
        if not value or len(value) > 1024:
            return None
        try:
            sid = cipher.decrypt(value.encode(), ttl=LIFETIME).decode()
            return sid if re.fullmatch(r"guest:[0-9a-f]{64}", sid) else None
        except (InvalidToken, UnicodeDecodeError, ValueError):
            return None

    def digest(value):
        return hmac.new(secret, ("velia-guest-quota:" + value).encode(), hashlib.sha256).hexdigest()

    def keys(request, sid):
        # Railway's edge sets X-Real-IP. Never trust client-supplied X-Forwarded-For.
        value = request.headers.get("X-Real-IP", "") if os.getenv("VELIA_WEB_GUEST_TRUST_RAILWAY_IP") == "true" else request.remote
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("invalid_guest_origin") from None
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return (digest(sid), digest("network:" + str(address) + ":" + day))

    async def cookie_header(request, response):
        value = request.get(GUEST)
        if value:
            response.set_cookie(COOKIE, value, max_age=LIFETIME, secure=True, httponly=True, samesite="Lax", path="/")
            response.headers.add("Set-Cookie", response.cookies[COOKIE].OutputString())
    app.on_response_prepare.append(cookie_header)

    async def current(request):
        sid = identity(request)
        if sid is None:
            sid = "guest:" + secrets.token_hex(32)
            request[GUEST] = cipher.encrypt(sid.encode()).decode()
        try:
            remaining = await asyncio.to_thread(store.remaining, keys(request, sid))
        except Exception:
            return error("guest_service_unavailable", 503)
        return json_response({"ok": True, "guest": True, "account": digest(sid)[:24],
            "limit": LIMIT, "remaining": remaining, "models": [FLASH_ID] if flash_enabled() else [],
            "web_search": bool(web_search and web_search.available)})

    async def chat(request):
        if (request.headers.get("Origin") != origin or request.headers.get("X-Velia-Request") != "1"
                or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin" or request.content_type != "application/json"):
            return error("invalid_origin", 403)
        sid = identity(request)
        if sid is None:
            return error("guest_session_required", 401)
        try:
            data = await request.json()
            if isinstance(data, dict) and data.get("model") != FLASH_ID:
                return error("guest_flash_only", 403)
            request[WEB_CHAT] = True
            payload = validate_payload(prepare_web_payload(request, data,
                search_enabled=bool(web_search and web_search.available)))
            if not flash_enabled():
                return error("flash_unavailable", 503)
            quota_keys = keys(request, sid)
            if await asyncio.to_thread(store.remaining, quota_keys) <= 0:
                return error("guest_limit_reached", 429)
        except web.HTTPRequestEntityTooLarge:
            return error("request_too_large", 413)
        except (ValueError, TypeError, UnicodeDecodeError):
            return error("invalid_messages", 400)
        except Exception:
            return error("guest_service_unavailable", 503)
        failure = handlers["reserve"](sid)
        if failure is not None:
            return failure
        response = None
        try:
            async with ClientSession(timeout=ClientTimeout(total=360, sock_read=300)) as client:
                headers = {"Authorization": "Bearer " + os.environ["VELIA_DESKTOP_FLASH_API_KEY"]}
                endpoint = flash_endpoint()
                await check_flash_context(client, endpoint, headers, payload)
                try:
                    remaining = await asyncio.to_thread(store.reserve, quota_keys)
                except GuestLimitReached:
                    return error("guest_limit_reached", 429)
                except Exception:
                    return error("guest_service_unavailable", 503)
                from desktop.web_search import SEARCH, SearchUnavailable
                if request.get(SEARCH):
                    if web_search is None:
                        raise SearchUnavailable()
                    payload = await web_search.enrich(request, payload)
                    await check_flash_context(client, endpoint, headers, payload)
                async with client.post(endpoint + "/v1/chat/completions", json=payload, headers=headers, allow_redirects=False) as upstream:
                    if upstream.status != 200 or "text/event-stream" not in upstream.headers.get("Content-Type", ""):
                        return error("model_request_failed", 502)
                    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store",
                        "X-Accel-Buffering": "no", "X-Velia-Guest-Remaining": str(remaining)})
                    await response.prepare(request)
                    async for chunk in public_web_stream(request, upstream.content):
                        await response.write(chunk)
                    await response.write_eof()
                    return response
        except FlashContextTooLong:
            return error("flash_context_too_long", 400)
        except (ClientError, TimeoutError, OSError, ValueError):
            if response is not None and response.prepared:
                response.force_close()
                return response
            return error("model_connection_failed", 502)
        finally:
            handlers["release"](sid)

    app.router.add_get("/web-api/v1/guest", current)
    app.router.add_post("/web-api/v1/guest/chat/completions", chat)
