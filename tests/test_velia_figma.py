import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from desktop.figma_connector import CANVAS, FigmaConnector, FigmaError
from desktop.figma_routes import setup_figma_routes

TOKEN = "figd_" + "fixture-secret-" * 3
TASK = {"request_id": "task-1234", "run_id": "run-1234"}
TOOLS = [{"name": name, "inputSchema": {"type": "object"}}
         for name in ("figma_pair_plugin", "figma_execute", "figma_create_child")]


def remote():
    calls = []

    async def request(headers, payload):
        assert headers["Authorization"] == "Bearer " + TOKEN
        method = payload["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26"}
        elif method == "notifications/initialized":
            return {}, {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        else:
            params = payload["params"]
            calls.append(params)
            if params["name"] == "figma_pair_plugin":
                result = {"content": [{"type": "text", "text": '{"pairingCode":"AB12CD"}'}]}
            elif params["arguments"].get("code") == CANVAS:
                result = {"content": [{"type": "text", "text": '{"success":true,"result":{"fileName":"Fixture design"}}'}]}
            else:
                result = {"content": [{"type": "text", "text": json.dumps({"id": "1:2", "secret": TOKEN})}]}
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}, {}
    return request, calls


def test_figma_isolation_and_duplicate_write_receipts():
    async def scenario():
        request, calls = remote()
        bridge = FigmaConnector(request)
        owner, other = ("7", "session-a"), ("8", "session-b")
        pairing = await bridge.connect(owner, TOKEN)
        assert pairing["pairing_code"] == "AB12CD"
        assert bridge.status(owner)["connected"] is False
        assert bridge.status(other) == {"connected": False, "status": "disconnected"}
        assert (await bridge.verify(owner))["file_name"] == "Fixture design"
        data = {**TASK, "connection_id": pairing["connection_id"], "operation_id": "task-1234:0",
                "name": "figma_create_child", "arguments": {"nodeType": "FRAME", "parentId": "1:1"}}
        with pytest.raises(FigmaError, match="figma_connect_required"):
            await bridge.call(other, data)
        first = await bridge.call(owner, data)
        assert TOKEN not in json.dumps(first)
        assert await bridge.call(owner, data) == first
        assert sum(call["name"] == "figma_create_child" for call in calls) == 1
        with pytest.raises(FigmaError, match="figma_operation_conflict"):
            await bridge.call(owner, {**data, "arguments": {"nodeType": "TEXT"}})
        with pytest.raises(FigmaError, match="figma_task_already_attempted"):
            await bridge.call(owner, {**data, "run_id": "new-run-1234", "operation_id": "task-1234:1"})
        bridge.disconnect(owner)
        with pytest.raises(FigmaError, match="figma_connect_required"):
            await bridge.call(owner, data)
    asyncio.run(scenario())


def test_figma_uncertain_write_is_never_retried():
    async def scenario():
        request, calls = remote()
        bridge = FigmaConnector(request)
        owner = ("7", "s")
        pair = await bridge.connect(owner, TOKEN)
        await bridge.verify(owner)
        attempted = []

        async def failed(headers, payload):
            if payload.get("params", {}).get("arguments", {}).get("code") == CANVAS:
                return await request(headers, payload)
            attempted.append(payload)
            raise FigmaError("figma_unavailable", 503)
        bridge.request = failed
        data = {**TASK, "connection_id": pair["connection_id"], "operation_id": "task-1234:0",
                "name": "figma_create_child", "arguments": {}}
        for _ in range(2):
            with pytest.raises(FigmaError, match="figma_execution_uncertain"):
                await bridge.call(owner, data)
        assert len(attempted) == 1
    asyncio.run(scenario())


