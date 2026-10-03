import pytest
from velia_desktop_routes import validate_payload
import asyncio
import json
from types import SimpleNamespace
from aiohttp import web
from velia_desktop_routes import setup_velia_desktop_routes


def test_tool_round_trip_retains_tool_call_ids(monkeypatch):
    monkeypatch.setenv('VELIA_DESKTOP_PRO_MODEL', 'test-model')
    messages = [
        {'role': 'user', 'content': 'Read file'},
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call_1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'file text'},
    ]
    result = validate_payload({'model': 'velia-pro', 'messages': messages, 'stream': True,
        'tools': [{'type': 'function', 'function': {'name': 'read', 'parameters': {'type': 'object'}}}]})
    assert result['messages'] == messages
    assert result['stream'] is True
    assert result['model'] == 'test-model'
    assert result['tools'][0]['function']['name'] == 'read'


def test_accepts_harness_text_content_blocks():
    result = validate_payload({'model': 'velia-pro', 'messages': [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Read file'},
                                    {'type': 'text', 'text': 'Then summarize'}]}]})
    assert result['messages'][0]['content'] == 'Read file\nThen summarize'


@pytest.mark.parametrize('change', [
    {'model': 'provider-arbitrary'}, {'max_tokens': 999999}, {'max_tokens': True},
    {'stream': 'true'}, {'messages': []},
    {'messages': [{'role': 'user', 'content': [{'type': 'image_url'}]}]},
    {'tools': [{'type': 'shell'}]}, {'tool_choice': {'function': 'arbitrary'}},
])
def test_rejects_unbounded_or_unsupported_input(change):
    with pytest.raises(ValueError):
        validate_payload({'model': 'velia-pro', 'messages': [{'role': 'user', 'content': 'hi'}], **change})


def handlers(authenticate):
    app = web.Application()
    setup_velia_desktop_routes(app, authenticate)
    return {route.resource.canonical + ':' + route.method: route.handler for route in app.router.routes()}


@pytest.mark.parametrize('enabled,token,allowed,expected', [
    ('false', 'va_test', '7', 503), ('true', '', '7', 401),
    ('true', 'invalid', '7', 401), ('true', 'va_test', '', 403),
    ('true', 'va_test', '8', 403), ('true', 'va_test', '7', 200),
])
def test_model_catalog_requires_opt_in_authenticated_allowlisted_user(monkeypatch, enabled, token, allowed, expected):
    monkeypatch.setenv('VELIA_DESKTOP_API_ENABLED', enabled)
    monkeypatch.setenv('VELIA_DESKTOP_PREVIEW_USER_IDS', allowed)
    route = handlers(lambda value: {'user_id': 7} if value == 'va_test' else None)
    request = SimpleNamespace(headers={'Authorization': 'Bearer ' + token})
    result = asyncio.run(route['/desktop-api/v1/models:GET'](request))
    assert result.status == expected
    if expected == 200:
        assert json.loads(result.text)['data'][0]['id'] == 'velia-pro'


def test_invalid_body_never_reaches_model_provider(monkeypatch):
    monkeypatch.setenv('VELIA_DESKTOP_API_ENABLED', 'true')
    monkeypatch.setenv('VELIA_DESKTOP_PREVIEW_USER_IDS', '7')
    class Content:
        async def iter_chunked(self, size):
            yield b'{bad json'
    route = handlers(lambda _: {'user_id': 7})
    result = asyncio.run(route['/desktop-api/v1/chat/completions:POST'](
        SimpleNamespace(headers={'Authorization': 'Bearer va_test'}, content=Content())))
    assert result.status == 400
    assert json.loads(result.text)['error']['message'] == 'invalid_json'
