import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from desktop.figma_connector import FigmaConnector, FigmaError
from desktop.figma_oauth import FigmaOAuth, ORIGIN
from desktop.figma_routes import setup_figma_routes
from test_velia_figma import TOKEN, TASK, remote

ACCESS = "oauth-fixture-access-token"


def fixture():
    calls = []
    rpc, tool_calls = remote()

    async def authenticated(headers, payload):
        assert headers["Authorization"] == "Bearer " + ACCESS
        return await rpc({**headers, "Authorization": "Bearer " + TOKEN}, payload)

    async def request(method, path, *, data=None):
        calls.append((method, path, data))
        if path == "/.well-known/oauth-authorization-server":
            return {"issuer": ORIGIN, "authorization_endpoint": ORIGIN + "/authorize",
                    "registration_endpoint": ORIGIN + "/oauth/register", "token_endpoint": ORIGIN + "/token",
                    "code_challenge_methods_supported": ["S256"], "scopes_supported": ["file_content:read"]}
        if path == "/oauth/register":
            assert data["client_name"] == "VELIA" and data["token_endpoint_auth_method"] == "none"
            return {"client_id": "fixture-client", "redirect_uris": data["redirect_uris"], "token_endpoint_auth_method": "none"}
        assert path == "/token"
        return {"access_token": ACCESS, "refresh_token": "fixture-refresh-never-exposed", "token_type": "Bearer", "expires_in": 3600}

    bridge = FigmaConnector(authenticated)
    oauth = FigmaOAuth(bridge, "https://velia.example", request)
    bridge.oauth = oauth
    return bridge, oauth, calls, tool_calls


def test_oauth_pkce_callback_can_pair_and_edit_without_a_personal_token():
    async def scenario():
        bridge, oauth, calls, tool_calls = fixture()
        owner = ("7", "session-a")
        started = await oauth.start(owner)
        query = parse_qs(urlsplit(started["authorization_url"]).query)
        assert query["code_challenge_method"] == ["S256"]
        with pytest.raises(FigmaError, match="figma_invalid_callback"):
            await oauth.complete(("8", "session-b"), query["state"][0], "code")
        with pytest.raises(FigmaError, match="figma_invalid_callback"):
            await oauth.complete(owner, "wrong-state", "code")
        paired = await oauth.complete(owner, query["state"][0], "code")
        exchanged = calls[-1][2]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(exchanged["code_verifier"].encode()).digest()).decode().rstrip("=")
        assert query["code_challenge"] == [challenge]
        assert exchanged["redirect_uri"] == "https://velia.example/web-api/v1/figma/callback"
        assert paired["pairing_code"] == "AB12CD"
        assert bridge.status(owner)["pairing_code"] == "AB12CD"
        assert (await bridge.verify(owner))["connected"] is True
        result = await bridge.call(owner, {**TASK, "connection_id": paired["connection_id"],
            "operation_id": "task-1234:1", "name": "figma_create_child", "arguments": {}})
        assert result["isError"] is False and any(call["name"] == "figma_create_child" for call in tool_calls)
        assert ACCESS not in json.dumps(paired) + json.dumps(bridge.status(owner)) + json.dumps(result)
        with pytest.raises(FigmaError, match="figma_invalid_callback"):
            await oauth.complete(owner, query["state"][0], "code")
        assert sum(path == "/token" for _, path, _ in calls) == 1
    asyncio.run(scenario())


def test_expired_and_cancelled_oauth_cannot_replace_a_connection():
    async def scenario():
        bridge, oauth, _, _ = fixture()
        owner = ("7", "s")
        await oauth.start(owner)
        state = oauth.pending[owner]["state"]
        oauth.pending[owner]["expires"] = 0
        with pytest.raises(FigmaError, match="figma_invalid_callback"):
            await oauth.complete(owner, state, "code")
        await oauth.start(owner)
        state = oauth.pending[owner]["state"]
        original = oauth.request
        exchanging, release = asyncio.Event(), asyncio.Event()

        async def delayed(method, path, *, data=None):
            if path == "/token":
                exchanging.set()
                await release.wait()
            return await original(method, path, data=data)
        oauth.request = delayed
        task = asyncio.create_task(oauth.complete(owner, state, "code"))
        await exchanging.wait()
        bridge.disconnect(owner)
        release.set()
        with pytest.raises(FigmaError, match="figma_authorization_cancelled"):
            await task
        assert not bridge.connections and not oauth.pending
    asyncio.run(scenario())


def test_oauth_callback_routes_keep_codes_and_tokens_off_the_return_page():
    async def scenario():
        bridge, oauth, calls, _ = fixture()
        app = web.Application()

        async def session_for(request):
            user = request.headers.get("X-User")
            return SimpleNamespace(user_id=user, id="session-" + user) if user else None
        setup_figma_routes(app, session_for=session_for, same_origin=lambda request: request.headers.get("Origin") == "https://velia.example",
            json_response=lambda data, status=200: web.json_response(data, status=status), connector=bridge, oauth=oauth)
        async with TestServer(app) as server, ClientSession() as client:
            start = server.make_url("/web-api/v1/figma/authorize")
            callback = server.make_url("/web-api/v1/figma/callback")
            headers = {"Origin": "https://velia.example", "X-User": "7"}
            assert (await client.post(start, json={})).status == 403
            assert (await client.post(start, json={}, headers={"Origin": headers["Origin"]})).status == 401
            response = await client.post(start, json={}, headers=headers)
            query = parse_qs(urlsplit((await response.json())["authorization_url"]).query)
            state = query["state"][0]
            wrong = await client.get(callback, params={"state": state, "code": "code"}, headers={"X-User": "8"}, allow_redirects=False)
            assert wrong.headers["Location"] == "/?figma=error"
            response = await client.get(callback, params={"state": state, "code": "code"}, headers=headers, allow_redirects=False)
            assert response.status == 303 and response.headers["Location"] == "/?figma=connected"
            assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
            assert ACCESS not in await response.text() and "code=" not in response.headers["Location"]
            response = await client.get(server.make_url("/web-api/v1/figma/status"), headers=headers)
            result = await response.json()
            assert result["pairing_code"] == "AB12CD" and result["oauth_available"] is True
            assert ACCESS not in json.dumps(result)
            replay = await client.get(callback, params={"state": state, "code": "code"}, headers=headers, allow_redirects=False)
            assert replay.headers["Location"] == "/?figma=error"
            assert sum(path == "/token" for _, path, _ in calls) == 1
            response = await client.post(start, json={}, headers=headers)
            query = parse_qs(urlsplit((await response.json())["authorization_url"]).query)
            denied = await client.get(callback, params={"state": query["state"][0], "error": "access_denied"}, headers=headers, allow_redirects=False)
            assert denied.headers["Location"] == "/?figma=error" and not oauth.pending
    asyncio.run(scenario())


def test_oauth_registration_cannot_redirect_network_requests_to_another_host():
    async def scenario():
        _, oauth, _, _ = fixture()

        async def hostile(method, path, *, data=None):
            assert path == "/.well-known/oauth-authorization-server"
            return {"issuer": ORIGIN, "token_endpoint": "https://attacker.example/token"}
        oauth.request = hostile
        with pytest.raises(FigmaError, match="figma_authorization_unavailable"):
            await oauth.start(("7", "s"))
        assert not oauth.pending
    asyncio.run(scenario())
