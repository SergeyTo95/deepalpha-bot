import asyncio
from contextlib import asynccontextmanager
import json

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from velia_desktop_routes import flash_endpoint, setup_velia_desktop_routes, validate_payload
from velia_request_understanding import REQUEST_UNDERSTANDING


def test_plain_text_and_tools_keep_separate_qualified_sampling_profiles():
    plain = {"model":"velia-flash", "messages":[{"role":"user", "content":"Объясни выручку"}]}
    payload = validate_payload(plain)
    assert payload["min_p"] == 0.0 and payload["presence_penalty"] == 1.5
    tool = {"type":"function", "function":{"name":"read", "parameters":{"type":"object"}}}
    payload = validate_payload({**plain, "tools":[tool]})
    assert payload["min_p"] == 0.05 and "presence_penalty" not in payload


@asynccontextmanager
async def fixture(monkeypatch, *, enabled=True, input_tokens=300, model_status=200, redirect=None):
    state = {"kimi": 0, "flash": 0, "templates": []}
    async def template(request):
        assert request.headers["Authorization"] == "Bearer flash-key"
        state["templates"].append(await request.json())
        if redirect:
            raise web.HTTPTemporaryRedirect(location=redirect)
        return web.json_response({"prompt": "rendered-with-tools"})
    async def tokenize(request):
        assert (await request.json())["content"] == "rendered-with-tools"
        return web.json_response({"tokens": [1] * input_tokens})
    async def flash(request):
        state["flash"] += 1
        assert request.headers["Authorization"] == "Bearer flash-key"
        payload = await request.json()
        state["payload"] = payload
        if model_status != 200:
            return web.json_response({"error": "private-provider-error"}, status=model_status)
        assert payload["model"] == "velia-flash" and payload["max_tokens"] <= 512
        assert "max_completion_tokens" not in payload
        assert payload["parallel_tool_calls"] is False
        if payload["stream"]:
            return web.Response(text='data: {"choices":[{"delta":{"content":"Прочитала файл"}}]}\n\ndata: [DONE]\n\n',
                                content_type="text/event-stream")
        return web.json_response({"model": "private-name", "choices": [{"message": {"content": "Прочитала файл"}}]})
    async def kimi(request):
        state["kimi"] += 1
        return web.json_response({"choices": []})
    source = web.Application()
    source.router.add_post("/apply-template", template)
    source.router.add_post("/tokenize", tokenize)
    source.router.add_post("/v1/chat/completions", flash)
    source.router.add_post("/kimi/chat/completions", kimi)
    async with TestServer(source) as worker:
        monkeypatch.setenv("VELIA_DESKTOP_API_ENABLED", "true")
        monkeypatch.setenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "7")
        monkeypatch.setenv("VELIA_DESKTOP_FLASH_ENABLED", "true" if enabled else "false")
        monkeypatch.setenv("VELIA_DESKTOP_FLASH_BASE_URL", str(worker.make_url("/")).rstrip("/"))
        monkeypatch.setenv("VELIA_DESKTOP_FLASH_API_KEY", "flash-key")
        monkeypatch.setenv("KIMI_BASE_URL", str(worker.make_url("/kimi")))
        monkeypatch.setenv("KIMI_API_KEY", "paid-key")
        app = web.Application()
        setup_velia_desktop_routes(app, lambda token: {"user_id": 7} if token == "va_test" else None)
        async with TestServer(app) as gateway, ClientSession(headers={"Authorization": "Bearer va_test"}) as client:
            yield gateway, client, state


def test_flash_normalizes_template_and_preserves_correlated_tool_result():
    result = validate_payload({"model": "velia-flash", "max_tokens": 4096, "messages": [
        {"role": "system", "content": "Ты Велия"}, {"role": "developer", "content": "Рабочая папка"},
        {"role": "user", "content": "Прочитай файл"},
        {"role": "assistant", "content": None, "reasoning_content": "old reasoning", "tool_calls": [
            {"id": "read-1", "type": "function", "function": {"name": "read", "arguments": ""}}]},
        {"role": "tool", "tool_call_id": "read-1", "content": "текст файла"},
        {"role": "system", "content": "Без изменений файлов"},
    ]})
    assert result["messages"][0] == {"role": "system", "content":
        "Ты Велия\n\n" + REQUEST_UNDERSTANDING + "\n\nРабочая папка\n\nБез изменений файлов"}
    assert len([m for m in result["messages"] if m["role"] == "system"]) == 1
    assert "reasoning_content" not in result["messages"][2]
    assert result["messages"][2]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert result["messages"][3]["tool_call_id"] == "read-1"
    assert result["max_tokens"] == 512


