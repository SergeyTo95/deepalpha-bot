"""Transport regression checks; real model quality is evaluated separately."""
import asyncio
import copy
import json

import pytest

from tests.test_velia_web_chat import fixture, headers, login
from velia_desktop_routes import validate_payload
from velia_request_understanding import REQUEST_UNDERSTANDING


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
