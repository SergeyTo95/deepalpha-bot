import pytest
from velia_desktop_routes import validate_payload
import asyncio
import json
from types import SimpleNamespace
from aiohttp import web
from velia_desktop_routes import setup_velia_desktop_routes
from velia_request_understanding import REQUEST_UNDERSTANDING


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
    assert result['messages'][0] == {'role': 'system', 'content': REQUEST_UNDERSTANDING}
    assert result['messages'][1:] == messages
    assert result['stream'] is True
    assert result['model'] == 'test-model'
    assert result['tools'][0]['function']['name'] == 'read'


def test_accepts_harness_text_content_blocks():
    result = validate_payload({'model': 'velia-pro', 'messages': [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Read file'},
                                    {'type': 'text', 'text': 'Then summarize'}]}]})
    assert result['messages'][1]['content'] == 'Read file\nThen summarize'


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


def test_streaming_and_tool_round_trip_through_http_gateway(monkeypatch):
    async def scenario():
        from aiohttp import ClientSession
        from aiohttp.test_utils import TestServer
        received = []
        async def provider(request):
            assert request.headers['Authorization'] == 'Bearer provider-secret'
            payload = await request.json()
            received.append(payload)
            if payload['stream']:
                response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await response.prepare(request)
                for fragment in [b'data: {"choices":[{"delta":{"content":"Hello"}}]}', b'\n\ndata: [DONE]\n\n']:
                    await response.write(fragment)
                await response.write_eof()
                return response
            return web.json_response({'model': 'internal-provider', 'choices': [{'message': {'role': 'assistant', 'content': 'Read file'}}]})
        upstream = web.Application()
        upstream.router.add_post('/v1/chat/completions', provider)
        async with TestServer(upstream) as provider_server:
            monkeypatch.setenv('VELIA_DESKTOP_API_ENABLED', 'true')
            monkeypatch.setenv('VELIA_DESKTOP_PREVIEW_USER_IDS', '7')
            monkeypatch.setenv('KIMI_API_KEY', 'provider-secret')
            monkeypatch.setenv('KIMI_BASE_URL', str(provider_server.make_url('/v1')))
            gateway = web.Application()
            setup_velia_desktop_routes(gateway, lambda token: {'user_id': 7} if token == 'va_test' else None)
            async with TestServer(gateway) as server, ClientSession() as client:
                endpoint = server.make_url('/desktop-api/v1/chat/completions')
                headers = {'Authorization': 'Bearer va_test'}
                payload = {'model': 'velia-pro', 'messages': [{'role': 'user', 'content': 'Read file'}], 'stream': True}
                async with client.post(endpoint, headers=headers, json=payload) as response:
                    assert response.status == 200
                    assert response.headers['Content-Type'].startswith('text/event-stream')
                    assert '[DONE]' in await response.text()
                payload.update(stream=False, messages=[
                    {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
                    {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'actual file text'},
                ])
                async with client.post(endpoint, headers=headers, json=payload) as response:
                    result = await response.json()
                    assert response.status == 200
                    assert result['model'] == 'velia-pro'
                assert received[-1]['messages'][2]['tool_call_id'] == 'call_1'
                assert received[-1]['messages'][2]['content'] == 'actual file text'
    asyncio.run(scenario())


def test_gateway_does_not_forward_provider_secret_to_redirect(monkeypatch):
    async def scenario():
        from aiohttp import ClientSession
        from aiohttp.test_utils import TestServer
        contacted = []
        async def target(request):
            contacted.append(request.headers.get('Authorization'))
            return web.json_response({'choices': []})
        destination = web.Application()
        destination.router.add_route('*', '/target', target)
        async with TestServer(destination) as redirect_server:
            async def provider(request):
                raise web.HTTPTemporaryRedirect(location=str(redirect_server.make_url('/target')))
            upstream = web.Application()
            upstream.router.add_post('/v1/chat/completions', provider)
            async with TestServer(upstream) as provider_server:
                monkeypatch.setenv('VELIA_DESKTOP_API_ENABLED', 'true')
                monkeypatch.setenv('VELIA_DESKTOP_PREVIEW_USER_IDS', '7')
                monkeypatch.setenv('KIMI_API_KEY', 'provider-secret')
                monkeypatch.setenv('KIMI_BASE_URL', str(provider_server.make_url('/v1')))
                gateway = web.Application()
                setup_velia_desktop_routes(gateway, lambda _: {'user_id': 7})
                async with TestServer(gateway) as server, ClientSession() as client:
                    async with client.post(server.make_url('/desktop-api/v1/chat/completions'), headers={'Authorization': 'Bearer va_test'},
                            json={'model': 'velia-pro', 'messages': [{'role': 'user', 'content': 'Hello'}]}) as response:
                        assert response.status == 502
                        assert (await response.json())['error']['message'] == 'model_request_failed'
                assert contacted == []
    asyncio.run(scenario())
