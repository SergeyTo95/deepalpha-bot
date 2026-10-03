"""Real HTTP relay tests use synthetic identities, never production tokens."""
import asyncio
from contextlib import asynccontextmanager
import json

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from desktop.gateway import GatewayConfig, create_app, https_origin

TOKEN = "va_" + "a" * 48
REFRESH = "vr_" + "r" * 48
CODE = "ABCDEFGH23456789"


@asynccontextmanager
async def fixture(monkeypatch, **state):
    state.setdefault("user_id", 7)
    state.update(seen=[], model_calls=0, revoked=[])
    async def me(request):
        state["seen"].append({"path": request.path, "cookie": request.headers.get("Cookie"),
                              "authorization": request.headers.get("Authorization")})
        if state.get("me_redirect"):
            raise web.HTTPTemporaryRedirect(location=state["me_redirect"])
        if state.get("me_status"):
            return web.json_response({"secret": "private-authority-error"}, status=state["me_status"])
        if request.headers.get("Authorization") != "Bearer " + TOKEN:
            return web.json_response({"ok": False}, status=401)
        return web.json_response({"ok": True, "user": {"id": state["user_id"]}})
    async def session(request):
        data = await request.json()
        state["seen"].append({"path": request.path, "data": data, "cookie": request.headers.get("Cookie")})
        response = web.json_response({"ok": True, "access_token": TOKEN, "refresh_token": REFRESH,
                                      "access_expires_in": 900, "internal": "private"})
        response.set_cookie("browser_session", "never-relay-this")
        return response
    async def logout(request):
        state["revoked"].append(request.headers.get("Authorization"))
        return web.json_response({"ok": True})
    async def health(request):
        return web.json_response({"ok": True, "enabled": True})
    async def economy(request):
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        return web.json_response({"ok": True, "account": {"credits": state.get("credits", 10)}})
    async def model(request):
        state["model_calls"] += 1
        assert request.headers["Authorization"] == "Bearer provider-key"
        payload = await request.json()
        if payload["stream"]:
            return web.Response(text='data: {"choices":[{"delta":{"content":"Привет"}}]}\n\ndata: [DONE]\n\n',
                                content_type="text/event-stream")
        return web.json_response({"choices": [{"message": {"role": "assistant", "content": "Привет"}}]})
    authority = web.Application()
    authority.router.add_get("/mobile-api/v1/me", me)
    authority.router.add_get("/mobile-api/v1/health", health)
    authority.router.add_get("/mobile-api/v1/economy/me", economy)
    authority.router.add_post("/mobile-api/v1/auth/exchange", session)
    authority.router.add_post("/mobile-api/v1/auth/refresh", session)
    authority.router.add_post("/mobile-api/v1/auth/logout", logout)
    authority.router.add_post("/v1/chat/completions", model)
    async with TestServer(authority) as source:
        monkeypatch.setenv("VELIA_DESKTOP_API_ENABLED", "true")
        monkeypatch.setenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "7")
        monkeypatch.setenv("KIMI_API_KEY", "provider-key")
        monkeypatch.setenv("KIMI_BASE_URL", str(source.make_url("/v1")))
        # Test-only explicit loopback config; production from_env requires HTTPS.
        config = GatewayConfig(str(source.make_url("/")).rstrip("/"), "https://deepalpha-ai.com")
        async with TestServer(create_app(config)) as server, ClientSession() as client:
            yield server, client, state


@pytest.mark.parametrize("origin", ["http://host", "https://user:password@host", "https://host/path", "https://host?q=x", "https://host#fragment"])
def test_identity_origin_rejects_unsafe_config(origin):
    with pytest.raises(ValueError):
        https_origin(origin)


