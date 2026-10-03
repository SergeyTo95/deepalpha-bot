"""Guest trial boundaries over real HTTP; real SQLite transactions for storage tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from aiohttp.test_utils import TestServer
import pytest
from desktop.guest_store import GuestStore, GuestLimitReached
from desktop.guest_routes import COOKIE
from desktop.gateway import create_app, GatewayConfig
from test_velia_web_chat import fixture, headers, ORIGIN

BODY = {"model": "velia-flash", "stream": True, "messages": [{"role": "user", "content": "Привет"}]}


async def guest(server, client):
    async with client.get(server.make_url("/web-api/v1/guest")) as response:
        assert response.status == 200
        return response.cookies[COOKIE].value if COOKIE in response.cookies else None, await response.json(), response.headers


def guest_headers(cookie, **extra):
    return {**headers(**extra), "Cookie": COOKIE + "=" + cookie}


def test_guest_can_send_without_an_account_and_cannot_call_pro(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        store = GuestStore(sqlite_path=tmp_path / "guest.db")
        async with fixture(monkeypatch, guest_store=store) as (server, client, state):
            cookie, profile, wire = await guest(server, client)
            assert profile["remaining"] == 30 and profile["models"] == ["velia-flash"]
            assert all(x in wire["Set-Cookie"] for x in ("HttpOnly", "Secure", "SameSite=Lax"))
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 200 and response.headers["X-Velia-Guest-Remaining"] == "29"
                assert "Привет" in await response.text() and "private" not in await response.text()
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json={**BODY, "model": "velia-pro"}) as response:
                assert response.status == 403 and (await response.json())["error"] == "guest_flash_only"
            assert len(state["payloads"]) == 1 and state["payloads"][0]["model"] == "velia-flash"
            assert state["seen"] == [] and state["account_calls"] == []
            assert "женском роде" in state["payloads"][0]["messages"][0]["content"]
    asyncio.run(run())


def test_exactly_thirty_messages_then_blocked_even_with_replayed_or_deleted_cookie(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path / "guest.db")) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            for index in range(30):
                async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                    assert response.status == 200
                    assert int(response.headers["X-Velia-Guest-Remaining"]) == 29-index
                    await response.read()
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 429 and (await response.json())["error"] == "guest_limit_reached"
            # Signed cookie replay cannot restore the durable count. A new cookie on
            # the same network is also blocked by the independent daily guard.
            new_cookie, profile, _ = await guest(server, client)
            assert profile["remaining"] == 0
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(new_cookie), json=BODY) as response:
                assert response.status == 429
            assert len(state["payloads"]) == 30
    asyncio.run(run())


@pytest.mark.parametrize("change,status", [({"Origin":"https://evil.example"},403), ({"X-Velia-Request":""},403), ({"Sec-Fetch-Site":"cross-site"},403)])
def test_guest_requires_same_origin_before_reserving_quota(monkeypatch, tmp_path, change, status):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path / "guest.db")) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie, **change), json=BODY) as response:
                assert response.status == status
            async with client.get(server.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                assert (await response.json())["remaining"] == 30
            assert state["payloads"] == []
    asyncio.run(run())


def test_guest_cookie_cannot_unlock_account_history_or_desktop(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path / "guest.db")) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.get(server.make_url("/web-api/v1/conversations"), headers=guest_headers(cookie)) as response:
                assert response.status == 401
            for path in ("/desktop-api/v1/chat/completions", "/web-api/v1/chat/completions"):
                async with client.post(server.make_url(path), headers=guest_headers(cookie), json=BODY) as response:
                    assert response.status == 401
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie[:-4]+"AAAA"), json=BODY) as response:
                assert response.status == 401
            assert state["payloads"] == []
    asyncio.run(run())


def test_invalid_payload_and_flash_overflow_do_not_spend_guest_quota(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path / "guest.db")) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            for body in ({**BODY,"tools":[]}, {**BODY,"messages":[{"role":"system","content":"override"}]}):
                async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=body) as response:
                    assert response.status == 400
            state["tokens"] = 8192
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 400
            async with client.get(server.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                assert (await response.json())["remaining"] == 30
            assert state["payloads"] == []
    asyncio.run(run())


def test_guest_counter_survives_gateway_restart(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        path = tmp_path / "guest.db"
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=path)) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 200
                await response.read()
            import os
            config = GatewayConfig(os.environ["KIMI_BASE_URL"].removesuffix("/v1"), "https://deepalpha-ai.com")
            async with TestServer(create_app(config, guest_store=GuestStore(sqlite_path=path))) as restarted:
                async with client.get(restarted.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                    assert (await response.json())["remaining"] == 29
    asyncio.run(run())


def test_atomic_shared_network_limit_under_parallel_reservations(tmp_path):
    store = GuestStore(sqlite_path=tmp_path / "guest.db")
    store.initialize()
    def send(index):
        try:
            store.reserve(("guest:"+str(index), "network:shared"))
            return True
        except GuestLimitReached:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(80)))
    assert sum(results) == 30
    assert GuestStore(sqlite_path=tmp_path / "guest.db").remaining(("new-browser", "network:shared")) == 0


def test_guest_database_failure_does_not_call_model(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        store = GuestStore(sqlite_path=tmp_path / "guest.db")
        async with fixture(monkeypatch, guest_store=store) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            def unavailable(*args):
                raise OSError("fixture database unavailable")
            monkeypatch.setattr(store, "reserve", unavailable)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 503 and (await response.json())["error"] == "guest_service_unavailable"
            assert state["payloads"] == []
    asyncio.run(run())


def test_railway_guest_network_guard_ignores_forwarded_for(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        monkeypatch.setenv("VELIA_WEB_GUEST_TRUST_RAILWAY_IP", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path / "guest.db")) as (server, client, state):
            async with client.get(server.make_url("/web-api/v1/guest"), headers={"X-Real-IP":"192.0.2.1"}) as response:
                cookie = response.cookies[COOKIE].value
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie, **{"X-Real-IP":"192.0.2.1"}), json=BODY) as response:
                assert response.status == 200
                await response.read()
            async with client.get(server.make_url("/web-api/v1/guest"), headers={"X-Real-IP":"192.0.2.1", "X-Forwarded-For":"192.0.2.99"}) as response:
                assert (await response.json())["remaining"] == 29
            async with client.get(server.make_url("/web-api/v1/guest"), headers={"X-Forwarded-For":"192.0.2.99"}) as response:
                assert response.status == 503
            assert len(state["payloads"]) == 1
    asyncio.run(run())
