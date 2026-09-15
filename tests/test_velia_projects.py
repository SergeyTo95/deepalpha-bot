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


@pytest.mark.parametrize("method", ["get", "post"])
def test_provider_responses_are_bounded_and_closed(monkeypatch, method):
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
    monkeypatch.setattr(research.requests, method, get)
    with pytest.raises(ValueError):
        research._fetch("https://api.binance.com", json_body={} if method == "post" else None)
    assert closed == [True]


@pytest.mark.parametrize("provider,payload", [
    ("tavily", {"results": [{"title": "Report", "content": "Evidence", "url": "https://example.com/report"}] * 6}),
    ("serper", {"organic": [{"title": "Report", "snippet": "Evidence", "link": "https://example.com/report"}] * 6}),
    ("bing", {"webPages": {"value": [{"name": "Report", "snippet": "Evidence", "url": "https://example.com/report"}] * 6}}),
])
def test_research_reuses_configured_search_once_with_bounded_results(monkeypatch, provider, payload):
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", provider)
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "unused-key")
    monkeypatch.setenv("WEB_SEARCH_MAX_RESULTS", "2")
    monkeypatch.setenv("WEB_SEARCH_TIMEOUT", "9999")
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return json.dumps(payload).encode()
    monkeypatch.setattr(research, "_fetch", fetch)
    rows = research._search("Question")
    assert len(calls) == 1 and len(rows) == 2
    assert rows[0]["title"] == "Report" and rows[0]["excerpt"] == "Evidence"
    assert rows[0]["url"] == "https://example.com/report" and rows[0]["coverage"] == "search_excerpt"
    assert calls[0][1]["timeout"] <= 15
    if provider == "tavily":
        request = calls[0][1]["json_body"]
        assert request["search_depth"] == "basic"
        assert request["auto_parameters"] is False and request["include_raw_content"] is False


def test_failed_configured_search_does_not_start_fallback(monkeypatch):
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "unused-key")
    monkeypatch.setattr(research.plugins, "_reserve_plugin_call", lambda *a: True)
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        raise ValueError("unavailable")
    monkeypatch.setattr(research, "_fetch", fetch)
    result = research._collect(1, "Company outlook")
    assert len(calls) == 1
    assert result["status"] == "unavailable" and result["gaps"] == ["search_unavailable"]


@pytest.mark.parametrize("setting,value", [("LIVE_WEB_RESEARCH_ENABLED", "false"), ("WEB_SEARCH_PROVIDER", "disabled")])
def test_search_disable_settings_stop_all_search_providers(monkeypatch, setting, value):
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    monkeypatch.setenv(setting, value)
    monkeypatch.setattr(research, "_fetch", lambda *a, **k: pytest.fail("Search is disabled"))
    assert research._search("Company outlook") == []


def test_search_configuration_change_invalidates_evidence_cache(monkeypatch):
    research._CACHE.clear()
    calls = []
    monkeypatch.setattr(research, "_collect", lambda *a: calls.append(True) or {"status": "available"})
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "true")
    research.collect_evidence(1, "question")
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "false")
    research.collect_evidence(1, "question")
    assert len(calls) == 2


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
    from services import velia_project_runtime
    monkeypatch.setattr(projects, "ensure_tables", lambda: None)
    monkeypatch.setattr(velia_project_runtime, "install", lambda chat: None)
    monkeypatch.setattr(routes, "_mobile_api_available", lambda: True)
    monkeypatch.setattr(projects, "ready", lambda: True)
    monkeypatch.setattr(routes, "_require_mobile_auth", lambda request: {"user_id": 1} if request.headers.get("Authorization") else None)
    async def run():
        app = web.Application()
        routes.setup_velia_project_routes(app)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/mobile-api/v1/projects", data=b"not json")
            assert r.status == 401
            r = await client.post("/mobile-api/v1/projects", data=b"not json", headers={
                "Authorization": "Bearer test", "X-Velia-Account": "2"})
            assert r.status == 409 and (await r.json())["error"] == "account_changed"
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