@pytest.mark.parametrize("arguments", ["{bad", "[]", "true", 7, [1]])
def test_malformed_tool_arguments_are_rejected_before_flash(arguments):
    with pytest.raises(ValueError, match="invalid_tool_arguments"):
        validate_payload({"model": "velia-flash", "messages": [{"role": "assistant", "content": None,
            "tool_calls": [{"type": "function", "id": "read-1", "function": {"name": "read", "arguments": arguments}}]}]})


@pytest.mark.parametrize("url", ["http://public.example:8080", "https://user:key@host", "https://host/path", "https://host?key=x"])
def test_flash_rejects_unsafe_worker_address(monkeypatch, url):
    monkeypatch.setenv("VELIA_DESKTOP_FLASH_BASE_URL", url)
    assert flash_endpoint() == ""


@pytest.mark.parametrize("enabled", [True, False])
def test_flash_catalog_and_requests_require_explicit_enabled_configuration(monkeypatch, enabled):
    async def run():
        async with fixture(monkeypatch, enabled=enabled) as (gateway, client, state):
            async with client.get(gateway.make_url("/desktop-api/v1/models")) as response:
                ids = [m["id"] for m in (await response.json())["data"]]
                assert ("velia-flash" in ids) == enabled
            async with client.post(gateway.make_url("/desktop-api/v1/chat/completions"), json={
                "model": "velia-flash", "messages": [{"role": "user", "content": "hi"}]}) as response:
                assert response.status == (200 if enabled else 503)
                if enabled:
                    assert (await response.json())["model"] == "velia-flash"
            assert state["flash"] == int(enabled) and state["kimi"] == 0
    asyncio.run(run())


def test_flash_stream_forwards_tool_results_and_includes_tools_in_context_count(monkeypatch):
    async def run():
        async with fixture(monkeypatch) as (gateway, client, state):
            tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
            async with client.post(gateway.make_url("/desktop-api/v1/chat/completions"), json={
                "model": "velia-flash", "stream": True, "tools": tools, "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "r1", "type": "function",
                        "function": {"name": "read", "arguments": '{"file_path":"probe.txt"}'}}]},
                    {"role": "tool", "tool_call_id": "r1", "content": "local file text"}]}) as response:
                assert response.status == 200
                assert "Прочитала" in await response.text()
            assert state["templates"][0]["tools"] == tools
            assert state["payload"]["messages"][-1]["tool_call_id"] == "r1"
            assert state["kimi"] == 0
    asyncio.run(run())


def test_flash_context_overflow_never_generates_or_falls_back_to_pro(monkeypatch):
    async def run():
        async with fixture(monkeypatch, input_tokens=8000) as (gateway, client, state):
            async with client.post(gateway.make_url("/desktop-api/v1/chat/completions"), json={
                "model": "velia-flash", "messages": [{"role": "user", "content": "hi"}]}) as response:
                assert response.status == 400
                assert (await response.json())["error"]["message"] == "flash_context_too_long"
            assert state["flash"] == 0 and state["kimi"] == 0
    asyncio.run(run())


def test_flash_error_is_sanitized_with_no_paid_fallback(monkeypatch):
    async def run():
        async with fixture(monkeypatch, model_status=503) as (gateway, client, state):
            async with client.post(gateway.make_url("/desktop-api/v1/chat/completions"), json={
                "model": "velia-flash", "messages": [{"role": "user", "content": "hi"}]}) as response:
                assert response.status == 502
                assert "private-provider-error" not in await response.text()
            assert state["flash"] == 1 and state["kimi"] == 0
    asyncio.run(run())


def test_flash_context_redirect_never_receives_service_key(monkeypatch):
    async def run():
        hits = []
        async def target(request):
            hits.append(request.headers.get("Authorization"))
            return web.json_response({"prompt": "unsafe"})
        app = web.Application()
        app.router.add_post("/target", target)
        async with TestServer(app) as destination:
            async with fixture(monkeypatch, redirect=str(destination.make_url("/target"))) as (gateway, client, state):
                async with client.post(gateway.make_url("/desktop-api/v1/chat/completions"), json={
                    "model": "velia-flash", "messages": [{"role": "user", "content": "hi"}]}) as response:
                    assert response.status == 502
                assert hits == [] and state["kimi"] == 0 and state["flash"] == 0
    asyncio.run(run())
