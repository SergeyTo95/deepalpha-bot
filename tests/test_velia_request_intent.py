"""Request interpretation precedes retrieval; ambiguous spans become complete questions."""
import asyncio
import json

import pytest

from desktop.guest_store import GuestStore
from desktop.request_intent import parse_decision
from desktop.spelling_hints import spelling_hints, phonetic_restoration
from tests.test_velia_web_chat import fixture, headers, login
from tests.test_velia_web_guest import guest, guest_headers
from velia_request_understanding import CLARIFICATION_MARKER, RESTORATION_MARKER, clarification_content, clarification_reply, plausible_restoration, restoration_content, interpreted_content


def decision(args):
    return {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "understand_request", "arguments": json.dumps(args)}}]}}]}


def test_personal_context_carries_exact_spans_without_invented_diagnoses():
    question = "У меня гестамин эпное и астма. Как похудеть?"
    args = {"action": "search", "quote": "гестамин эпное", "candidate": "гистамин, апноэ",
        "query": "healthy weight loss", "source_scope": "official_health", "task_query": "healthy weight loss",
        "context": [{"quote": "эпное", "status": "stated"}, {"quote": "астма", "status": "stated"},
            {"quote": "гестамин", "status": "unspecified"}]}
    result = parse_decision(decision(args), question)
    assert [(question[row["span"][0]:row["span"][1]], row["status"]) for row in result["context"]] == [
        ("эпное", "stated"), ("астма", "stated"), ("гестамин", "unspecified")]
    assert "гестамин" not in json.dumps(result["context"], ensure_ascii=False)


@pytest.mark.parametrize("context", [
    [{"quote": "аллергия", "status": "stated"}],
    [{"quote": "гистамин", "status": "stated"}],
    [{"quote": "астма", "status": "diagnosed"}],
    [{"quote": "астма", "status": ["stated"]}],
    [{"quote": "астма", "status": "stated", "diagnosis": "allergy"}],
    [{"quote": "астма", "status": "stated"}] * 2,
    [{"quote": "стма", "status": "stated"}],
    "аллергия", None,
])
def test_context_rejects_new_facts_or_unbounded_evidence(context):
    with pytest.raises(ValueError):
        parse_decision(decision({"action": "direct", "quote": "", "candidate": "", "query": "", "context": context}),
            "У меня гестамин и астма")


def test_context_preserves_negations_and_explicitly_stated_allergy():
    question = "У меня аллергия на арахис, но нет астмы."
    result = parse_decision(decision({"action": "direct", "quote": "", "candidate": "", "query": "",
        "context": [{"quote": "аллергия на арахис", "status": "stated"}, {"quote": "нет астмы", "status": "stated"}]}), question)
    assert [question[row["span"][0]:row["span"][1]] for row in result["context"]] == ["аллергия на арахис", "нет астмы"]


@pytest.mark.parametrize("quote", ["аллергия на арахис", "нет астмы"])
def test_an_identical_candidate_preserves_the_valid_plan_without_a_restoration(quote):
    question = "У меня аллергия на арахис, но нет астмы. Дай общие советы по питанию."
    result = parse_decision(decision({"action":"search", "quote":quote, "candidate":quote,
        "query":"peanut allergy diet advice", "source_scope":"official_health",
        "task_query":"peanut allergy diet advice", "context":[
            {"quote":"аллергия на арахис", "kind":"condition", "status":"stated"},
            {"quote":"нет астмы", "kind":"condition", "status":"stated"}]}), question)
    assert result["action"] == "search"
    assert "span" not in result and "candidate" not in result
    assert [question[row["span"][0]:row["span"][1]] for row in result["context"]] == [
        "аллергия на арахис", "нет астмы"]


def test_an_identical_candidate_still_cannot_supply_a_quote_absent_from_the_question():
    with pytest.raises(ValueError):
        parse_decision(decision({"action":"direct", "quote":"аллергия", "candidate":"аллергия",
            "query":""}), "У меня гестамин и астма")


