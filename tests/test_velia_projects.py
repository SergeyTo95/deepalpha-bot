import asyncio
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from services import velia_project_service as projects
from services import velia_deepalpha_research as research


@pytest.mark.parametrize("data", [None, {}, {"title": ""}, {"title": []}, {"title": "x", "owner_id": 2},
                                      {"title": "x", "goal": "x" * 1201}, {"title": "x\x00"}])
def test_passport_rejects_malformed_and_oversized_data(data):
    with pytest.raises(projects.ProjectError):
        projects.validate_passport(data)


@pytest.mark.parametrize("url", ["http://example.com", "https://user:secret@example.com", "https://127.0.0.1/a",
                                     "https://169.254.169.254", "https://private.local", "https://example.com:9000",
                                     "javascript:alert(1)", "https://x.com\nheader", "https://x.com\\evil"])
def test_rejects_unsafe_source_links(url):
    assert not research.safe_source_url(url)


def test_crypto_pair_is_explicit_and_unambiguous():
    assert research.explicit_pair("BTC/USDT через сутки") == "BTCUSDT"
    assert research.explicit_pair("биткоин завтра") == "BTCUSDT"
    assert research.explicit_pair("сравни ETH и BTC") is None
    assert research.explicit_pair("Apple prospects") is None


@pytest.mark.parametrize("price,age", [("nan", 0), ("-1", 0), ("100", 600), ("100", -300)])
def test_quote_rejects_bad_price_and_stale_clock(monkeypatch, price, age):
    monkeypatch.setattr(research, "_fetch", lambda *a, **k: json.dumps({"symbol": "BTCUSDT", "lastPrice": price,
        "priceChangePercent": "1", "closeTime": (time.time() - age) * 1000}).encode())
    with pytest.raises(ValueError):
        research._quote("BTCUSDT")


def test_provider_responses_are_bounded_and_closed(monkeypatch):
    closed = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): closed.append(True)
        def iter_content(self, size):
            yield b"x" * (research.MAX_HTTP_BYTES + 1)
    def get(*args, **kwargs):
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        return Response()
    monkeypatch.setattr(research.requests, "get", get)
    with pytest.raises(ValueError):
        research._fetch("https://api.binance.com")
    assert closed == [True]


def test_cache_is_tenant_scoped_bounded_and_returns_copies(monkeypatch):
    research._CACHE.clear()
    calls = []
    monkeypatch.setattr(research, "MAX_CACHE", 2)
    monkeypatch.setattr(research, "_collect", lambda uid, q: calls.append((uid, q)) or
                        {"status": "available", "sources": [{"title": "original"}]})
    first = research.collect_evidence(1, "query")
    first["sources"][0]["title"] = "mutated"
    assert research.collect_evidence(1, "query")["sources"][0]["title"] == "original"
    research.collect_evidence(2, "query")
    research.collect_evidence(3, "query")
    assert calls == [(1, "query"), (2, "query"), (3, "query")]
    assert len(research._CACHE) == 2


def test_concurrent_duplicate_research_has_one_provider_call(monkeypatch):
    research._CACHE.clear()
    calls, entered, finish = [], threading.Event(), threading.Event()
    def collect(uid, query):
        calls.append(query)
        entered.set()
        assert finish.wait(3)
        return {"status": "available", "sources": []}
    monkeypatch.setattr(research, "_collect", collect)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(research.collect_evidence, 1, "same")
        assert entered.wait(3)
        b = pool.submit(research.collect_evidence, 1, "same")
        finish.set()
        assert a.result()["status"] == b.result()["status"] == "available"
    assert calls == ["same"]


def test_missing_sources_do_not_invoke_model_or_action_router(monkeypatch):
    from services import velia_project_runtime as runtime, velia_live_plugins_patch as live
    monkeypatch.setattr(projects, "resource_context", lambda *a: {"kind": "deepalpha", "seed_query": "BTC", "revision": 2})
    monkeypatch.setattr(projects, "save_evidence", lambda *a: None)
    monkeypatch.setattr(research, "collect_evidence", lambda *a: {"status": "unavailable", "sources": []})
    monkeypatch.setattr(live, "_latest_user_message", lambda *a: "Купить биткоин?")
    monkeypatch.setenv("VELIA_LIVE_PLUGINS_ENABLED", "true")
    calls = []
    chat = SimpleNamespace(_build_prompt=lambda *a: "prompt",
        generate_velia_chat_result=lambda *a, **k: calls.append(True))
    runtime.install(chat)
    assert chat._build_prompt(1, "chat") == "prompt"
    assert runtime.IN_DEEPALPHA.get() is False
    result = chat.generate_velia_chat_result("prompt", user_id=1, conversation_id="chat", request_id="r")
    assert result["estimated_cost_usd"] == 0
    assert "источники" in result["text"]
    assert not calls


