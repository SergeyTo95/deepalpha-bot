"""VELIA Guardian gateway security boundaries: no chat text or identity leakage."""
import asyncio
from types import SimpleNamespace

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from desktop.guardian_routes import setup_guardian_routes


def test_guardian_inbox_requires_owner_and_origin():
    async def scenario():
        seen = []

        async def session_for(request):
            who = request.headers.get("X-Test-User")
            return SimpleNamespace(access=f"owner-{who}", user_id=int(who)) if who in {"7", "8"} else None

        async def upstream(method, path, **kwargs):
            seen.append((method, path, kwargs))
            assert "/mobile-api/v1/guardian/" in path
            if method == "GET":
                assert "data" not in kwargs
                return 200, {"ok": True, "unread": 1, "events": [{
                    "id": "11111111-1111-4111-8111-111111111111",
                    "kind": "web_login", "severity": "info", "source": "telegram",
                    "detail": "VELIA Web", "created_at": "2026-11-15T09:00:00+00:00",
                    "read": False, "refresh_token": "must-not-leak",
                    "user_id": 999,
                }]}
            return 200, {"ok": True, "updated": 1, "access_token": "must-not-leak"}

        def same_origin(request):
            return (
                request.headers.get("Origin") == "https://velia.velyoncore.ai"
                and request.headers.get("X-Velia-Request") == "1"
                and request.content_type == "application/json"
            )

        app = web.Application()
        setup_guardian_routes(
            app, session_for=session_for, same_origin=same_origin,
            upstream=upstream, json_response=lambda d, status=200: web.json_response(d, status=status)
        )
        async with TestServer(app) as server, ClientSession() as client:
            url = str(server.make_url("/web-api/v1/features/guardian/events"))
            assert (await client.get(url)).status == 401
            assert (await client.post(url + "/read", json={})).status == 403
            assert not seen
            for user in ("7", "8"):
                headers = {"X-Test-User": user}
                async with client.get(url, headers=headers) as response:
                    assert response.status == 200
                    assert response.headers["Cache-Control"] == "no-store"
                    payload = await response.json()
                    assert payload["events"][0]["kind"] == "web_login"
                    assert "user_id" not in payload["events"][0]
                    assert "refresh_token" not in payload["events"][0]
                assert seen[-1] == (
                    "GET", "/mobile-api/v1/guardian/events", {"token": "owner-" + user}
                )

            owner = {
                "X-Test-User": "7", "Origin": "https://velia.velyoncore.ai",
                "X-Velia-Request": "1",
            }
            async with client.post(url + "/read", headers=owner, json={
                "event_id": "11111111-1111-4111-8111-111111111111"
            }) as response:
                assert response.status == 200
                assert (await response.json()) == {"ok": True, "updated": 1}
            assert seen[-1][2]["token"] == "owner-7"
            assert seen[-1][2]["data"] == {
                "event_id": "11111111-1111-4111-8111-111111111111"
            }
            for invalid in ({"user_id": 8}, {"event_id": "../../other"}, {"event_id": 33}):
                async with client.post(url + "/read", headers=owner, json=invalid) as response:
                    assert response.status == 400
            assert len(seen) == 3

    asyncio.run(scenario())


def test_guardian_upstream_failure_fails_closed():
    async def scenario():
        async def upstream(*_args, **_kwargs):
            return 503, {"ok": False, "detail": "database secret"}
        app = web.Application()
        setup_guardian_routes(
            app, session_for=lambda _: asyncio.sleep(0, result=SimpleNamespace(access="owner", user_id=7)),
            same_origin=lambda _: True, upstream=upstream,
            json_response=lambda d, status=200: web.json_response(d, status=status),
        )
        async with TestServer(app) as server, ClientSession() as client:
            response = await client.get(server.make_url("/web-api/v1/features/guardian/events"))
            assert response.status == 503
            assert await response.json() == {"ok": False, "error": "guardian_unavailable"}

    asyncio.run(scenario())
