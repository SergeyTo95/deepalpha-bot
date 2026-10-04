"""Bounded real HTTP search, guest quota and persisted native-chat provenance."""
import asyncio
import json
import pytest
from desktop.guest_store import GuestStore
from desktop.web_search import MARKER, public_url
from test_velia_web_chat import fixture, login, headers
from test_velia_web_guest import BODY, guest, guest_headers


def test_duplicate_pages_leave_room_for_distinct_relevant_sources(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        rows = [
            {"title":"Weight advice", "url":"https://www.nhs.uk/weight#one", "content":"Weight guidance."},
            {"title":"Weight advice with an anchor", "url":"https://www.nhs.uk/weight#two", "content":"Same page."},
            {"title":"Weight advice", "url":"https://www.nhs.uk/weight-copy.pdf", "content":"Same title from same host."},
            {"title":"Healthy Weight", "url":"https://www.cdc.gov/healthy-weight", "content":"Gradual weight loss."},
            {"title":"Managing weight", "url":"https://www.niddk.nih.gov/weight", "content":"Nutrition and activity."},
        ]
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True,
                intent={"action":"search", "quote":"", "candidate":"", "query":"healthy weight loss", "source_scope":"official_health"},
                search_response={"results":rows}) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 200
                wire = await response.text()
            assert "weight-copy.pdf" not in wire and "#two" not in wire
            assert "https://www.cdc.gov/healthy-weight" in wire and "https://www.niddk.nih.gov/weight" in wire
            assert len(state["search_queries"]) == 1
    asyncio.run(run())


def test_guest_internet_enriches_only_latest_turn_and_exposes_real_sources(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True) as (server, client, state):
            cookie, profile, _ = await guest(server, client)
            assert profile["web_search"] is True
            messages = [{"role":"user","content":"Раньше"}, {"role":"assistant","content":"Прежний ответ"}, {"role":"user","content":"Какая версия Python?"}]
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={**BODY, "messages":messages}) as response:
                assert response.status == 200 and response.headers["X-Velia-Guest-Remaining"] == "29"
                wire = await response.text()
                assert "web_search" in wire and "https://www.python.org/downloads/" in wire
                assert "fixture-search-key" not in wire and "Python test release" not in wire
            payload = state["payloads"][0]
            assert payload["messages"][1:3] == messages[:2]
            assert payload["messages"][-1]["content"].startswith("Какая версия Python?")
            assert "Python test release" in payload["messages"][-1]["content"] and "внешние данные" in payload["messages"][-1]["content"]
            assert "web_search" not in payload and state["search_queries"] == ["Какая версия Python?"]
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={**BODY, "model":"velia-pro", "web_search":True}) as response:
                assert response.status == 403
            assert len(state["search_queries"]) == 1
    asyncio.run(run())


@pytest.mark.parametrize("extra", [{}, {"web_search":False}])
def test_guest_search_is_default_and_legacy_false_cannot_disable_it(monkeypatch, tmp_path, extra):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json={**BODY, **extra}) as response:
                assert response.status == 200
                assert '"web_search"' in await response.text()
            assert state["search_queries"] == ["Привет"]
            assert MARKER in state["payloads"][0]["messages"][-1]["content"]
    asyncio.run(run())


@pytest.mark.parametrize("extra", [{"web_search":"true"}, {"web_search":1}, {"web_search":True,"sources":[]}, {"web_search":True,"search_endpoint":"http://localhost"}])
def test_search_request_cannot_override_tools_or_provider(monkeypatch, tmp_path, extra):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json={**BODY, **extra}) as response:
                assert response.status == 400
            assert state["search_queries"] == state["payloads"] == []
    asyncio.run(run())


@pytest.mark.parametrize("configuration", [{"search_status":503}, {"search_response":{"results":[]}},
    {"search_response":{"results":[{"title":"unsafe","url":"http://127.0.0.1/admin","content":"private"}]}}])