def test_a_noun_quote_carries_its_immediate_original_negation():
    question = "У меня аллергия, но нет астмы."
    result = parse_decision(decision({"action":"direct", "quote":"", "query":"",
        "context":[{"quote":"астмы", "kind":"condition", "status":"stated"}]}), question)
    start, end = result["context"][0]["span"]
    assert question[start:end] == "нет астмы"


@pytest.mark.parametrize("recover", [True, False])
def test_schema_repair_keeps_the_original_question_and_never_searches_an_invalid_plan(monkeypatch, tmp_path, recover):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        question = "У меня аллергия на арахис, но нет астмы. Дай общие советы по питанию."
        bad = {"action":"search", "quote":"", "query":"diet", "context":[{"quote":"астма", "status":"stated"}]}
        good = {"action":"search", "quote":"", "query":"diet", "context":[{"quote":"аллергия на арахис", "status":"stated"}, {"quote":"нет астмы", "status":"stated"}]}
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent_results=[bad, good if recover else bad]) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={"model":"velia-flash", "stream":True, "messages":[{"role":"user", "content":question}]}) as response:
                assert response.status == (200 if recover else 503)
                await response.read()
            assert len(state["intent_payloads"]) == 2
            assert all(data["messages"][-1]["content"] == question for data in state["intent_payloads"])
            assert len(state["search_queries"]) == (1 if recover else 0)
            assert len(state["payloads"]) == (1 if recover else 0)
            async with client.get(server.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                assert (await response.json())["remaining"] == 29
    asyncio.run(run())


def test_a_substance_mention_cannot_be_promoted_to_a_personal_condition():
    question = "У меня гестамин эпное и астма."
    result = parse_decision(decision({"action": "direct", "quote": "гестамин эпное", "candidate": "гистамин, апноэ", "query": "",
        "context": [{"quote": "гестамин", "kind": "substance", "status": "stated"},
            {"quote": "эпное", "kind": "condition", "status": "stated"}, {"quote": "астма", "kind": "condition", "status": "stated"}]}), question)
    assert result["context"][0]["status"] == "unspecified"
    from desktop.web_search import grounded_context
    context = grounded_context(question, result)
    assert 'Пользователь явно сообщает: "эпное"; "астма"' in context
    assert 'Названо вещество: "гестамин"' in context
    assert 'Пользователь явно сообщает: "гестамин"' not in context


@pytest.mark.parametrize("kind", ["condition", "measurement"])
def test_an_explicit_reaction_or_measurement_remains_a_reported_fact(kind):
    question = "Аллергия на железо" if kind == "condition" else "Сывороточное железо 15 мкмоль/л"
    result = parse_decision(decision({"action": "direct", "quote": "", "candidate": "", "query": "",
        "context": [{"quote": question, "kind": kind, "status": "stated"}]}), question)
    assert result["context"][0]["status"] == "stated"


@pytest.mark.parametrize("kind", [None, [], "invented_diagnosis"])
def test_context_kind_is_bounded_to_a_known_semantic_category(kind):
    with pytest.raises(ValueError):
        parse_decision(decision({"action": "direct", "quote": "", "query": "",
            "context": [{"quote": "астма", "kind": kind, "status": "stated"}]}), "У меня астма")


def test_health_retrieval_uses_the_main_task_without_changing_named_conditions():
    args = {"action":"search", "quote":"гестамин эпное", "candidate":"гистамин, апноэ",
        "query":"weight loss asthma apnea histamine", "source_scope":"official_health", "task_query":"healthy weight loss advice"}
    result = parse_decision(decision(args), "Как похудеть, у меня гестамин эпное и астма?")
    assert result["query"] == "healthy weight loss advice"
    assert result["candidate"] == "гистамин, апноэ"
    args.update(quote="", candidate="", source_scope="general")
    assert parse_decision(decision(args), "Объясни текущие рекомендации") ["query"] == args["query"]


@pytest.mark.parametrize("task_query", ["", "a" * 201, "a " * 17])
def test_invalid_primary_task_queries_are_rejected(task_query):
    with pytest.raises(ValueError):
        parse_decision(decision({"action":"search", "quote":"", "candidate":"", "query":"weight loss",
            "source_scope":"official_health", "task_query":task_query}), "Как похудеть?")


def test_packaged_vocabulary_restores_the_screenshot_terms_and_ordinary_technical_words():
    hints = {value["word"]: value["candidates"] for value in spelling_hints(
        "У меня гестамин эпное и астма. Как перезагрузить роутор и открыть терменал?")}
    assert hints["гестамин"][0] == "гистамин"
    assert hints["эпное"][0] == "апноэ"
    assert "роутер" in hints["роутор"] and "терминал" in hints["терменал"]


def test_lexical_hints_cover_english_without_changing_literals_or_original_text():
    question = 'Explain revnue, but leave «гестамин эпное», "терменал", app.py, error_100, myFile and 0,5 TON unchanged.'
    hints = spelling_hints(question)
    assert any(value["word"] == "revnue" and "revenue" in value["candidates"] for value in hints)
    assert not any(value["word"] in {"гестамин", "эпное", "терменал", "app", "error", "myFile", "TON"} for value in hints)
    assert question == 'Explain revnue, but leave «гестамин эпное», "терменал", app.py, error_100, myFile and 0,5 TON unchanged.'


@pytest.mark.parametrize("quote", ["флумпенсор", "error_100"])
def test_clarification_is_an_exact_user_span_and_a_complete_question(quote):
    question = "Уточни " + quote + ", не меняй 8080 и app.py."
    result = parse_decision(decision({"action": "clarify", "quote": quote, "query": ""}), question)
    content = clarification_content(question, result["span"])
    assert content.startswith(question)
    assert clarification_reply(content) == "Уточните, пожалуйста, что вы имеете в виду под «" + quote + "»?"


def test_empty_model_candidate_can_use_unique_phonetic_readings_without_asserting_a_diagnosis():
    question = "У меня гестамин эпное и астма"
    result = parse_decision(decision({"action": "clarify", "quote": "гестамин эпное",
        "candidate": "", "query": ""}), question)
    assert result["candidate"] == "гистамин, апноэ"
    reply = clarification_reply(clarification_content(question, result["span"], result["candidate"]))
    assert reply.startswith("Правильно ли я поняла:") and reply.endswith("?")
    assert phonetic_restoration("квампер") is None
    assert phonetic_restoration("app.py") is None
    assert phonetic_restoration("0,5 TON") is None


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


@pytest.mark.parametrize("source,candidate", [
    ("гестамин эпное", "гистамин, апноэ"),
    ("гестамин эпное", "гистамин и апноэ"),
    ("стартир генератр", "стартер, генератор"),
    ("фоторезистар", "фоторезистор"),
    ("revnue", "revenue"),
])
def test_nearby_readings_become_a_confirmation_without_rewriting_user_text(source, candidate):
    question = "  Объясни " + source + ", не меняй порт 8080 и app.py."
    result = parse_decision(decision({"action": "clarify", "quote": source,
        "candidate": candidate, "query": ""}), question)
    assert result["candidate"] == candidate
    content = clarification_content(question, result["span"], result["candidate"])
    reply = clarification_reply(content.strip())
    assert content.startswith(question) and reply.startswith("Правильно ли я поняла:")
    if source in {"гестамин эпное", "стартир генератр"}:
        assert " — это «" in reply and ", а «" in reply and reply.endswith("?")
    else:
        assert "«" + source + "»" in reply and "«" + candidate + "»?" in reply


@pytest.mark.parametrize("source,candidate,expected", [
    ("гестамин эпное", "гистамин апноэ", "Правильно ли я поняла: «гестамин» — это «гистамин», а «эпное» — «апноэ»?"),
    ("сульфат магнйя", "сульфат магния", "Правильно ли я поняла: под «сульфат магнйя» вы имеете в виду «сульфат магния»?"),
    ("opn sorce", "open source", "Правильно ли я поняла: «opn» — это «open», а «sorce» — «source»?"),
])
def test_multiple_readings_do_not_create_a_false_compound_or_split_a_real_one(source, candidate, expected):
    question = "Что означает " + source + "?"
    start = question.index(source)
    assert clarification_reply(clarification_content(question, [start, start + len(source)], candidate)) == expected


@pytest.mark.parametrize("source,candidate", [
    ("гестамин эпное", "гестационный диабет"),
    ("гестамин эпное", "гистаминовая непереносимость и апноэ"),
    ("гестамин эпное", "гистамин, апноэ. Вы беременны"),
    ("квампер", "водонагреватель"),
    ("error_100", "error_101"),
    ("app.py", "app.js"),
    ("0,5 TON", "0,6 TON"),
    ("гестамин", "гестамин"),
])
def test_extra_facts_unrelated_meanings_and_literal_changes_are_not_proposed(source, candidate):
    assert plausible_restoration(source, candidate) is None
    result = parse_decision(decision({"action": "clarify", "quote": source,
        "candidate": candidate, "query": ""}), "Что означает " + source + "?")
    assert result.get("candidate") != candidate
    if result.get("candidate"):
        assert plausible_restoration(source, result["candidate"]) == result["candidate"]


@pytest.mark.parametrize("candidate", ["гестационный диабет", "гистамин, апноэ. Вы беременны"])
def test_candidate_handoff_is_validated_again_at_native_boundary(candidate):
    question = "У меня гестамин эпное и астма"
    start = question.index("гестамин эпное")
    with pytest.raises(ValueError):
        clarification_content(question, [start, start + len("гестамин эпное")], candidate)
    relative = [len(question) - start, len(question) - start - len("гестамин эпное")]
    assert clarification_reply(question + CLARIFICATION_MARKER + json.dumps({
        "span": relative, "candidate": candidate}, ensure_ascii=False)) is None


@pytest.mark.parametrize("action", ["direct", "search"])
def test_resolved_spelling_reaches_the_answer_generator_without_confirmation(monkeypatch, tmp_path, action):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        quote = "гестамин эпное"
        question = "Привет . Рада познакомиться . Идеи для похудения к 31 ок ября у меня " + quote + " и астма . Как мне похудеть быстро"
        query = "безопасное снижение веса астма апноэ гистамин" if action == "search" else ""
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action": action, "quote": quote, "candidate": "гистамин, апноэ", "query": query}) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={"model": "velia-flash", "stream": True, "messages": [{"role": "user", "content": question}]}) as response:
                assert response.status == 200 and response.headers["X-Velia-Guest-Remaining"] == "29"
                wire = await response.text()
            assert "Правильно ли я поняла" not in wire and "[DONE]" in wire
            assert ('"web_search"' in wire) == (action == "search")
            assert state["search_queries"] == ([query] if query else [])
            assert len(state["payloads"]) == len(state["intent_payloads"]) == 1
            assert state["intent_payloads"][0]["messages"][-1]["content"] == question
            content = state["payloads"][0]["messages"][-1]["content"]
            assert content.startswith(question.replace(quote, "гистамин, апноэ"))
            assert "гистамин" in content and "апноэ" in content
            assert CLARIFICATION_MARKER not in content and RESTORATION_MARKER not in content
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
                intent={"action": "clarify", "quote": "квампер", "candidate": "", "query": ""}) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/conversations"), headers=headers(cookie), json={"title": "Уточнение"}) as response:
                cid = (await response.json())["conversation"]["id"]
            question = "У меня сломался квампер. Как починить?"
            body = {"content": question, "model": model, "idempotency_key": "clarification-123"}
            for _ in range(2):
                async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"), headers=headers(cookie), json=body) as response:
                    assert response.status == 200
                    wire = await response.text()
                    assert "что вы имеете в виду под «квампер»?" in wire and '"web_search"' not in wire
            async with client.get(server.make_url(f"/web-api/v1/conversations/{cid}/messages"), headers=headers(cookie)) as response:
                values = (await response.json())["messages"]
            assert all(v["content"] == question for v in values if v["role"] == "user")
            assert all(v["content"].endswith("«квампер»?") for v in values if v["role"] == "assistant")
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


