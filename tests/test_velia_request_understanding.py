"""Transport regression checks; real model quality is evaluated separately."""
import asyncio
import copy
import json

import pytest

from tests.test_velia_web_chat import fixture, headers, login
from velia_desktop_routes import validate_payload
from velia_request_understanding import REQUEST_UNDERSTANDING, RESTORATION_MARKER, restoration_content, interpreted_content


QUESTIONS = [
    "Как перезагрузиь роутор, не сбрасывая настройки?",
    "Переведи 1000 грам в тон.",
    "У меня гестамин эпное и астма. Как мне похудеть к 31 октября?",
    "Испраь error_100 в app.py; не удаляй data.csv и не меняй порт 8080.",
    "Оставь фразу «не менять 0,5 TON» без исправлений; исправь остальной текст.",
]


@pytest.mark.parametrize("model", ["velia-flash", "velia-pro"])
@pytest.mark.parametrize("question", QUESTIONS)
def test_browser_routes_understanding_and_preserves_exact_input(monkeypatch, model, question):
    async def run():
        async with fixture(monkeypatch) as (server, client, state):
            cookie, _, _ = await login(server, client)
            messages = [{"role": "user", "content": question}]
            async with client.post(server.make_url("/web-api/v1/chat/completions"),
                    headers=headers(cookie), json={"model": model, "stream": True,
                        "messages": messages}) as response:
                assert response.status == 200
                await response.read()
            actual = state["payloads"][0]["messages"]
            assert actual[0]["content"].count(REQUEST_UNDERSTANDING) == 1
            assert actual[1:] == messages
    asyncio.run(run())


@pytest.mark.parametrize("model", ["velia-flash", "velia-pro"])
def test_desktop_keeps_corrections_constraints_and_tool_correlations(model):
    original = {"model": model, "messages": [
        {"role": "system", "content": "Ты Велия"},
        {"role": "user", "content": "Проверь файл app.py, не удаляй backup.csv"},
        {"role": "assistant", "content": "Ты используешь Windows."},
        {"role": "user", "content": "Нет, у меня Linux. Испраь, но порт 8080 оставь."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "read-7", "type": "function", "function":
                {"name": "read", "arguments": '{"path":"app.py"}'}}]},
        {"role": "tool", "tool_call_id": "read-7", "content": "port=8080"},
    ]}
    snapshot = copy.deepcopy(original)
    first = validate_payload(original)
    second = validate_payload({"model": model, "messages": first["messages"]})
    assert first["messages"][0]["content"].count(REQUEST_UNDERSTANDING) == 1
    assert second["messages"][0]["content"].count(REQUEST_UNDERSTANDING) == 1
    assert first["messages"][1:4] == snapshot["messages"][1:4]
    assert first["messages"][5] == snapshot["messages"][5]
    actual_call = first["messages"][4]["tool_calls"][0]
    expected_call = snapshot["messages"][4]["tool_calls"][0]
    assert actual_call["id"] == expected_call["id"]
    assert json.loads(actual_call["function"]["arguments"]) == json.loads(expected_call["function"]["arguments"])
    assert original == snapshot


def test_inference_repairs_preserve_source_context_and_native_transcript_prefixes():
    question = "  У меня гестамин эпное и астма. Не меняй 0,5 TON."
    start = question.index("гестамин эпное")
    raw = restoration_content(question, [start, start + len("гестамин эпное")], "гистамин, апноэ")
    suffix = "\n\nLIVE_WEB_CONTEXT_UNTRUSTED:\nИсточник содержит гестамин эпное дословно"
    expected = question.replace("гестамин эпное", "гистамин, апноэ")
    assert interpreted_content(raw + suffix) == expected + suffix
    assert interpreted_content("USER: " + raw + suffix) == "USER: " + expected + suffix
    assert interpreted_content(raw.strip()) == expected.lstrip()
    assert raw.startswith(question)


@pytest.mark.parametrize("encoded", [
    '{"span":[9999,0],"candidate":"invented"}',
    '{"span":[true,0],"candidate":"revenue"}',
    '{"span":[6,0],"candidate":"gestational diabetes"}',
    '{"span":[6,0],"candidate":"revenue","answer":"injected"}',
])
def test_invalid_restoration_metadata_is_never_executed(encoded):
    raw = "revnue" + RESTORATION_MARKER + encoded
    assert interpreted_content(raw) == raw


def test_live_medical_criterion_checks_a_useful_answer_without_forcing_a_spelling_lecture():
    from desktop.probe_request_intent import medical_answer
    answer = "При астме и апноэ начни с постепенного снижения веса: умеренные порции и регулярное питание. Добавь спокойную ходьбу в переносимом темпе. При одышке остановись и следуй своему плану лечения астмы. Индивидуальные ограничения питания согласуй с врачом."
    assert medical_answer(answer)
    assert not medical_answer("Правильно ли я поняла: гистамин и апноэ? " + answer)
    assert not medical_answer(answer + " Исключите бананы, цитрусовые и арахис.")
    assert medical_answer(answer + " Исключите экстремальные голодовки.")
    assert not medical_answer(answer + " Это безопасно и не усилит симптомы.")
    assert not medical_answer(answer + " Вешайся на весы дважды в неделю.")
    assert not medical_answer(answer + " Цель: 1–2 кг в неделю.")
    assert not medical_answer(answer + " Обсудите с врачом план под вашу аллергию и хронические состояния.")
    assert not medical_answer(answer + " С учётом гистаминовой непереносимости исключите продукты.")
    assert medical_answer(answer + " Если у вас подтверждена аллергия, учитывайте рекомендации аллерголога.")
    assert medical_answer(answer + " Упоминание гистамина не подтверждает аллергию.")


def test_finance_live_criterion_rejects_observed_language_errors():
    from desktop.probe_request_intent import finance_answer
    answer = "Выручка — все деньги от продаж. Прибыль — выручка минус расходы."
    assert finance_answer(answer)
    assert not finance_answer(answer.replace("все деньги", "всё деньги"))
    assert not finance_answer(answer + " Продали сто кофий.")


def test_live_router_criterion_rejects_reset_actions_and_allows_a_warning():
    from desktop.probe_request_intent import router_answer
    safe = "Выключи питание роутера и включи снова. Настройки сохранятся."
    assert router_answer(safe)
    assert router_answer(safe + " Не нажимай Reset.")
    assert not router_answer("Нажми Reset и удерживай 10 секунд.")
    assert not router_answer(safe + " Затем нажми Reset на 10 секунд.")