def test_figma_routes_require_session_origin_and_matching_connection():
    async def scenario():
        request, _ = remote()
        bridge = FigmaConnector(request)
        app = web.Application()

        async def session_for(request):
            owner = request.headers.get("X-Fixture-User")
            return SimpleNamespace(user_id=owner, id="session-" + owner) if owner else None
        setup_figma_routes(app, session_for=session_for,
            same_origin=lambda request: request.headers.get("Origin") == "https://velia.example" and request.headers.get("X-Velia-Request") == "1",
            json_response=lambda data, status=200: web.json_response(data, status=status), connector=bridge)
        headers = {"Origin": "https://velia.example", "X-Velia-Request": "1", "X-Fixture-User": "7"}
        async with TestServer(app) as server, ClientSession() as client:
            async def post(action, data, h=headers):
                return await client.post(server.make_url("/web-api/v1/figma/" + action), json=data, headers=h)
            assert (await client.get(server.make_url("/web-api/v1/figma/status"))).status == 401
            assert (await post("connect", {"token": TOKEN}, {})).status == 403
            assert (await post("connect", {"token": "invalid"})).status == 400
            connected = await (await post("connect", {"token": TOKEN})).json()
            assert connected["pairing_code"] == "AB12CD" and TOKEN not in json.dumps(connected)
            assert (await post("verify", {})).status == 200
            data = {**TASK, "connection_id": connected["connection_id"], "operation_id": "task-1234:1", "name": "figma_create_child", "arguments": {}}
            assert (await post("call", data, {**headers, "X-Fixture-User": "8"})).status == 409
            assert (await post("call", {**data, "name": "figma_pair_plugin"})).status == 400
            assert (await post("call", data)).status == 200
            assert (await post("disconnect", {})).status == 200
        assert not bridge.connections
    asyncio.run(scenario())


def test_figma_refuses_a_silently_changed_file():
    async def scenario():
        request, calls = remote()
        bridge = FigmaConnector(request)
        owner = ("7", "s")
        pair = await bridge.connect(owner, TOKEN)
        await bridge.verify(owner)

        async def changed(headers, payload):
            result, received = await request(headers, payload)
            if payload.get("params", {}).get("arguments", {}).get("code") == CANVAS:
                result["result"]["content"][0]["text"] = '{"success":true,"result":{"fileName":"Other file"}}'
            return result, received
        bridge.request = changed
        with pytest.raises(FigmaError, match="figma_file_changed"):
            await bridge.call(owner, {**TASK, "connection_id": pair["connection_id"], "operation_id": "task-1234:0",
                "name": "figma_create_child", "arguments": {}})
        assert not any(call["name"] == "figma_create_child" for call in calls)
    asyncio.run(scenario())


def test_figma_planning_uses_normal_model_authorization_and_logout_clears_token(monkeypatch, tmp_path):
    from test_velia_web_chat import fixture, headers, login
    from desktop.guest_store import GuestStore

    async def scenario():
        request, _ = remote()
        bridge = FigmaConnector(request)
        monkeypatch.setattr("desktop.figma_routes.FigmaConnector", lambda: bridge)
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path / "quota.db")) as (server, client, state):
            cookie, _, _ = await login(server, client)
            body = {"model": "velia-flash", "messages": [{"role": "user", "content": 'Верни JSON {"answer":"Готово"}'}], "stream": True, "web_search": False}

            async def post(action, data):
                return await client.post(server.make_url("/web-api/v1/figma/" + action), headers=headers(cookie), json=data)
            assert (await post("plan", body)).status == 409
            assert (await post("connect", {"token": TOKEN})).status == 200
            assert (await post("verify", {})).status == 200
            response = await post("plan", body)
            assert response.status == 200 and "[DONE]" in await response.text()
            assert state["payloads"] and not state["search_queries"] and not state["intent_payloads"]
            assert TOKEN not in json.dumps(state["payloads"])
            response = await client.post(server.make_url("/web-api/v1/auth/logout"), headers=headers(cookie), json={})
            assert response.status == 200 and not bridge.connections
    asyncio.run(scenario())