@pytest.mark.parametrize("action", ["direct", "search"])
def test_resolved_pair_is_validated_and_keeps_the_raw_question(action):
    question = "Объясни revnue, оставь app.py, порт 8080 и 0,5 TON."
    args = {"action": action, "quote": "revnue", "candidate": "revenue", "query": "revenue definition" if action == "search" else ""}
    result = parse_decision(decision(args), question)
    assert result["action"] == action and result["candidate"] == "revenue"
    content = restoration_content(question, result["span"], result["candidate"])
    assert content.startswith(question + RESTORATION_MARKER)
    assert clarification_reply(content) is None
    assert interpreted_content(content) == question.replace("revnue", "revenue")


@pytest.mark.parametrize("question,quote,candidate", [
    ('Оставь «гестамин» дословно', 'гестамин', 'гистамин'),
    ('Оставь `терменал` дословно', 'терменал', 'терминал'),
    ('Оставь "revnue" дословно', 'revnue', 'revenue'),
    ('Не меняй error_100', 'error', 'errors'),
    ('Не меняй app.py', 'app', 'apps'),
    ('Объясни супергестамин', 'гестамин', 'гистамин'),
    ('У меня гестамин эпное', 'гестамин эпное', 'гестационный диабет'),
    ('У меня гестамин эпное', 'гестамин эпное', 'гистаминовая непереносимость и апноэ'),
    ('Не гестамин', 'Не гестамин', 'На гистамин'),
    ('not revnue', 'not revnue', 'now revenue'),
    ('Доза 5 мг', 'мг', 'г'),
])
def test_resolved_interpretation_cannot_change_literals_or_add_diagnoses(question, quote, candidate):
    with pytest.raises(ValueError):
        parse_decision(decision({"action": "direct", "quote": quote, "candidate": candidate, "query": ""}), question)


