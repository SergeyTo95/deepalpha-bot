"""Project -> saved Flash stages -> resumed progress, with no live provider calls."""
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from desktop import project_workflow as flow
from desktop.account_routes import setup_account_routes

PROJECT = "11111111-1111-4111-8111-111111111111"
RESOURCE = "22222222-2222-4222-8222-222222222222"
URL = "https://example.com/product"


def context(completed=()):
    return {"project_id": PROJECT, "resource_id": RESOURCE, "url": URL,
            "goal": "Private project goal", "completed": list(completed)}


@pytest.mark.parametrize("url", ["https://127.0.0.1", "https://[::1]", "https://169.254.169.254",
    "https://127.1", "https://0x7f000001", "http://example.com", "https://user:secret@example.com",
    "https://private.local", "https://x.railway.internal", "https://example.com:9000", "https://example.com\\evil",
    "https://example.com\nheader", "javascript:alert(1)"])
def test_target_urls_reject_credentials_private_hosts_and_non_https(url):
    with pytest.raises(flow.WorkflowError):
        flow.workflow_query(url, "Goal")


def test_workflow_metadata_is_bounded_and_versioned():
    query = flow.workflow_query(URL, "A useful service")
    assert flow.parse_workflow(query)["goal"] == "A useful service"
    for data in ("plain chat", flow.PREFIX + "[]", flow.PREFIX + '{"version":"bad"}'):
        with pytest.raises(flow.WorkflowError):
            flow.parse_workflow(data)
    with pytest.raises(flow.WorkflowError):
        flow.workflow_query(URL, "x" * 801)


def test_ru_en_tr_reviews_preserve_quotes_links_and_unknown_languages():
    original = ["Приложение медленное, вылетает. Нужен экспорт.", "Slow and crashes. Please add export.",
                "Uygulama yavaş ve hata var.", "请增加离线功能"]
    report = flow.review_report("\n".join(f"https://reviews.example/{i} | {text}" for i, text in enumerate(original))
        + "\nhttps://reviews.example/duplicate | " + original[0] + "\nMissing a source")
    assert report["sample_size"] == 4 and report["rejected_or_duplicate"] == 2
    assert [r["quote"] for r in report["reviews"]] == original
    assert report["reviews"][0]["themes"] == ["performance", "bugs", "data"]
    assert report["reviews"][1]["themes"] == ["performance", "bugs", "data"]
    assert report["reviews"][2]["themes"] == ["performance", "bugs"]
    assert report["unclassified"] == 1 and report["independently_verified"] is False
    assert all(theme["thin"] for theme in report["themes"])


@pytest.mark.parametrize("state", ["failed", "partial", "not_run"])
def test_high_average_does_not_hide_missing_mandatory_cases(state):
    lines = [f"Login | must | {state} | https://tests.example/login"]
    lines += [f"Optional {i} | should | passed | https://tests.example/{i}" for i in range(20)]
    result = flow.readiness_report("\n".join(lines))
    assert result["score"] > 80
    assert result["blockers"] == ["Login"]
    assert result["ready_for_review"] is False and result["release_authorized"] is False


def test_evidence_mandatory_scope_and_open_critical_bugs_are_required():
    assert flow.readiness_report("Login | must | passed | -")["blockers"] == ["Login"]
    assert flow.readiness_report("Color | could | passed | https://tests.example/a")["blockers"] == ["mandatory_scope_missing"]
    result = flow.readiness_report("Login | must | passed | https://tests.example/login\nData leak | S1 | open | https://tests.example/bug")
    assert result["blockers"] == ["Data leak"] and not result["ready_for_review"]
    clean = flow.readiness_report("Login | must | passed | https://tests.example/login")
    assert clean["ready_for_review"] and not clean["release_authorized"] and not clean["independently_verified"]
    for invalid in ("Login | must | skip | -", "Login | must | yes | -", "bad", "A | must | passed | -\nA | must | passed | -"):
        with pytest.raises(flow.WorkflowError):
            flow.readiness_report(invalid)


