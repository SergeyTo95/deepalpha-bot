"""Actual HTTP proof that an unsafe draft never leaks and quota is charged once."""
import asyncio
import json

import pytest
from desktop.guest_store import GuestStore
from tests.test_velia_web_chat import fixture
from tests.test_velia_web_guest import BODY, guest, guest_headers


@pytest.mark.parametrize("health", [True, False])
def test_guest_emits_only_reviewed_answer_with_original_sources_and_one_quota_charge(monkeypatch, tmp_path, health):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        question = "У меня гестамин эпное и астма. Как похудеть?" if health else "У меня Самсунг, модель не знаю. Как сделать скриншот?"
        intent = {"action": "search" if health else "direct", "quote": "гестамин эпное" if health else "",
            "candidate": "гистамин, апноэ" if health else "", "query": "weight loss" if health else "",
            "source_scope": "official_health" if health else "general", "context": (
                [{"quote": "гестамин", "kind": "substance", "status": "stated"}, {"quote": "эпное", "kind": "condition", "status": "stated"}, {"quote": "астма", "kind": "condition", "status": "stated"}]
                if health else [{"quote": "модель не знаю", "kind": "other", "status": "unspecified"}])}
        final = "При астме и апноэ начните с регулярного питания и спокойных прогулок [1]." if health else "Нажмите одновременно питание и уменьшение громкости."
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), intent=intent,
                search_response={"results": [{"title": "Weight advice", "url": "https://www.nhs.uk/weight", "content": "Eat well. Gradual physical activity."}]},
                model_content="UNSAFE_DRAFT: под вашу аллергию. SECRET_PROVIDER", review_content=final) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie), json={**BODY, "messages": [{"role": "user", "content": question}]}) as response:
                assert response.status == 200 and response.headers["X-Velia-Guest-Remaining"] == "29"
                wire = await response.text()
            assert final in wire and "[DONE]" in wire
            assert all(value not in wire for value in ("UNSAFE_DRAFT", "SECRET_PROVIDER", "private-thought", "private-runtime", "private-upstream-model"))
            assert len(state["payloads"]) == len(state["review_payloads"]) == 1
            payload = state["review_payloads"][0]
            assert payload["model"] == "velia-flash" and payload["stream"] is False
            evidence = json.loads(payload["messages"][-1]["content"])
            assert evidence["draft"].startswith("UNSAFE_DRAFT")
            if health:
                assert evidence["question"] == question.replace("гестамин эпное", "гистамин, апноэ")
                assert evidence["user_context"][:2] == [{"quote": "гистамин", "kind": "substance", "status": "unspecified"}, {"quote": "апноэ", "kind": "condition", "status": "stated"}]
                assert wire.count('"web_search"') == 1
            async with client.get(server.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                assert (await response.json())["remaining"] == 29
    asyncio.run(run())


@pytest.mark.parametrize("failure", [{"review_status": 503}, {"review_finish": "length"}, {"review_content": ""}])
def test_review_failure_never_falls_back_to_the_unreviewed_draft(monkeypatch, tmp_path, failure):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action":"direct", "quote":"", "query":"", "context":[{"quote":"модель не знаю", "status":"unspecified"}]},
                model_content="UNSAFE_DRAFT", **failure) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={**BODY, "messages":[{"role":"user", "content":"Модель не знаю: модель не знаю. Как сделать скриншот?"}]}) as response:
                wire = await response.text()
            assert "UNSAFE_DRAFT" not in wire and "[DONE]" not in wire
            assert '"error"' in wire and "model_request_failed" in wire
            assert len(state["review_payloads"]) == 1
    asyncio.run(run())


@pytest.mark.parametrize("finish,draft", [(None, ""), ("length", "Начни с")])
def test_a_completed_transport_can_hand_off_an_empty_or_truncated_draft_to_a_complete_editor(monkeypatch, tmp_path, finish, draft):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action":"direct", "quote":"", "query":"", "context":[{"quote":"модель не знаю", "status":"unspecified"}]},
                model_content=draft, model_finish=finish, review_content="Нажмите питание и уменьшение громкости.") as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={**BODY, "messages":[{"role":"user", "content":"Модель не знаю: модель не знаю. Как сделать скриншот?"}]}) as response:
                wire = await response.text()
            assert "Нажмите питание" in wire and "[DONE]" in wire
            assert len(state["review_payloads"]) == 1
    asyncio.run(run())
