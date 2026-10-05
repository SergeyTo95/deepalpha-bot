"""Actual HTTP proof that an unsafe draft never leaks and quota is charged once."""
import asyncio
import json

import pytest
from desktop.guest_store import GuestStore
from desktop.answer_review import review_omissions
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
            sources = json.loads(payload["messages"][1]["content"])["sources"]
            evidence = json.loads(payload["messages"][-1]["content"])
            assert "sources" not in evidence
            assert sources == ([{"id": 1, "title": "Weight advice", "url": "https://www.nhs.uk/weight",
                "snippet": "Eat well. Gradual physical activity."}] if health else [])
            assert evidence["draft"].startswith("UNSAFE_DRAFT")
            if health:
                assert evidence["question"] == question.replace("гестамин эпное", "гистамин, апноэ")
                assert evidence["user_context"][:2] == [{"quote": "гистамин", "kind": "substance", "status": "unspecified"}, {"quote": "апноэ", "kind": "condition", "status": "stated"}]
                assert [item["quote"] for item in evidence["required_context_mentions"]] == ["апноэ", "астма"]
                assert wire.count('"web_search"') == 1
            async with client.get(server.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                assert (await response.json())["remaining"] == 29
    asyncio.run(run())


@pytest.mark.parametrize("omission", ["state", "citation", "citation_bounds", "regimen", "unspecified", "result_rate", "measurement_schedule", "assurance"])
@pytest.mark.parametrize("recover", [True, False])
def test_missing_conditions_or_source_support_are_repaired_privately_once(monkeypatch, tmp_path, omission, recover):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        question = "У меня гестамин эпное и астма. Как похудеть?"
        incomplete = {"state":"Питание и прогулки [1].", "citation":"При астме и апноэ начните с питания и прогулок.",
            "citation_bounds":"При астме и апноэ начните с питания и прогулок [9].",
            "regimen":"При астме и апноэ создайте дефицит 1000 ккал и стремитесь к 90 минутам нагрузки [1].",
            "result_rate":"При астме и апноэ реалистично потерять 1–2 фунта в неделю [1].",
            "measurement_schedule":"При астме и апноэ взвешивайтесь 1 раз в неделю [1].",
            "assurance":"При астме и апноэ можно похудеть без риска [1].",
            "unspecified":"При астме и апноэ начните постепенно [1]. Если есть реакция на гистамин, исключите продукты с высоким содержанием гистамина."}[omission]
        final = "При астме и апноэ начните с регулярного питания и спокойных прогулок [1]."
        intent = {"action":"search", "quote":"гестамин эпное", "candidate":"гистамин, апноэ", "query":"weight loss",
            "source_scope":"official_health", "context":[{"quote":"гестамин", "kind":"substance", "status":"unspecified"}, {"quote":"эпное", "kind":"condition", "status":"stated"},
                {"quote":"астма", "kind":"condition", "status":"stated"}]}
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"),
                intent=intent, search_response={"results":[{"title":"Weight advice", "url":"https://www.nhs.uk/weight", "content":"Eat well. Gradual activity."}]},
                model_content="PRIVATE_DRAFT", review_contents=[incomplete, final if recover else incomplete]) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={**BODY, "messages":[{"role":"user", "content":question}]}) as response:
                wire = await response.text()
            assert len(state["review_payloads"]) == 2
            assert len(state["intent_payloads"]) == len(state["payloads"]) == len(state["search_queries"]) == 1
            assert "PRIVATE_DRAFT" not in wire and incomplete not in wire
            repair = json.loads(state["review_payloads"][1]["messages"][-1]["content"])
            original = json.loads(state["review_payloads"][0]["messages"][-1]["content"])
            assert repair["question"] == original["question"] == question.replace("гестамин эпное", "гистамин, апноэ")
            assert repair["user_context"] == original["user_context"]
            assert state["review_payloads"][0]["messages"][:-1] == state["review_payloads"][1]["messages"][:-1]
            assert "unsupported_context_advice" in repair["repair"]["instruction"]
            assert "включая условные" in repair["repair"]["instruction"]
            assert (final in wire and "[DONE]" in wire) if recover else ('"error"' in wire and "[DONE]" not in wire)
            async with client.get(server.make_url("/web-api/v1/guest"), headers=guest_headers(cookie)) as response:
                assert (await response.json())["remaining"] == 29
    asyncio.run(run())


def test_coverage_allows_case_endings_for_named_conditions():
    data = {"required_context_mentions":[{"quote":"астма"}, {"quote":"апноэ"}], "sources":[]}
    assert review_omissions("При астме и апноэ начните постепенно.", data) == {
        "missing_stated_terms":[], "invalid_citations":False, "new_personal_regimens":[], "unsupported_context_advice":[],
        "unsupported_safety_assurances":[]}


@pytest.mark.parametrize("ending,blocked", [
    ("быстро похудеть без риска для здоровья не получится.", False),
    ("похудеть без риска не удастся.", False),
    ("похудеть без риска возможно.", True),
    ("без риска похудеть не получится. Этот план поможет без риска.", True),
])
def test_health_review_handles_negation_after_a_risk_phrase(ending, blocked):
    data = {"question":"Как похудеть?", "required_context_mentions":[], "sources":[],
        "avoid_new_numeric_regimens":True}
    assert bool(review_omissions(ending, data)["unsupported_safety_assurances"]) is blocked