def test_stage_requirements_restoration_and_search_augmented_resume():
    with pytest.raises(flow.WorkflowError, match="prerequisite"):
        flow.stage_content(context(), "test", "Prepare tests")
    original = "Known screens and real notes"
    wire = flow.stage_content(context(), "recon", original)
    saved = wire + "\n\nLIVE_WEB_CONTEXT_UNTRUSTED:\nretrieved source snippets"
    messages = [{"role": "user", "content": saved}, {"role": "assistant", "content": "Map", "status": "completed"}]
    assert flow.progress(messages) == ["recon"]
    restored = flow.restore_messages(messages)
    assert restored[0]["content"] == original
    assert restored[0]["project_stage"] == {"project_id": PROJECT, "stage": "recon"}
    assert flow.progress([{"role": "user", "content": wire}, {"role": "assistant", "content": "", "status": "pending"}]) == []


def test_deterministic_readiness_footer_survives_reloading_history():
    wire = flow.stage_content(context(["recon", "test"]), "diff", "Login | must | failed | https://tests.example/a")
    messages = [{"role": "user", "content": wire}, {"role": "assistant", "content": "Model report", "status": "completed"}]
    assert "0.0%" in flow.assessment_footer(wire)
    assert "Login" in flow.restore_messages(messages)[1]["content"]
    assert "USER_REQUEST_JSON" not in messages[0]["content"]


def test_project_search_is_cached_and_restores_clean_history_without_a_planner_call(monkeypatch, tmp_path):
    from desktop.web_search import WebSearch
    from desktop.guest_store import GuestStore
    monkeypatch.setenv("VELIA_WEB_SESSION_KEY", "synthetic-session-secret")
    async def scenario():
        search = WebSearch(provider="tavily", api_key="fixture-key", store=GuestStore(sqlite_path=str(tmp_path / "search.sqlite")))
        search.store.initialize_search()
        calls = []
        async def fetch(query):
            calls.append(query)
            return {"results": [{"title": "Docs", "url": "https://example.com/docs", "snippet": "Public documentation"}],
                    "retrieved_at": "2026-10-23T12:00:00Z"}
        async def no_plan(*args):
            pytest.fail("Structured recon must not add a model-backed planning call")
        monkeypatch.setattr(search, "search", fetch)
        monkeypatch.setattr(search, "plan", no_plan)
        original = flow.stage_content(context(), "recon", "Private user notes")
        first, _ = await search.account_question(7, RESOURCE, "stable-request-1", original, "velia-flash", search_query="site:example.com documentation")
        second, _ = await search.account_question(7, RESOURCE, "stable-request-1", original, "velia-flash", search_query="site:example.com documentation")
        assert first == second and calls == ["site:example.com documentation"]
        messages = [{"role": "user", "content": first}, {"role": "assistant", "status": "completed", "content": "Report"}]
        assert flow.progress(messages) == ["recon"]
        restored = await search.restore_messages(7, RESOURCE, messages)
        assert flow.restore_messages(restored)[0]["content"] == "Private user notes"
        assert restored[1]["web_search"]["sources"][0]["url"] == "https://example.com/docs"
    asyncio.run(scenario())


