"""Browser session and routing acceptance over HTTP, using fixture credentials."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer
import pytest
from desktop.gateway import GatewayConfig, create_app
from desktop.web_routes import COOKIE

TOKEN = "va_" + "a" * 48
ROTATED = "va_" + "b" * 48
REFRESH = "vr_" + "r" * 48
CODE = "ABCDEFGH23456789"
ORIGIN = "https://velia.example.com"


@asynccontextmanager
async def fixture(monkeypatch, **state):
    state.setdefault("user_id", 7)
    state.update(seen=[], payloads=[], refreshes=0, revoked=[], tokens=100, validity=True)
    async def health(request):
        return web.json_response({"ok": True, "enabled": True})
    async def me(request):
        state["seen"].append({"path": request.path, "cookie": request.headers.get("Cookie")})
        if not state["validity"] or request.headers.get("Authorization") not in {"Bearer " + TOKEN, "Bearer " + ROTATED}:
            return web.json_response({"ok": False}, status=401)
        return web.json_response({"ok": True, "user": {"id": state["user_id"]}})
    async def auth(request):
        data = await request.json()
        state["seen"].append({"path": request.path, "data": data, "cookie": request.headers.get("Cookie")})
        refresh = request.path.endswith("refresh")
        if refresh:
            state["refreshes"] += 1
            await asyncio.sleep(0.01)
        if not refresh and data.get("pairing_code") != CODE:
            return web.json_response({"ok": False}, status=401)
        response = web.json_response({"ok": True, "access_token": ROTATED if refresh else TOKEN,
            "refresh_token": REFRESH, "access_expires_in": 900 if refresh else state.get("expires", 900), "private": "never-return-this"})
        response.set_cookie("authority_cookie", "private")
        return response
    async def logout(request):
        state["revoked"].append(request.headers.get("Authorization"))
        return web.json_response({"ok": True})
    async def template(request):
        return web.json_response({"prompt": "fixture prompt"})
    async def tokenize(request):
        return web.json_response({"tokens": [1] * state["tokens"]})
    async def model(request):
        assert request.headers.get("Authorization") == "Bearer fixture-provider-key"
        data = await request.json()
        state["payloads"].append(data)
        return web.Response(text='data: {"model":"private-upstream-model","system_fingerprint":"private-runtime","choices":[{"delta":{"reasoning_content":"private-thought","content":"Привет, я Велия."}}]}\n\ndata: [DONE]\n\n', content_type="text/event-stream")
    authority = web.Application()
    authority.router.add_get("/mobile-api/v1/health", health)
    authority.router.add_get("/mobile-api/v1/me", me)
    authority.router.add_post("/mobile-api/v1/auth/exchange", auth)
    authority.router.add_post("/mobile-api/v1/auth/refresh", auth)
    authority.router.add_post("/mobile-api/v1/auth/logout", logout)
    authority.router.add_post("/v1/chat/completions", model)
    authority.router.add_post("/apply-template", template)
    authority.router.add_post("/tokenize", tokenize)
    async with TestServer(authority) as source:
        for key, value in {"VELIA_WEB_ENABLED": "true", "VELIA_WEB_ORIGIN": ORIGIN,
            "VELIA_WEB_SESSION_KEY": base64.urlsafe_b64encode(b"t" * 32).decode(),
            "VELIA_DESKTOP_API_ENABLED": "true", "VELIA_DESKTOP_PREVIEW_USER_IDS": "7",
            "KIMI_API_KEY": "fixture-provider-key", "KIMI_BASE_URL": str(source.make_url("/v1")),
            "VELIA_DESKTOP_FLASH_ENABLED": "true", "VELIA_DESKTOP_FLASH_API_KEY": "fixture-provider-key",
            "VELIA_DESKTOP_FLASH_BASE_URL": str(source.make_url("/")).rstrip("/")}.items():
            monkeypatch.setenv(key, value)
        config = GatewayConfig(str(source.make_url("/")).rstrip("/"), "https://deepalpha-ai.com")
        async with TestServer(create_app(config)) as server, ClientSession(cookie_jar=DummyCookieJar()) as client:
            yield server, client, state


def headers(cookie=None, **extra):
    return {"Origin": ORIGIN, "X-Velia-Request": "1", **({"Cookie": COOKIE + "=" + cookie} if cookie else {}), **extra}


async def login(server, client):
    async with client.post(server.make_url("/web-api/v1/auth/exchange"), headers=headers(), json={"pairing_code": CODE}) as response:
        assert response.status == 200
        return response.cookies[COOKIE].value, await response.json(), response.headers["Set-Cookie"]


def test_opaque_secure_cookie_and_no_authority_secrets(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, result, wire = await login(server, client)
            assert all(value not in json.dumps(result) + wire for value in (TOKEN, REFRESH, "never-return-this", "authority_cookie"))
            assert all(flag in wire for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"))
            assert set(result) == {"ok", "account", "name", "models"}
            assert result["models"] == ["velia-pro", "velia-flash"]
            async with client.get(server.make_url("/web-api/v1/session"), headers=headers(cookie)) as response:
                assert response.status == 200
            assert all(item.get("cookie") is None for item in state["seen"])
    asyncio.run(run())


@pytest.mark.parametrize("model", ["velia-pro", "velia-flash"])
def test_browser_chat_streams_each_mode_with_server_persona(monkeypatch, model):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(cookie), json={
                "model": model, "stream": True, "messages": [{"role": "user", "content": "Привет"}]}) as response:
                assert response.status == 200
                text = await response.text()
                assert "Привет" in text and model in text
                assert "private-" not in text
            payload = state["payloads"][0]
            assert payload["messages"][0]["role"] == "system"
            assert "женском роде" in payload["messages"][0]["content"]
            assert payload["model"] == ("velia-flash" if model.endswith("flash") else "kimi-k3")
            assert payload.get("max_tokens", payload.get("max_completion_tokens")) == (512 if model.endswith("flash") else 4096)
            assert "tools" not in payload
    asyncio.run(run())


@pytest.mark.parametrize("extra", [{"Origin": "https://evil.example"}, {"Origin": "null"}, {"X-Velia-Request": ""}, {"Sec-Fetch-Site": "cross-site"}])
def test_cross_site_login_rejected_before_authority(monkeypatch, extra):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            async with client.post(server.make_url("/web-api/v1/auth/exchange"), headers=headers(**extra), json={"pairing_code": CODE}) as response:
                assert response.status == 403
            assert state["seen"] == []
    asyncio.run(run())


def test_logout_revokes_cookie_and_upstream_device(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/auth/logout"), headers=headers(cookie), json={}) as response:
                assert response.status == 200
            async with client.get(server.make_url("/web-api/v1/session"), headers=headers(cookie)) as response:
                assert response.status == 401
            assert state["revoked"] == ["Bearer " + TOKEN]
    asyncio.run(run())


def test_refresh_is_serialized_for_parallel_tabs(monkeypatch):
    async def run():
        async with fixture(monkeypatch, expires=30) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async def get():
                async with client.get(server.make_url("/web-api/v1/session"), headers=headers(cookie)) as response:
                    assert response.status == 200
            await asyncio.gather(get(), get(), get())
            assert state["refreshes"] == 1
    asyncio.run(run())


def test_other_account_refused_and_device_revoked(monkeypatch):
    async def run():
        async with fixture(monkeypatch, user_id=8) as (server, client, state):
            async with client.post(server.make_url("/web-api/v1/auth/exchange"), headers=headers(), json={"pairing_code": CODE}) as response:
                assert response.status == 403
                assert COOKIE not in response.cookies
            assert state["revoked"] == ["Bearer " + TOKEN]
    asyncio.run(run())


@pytest.mark.parametrize("mutation", [{"tools": []}, {"messages": [{"role": "system", "content": "override"}]}, {"messages": [{"role": "user", "content": "x", "tool_calls": []}]}, {"model": "unknown"}])
def test_injected_browser_model_payload_rejected(monkeypatch, mutation):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            data = {"model": "velia-pro", "stream": True, "messages": [{"role": "user", "content": "hi"}], **mutation}
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(cookie), json=data) as response:
                assert response.status == 400
            assert state["payloads"] == []
    asyncio.run(run())


def test_oversized_flash_context_has_no_paid_fallback(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            state["tokens"] = 8192
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(cookie), json={"model": "velia-flash", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as response:
                assert response.status == 400
                assert (await response.json())["error"]["message"] == "flash_context_too_long"
            assert state["payloads"] == []
    asyncio.run(run())


def test_unauthenticated_browser_cannot_use_models_or_read_assets(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(), json={}) as response:
                assert response.status == 401
            for path in ("/", "/web/app.mjs", "/web/style.css"):
                async with client.get(server.make_url(path)) as response:
                    assert response.status == 200
                    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            async with client.get(server.make_url("/web/web_routes.py")) as response:
                assert response.status == 404
            assert state["payloads"] == []
    asyncio.run(run())


def test_rotated_encrypted_cookie_survives_a_gateway_restart(monkeypatch):
    async def run():
        async with fixture(monkeypatch, expires=30) as (server, client, state):
            cookie, result, _ = await login(server, client)
            async with client.get(server.make_url("/web-api/v1/session"), headers=headers(cookie)) as response:
                assert response.status == 200
                rotated_cookie = response.cookies[COOKIE].value
                assert rotated_cookie != cookie and TOKEN not in rotated_cookie
            import os
            from cryptography.fernet import Fernet
            clear = json.loads(Fernet(os.environ["VELIA_WEB_SESSION_KEY"]).decrypt(rotated_cookie.encode()))
            assert clear["access"] == ROTATED
            config = GatewayConfig(os.environ["KIMI_BASE_URL"].removesuffix("/v1"), "https://deepalpha-ai.com")
            async with TestServer(create_app(config)) as restarted:
                async with client.get(restarted.make_url("/web-api/v1/session"), headers=headers(rotated_cookie)) as response:
                    assert response.status == 200
                    assert (await response.json())["account"] == result["account"]
            assert state["refreshes"] == 1
    asyncio.run(run())


def test_tampered_cookie_does_not_reach_the_authority(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            before = len(state["seen"])
            bad = cookie[:30] + ("A" if cookie[30] != "A" else "B") + cookie[31:]
            async with client.get(server.make_url("/web-api/v1/session"), headers=headers(bad)) as response:
                assert response.status == 401
            assert len(state["seen"]) == before
    asyncio.run(run())