def test_search_failure_never_generates_an_unverified_answer(monkeypatch, tmp_path, configuration):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True, **configuration) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 503 and (await response.json())["error"] == "web_search_unavailable"
            assert state["payloads"] == [] and len(state["search_queries"]) == 1
            async with client.get(server.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                assert (await response.json())["remaining"] == 29
    asyncio.run(run())


@pytest.mark.parametrize("model", ["velia-flash", "velia-pro"])
@pytest.mark.parametrize("legacy_flag", [None, False, True])
def test_account_search_persists_clean_history_and_sources_without_repeating_search(monkeypatch, tmp_path, model, legacy_flag):
    async def run():
        store = GuestStore(sqlite_path=tmp_path/"metadata.db")
        async with fixture(monkeypatch, guest_store=store, with_search=True) as (server, client, state):
            cookie, profile, _ = await login(server, client)
            assert profile["web_search"] is True
            async with client.post(server.make_url("/web-api/v1/conversations"), headers=headers(cookie), json={"title":"Поиск"}) as response:
                cid = (await response.json())["conversation"]["id"]
            body = {"content":"Какая стабильная версия Python?", "model":model, "idempotency_key":"web-search-test-01"}
            if legacy_flag is not None:
                body["web_search"] = legacy_flag
            for _ in range(2):
                async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"), headers=headers(cookie), json=body) as response:
                    assert response.status == 200
                    wire = await response.text()
                    assert "https://www.python.org/downloads/" in wire and "fixture-search-key" not in wire
            assert len(state["search_queries"]) == 1
            assert state["account_calls"][0]["content"] == state["account_calls"][1]["content"]
            async with client.get(server.make_url(f"/web-api/v1/conversations/{cid}/messages"), headers=headers(cookie)) as response:
                assert response.status == 200
                messages = (await response.json())["messages"]
            assert messages[0]["content"] == body["content"] and messages[0]["web_search"] is True
            assert messages[1]["web_search"]["sources"][0]["url"] == "https://www.python.org/downloads/"
            assert MARKER not in json.dumps(messages)
            # Only opaque hashes, lengths and public search results are cached.
            with store.connect() as conn:
                row = conn.execute("SELECT query_hash, result_json FROM velia_web_search_context").fetchone()
                assert len(row[0]) == 64 and body["content"] not in row[1]
    asyncio.run(run())


def test_pro_without_tokens_rejects_before_search(monkeypatch, tmp_path):
    async def run():
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"metadata.db"), with_search=True, credits=0) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/conversations"), headers=headers(cookie), json={"title":"Поиск"}) as response:
                cid = (await response.json())["conversation"]["id"]
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"), headers=headers(cookie),
                    json={"content":"Поиск", "model":"velia-pro", "idempotency_key":"no-token-test"}) as response:
                assert response.status == 402
            assert state["search_queries"] == state["account_calls"] == []
    asyncio.run(run())


def test_exhausted_guest_cannot_call_search(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            for _ in range(30):
                async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                    assert response.status == 200
                    await response.read()
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json={**BODY, "web_search":True}) as response:
                assert response.status == 429
            assert len(state["search_queries"]) == 30
    asyncio.run(run())


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "http://127.0.0.1", "http://[::1]", "http://169.254.169.254/", "https://user:pass@example.com/", "https://worker.railway.internal/", "http://localhost", "https://example.com:8080/"])
def test_nonpublic_source_links_are_rejected(url):
    assert public_url(url) is None


@pytest.mark.parametrize("model", ["velia-flash", "velia-pro"])
def test_legacy_account_web_chat_also_receives_search_context(monkeypatch, tmp_path, model):
    async def run():
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"metadata.db"), with_search=True) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(cookie),
                    json={**BODY, "model":model}) as response:
                assert response.status == 200
                assert "https://www.python.org/downloads/" in await response.text()
            assert len(state["search_queries"]) == len(state["payloads"]) == 1
            assert MARKER in state["payloads"][0]["messages"][-1]["content"]
    asyncio.run(run())


def test_health_search_restricts_provider_and_filters_unrelated_or_impostor_sources(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        intent = {"action": "search", "quote": "", "candidate": "", "query": "safe gradual weight loss", "source_scope": "official_health"}
        results = [
            {"title": "Clinic exclusions", "url": "https://clinic.example/diet", "content": "Exclude many foods."},
            {"title": "Impostor", "url": "https://www.cdc.gov.example/diet", "content": "Not an official CDC source."},
            {"title": "Healthy weight", "url": "https://www.cdc.gov/healthy-weight-growth/losing-weight/index.html", "content": "Gradual weight loss, healthy food and physical activity."},
        ]
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True,
                intent=intent, search_response={"results": results}) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={**BODY, "messages": [{"role": "user", "content": "Как безопасно снизить вес?"}]}) as response:
                assert response.status == 200
                wire = await response.text()
            query = state["search_payloads"][0]
            assert "cdc.gov" in query["include_domains"] and query["include_domains_mode"] == "restrict"
            assert len(state["search_queries"]) == len(state["intent_payloads"]) == len(state["payloads"]) == 1
            assert "https://www.cdc.gov/" in wire and "clinic.example" not in wire and "cdc.gov.example" not in wire
            content = state["payloads"][0]["messages"][-1]["content"]
            assert "Exclude many foods" not in content and "Not an official" not in content
    asyncio.run(run())


def test_health_source_failure_does_not_fall_back_to_unverified_restrictions(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), with_search=True,
                intent={"action": "search", "quote": "", "candidate": "", "query": "weight loss", "source_scope": "official_health"},
                search_response={"results": [{"title": "Clinic", "url": "https://clinic.example/diet", "content": "Exclude foods."}]}) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json=BODY) as response:
                assert response.status == 503
                assert (await response.json())["error"] == "web_search_unavailable"
            assert len(state["search_queries"]) == 1 and state["payloads"] == []
    asyncio.run(run())