def test_pairing_refresh_and_stream_use_authoritative_identity_without_cookies(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            for suffix, data in [("exchange", {"pairing_code": CODE, "device_id": "desktop-id", "device_name": "VELIA Desktop"}),
                                 ("refresh", {"refresh_token": REFRESH, "device_id": "desktop-id"})]:
                async with client.post(server.make_url("/mobile-api/v1/auth/" + suffix), json=data,
                                       headers={"Cookie": "web_account=never-forward-this"}) as response:
                    assert response.status == 200
                    assert "Set-Cookie" not in response.headers
                    result = await response.json()
                    assert set(result) == {"ok", "access_token", "refresh_token", "access_expires_in"}
                    assert result["access_token"] == TOKEN
            async with client.post(server.make_url("/desktop-api/v1/chat/completions"),
                    headers={"Authorization": "Bearer " + TOKEN}, json={"model": "velia-pro",
                    "messages": [{"role": "user", "content": "Привет"}], "stream": True}) as response:
                assert response.status == 200
                assert "[DONE]" in await response.text()
            assert state["model_calls"] == 1
            assert all(item.get("cookie") is None for item in state["seen"])
            async with client.get(server.make_url("/mobile-connect"), allow_redirects=False) as response:
                assert response.status == 302
                assert response.headers["Location"] == "https://deepalpha-ai.com/mobile-connect"
    asyncio.run(run())


@pytest.mark.parametrize("user_id,token,status", [(7, None, 401), (7, "va_" + "x" * 48, 401), (8, TOKEN, 403), (7, TOKEN, 200)])
def test_only_allowlisted_valid_device_can_use_model(monkeypatch, user_id, token, status):
    async def run():
        async with fixture(monkeypatch, user_id=user_id) as (server, client, state):
            headers = {"Authorization": "Bearer " + token} if token else {}
            async with client.post(server.make_url("/desktop-api/v1/chat/completions"), headers=headers,
                json={"model": "velia-pro", "messages": [{"role": "user", "content": "hi"}]}) as response:
                assert response.status == status
            assert state["model_calls"] == (1 if status == 200 else 0)
    asyncio.run(run())


def test_pairing_other_account_revokes_new_session_and_returns_no_tokens(monkeypatch):
    async def run():
        async with fixture(monkeypatch, user_id=8) as (server, client, state):
            async with client.post(server.make_url("/mobile-api/v1/auth/exchange"), json={
                    "pairing_code": CODE, "device_id": "desktop-id"}) as response:
                assert response.status == 403
                body = await response.text()
                assert TOKEN not in body and REFRESH not in body
            assert state["revoked"] == ["Bearer " + TOKEN]
            assert state["model_calls"] == 0
    asyncio.run(run())


def test_identity_redirect_never_receives_device_token(monkeypatch):
    async def run():
        seen = []
        async def target(request):
            seen.append(request.headers.get("Authorization"))
            return web.json_response({"ok": True, "user": {"id": 7}})
        app = web.Application()
        app.router.add_get("/target", target)
        async with TestServer(app) as destination:
            async with fixture(monkeypatch, me_redirect=str(destination.make_url("/target"))) as (server, client, state):
                async with client.get(server.make_url("/desktop-api/v1/models"), headers={"Authorization": "Bearer " + TOKEN}) as response:
                    assert response.status == 503
                    assert (await response.json())["error"]["message"] == "authentication_unavailable"
                assert seen == [] and state["model_calls"] == 0
    asyncio.run(run())


def test_authority_outage_is_sanitized_and_never_uses_model(monkeypatch):
    async def run():
        async with fixture(monkeypatch, me_status=503) as (server, client, state):
            async with client.get(server.make_url("/desktop-api/v1/models"), headers={"Authorization": "Bearer " + TOKEN}) as response:
                assert response.status == 503
                assert "private-authority-error" not in await response.text()
            assert state["model_calls"] == 0
    asyncio.run(run())


def test_oversized_or_injected_auth_request_does_not_reach_identity_authority(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            endpoint = server.make_url("/mobile-api/v1/auth/exchange")
            async with client.post(endpoint, json={"pairing_code": CODE, "device_id": "desktop-id", "user_id": 7}) as response:
                assert response.status == 400
            async with client.post(endpoint, data=json.dumps({"device_name": "x" * 17000}), headers={"Content-Type": "application/json"}) as response:
                assert response.status == 413
            assert state["seen"] == []
            async with client.post(server.make_url("/mobile-api/v1/conversations"), json={}) as response:
                assert response.status == 404
    asyncio.run(run())