def test_owner_scoped_http_workflow_roundtrip_and_failure_boundaries(monkeypatch):
    monkeypatch.setattr("desktop.account_routes.flash_enabled", lambda: True)
    async def scenario():
        stored, calls, reservations = [], [], []
        current = {"owner": 7, "search": 0}
        resource = {"id": RESOURCE, "project_id": PROJECT, "kind": "chat", "seed_query": flow.workflow_query(URL, "Private project goal")}
        async def session_for(request):
            return SimpleNamespace(user_id=int(request.headers["X-Test-User"]), access="server-only-token") if request.headers.get("X-Test-User") else None
        async def upstream(method, path, **kwargs):
            assert kwargs["token"] == "server-only-token"
            calls.append(path)
            if path.startswith("/mobile-api/v1/projects/"):
                return 200, {"ok": True, "project": {"id": PROJECT}}
            if method == "POST" and path == "/mobile-api/v1/project-resources":
                assert kwargs["idempotency_key"] == "create-workflow-123"
                assert kwargs["data"]["query"] == resource["seed_query"]
                return 201, {"ok": True, "resource": {**resource, "title": "Разбор", "access_token": "private"}}
            if path.startswith("/mobile-api/v1/project-resources?"):
                assert parse_qs(urlsplit(path).query)["project_id"] == [PROJECT]
                return 200, {"ok": True, "resources": [resource] if current["owner"] == 7 else [], "next_offset": None}
            if path.endswith("/messages?limit=200"):
                return 200, {"ok": True, "messages": [dict(item) for item in stored]}
            raise AssertionError(path)
        async def upstream_stream(path, **kwargs):
            value = kwargs["data"]
            assert value["chat_mode"] == "flash"
            stored.extend([{"role": "user", "content": value["content"], "status": "completed"},
                           {"role": "assistant", "content": "Stage response", "status": "completed"}])
            yield b'data: {"type":"ready"}\n\n'
            yield b'data: {"type":"complete","result":{"assistant_message":{"content":"Stage response"}}}\n\n'
        async def authorize(*args):
            return None
        class Search:
            available = True
            async def account_question(self, user, conversation, request_id, content, model, **kwargs):
                current["search"] += 1
                assert kwargs["search_query"] == "site:example.com/product features help documentation pricing"
                assert "Private" not in kwargs["search_query"] and "private review" not in kwargs["search_query"]
                return content, None
            async def restore_messages(self, *args):
                return args[-1]
        search = Search()
        app = web.Application()
        common = dict(session_for=session_for, upstream=upstream, json_response=lambda data, status=200: web.json_response(data, status=status), web_search=search)
        same_origin=lambda r: r.headers.get("Origin") == "https://velia.example" and r.headers.get("X-Velia-Request") == "1"
        setup_account_routes(app, **common, same_origin=same_origin,
            upstream_stream=upstream_stream, authorize_model=authorize,
            handlers={"reserve": lambda uid: reservations.append(("reserve", uid)), "release": lambda uid: reservations.append(("release", uid))})
        flow.setup_project_workflow_routes(app, **common, same_origin=same_origin)
        async with TestServer(app) as server, ClientSession() as client:
            endpoint = server.make_url(f"/web-api/v1/project-workflows/{PROJECT}/{RESOURCE}")
            assert (await client.get(endpoint)).status == 401
            headers = {"X-Test-User": "7", "Origin": "https://velia.example", "X-Velia-Request": "1"}
            creation = server.make_url("/web-api/v1/project-workflows")
            new = {"project_id": PROJECT, "url": URL, "goal": "Private project goal"}
            assert (await client.post(creation, json=new)).status == 403
            assert (await client.post(creation, headers={"Origin": "https://velia.example", "X-Velia-Request": "1"}, json=new)).status == 401
            assert (await client.post(creation, headers={**headers, "Idempotency-Key": "create-workflow-123"}, json={**new,"url":"https://127.0.0.1"})).status == 400
            async with client.post(creation, headers={**headers, "Idempotency-Key": "create-workflow-123"}, json=new) as response:
                assert response.status == 201
                assert "private" not in await response.text()
            send = server.make_url(f"/web-api/v1/conversations/{RESOURCE}/messages/stream")
            def payload(stage, content="Analyze"):
                return {"model": "velia-flash", "content": content, "idempotency_key": "stage-test-key-" + stage,
                        "project_stage": {"project_id": PROJECT, "stage": stage}, "web_search": True}
            assert (await client.post(send, json=payload("recon"))).status == 403
            assert not reservations
            assert (await client.post(send, headers=headers, json={**payload("recon"), "model": "velia-pro"})).status == 400
            assert (await client.post(send, headers=headers, json=payload("test"))).status == 409
            current["owner"] = 8
            assert (await client.get(endpoint, headers=headers)).status == 404
            assert (await client.post(send, headers=headers, json=payload("recon"))).status == 404
            current["owner"] = 7
            for stage, content in [("recon", "Map service"), ("feedback", "https://reviews.example/a | Slow private review"),
                                   ("test", "Plan checks"), ("diff", "Login | must | failed | https://tests.example/login")]:
                async with client.post(send, headers=headers, json=payload(stage, content)) as response:
                    assert response.status == 200, await response.text()
                    wire = await response.text()
                    assert "[DONE]" in wire
                    if stage == "diff":
                        assert "0.0%" in wire and "Login" in wire
            # A fresh read rebuilds progress from persisted messages, not process-local state.
            result = await (await client.get(endpoint, headers=headers)).json()
            assert result["workflow"]["completed"] == list(flow.STAGES)
            history = await (await client.get(server.make_url(f"/web-api/v1/conversations/{RESOURCE}/messages"), headers=headers)).json()
            assert history["messages"][0]["content"] == "Map service"
            assert "Расчёт по внесённым результатам" in history["messages"][-1]["content"]
            assert current["search"] == 1
            assert len(reservations) == 8 and all(reservations[i][0] == ("reserve" if i % 2 == 0 else "release") for i in range(8))
    asyncio.run(scenario())
