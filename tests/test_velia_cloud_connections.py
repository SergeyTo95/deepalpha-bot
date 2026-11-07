import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from desktop.cloud_connections import CloudOAuth, PROVIDERS
from desktop.figma_connector import FigmaConnector, FigmaError
from desktop.figma_routes import setup_figma_routes


@pytest.mark.parametrize("provider", ["railway", "vercel", "notion"])
def test_cloud_oauth_resource_grant_tools_and_receipts(provider):
    async def scenario():
        calls = []
        token = "fixture-cloud-access-token"
        settings = PROVIDERS[provider]

        async def oauth_request(method, path, *, data=None):
            calls.append((path, data))
            if path == "/.well-known/oauth-authorization-server":
                return {"issuer": settings["issuer"], "authorization_endpoint": settings["authorize"],
                    "token_endpoint": settings["token"], "registration_endpoint": settings.get("register"),
                    "code_challenge_methods_supported": ["S256"], "client_id_metadata_document_supported": True}
            if path == "/oauth/register":
                assert provider in {"railway", "notion"}
                return {"client_id": "public-client", "redirect_uris": data["redirect_uris"], "token_endpoint_auth_method": "none"}
            assert path == "/token" and data["resource"] == settings["endpoint"]
            return {"access_token": token, "token_type": "Bearer", "expires_in": 3600}

        async def rpc(headers, payload):
            assert headers["Authorization"] == "Bearer " + token
            method = payload["method"]
            if method == "notifications/initialized":
                return {}, {}
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26"}
            elif method == "tools/list":
                result = {"tools": [{"name": "list_projects", "inputSchema": {"type": "object"}}]}
            elif method == "ping":
                result = {}
            else:
                calls.append(("tools/call", payload["params"]))
                result = {"structuredContent": {"projects": [{"id": "project-1", "name": "User project"}],
                    "access_token": token, "variables": [{"name": "SERVICE_API_KEY", "value": "private"}]},
                    "content": [{"type": "text", "text": "Action completed."}]}
            return {"jsonrpc": "2.0", "id": payload["id"], "result": result}, {}

        bridge = FigmaConnector(rpc, endpoint=settings["endpoint"], pair_required=False)
        oauth = CloudOAuth(bridge, "https://velia.example", provider, oauth_request)
        bridge.oauth = oauth
        owner = ("7", "session-7")
        result = await oauth.start(owner)
        params = parse_qs(urlsplit(result["authorization_url"]).query)
        assert params["resource"] == [settings["endpoint"]]
        if provider == "vercel":
            assert params["client_id"] == ["https://velia.example/.well-known/velia/vercel.json"]
            assert not any(path == "/oauth/register" for path, _ in calls)
        authorized = await oauth.complete(owner, params["state"][0], "code")
        assert (await bridge.verify(owner))["connected"]
        data = {"connection_id": authorized["connection_id"], "request_id": "request-1234", "run_id": "run-1234",
                "operation_id": "operation-1234", "name": "list_projects", "arguments": {}}
        with pytest.raises(FigmaError, match="figma_connect_required"):
            await bridge.call(("8", "session-8"), data)
        result = await bridge.call(owner, data)
        assert "project-1" in result["text"] and "private" not in result["text"] and token not in result["text"]
        assert await bridge.call(owner, data) == result
        assert sum(path == "tools/call" for path, _ in calls) == 1
        bridge.disconnect(owner)
        assert not bridge.connections and not oauth.pending
    asyncio.run(scenario())


def test_vercel_client_document_is_public_and_exactly_matches_callback(monkeypatch):
    from test_velia_web_chat import fixture

    async def scenario():
        async with fixture(monkeypatch) as (server, client, state):
            response = await client.get(server.make_url("/.well-known/velia/vercel.json"))
            assert response.status == 200 and "public" in response.headers["Cache-Control"]
            result = await response.json()
            assert result["client_name"] == "VELIA" and result["token_endpoint_auth_method"] == "none"
            assert result["client_id"].endswith("/.well-known/velia/vercel.json")
            assert result["redirect_uris"] == ["https://velia.example.com/web-api/v1/connections/vercel/callback"]
            assert not state["payloads"]
    asyncio.run(scenario())


def test_cloud_routes_do_not_share_sessions_and_require_origin():
    async def scenario():
        bridge = FigmaConnector(pair_required=False)
        app = web.Application()

        async def session_for(request):
            return SimpleNamespace(user_id=7, id="s") if request.headers.get("X-User") else None
        setup_figma_routes(app, connector=bridge, provider="vercel", prefix="/web-api/v1/connections/vercel",
            session_for=session_for, same_origin=lambda request: request.headers.get("Origin") == "https://velia.example",
            json_response=lambda data, status=200: web.json_response(data, status=status))
        async with TestServer(app) as server, ClientSession() as client:
            assert (await client.get(server.make_url("/web-api/v1/connections/vercel/status"))).status == 401
            assert (await client.post(server.make_url("/web-api/v1/connections/vercel/call"), json={})).status == 403
    asyncio.run(scenario())
