import asyncio
import json
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import velia_quantum_provider as quantum
from velia_desktop_routes import setup_velia_desktop_routes, validate_payload
from test_velia_quantum_provider import enabled, wire


@pytest.mark.parametrize("status,code", [(402, "quantum_access_required"), (429, "quantum_quota_exhausted"),
    (401, "quantum_unavailable"), (503, "quantum_provider_error"), (307, "quantum_provider_error")])
def test_gateway_quantum_route_uses_own_key_and_never_retries(enabled, monkeypatch, status, code):
    async def run():
        calls = []
        async def provider(request):
            calls.append((request.headers.get("Authorization"), await request.json()))
            return web.json_response({"error": "PRIVATE"}, status=status)
        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", provider)
        async with TestServer(upstream) as host:
            monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
            monkeypatch.setenv("VELIA_QUANTUM_MODEL", "future-owned-model")
            monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", str(host.make_url("/v1")))
            monkeypatch.setenv("VELIA_DESKTOP_API_ENABLED", "true")
            monkeypatch.setenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "7")
            monkeypatch.setenv("KIMI_BASE_URL", "http://127.0.0.1:1/must-not-be-contacted")
            app = web.Application()
            setup_velia_desktop_routes(app, lambda _: {"user_id": 7})
            async with TestServer(app) as server, ClientSession() as client:
                async with client.post(server.make_url("/desktop-api/v1/chat/completions"),
                        headers={"Authorization": "Bearer va_test"},
                        json={"model": quantum.MODEL_ID, "messages": [{"role": "user", "content": "hi"}]}) as response:
                    assert response.status != 200
                    assert (await response.json())["error"]["message"] == code
        assert len(calls) == 1 and calls[0][0] == "Bearer quantum-test-secret"
        assert calls[0][1]["model"] == "future-owned-model"
    asyncio.run(run())


def test_gateway_quantum_stream_and_tool_round_trip(enabled, monkeypatch):
    async def run():
        calls = []
        async def provider(request):
            data = await request.json()
            calls.append(data)
            return web.Response(body=wire(), content_type="text/event-stream")
        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", provider)
        async with TestServer(upstream) as host:
            monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
            monkeypatch.setenv("VELIA_QUANTUM_MODEL", "owned-model")
            monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", str(host.make_url("/v1")))
            monkeypatch.setenv("VELIA_DESKTOP_API_ENABLED", "true")
            monkeypatch.setenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "7")
            app = web.Application()
            setup_velia_desktop_routes(app, lambda _: {"user_id": 7})
            async with TestServer(app) as server, ClientSession() as client:
                data = {"model": quantum.MODEL_ID, "stream": True, "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "read", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "call_1", "content": "read result"}]}
                async with client.post(server.make_url("/desktop-api/v1/chat/completions"),
                        headers={"Authorization": "Bearer va_test"}, json=data) as response:
                    text = await response.text()
                    assert response.status == 200 and "Поняла" in text and "[DONE]" in text
                    assert "PRIVATE" not in text and "owned-model" not in text
            assert calls[0]["messages"][-1]["tool_call_id"] == "call_1"
    asyncio.run(run())
