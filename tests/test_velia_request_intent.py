"""Request interpretation precedes retrieval; ambiguous spans become complete questions."""
import asyncio
import json

import pytest

from desktop.guest_store import GuestStore
from desktop.request_intent import parse_decision
from tests.test_velia_web_chat import fixture, headers, login
from tests.test_velia_web_guest import guest, guest_headers
from velia_request_understanding import CLARIFICATION_MARKER, clarification_content, clarification_reply


def decision(args):
    return {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "understand_request", "arguments": json.dumps(args)}}]}}]}


@pytest.mark.parametrize("quote", ["гестамин эпное", "флумпенсор", "error_100"])
def test_clarification_is_an_exact_user_span_and_a_complete_question(quote):
    question = "Уточни " + quote + ", не меняй 8080 и app.py."
    result = parse_decision(decision({"action": "clarify", "quote": quote, "query": ""}), question)
    content = clarification_content(question, result["span"])
    assert content.startswith(question)
    assert clarification_reply(content) == "Уточните, пожалуйста, что вы имеете в виду под «" + quote + "»?"


@pytest.mark.parametrize("args", [
    {"action": "clarify", "quote": "гестационный диабет", "query": ""},
    {"action": "clarify", "quote": "эпное", "query": "советы беременным"},
    {"action": "clarify", "quote": "", "query": ""},
    {"action": "direct", "quote": "эпное", "query": ""},
    {"action": "search", "quote": "", "query": ""},
    {"action": "search", "quote": "", "query": "a" * 401},
])
def test_unconfirmed_or_contradictory_interpretations_are_rejected(args):
    with pytest.raises(ValueError):
        parse_decision(decision(args), "У меня гестамин эпное и астма")


@pytest.mark.parametrize("encoded", ["[-1, 2]", "[0, 9999]", "[true, 3]", "[2, 2]", '{"answer":"invented"}'])
def test_handoff_cannot_inject_an_answer_or_quote_outside_user_text(encoded):
    assert clarification_reply("Пример" + CLARIFICATION_MARKER + encoded) is None


def test_native_leading_whitespace_normalization_retains_the_exact_fragment():
    question = "  У меня флумпенсор и вопрос  "
    start = question.index("флумпенсор")
    content = clarification_content(question, [start, start + len("флумпенсор")])
    assert clarification_reply(content.strip()) == "Уточните, пожалуйста, что вы имеете в виду под «флумпенсор»?"


def test_guest_clarification_never_searches_and_never_generates_a_quote_only_answer(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        quote = "гестамин эпное"
        question = "Привет . Рада познакомиться . Идеи для похудения к 31 ок ября у меня " + quote + " и астма . Как мне похудеть быстро"
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action": "clarify", "quote": quote, "query": ""}) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={"model": "velia-flash", "stream": True, "messages": [{"role": "user", "content": question}]}) as response:
                assert response.status == 200 and response.headers["X-Velia-Guest-Remaining"] == "29"
                wire = await response.text()
            assert "что вы имеете в виду" in wire and "«гестамин эпное»?" in wire and "[DONE]" in wire
            assert '"web_search"' not in wire and "гестацион" not in wire
            assert state["search_queries"] == [] and state["payloads"] == []
            assert len(state["intent_payloads"]) == 1
            assert state["intent_payloads"][0]["messages"][-1]["content"] == question
    asyncio.run(run())


@pytest.mark.parametrize("model", ["velia-flash", "velia-pro"])
def test_legacy_signed_in_route_uses_the_same_complete_clarification(monkeypatch, tmp_path, model):
    async def run():
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action": "clarify", "quote": "флумпенсор", "query": ""}) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/chat/completions"), headers=headers(cookie),
                    json={"model": model, "stream": True, "messages": [{"role": "user", "content": "Как починить флумпенсор?"}]}) as response:
                assert response.status == 200
                assert "что вы имеете в виду под «флумпенсор»?" in await response.text()
            assert state["search_queries"] == state["payloads"] == []
    asyncio.run(run())


@pytest.mark.parametrize("model", ["velia-flash", "velia-pro"])
def test_account_clarification_persists_and_replay_does_not_repeat_intent_or_search(monkeypatch, tmp_path, model):
    async def run():
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action": "clarify", "quote": "гестамин эпное", "query": ""}) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/conversations"), headers=headers(cookie), json={"title": "Уточнение"}) as response:
                cid = (await response.json())["conversation"]["id"]
            question = "У меня гестамин эпное и астма. Как похудеть?"
            body = {"content": question, "model": model, "idempotency_key": "clarification-123"}
            for _ in range(2):
                async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"), headers=headers(cookie), json=body) as response:
                    assert response.status == 200
                    wire = await response.text()
                    assert "что вы имеете в виду под «гестамин эпное»?" in wire and '"web_search"' not in wire
            async with client.get(server.make_url(f"/web-api/v1/conversations/{cid}/messages"), headers=headers(cookie)) as response:
                values = (await response.json())["messages"]
            assert all(v["content"] == question for v in values if v["role"] == "user")
            assert all(v["content"].endswith("«гестамин эпное»?") for v in values if v["role"] == "assistant")
            assert len(state["intent_payloads"]) == 1 and state["search_queries"] == []
    asyncio.run(run())


def test_direct_turn_keeps_history_and_constraints_without_irrelevant_sources(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        messages = [{"role": "user", "content": "У меня Ubuntu"},
            {"role": "assistant", "content": "Windows"},
            {"role": "user", "content": "Нет, Ubuntu. Испраь app.py, не меняй порт 8080."}]
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action": "direct", "quote": "", "query": ""}) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={"model": "velia-flash", "stream": True, "messages": messages}) as response:
                assert response.status == 200
                assert '"web_search"' not in await response.text()
            assert state["intent_payloads"][0]["messages"][1:] == messages
            assert state["payloads"][0]["messages"][1:] == messages
            assert state["search_queries"] == []
    asyncio.run(run())


def test_failed_interpretation_does_not_fall_through_to_guessed_search(monkeypatch, tmp_path):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent_status=503) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={"model": "velia-flash", "stream": True, "messages": [{"role": "user", "content": "Непонятное название"}]}) as response:
                assert response.status == 503
                assert (await response.json())["error"] == "request_understanding_unavailable"
            assert state["search_queries"] == state["payloads"] == []
    asyncio.run(run())