def test_polymarket_selects_exact_child_and_preserves_closed_state(monkeypatch):
    target = research.polymarket_target("Разбери https://polymarket.com/ru/event/election/candidate-a")
    assert target == ("event", "election", "candidate-a")
    market = {"slug": "candidate-a", "question": "Will A win?", "outcomes": '["Yes","No"]',
              "outcomePrices": '["0.2","0.8"]', "active": True, "closed": True}
    monkeypatch.setattr(research, "_fetch", lambda *a, **k: json.dumps([{"slug": "election", "markets": [
        {**market, "slug": "candidate-b", "question": "Wrong candidate"}, market]}]).encode())
    result = research._prediction_market(target)
    assert len(result["markets"]) == 1
    assert result["markets"][0]["question"] == "Will A win?"
    assert result["markets"][0]["closed"] is True
    assert result["markets"][0]["outcome_prices"][0]["price"] == 0.2


def test_polymarket_rejects_lookalike_and_unmatched_market(monkeypatch):
    assert research.polymarket_target("https://polymarket.com.evil.test/event/secret") is None
    assert research.polymarket_target("https://user:password@polymarket.com/event/secret") is None
    monkeypatch.setattr(research, "_fetch", lambda *a, **k: b'[{"slug":"other"}]')
    with pytest.raises(ValueError, match="market_not_found"):
        research._prediction_market(("event", "asked-for", None))


def test_postgres_research_can_be_attached_without_regenerating(postgres):
    project = projects.create_project(1, {"title": "Research"}, "research-project")
    resource = projects.create_resource(1, {"kind": "deepalpha", "query": "BTC"}, "independent-research")
    projects.save_evidence(1, resource["id"], "initial", {"project_id": None, "status": "partial"})
    with pytest.raises(projects.ProjectError, match="project_not_found"):
        projects.assign_resource(2, resource["id"], project["id"], None)
    linked = projects.assign_resource(1, resource["id"], project["id"], None)
    assert linked["project_id"] == project["id"]
    assert projects.research_evidence(1, resource["id"])[0]["project_id"] is None
    with pytest.raises(projects.ProjectError, match="resource_conflict"):
        projects.assign_resource(1, resource["id"], None, None)


def test_postgres_research_sender_persists_one_answer_for_replayed_request(postgres, monkeypatch):
    from services import velia_chat_service as chat, velia_project_runtime as runtime
    from services import velia_live_plugins_patch as live, velia_llm_service as llm
    from services.velia_mobile_hardening_service import build_hardened_send_message
    monkeypatch.setenv("VELIA_CHAT_ENABLED", "true")
    monkeypatch.setenv("VELIA_LIVE_PLUGINS_ENABLED", "true")
    monkeypatch.delenv("VELIA_CHAT_BETA_USER_IDS", raising=False)
    monkeypatch.setattr(live, "get_connection", postgres)
    monkeypatch.setattr(chat, "_budget_error", lambda uid: None)
    # Record originals so the real sender's global hooks are restored afterwards.
    monkeypatch.setattr(chat, "_build_prompt", chat._build_prompt)
    monkeypatch.setattr(chat, "generate_velia_chat_result", chat.generate_velia_chat_result)
    monkeypatch.setattr(chat, "_velia_projects_installed", False, raising=False)
    calls = {"search": 0, "model": 0}
    def collect(uid, query):
        calls["search"] += 1
        return {"status": "available", "query": query, "retrieved_at": "2026-09-15T10:00:00Z",
                "sources": [{"title": "Evidence", "url": "https://example.com/report"}]}
    def model(prompt, **kwargs):
        calls["model"] += 1
        assert "violet" in prompt
        assert "DEEPALPHA RESEARCH MODE" in prompt
        return {"ok": True, "text": "Evidence-based answer", "estimated_cost_usd": 0.01,
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}
    monkeypatch.setattr(research, "collect_evidence", collect)
    monkeypatch.setattr(llm, "generate_velia_chat_result", model)
    runtime.install(chat)
    project = projects.create_project(1, {"title": "Markets", "style": "violet"}, "sender-project")
    resource = projects.create_resource(1, {"kind": "deepalpha", "query": "BTC tomorrow", "project_id": project["id"]}, "sender-resource")
    send = build_hardened_send_message(chat, chat.send_message)
    first = send(1, resource["id"], "BTC tomorrow", idempotency_key="research-initial-request")
    replay = send(1, resource["id"], "BTC tomorrow", idempotency_key="research-initial-request")
    assert first["ok"] and replay["ok"] and replay["duplicate"]
    assert calls == {"search": 1, "model": 1}
    messages = chat.list_messages(1, resource["id"])
    assert len(messages) == 2
    assert "https://example.com/report" in messages[-1]["content"]
    assert len(projects.research_evidence(1, resource["id"])) == 1
    assert chat.list_messages(2, resource["id"]) is None