@pytest.mark.parametrize("text,blocked", [
    ("В питании сократите порции, добавьте овощи. Гистамин не подтверждает реакцию или непереносимость, поэтому без данных о симптомах я не даю индивидуальных запретов.", False),
    ("Гистамин не подтверждает аллергию. Исключите продукты с высоким содержанием гистамина.", True),
    ("Гистамин не подтверждает аллергию, но исключите эти продукты.", True),
    ("Гистамин может вызывать реакцию. Исключите эти продукты.", True),
])
def test_health_review_separates_a_limit_explanation_from_unrelated_preceding_advice(text, blocked):
    data = {"question":"Гистамин", "required_context_mentions":[], "sources":[],
        "user_context":[{"quote":"гистамин", "kind":"substance", "status":"unspecified"}]}
    assert bool(review_omissions(text, data)["unsupported_context_advice"]) is blocked


@pytest.mark.parametrize("quote,text", [
    ("гистамин", "Если есть реакция на гистамин, временно исключите продукты с высоким содержанием гистамина."),
    ("калий", "При повышенном калии ограничьте эти продукты."),
    ("potassium", "If potassium is high, avoid these foods.")])
def test_unspecified_substances_do_not_authorize_even_conditional_personal_advice(quote, text):
    data = {"required_context_mentions":[], "sources":[],
        "user_context":[{"quote":quote, "kind":"substance", "status":"unspecified"}]}
    assert review_omissions(text, data)["unsupported_context_advice"] == [quote]
    data["user_context"][0]["status"] = "stated"
    assert review_omissions(text, data)["unsupported_context_advice"] == []


@pytest.mark.parametrize("text", ["Начните с указанного вами числа 30 [1].", "Начните с указанного вами числа 12.5 [1].", "Начните с указанного вами числа 0,5 [1].", "Запрошенный расчёт: 17 × 23 = 391 [1].", "Справочная публикация содержит 500 участников [1]."])
def test_numeric_review_preserves_user_numbers_calculations_and_reference_facts(text):
    data = {"question":"У меня астма. Сохрани 30, 12,5 и 0.5, вычисли 17 * 23.", "required_context_mentions":[],
        "sources":[{}], "avoid_new_numeric_regimens":True}
    assert review_omissions(text, data)["new_personal_regimens"] == []


@pytest.mark.parametrize("text,blocked", [
    ("Этот план поможет без риска.", True),
    ("Это без какого-либо риска для вас.", True),
    ("This plan is risk-free.", True),
    ("Нельзя обещать похудение без риска.", False),
    ("Похудение без риска невозможно гарантировать.", False),
    ("Не обещаю, что это без какого-либо риска.", False),
    ("We cannot guarantee a risk-free plan.", False),
    ("This plan is not risk-free.", False),
])
def test_health_review_rejects_safety_assurances_but_preserves_negated_claims(text, blocked):
    data = {"question": "У меня астма.", "required_context_mentions": [], "sources": [],
        "avoid_new_numeric_regimens": True}
    assert bool(review_omissions(text, data)["unsupported_safety_assurances"]) is blocked
    data["avoid_new_numeric_regimens"] = False
    assert review_omissions(text, data)["unsupported_safety_assurances"] == []


@pytest.mark.parametrize("invalid", [None, "out_of_bounds", "boolean", "duplicate", "extra_field", "empty_text"])
def test_structured_editor_sources_are_validated_and_rendered_next_to_their_paragraph(monkeypatch, tmp_path, invalid):
    async def run():
        monkeypatch.setenv("VELIA_WEB_GUEST_ENABLED", "true")
        args = {"paragraphs":[{"text":"Вы указали астму и апноэ.", "source_ids":[]},
            {"text":"Питание и прогулки.", "source_ids":[1]}]}
        if invalid == "out_of_bounds":
            args["paragraphs"][1]["source_ids"] = [9]
        elif invalid == "boolean":
            args["paragraphs"][1]["source_ids"] = [True]
        elif invalid == "duplicate":
            args["paragraphs"][1]["source_ids"] = [1, 1]
        elif invalid == "extra_field":
            args["url"] = "https://unverified.example"
        elif invalid == "empty_text":
            args["paragraphs"][1]["text"] = "  "
        intent = {"action":"search", "quote":"", "candidate":"", "query":"weight loss", "source_scope":"official_health",
            "context":[{"quote":"астма", "kind":"condition", "status":"stated"}, {"quote":"апноэ", "kind":"condition", "status":"stated"}]}
        async with fixture(monkeypatch, with_search=True, guest_store=GuestStore(sqlite_path=tmp_path/"quota.db"), intent=intent,
                search_response={"results":[{"title":"Weight advice", "url":"https://www.nhs.uk/weight", "content":"Eat well. Gradual activity."}]},
                model_content="PRIVATE_DRAFT", review_tool_args=args) as (server, client, state):
            cookie, _, _ = await guest(server, client)
            async with client.post(server.make_url("/web-api/v1/guest/chat/completions"), headers=guest_headers(cookie),
                    json={**BODY, "messages":[{"role":"user", "content":"У меня астма и апноэ. Как похудеть?"}]}) as response:
                wire = await response.text()
            assert len(state["review_payloads"]) == (1 if invalid is None else 2)
            assert state["review_payloads"][0]["tools"][0]["function"]["name"] == "publish_reviewed_answer"
            assert all(value not in wire for value in ("PRIVATE_DRAFT", "PRIVATE_REVIEW_WRAPPER", "publish_reviewed_answer", "unverified.example"))
            if invalid is None:
                assert 'Вы указали астму и апноэ.\\n\\nПитание и прогулки. [1]' in wire and "[DONE]" in wire
            else:
                assert '"error"' in wire and "[DONE]" not in wire and "Питание и прогулки" not in wire
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