@pytest.mark.parametrize("model", ["velia-flash", "velia-pro"])
@pytest.mark.parametrize("action", ["direct", "search"])
def test_account_resolved_spelling_generates_and_restores_raw_history_on_replay(monkeypatch, tmp_path, model, action):
    async def run():
        query = "revenue definition" if action == "search" else ""
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent={"action": action, "quote": "revnue", "candidate": "revenue", "query": query}) as (server, client, state):
            cookie, _, _ = await login(server, client)
            async with client.post(server.make_url("/web-api/v1/conversations"), headers=headers(cookie), json={"title": "Выручка"}) as response:
                cid = (await response.json())["conversation"]["id"]
            question = "Explain revnue. Keep app.py and 0.5 TON."
            body = {"content": question, "model": model, "idempotency_key": "restored-reading-123"}
            for _ in range(2):
                async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"), headers=headers(cookie), json=body) as response:
                    assert response.status == 200
                    wire = await response.text()
                    assert "Ответ из аккаунта" in wire and "Правильно ли я поняла" not in wire
            sent = state["account_calls"][0]["content"]
            assert sent.startswith(question + RESTORATION_MARKER) and "revenue" in sent
            assert state["account_calls"][1]["content"] == sent
            assert len(state["intent_payloads"]) == 1
            assert state["search_queries"] == ([query] if query else [])
            async with client.get(server.make_url(f"/web-api/v1/conversations/{cid}/messages"), headers=headers(cookie)) as response:
                values = (await response.json())["messages"]
            assert all(v["content"] == question for v in values if v["role"] == "user")
    asyncio.run(run())


def test_health_advice_selects_official_sources_without_changing_the_user_question():
    result = parse_decision(decision({"action": "search", "quote": "", "candidate": "",
        "query": "safe gradual weight loss", "source_scope": "official_health"}), "Как безопасно снизить вес?")
    assert result == {"action": "search", "query": "safe gradual weight loss", "source_scope": "official_health"}


@pytest.mark.parametrize("action,scope", [("direct", "official_health"), ("clarify", "official_health"), ("search", "unbounded")])
def test_invalid_source_scope_cannot_change_the_retrieval_policy(action, scope):
    with pytest.raises(ValueError):
        parse_decision(decision({"action": action, "quote": "", "candidate": "",
            "query": "weight loss" if action == "search" else "", "source_scope": scope}), "Пример")