def test_research_bypasses_actions_and_keeps_sources_in_saved_answer(monkeypatch):
    from services import velia_project_runtime as runtime, velia_live_plugins_patch as live, velia_llm_service as llm
    evidence = {"status": "partial", "retrieved_at": "2026-09-15T10:00:00Z", "sources": [
        {"url": "https://example.com/report", "title": "Report"}]}
    monkeypatch.setattr(projects, "resource_context", lambda *a: {"kind": "deepalpha", "seed_query": "рынок", "revision": 3})
    monkeypatch.setattr(projects, "save_evidence", lambda *a: None)
    monkeypatch.setattr(research, "collect_evidence", lambda *a: evidence)
    monkeypatch.setattr(live, "_latest_user_message", lambda *a: "Исследуй компанию")
    monkeypatch.setenv("VELIA_LIVE_PLUGINS_ENABLED", "true")
    def model(prompt, **kwargs):
        assert "competing scenarios" in prompt and "headline" in prompt
        return {"ok": True, "text": "Результат"}
    monkeypatch.setattr(llm, "generate_velia_chat_result", model)
    chat = SimpleNamespace(_build_prompt=lambda *a: "prompt",
        generate_velia_chat_result=lambda *a, **k: pytest.fail("Action router must not run"))
    runtime.install(chat)
    result = chat.generate_velia_chat_result("prompt", user_id=1, conversation_id="chat", request_id="r")
    assert "https://example.com/report" in result["text"]
    assert "частичные" in result["text"]


def test_routes_authenticate_before_body_and_bound_chunked_json(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    import velia_project_routes as routes
    monkeypatch.setattr(routes, "_mobile_api_available", lambda: True)
    monkeypatch.setattr(projects, "ready", lambda: True)
    monkeypatch.setattr(routes, "_require_mobile_auth", lambda request: {"user_id": 1} if request.headers.get("Authorization") else None)
    async def run():
        app = web.Application()
        routes.setup_velia_project_routes(app)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/mobile-api/v1/projects", data=b"not json")
            assert r.status == 401
            async def oversized():
                yield b"x" * (routes.MAX_BODY + 1)
            r = await client.post("/mobile-api/v1/projects", data=oversized(), headers={"Authorization": "Bearer test"})
            assert r.status == 413
    asyncio.run(run())


@pytest.fixture
def postgres(monkeypatch):
    monkeypatch.setattr(projects, "_READY", False)
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration is required in CI")
    import psycopg2
    from services import velia_chat_service as chat
    schema = "project_test_" + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    def connect():
        return psycopg2.connect(url, options=f"-c search_path={schema}")
    monkeypatch.setattr(projects, "get_connection", connect)
    monkeypatch.setattr(chat, "get_connection", connect)
    chat.ensure_velia_chat_tables()
    projects.ensure_tables()
    try:
        yield connect
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_postgres_owner_isolation_and_immutable_revisions(postgres):
    project = projects.create_project(1, {"title": "Velia", "style": "violet"}, "create-project-1")
    assert projects.create_project(1, {"title": "Velia", "style": "violet"}, "create-project-1")["id"] == project["id"]
    with pytest.raises(projects.ProjectError, match="idempotency_conflict"):
        projects.create_project(1, {"title": "Other"}, "create-project-1")
    with pytest.raises(projects.ProjectError, match="project_not_found"):
        projects.update_project(2, project["id"], {"style": "red"}, 1)
    edited = projects.update_project(1, project["id"], {"style": "blue"}, 1)
    assert edited["revision"] == 2 and edited["passport"]["title"] == "Velia"
    with pytest.raises(projects.ProjectError, match="revision_conflict"):
        projects.update_project(1, project["id"], {"style": "red"}, 1)
    history = projects.project_detail(1, project["id"])["revisions"]
    assert [v["passport"]["style"] for v in history] == ["blue", "violet"]
    restored = projects.update_project(1, project["id"], history[1]["passport"], 2)
    assert restored["revision"] == 3
    assert projects.list_projects(2) == []


def test_postgres_resource_idempotency_scope_and_deleted_history(postgres):
    project = projects.create_project(1, {"title": "Markets"}, "create-project-2")
    data = {"kind": "deepalpha", "query": "BTC tomorrow", "project_id": project["id"]}
    with pytest.raises(projects.ProjectError, match="project_not_found"):
        projects.create_resource(2, data, "resource-request-1")
    resource = projects.create_resource(1, data, "resource-request-1")
    assert projects.create_resource(1, data, "resource-request-1")["id"] == resource["id"]
    assert projects.resource_context(2, resource["id"]) is None
    assert projects.list_resources(1, kind="deepalpha")["resources"][0]["id"] == resource["id"]
    with pytest.raises(projects.ProjectError, match="idempotency_conflict"):
        projects.create_resource(1, {**data, "query": "ETH"}, "resource-request-1")
    projects.save_evidence(1, resource["id"], "request", {"status": "partial"})
    with pytest.raises(projects.ProjectError, match="research_not_found"):
        projects.research_evidence(2, resource["id"])
    assert projects.research_evidence(1, resource["id"])[0]["status"] == "partial"
    with postgres() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE velia_conversations SET deleted_at=NOW() WHERE conversation_id=%s", (resource["id"],))
    assert projects.list_resources(1)["resources"] == []
    with pytest.raises(projects.ProjectError, match="research_not_found"):
        projects.research_evidence(1, resource["id"])


def test_postgres_concurrent_revision_edit_has_one_winner(postgres):
    project = projects.create_project(1, {"title": "Draft"}, "concurrent-project")
    def edit(style):
        try:
            return projects.update_project(1, project["id"], {"style": style}, 1)["revision"]
        except projects.ProjectError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(edit, ["blue", "red"]))
    assert sorted(map(str, results)) == ["2", "revision_conflict"]


def test_media_brief_preserves_request_and_prompt_budget(monkeypatch):
    monkeypatch.setattr(projects, "resource_context", lambda *a: {"passport": {
        "style": "violet", "constraints": "no vocals", "audience": "adults"}})
    result = projects.media_prompt(1, "studio", "Create a landscape")
    assert result.startswith("Create a landscape") and "violet" in result
    original = "x" * 3999
    assert projects.media_prompt(1, "studio", original) == original
