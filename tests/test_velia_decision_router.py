import asyncio
import copy
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from desktop import decision_router as router


def scores():
    return {"answers": {
        "route": {"type": "choice", "choice": "direct", "confidence": 0.99,
                  "probabilities": {"direct": 0.99, "search": 0.005, "understand": 0.005}},
        "needs_interpretation": {"type": "noul", "noul": 0.01}}}


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("VELIA_DECISION_MODE", "active")
    monkeypatch.setenv("VELIA_DECISION_API_KEY", "test-only-secret")
    monkeypatch.setattr(router, "_blocked_until", 0)
    monkeypatch.setattr(router, "_inflight", 0)


@pytest.mark.parametrize("mutation", [
    lambda a: a["route"].update(confidence=0.8),
    lambda a: a["route"].update(choice="search"),
    lambda a: a["route"]["probabilities"].update(direct=float("nan")),
    lambda a: a["route"]["probabilities"].update(search=True),
    lambda a: a["route"]["probabilities"].update(unknown=0),
    lambda a: a["route"]["probabilities"].update(search=0.3),
    lambda a: a["route"].pop("confidence"),
    lambda a: a["needs_interpretation"].update(noul=0.5),
    lambda a: a["needs_interpretation"].update(noul=True),
    lambda a: a.pop("needs_interpretation"),
])
def test_untrusted_scores_cannot_skip_flash(mutation):
    data = scores()
    assert router.direct_answer(data)
    mutation(data["answers"])
    assert not router.direct_answer(data)


@pytest.mark.parametrize("mode,code,expected", [
    ("active", 200, {"action": "direct", "query": "", "context": []}),
    ("shadow", 200, None), ("active", 401, None), ("active", 402, None),
    ("active", 429, None), ("active", 500, None),
])
def test_protocol_modes_and_provider_fallback(monkeypatch, caplog, mode, code, expected):
    async def run():
        monkeypatch.setenv("VELIA_DECISION_MODE", mode)
        calls = []
        async def handle(request):
            calls.append(await request.json())
            assert request.headers["Authorization"] == "Bearer test-only-secret"
            return web.json_response(scores(), status=code)
        app = web.Application(); app.router.add_post("/decisions", handle)
        async with TestServer(app) as server:
            messages = [{"role": "user", "content": "Сколько будет 2 + 2?"}]
            before = copy.deepcopy(messages)
            assert await router.try_direct(messages, endpoint=str(server.make_url("/decisions"))) == expected
            assert messages == before
            assert calls[0]["model"] == router.MODEL
            assert calls[0]["state"] == {"message": messages[0]["content"]}
            assert calls[0]["questions"]["route"]["type"] == "choice"
            if code != 200:
                assert await router.try_direct(messages, endpoint=str(server.make_url("/decisions"))) is None
                assert len(calls) == 1  # Circuit breaker, no retries.
        assert "test-only-secret" not in caplog.text
        assert messages[0]["content"] not in caplog.text
    asyncio.run(run())


def test_disabled_missing_key_and_history_make_no_network_call(monkeypatch):
    async def run():
        messages = [{"role": "user", "content": "2+2"}]
        monkeypatch.setenv("VELIA_DECISION_MODE", "disabled")
        assert await router.try_direct(messages, endpoint="invalid") is None
        monkeypatch.setenv("VELIA_DECISION_MODE", "active")
        monkeypatch.delenv("VELIA_DECISION_API_KEY")
        assert await router.try_direct(messages, endpoint="invalid") is None
        monkeypatch.setenv("VELIA_DECISION_API_KEY", "test-only-secret")
        assert await router.try_direct(messages + messages, endpoint="invalid") is None
        assert await router.try_direct([{"role": "user", "content": "x" * 2001}], endpoint="invalid") is None
    asyncio.run(run())


@pytest.mark.parametrize("body", ["not-json", "x" * 65537, json.dumps({"answers": {}})])
def test_bad_provider_body_falls_back(body):
    async def run():
        async def handle(request):
            return web.Response(text=body)
        app = web.Application(); app.router.add_post("/decisions", handle)
        async with TestServer(app) as server:
            assert await router.try_direct([{"role": "user", "content": "2+2"}],
                endpoint=str(server.make_url("/decisions"))) is None
    asyncio.run(run())


def test_end_to_end_timeout_keeps_flash_available():
    async def run():
        async def handle(request):
            await asyncio.sleep(1)
            return web.json_response(scores())
        app = web.Application(); app.router.add_post("/decisions", handle)
        async with TestServer(app) as server:
            started = asyncio.get_running_loop().time()
            assert await router.try_direct([{"role": "user", "content": "2+2"}],
                endpoint=str(server.make_url("/decisions"))) is None
            assert asyncio.get_running_loop().time() - started < 0.95
            assert router._inflight == 0
    asyncio.run(run())


def test_real_search_interpreter_handoff(monkeypatch):
    from desktop.web_search import WebSearch
    async def run():
        calls = []
        async def direct(messages):
            return {"action": "direct", "query": "", "context": []}
        async def flash(messages):
            calls.append(messages)
            return {"action": "search", "query": "current news"}
        monkeypatch.setenv("VELIA_WEB_SESSION_KEY", "fixture")
        monkeypatch.setattr(router, "try_direct", direct)
        monkeypatch.setattr("desktop.web_search.understand", flash)
        search = WebSearch()
        messages = [{"role": "user", "content": "2+2"}]
        assert (await search._understand(messages))["action"] == "direct"
        assert not calls
        async def unavailable(messages):
            return None
        monkeypatch.setattr(router, "try_direct", unavailable)
        assert (await search._understand(messages))["action"] == "search"
        assert calls == [messages]
    asyncio.run(run())
