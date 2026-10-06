"""One-shot live acceptance for VELIA Web -> Agent Core -> Flash -> Chromium.

This starts a loopback-only synthetic identity authority and a loopback copy of
the gateway, then sends an authenticated Web Browser Agent request to the real
private Agent Core service. No owner token, pairing code, provider key or
Browser Agent secret is returned to a browser.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import secrets

from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer

from desktop.gateway import GatewayConfig, create_app
from desktop.web_routes import COOKIE

FIXTURE_USER = 99057701
PAIRING = "ABCDEFGH23456789"
ACCESS = "va_" + "a" * 48
REFRESH = "vr_" + "r" * 48
ORIGIN = "https://velia-agent-probe.invalid"


@asynccontextmanager
async def authority():
    async def health(_request):
        return web.json_response({"ok": True, "enabled": True})

    async def exchange(request):
        body = await request.json()
        if body.get("pairing_code") != PAIRING:
            return web.json_response({"ok": False}, status=401)
        return web.json_response({
            "ok": True,
            "access_token": ACCESS,
            "refresh_token": REFRESH,
            "access_expires_in": 900,
        })

    async def me(request):
        if request.headers.get("Authorization") != "Bearer " + ACCESS:
            return web.json_response({"ok": False}, status=401)
        return web.json_response({
            "ok": True,
            "user": {"id": FIXTURE_USER, "first_name": "VELIA Agent probe"},
        })

    async def economy(request):
        if request.headers.get("Authorization") != "Bearer " + ACCESS:
            return web.json_response({"ok": False}, status=401)
        return web.json_response({"ok": True, "account": {"credits": 0}})

    async def logout(_request):
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/mobile-api/v1/health", health)
    app.router.add_post("/mobile-api/v1/auth/exchange", exchange)
    app.router.add_get("/mobile-api/v1/me", me)
    app.router.add_get("/mobile-api/v1/economy/me", economy)
    app.router.add_post("/mobile-api/v1/auth/logout", logout)
    async with TestServer(app) as server:
        yield str(server.make_url("/")).rstrip("/")


async def run():
    agent_origin = str(os.getenv("VELIA_AGENT_CORE_BROWSER_ORIGIN", "") or "").strip()
    secret = str(os.getenv("VELIA_AGENT_CORE_INTERNAL_KEY", "") or "").strip()
    if not agent_origin or not secret:
        raise RuntimeError("browser_agent_configuration_missing")

    previous = {
        key: os.environ.get(key)
        for key in (
            "VELIA_WEB_ENABLED",
            "VELIA_WEB_ORIGIN",
            "VELIA_WEB_SESSION_KEY",
            "VELIA_DESKTOP_API_ENABLED",
            "VELIA_DESKTOP_PREVIEW_USER_IDS",
            "VELIA_BROWSER_AGENT_ENABLED",
        )
    }
    os.environ.update({
        "VELIA_WEB_ENABLED": "true",
        "VELIA_WEB_ORIGIN": ORIGIN,
        "VELIA_WEB_SESSION_KEY": base64.urlsafe_b64encode(b"w" * 32).decode(),
        "VELIA_DESKTOP_API_ENABLED": "true",
        "VELIA_DESKTOP_PREVIEW_USER_IDS": str(FIXTURE_USER),
        "VELIA_BROWSER_AGENT_ENABLED": "true",
    })

    try:
        async with authority() as auth_origin:
            config = GatewayConfig(
                auth_origin=auth_origin,
                browser_origin=ORIGIN,
                admin_origin=auth_origin,
                agent_origin=agent_origin,
            )
            async with TestServer(create_app(config, web_origin=ORIGIN)) as gateway:
                async with ClientSession(cookie_jar=DummyCookieJar()) as client:
                    headers = {"Origin": ORIGIN, "X-Velia-Request": "1"}
                    async with client.post(
                        gateway.make_url("/web-api/v1/auth/exchange"),
                        headers=headers,
                        json={"pairing_code": PAIRING},
                    ) as response:
                        login = await response.json()
                        if response.status != 200 or login.get("browser_agent") is not True:
                            raise RuntimeError("browser_agent_web_login_failed")
                        cookie = response.cookies[COOKIE].value

                    headers["Cookie"] = COOKIE + "=" + cookie
                    prompt = (
                        "Открой https://example.com. Прочитай заголовок страницы и первый абзац. "
                        "Ответь одной короткой фразой по-русски. Не отвечай по памяти: обязательно "
                        "используй браузерный инструмент."
                    )
                    async with client.post(
                        gateway.make_url("/web-api/v1/agent/browser"),
                        headers=headers,
                        json={"prompt": prompt},
                    ) as response:
                        body = await response.json()
                        if response.status != 200:
                            raise RuntimeError("browser_agent_web_status_" + str(response.status))
                        if (body.get("ok") is not True or body.get("model") != "velia-flash"
                                or int(body.get("tool_count") or 0) < 1
                                or not str(body.get("text") or "").strip()):
                            raise RuntimeError("browser_agent_web_result_invalid")
                        receipt = {
                            "ok": True,
                            "model": "velia-flash",
                            "authenticated_web_route": True,
                            "private_agent_route": True,
                            "browser_tool_used": True,
                            "tool_count": int(body["tool_count"]),
                            "answer_chars": len(body["text"]),
                            "paid_fallback": False,
                        }
                        print(
                            "VELIA_WEB_BROWSER_AGENT_ACCEPTED "
                            + json.dumps(receipt, sort_keys=True),
                            flush=True,
                        )
                        return receipt
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    print(
        "VELIA_WEB_BROWSER_AGENT_E2E "
        + json.dumps(asyncio.run(run()), sort_keys=True),
        flush=True,
    )
