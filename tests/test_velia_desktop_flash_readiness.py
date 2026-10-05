"""A waking worker must retain exact context checks and generate at most once."""
import asyncio
import time

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from velia_desktop_routes import check_flash_context, FlashContextTooLong, _wait_for_flash_worker


@pytest.mark.parametrize("initial_status,health_status,malformed,tokens,expected", [
    (200, 200, False, 100, None),
    (503, 200, False, 100, None),
    (502, 200, False, 100, None),
    (401, 200, False, 100, ValueError),
    (503, 403, False, 100, ValueError),
    (200, 200, True, 100, ValueError),
    (503, 200, False, 8000, FlashContextTooLong),
])
def test_worker_wake_preserves_authentication_template_and_context_bounds(
        monkeypatch, initial_status, health_status, malformed, tokens, expected):
    monkeypatch.setenv("VELIA_DESKTOP_FLASH_CONTEXT_TOKENS", "8192")
    async def run():
        seen = {"template": [], "health": 0, "tokenize": 0, "completion": 0}
        payload = {"messages": [{"role": "user", "content": "Keep app.py, 31 and not asthmatic verbatim."}],
                   "max_tokens": 512, "tools": [{"type": "function", "function": {
                       "name": "read", "parameters": {"type": "object"}}}], "tool_choice": "required"}

        async def template(request):
            assert request.headers["Authorization"] == "Bearer synthetic-fixture-key"
            seen["template"].append(await request.json())
            if len(seen["template"]) == 1 and initial_status != 200:
                return web.Response(status=initial_status)
            return web.json_response({"wrong": True} if malformed else {"prompt": "exact-rendered-prompt"})

        async def health(request):
            seen["health"] += 1
            assert request.headers["Authorization"] == "Bearer synthetic-fixture-key"
            return web.Response(status=health_status)

        async def tokenize(request):
            seen["tokenize"] += 1
            assert await request.json() == {"content": "exact-rendered-prompt", "add_special": True}
            return web.json_response({"tokens": [1] * tokens})

        async def completion(request):
            seen["completion"] += 1
            return web.Response(status=500)

        app = web.Application()
        app.router.add_post("/apply-template", template)
        app.router.add_post("/tokenize", tokenize)
        app.router.add_get("/health", health)
        app.router.add_post("/v1/chat/completions", completion)
        async with TestServer(app) as server, ClientSession() as client:
            call = check_flash_context(client, str(server.make_url("")).rstrip("/"),
                                       {"Authorization": "Bearer synthetic-fixture-key"}, payload)
            if expected:
                with pytest.raises(expected):
                    await call
            else:
                await call
        assert seen["completion"] == 0
        assert all(row == {"messages": payload["messages"], "chat_template_kwargs": {"enable_thinking": False},
                           "add_generation_prompt": True, "tools": payload["tools"],
                           "tool_choice": "required"} for row in seen["template"])
        cold = initial_status in {502, 503}
        assert seen["health"] == int(cold)
        assert len(seen["template"]) == (2 if cold and health_status == 200 else 1)
        if initial_status == 401 or malformed or health_status == 403:
            assert seen["tokenize"] == 0
        else:
            assert seen["tokenize"] == 1
    asyncio.run(run())


def test_worker_readiness_deadline_stops_unavailable_service():
    async def run():
        seen = []
        async def health(request):
            seen.append(request.path)
            return web.Response(status=503)
        app = web.Application()
        app.router.add_get("/health", health)
        async with TestServer(app) as server, ClientSession() as client:
            with pytest.raises(ValueError, match="flash_context_validation_failed"):
                await _wait_for_flash_worker(client, str(server.make_url("")).rstrip("/"),
                                             {}, time.monotonic() + 0.05)
        assert seen == ["/health"]
    asyncio.run(run())


def test_readiness_clock_expiry_cannot_disable_the_request_timeout(monkeypatch):
    from types import SimpleNamespace
    import velia_desktop_routes as routes
    ticks = iter([0, 2])
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    client = SimpleNamespace(get=lambda *args, **kwargs: pytest.fail("deadline already expired"))
    async def run():
        with pytest.raises(ValueError, match="flash_context_validation_failed"):
            await routes._wait_for_flash_worker(client, "http://127.0.0.1", {}, 1)
    asyncio.run(run())
