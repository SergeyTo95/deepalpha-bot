"""Browser session and routing acceptance over HTTP, using fixture credentials."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import uuid
from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer
import pytest
from desktop.gateway import GatewayConfig, create_app
from desktop.web_routes import COOKIE
from desktop.web_search import WebSearch

TOKEN = "va_" + "a" * 48
ROTATED = "va_" + "b" * 48
REFRESH = "vr_" + "r" * 48
CODE = "ABCDEFGH23456789"
ORIGIN = "https://velia.example.com"


@asynccontextmanager
async def fixture(monkeypatch, **state):
    state.setdefault("user_id", 7)
    state.update(seen=[], payloads=[], refreshes=0, revoked=[], tokens=100, validity=True)
    state.setdefault("credits", 100)
    state.setdefault("conversations", {})
    state["account_calls"] = []
    state["search_queries"] = []
    async def search(request):
        data = await request.json()
        assert data["api_key"] == "fixture-search-key"
        assert request.headers.get("Cookie") is None
        state["search_queries"].append(data["query"])
        return web.json_response(state.get("search_response", {"results": [
            {"title":"Python official", "url":"https://www.python.org/downloads/", "content":"Python test release, official source."},
            {"title":"Docs", "url":"https://docs.python.org/", "content":"Python documentation."}]}),
            status=state.get("search_status", 200))
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
    async def economy(request):
        if state.get("economy_unavailable"):
            return web.json_response({"ok": False, "secret": "private"}, status=503)
        return web.json_response({"ok": True, "account": {"credits": state["credits"]}})
    async def conversations(request):
        assert request.headers.get("Authorization") in {"Bearer " + TOKEN, "Bearer " + ROTATED}
        assert request.headers.get("Cookie") is None
        if request.method == "GET":
            return web.json_response({"ok": True, "conversations": [c["conversation"] for c in state["conversations"].values()]})
        data = await request.json()
        value = {"id": str(uuid.uuid4()), "title": data["title"], "user_id": state["user_id"], "updated_at": "2026-10-03T20:00:00"}
        state["conversations"][value["id"]] = {"conversation": value, "messages": []}
        return web.json_response({"ok": True, "conversation": value}, status=201)
    async def stored_messages(request):
        value = state["conversations"].get(request.match_info["conversation_id"])
        if not value:
            return web.json_response({"ok": False, "error": "conversation_not_found"}, status=404)
        return web.json_response({"ok": True, "messages": value["messages"]})
    async def delete(request):
        if state["conversations"].pop(request.match_info["conversation_id"], None) is None:
            return web.json_response({"ok": False, "error": "conversation_not_found"}, status=404)
        return web.json_response({"ok": True, "private": "never"})
    async def stored_send(request):
        data = await request.json()
        state["account_calls"].append(data)
        value = state["conversations"].get(request.match_info["conversation_id"])
        if not value:
            return web.Response(text='data: {"type":"error","error":"conversation_not_found"}\n\n', content_type="text/event-stream")
        answer = {"id": str(uuid.uuid4()), "role": "assistant", "content": "Ответ из аккаунта", "status": "completed", "chat_mode": data["chat_mode"], "provider": "private-provider", "usage": {"private": True}}
        value["messages"].extend([{"role": "user", "content": data["content"], "status": "completed"}, answer])
        events = [{"type": "ready"}, {"type": "delta", "text": "Ответ из аккаунта"},
            {"type": "complete", "result": {"ok": True, "assistant_message": answer, "generation": {"private": "never"}}}]
        return web.Response(text="".join("data: " + json.dumps(e) + "\n\n" for e in events), content_type="text/event-stream")
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
    authority.router.add_get("/mobile-api/v1/economy/me", economy)
    authority.router.add_get("/mobile-api/v1/conversations", conversations)
    authority.router.add_post("/mobile-api/v1/conversations", conversations)
    authority.router.add_get("/mobile-api/v1/conversations/{conversation_id}/messages", stored_messages)
    authority.router.add_delete("/mobile-api/v1/conversations/{conversation_id}", delete)
    authority.router.add_post("/mobile-api/v1/conversations/{conversation_id}/messages/stream", stored_send)
    authority.router.add_post("/mobile-api/v1/auth/exchange", auth)
    authority.router.add_post("/mobile-api/v1/auth/refresh", auth)
    authority.router.add_post("/mobile-api/v1/auth/logout", logout)
    authority.router.add_post("/v1/chat/completions", model)
    authority.router.add_post("/apply-template", template)
    authority.router.add_post("/tokenize", tokenize)
    authority.router.add_post("/search", search)
    async with TestServer(authority) as source:
        for key, value in {"VELIA_WEB_ENABLED": "true", "VELIA_WEB_ORIGIN": ORIGIN,
            "VELIA_WEB_SESSION_KEY": base64.urlsafe_b64encode(b"t" * 32).decode(),
            "VELIA_DESKTOP_API_ENABLED": "true", "VELIA_DESKTOP_PREVIEW_USER_IDS": "7",
            "KIMI_API_KEY": "fixture-provider-key", "KIMI_BASE_URL": str(source.make_url("/v1")),
            "VELIA_DESKTOP_FLASH_ENABLED": "true", "VELIA_DESKTOP_FLASH_API_KEY": "fixture-provider-key",
            "VELIA_DESKTOP_FLASH_BASE_URL": str(source.make_url("/")).rstrip("/")}.items():
            monkeypatch.setenv(key, value)
        config = GatewayConfig(str(source.make_url("/")).rstrip("/"), "https://deepalpha-ai.com")
        search_service = WebSearch(provider="tavily", api_key="fixture-search-key",
            endpoint=str(source.make_url("/search")), store=state["guest_store"]) if state.get("with_search") else None
        async with TestServer(create_app(config, guest_store=state.get("guest_store"),
                web_search=search_service)) as server, ClientSession(cookie_jar=DummyCookieJar()) as client:
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
            assert set(result) == {"ok", "account", "name", "models", "credits", "pro_locked_reason", "web_search"}
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


@pytest.mark.parametrize("credits,unavailable,status", [(0, False, 402), (-3, False, 402), (0, True, 503)])
def test_pro_balance_gate_cannot_be_bypassed_by_browser_or_desktop(monkeypatch, credits, unavailable, status):
    async def run():
        async with fixture(monkeypatch, credits=credits, economy_unavailable=unavailable) as (server, client, state):
            cookie, profile, _ = await login(server, client)
            assert profile["models"] == ["velia-flash"]
            assert profile["pro_locked_reason"]
            for path, extra, body in [
                ("/web-api/v1/chat/completions", headers(cookie), {"model": "velia-pro", "stream": True, "messages": [{"role": "user", "content": "hi"}]}),
                ("/desktop-api/v1/chat/completions", {"Authorization": "Bearer " + TOKEN}, {"model": "velia-pro", "stream": True, "messages": [{"role": "user", "content": "hi"}]}),
                ("/web-api/v1/conversations/" + str(uuid.uuid4()) + "/messages/stream", headers(cookie), {"model": "velia-pro", "content": "hi", "idempotency_key": str(uuid.uuid4())}),
            ]:
                async with client.post(server.make_url(path), headers=extra, json=body) as response:
                    assert response.status == status
                    wire = await response.text()
                    assert "private" not in wire
            assert state["payloads"] == [] and state["account_calls"] == []
    asyncio.run(run())


def test_flash_remains_available_without_tokens_and_balance_refreshes(monkeypatch):
    async def run():
        async with fixture(monkeypatch, credits=0) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(cookie), json={
                    "model": "velia-flash", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as response:
                assert response.status == 200
            state["credits"] = 2
            async with client.get(server.make_url("/web-api/v1/session"), headers=headers(cookie)) as response:
                assert (await response.json())["models"] == ["velia-pro", "velia-flash"]
            state["credits"] = 0
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(cookie), json={
                    "model": "velia-pro", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as response:
                assert response.status == 402
            assert len(state["payloads"]) == 1 and state["payloads"][0]["model"] == "velia-flash"
    asyncio.run(run())


def test_old_account_history_new_messages_and_deletion_use_the_same_store(monkeypatch):
    async def run():
        old = str(uuid.uuid4())
        conversations = {old: {"conversation": {"id": old, "title": "Старый диалог", "user_id": 7}, "messages": [
            {"id": "old-user", "role": "user", "content": "Моя старая идея", "status": "completed"},
            {"id": "old-assistant", "role": "assistant", "content": "Помню идею", "status": "completed", "usage": {"provider": "secret"}},
        ]}}
        async with fixture(monkeypatch, credits=0, conversations=conversations) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.get(server.make_url("/web-api/v1/conversations"), headers=headers(cookie)) as response:
                result = await response.json()
                assert result["conversations"][0]["id"] == old and "user_id" not in await response.text()
            async with client.get(server.make_url("/web-api/v1/conversations/" + old + "/messages"), headers=headers(cookie)) as response:
                result = await response.json()
                assert [m["content"] for m in result["messages"]] == ["Моя старая идея", "Помню идею"]
                assert "usage" not in await response.text()
            async with client.post(server.make_url("/web-api/v1/conversations/" + old + "/messages/stream"), headers=headers(cookie), json={
                    "model": "velia-flash", "content": "Продолжи мою идею", "idempotency_key": str(uuid.uuid4())}) as response:
                assert response.status == 200
                wire = await response.text()
                assert "Ответ из аккаунта" in wire and "[DONE]" in wire and "private" not in wire
            assert state["account_calls"][0]["chat_mode"] == "flash" and len(conversations[old]["messages"]) == 4
            async with client.post(server.make_url("/web-api/v1/conversations"), headers=headers(cookie), json={"title": "Новый диалог"}) as response:
                assert response.status == 201
                new = (await response.json())["conversation"]["id"]
            assert new in state["conversations"]
            async with client.post(server.make_url("/web-api/v1/conversations/" + old + "/delete"), headers=headers(cookie), json={}) as response:
                assert response.status == 200
            assert old not in state["conversations"]
    asyncio.run(run())


@pytest.mark.parametrize("path,body", [("conversations", {"title": "hi"}),
    ("conversations/11111111-1111-1111-1111-111111111111/delete", {}),
    ("conversations/11111111-1111-1111-1111-111111111111/messages/stream", {"model": "velia-flash", "content": "hi", "idempotency_key": "request-test"})])
def test_account_mutations_require_same_origin_and_a_signed_in_user(monkeypatch, path, body):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            for incoming, status in [(headers(), 401), (headers(cookie, Origin="https://evil.example"), 403)]:
                async with client.post(server.make_url("/web-api/v1/" + path), headers=incoming, json=body) as response:
                    assert response.status == status
            assert state["account_calls"] == [] and state["conversations"] == {}
    asyncio.run(run())


def test_account_history_rejects_other_accounts_and_path_injection(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            for path in [str(uuid.uuid4()), "not-a-uuid", "..%2Fauth%2Flogout"]:
                async with client.get(server.make_url("/web-api/v1/conversations/" + path + "/messages"), headers=headers(cookie)) as response:
                    assert response.status == 404
            async with client.get(server.make_url("/web-api/v1/conversations"), headers=headers()) as response:
                assert response.status == 401
    asyncio.run(run())
