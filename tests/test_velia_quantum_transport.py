import asyncio
import json
from types import SimpleNamespace

import pytest

import velia_quantum_transport as transport
from velia_quantum_provider import QuantumConfig, QuantumError, ZAI_URL, ZAI_FREE_MODEL
from velia_quantum_turn import stream_turn


def wire(text="Ready"):
    return ("data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]})
        + "\n\ndata: [DONE]\n\n").encode()


class Response:
    def __init__(self, status=200, code="1305", *, body=None, retry_after=None):
        self.status = self.status_code = status
        self.body = body if body is not None else (wire() if status == 200 else
            json.dumps({"error": {"code": code, "message": "secret account details"}}).encode())
        self.headers = {"Content-Type": "text/event-stream"}
        if retry_after is not None:
            self.headers["Retry-After"] = retry_after
        self.closed = False
        self.content = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    def iter_content(self, _size):
        yield self.body

    async def iter_chunked(self, _size):
        yield self.body


class Client:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.responses.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture
def setup(monkeypatch):
    transport._CIRCUITS.clear()
    clock, waits = [100.0], []
    def sleep(delay):
        waits.append(delay)
        clock[0] += delay
    async def asleep(delay):
        sleep(delay)
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(transport.asyncio, "sleep", asleep)
    monkeypatch.setattr(transport.random, "uniform", lambda _a, _b: 1.5)
    yield QuantumConfig("zai", ZAI_URL, ZAI_FREE_MODEL, "private-test-key"), clock, waits
    transport._CIRCUITS.clear()


def request(kind, client, config, *, retry=True, consume=None, backup=None, on_route=None):
    body = {"model": config.model, "messages": [{"role": "user", "content": "private prompt"}]}
    if kind == "sync":
        with transport.completion(client, config, body, timeout=(3, 15), retry_overload=retry, backup=backup, on_route=on_route) as response:
            if consume:
                consume(response)
    else:
        async def run():
            async with transport.completion_async(client, config, body, retry_overload=retry, backup=backup, on_route=on_route) as response:
                if consume:
                    consume(response)
        asyncio.run(run())


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_one_overload_then_success_closes_response_before_wait(setup, monkeypatch, kind, caplog):
    config, clock, waits = setup
    rejected, accepted = Response(429), Response()
    client = Client(rejected, accepted)
    # Response closure is checked at sleep time, after computing the delay.
    def sleeping(delay):
        assert rejected.closed and not accepted.closed
        waits.append(delay)
        clock[0] += delay
    async def asleep(delay):
        sleeping(delay)
    monkeypatch.setattr(transport.time, "sleep", sleeping)
    monkeypatch.setattr(transport.asyncio, "sleep", asleep)
    request(kind, client, config)
    assert len(client.calls) == 2 and waits == [1.5] and accepted.closed
    assert client.calls[0] == client.calls[1]
    assert client.calls[0][1]["headers"]["User-Agent"] == "VELIA-Quantum/1.0"
    assert client.calls[0][1]["allow_redirects"] is False
    assert "secret" not in caplog.text and "private" not in caplog.text


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_double_overload_blocks_following_calls_then_recovers(setup, kind):
    config, clock, waits = setup
    client = Client(Response(429), Response(429), Response())
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        request(kind, client, config)
    assert len(client.calls) == 2 and waits == [1.5]
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        request(kind, client, config)
    assert len(client.calls) == 2
    clock[0] += 31
    request(kind, client, config)
    assert len(client.calls) == 3 and not transport._CIRCUITS


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("status,code", [(429, "1302"), (429, "1113"), (429, "1308"),
    (401, "1000"), (500, "1305"), (429, "unknown"), (402, "card_required")])
def test_other_failures_are_never_retried_or_open_overload_circuit(setup, kind, status, code):
    config, _clock, waits = setup
    client = Client(Response(status, code), Response())
    with pytest.raises(QuantumError):
        request(kind, client, config)
    assert len(client.calls) == 1 and waits == [] and not transport._CIRCUITS


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("retry_after", ["10", "-1", "NaN", "inf", "Wed, 28 Oct 2026 12:00:00 GMT"])
def test_retry_after_is_never_ignored_for_an_early_retry(setup, kind, retry_after):
    config, _clock, waits = setup
    client = Client(Response(429, retry_after=retry_after), Response())
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        request(kind, client, config)
    assert len(client.calls) == 1 and waits == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_no_retry_after_a_tool_or_stream_started(setup, kind):
    config, _clock, waits = setup
    client = Client(Response(429), Response())
    with pytest.raises(QuantumError):
        request(kind, client, config, retry=False)
    assert len(client.calls) == 1 and not waits
    client = Client(Response(), Response())
    def failed_stream(_response):
        raise QuantumError("quantum_overloaded")
    with pytest.raises(QuantumError):
        request(kind, client, config, consume=failed_stream)
    assert len(client.calls) == 1 and not waits


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_network_failure_is_never_replayed(setup, kind):
    config, _clock, waits = setup
    client = Client(OSError("private network failure"), Response())
    with pytest.raises(OSError):
        request(kind, client, config)
    assert len(client.calls) == 1 and not waits


def test_cooldown_does_not_affect_a_rotated_key(setup):
    config, _clock, waits = setup
    with pytest.raises(QuantumError):
        request("sync", Client(Response(429), Response(429)), config)
    rotated = QuantumConfig("zai", ZAI_URL, ZAI_FREE_MODEL, "rotated-key")
    client = Client(Response())
    request("sync", client, rotated)
    assert len(client.calls) == 1


def test_browser_native_turn_retries_before_emitting_text_only(setup):
    config, _clock, waits = setup
    client = Client(Response(429), Response(body=wire("こんにちは")))
    async def run():
        return [event async for event in stream_turn(client, config, [{"role": "user", "content": "hello"}])]
    assert asyncio.run(run()) == [{"text": "こんにちは"}]
    assert len(client.calls) == 2 and waits == [1.5]


def test_browser_native_tool_continuation_is_not_replayed(setup):
    config, _clock, waits = setup
    tool = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
        "function": {"name": "web_search", "arguments": '{"query":"Python","scope":"general"}'}}]},
        "finish_reason": "tool_calls"}]}
    client = Client(Response(body=("data: " + json.dumps(tool) + "\n\ndata: [DONE]\n\n").encode()), Response(429), Response())
    searches = []
    async def search(query, **kwargs):
        searches.append(query)
        return {"results": [{"title": "Docs", "url": "https://python.org", "snippet": "Public"}]}
    async def run():
        return [event async for event in stream_turn(client, config, [{"role": "user", "content": "search"}], search=search)]
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        asyncio.run(run())
    assert len(client.calls) == 2 and searches == ["Python"] and not waits


@pytest.fixture
def groq(monkeypatch):
    from velia_quantum_provider import groq_backup_configuration
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('GROQ_API_KEY', 'groq-synthetic-secret')
    return groq_backup_configuration()


@pytest.mark.parametrize('kind', ['sync', 'async'])
@pytest.mark.parametrize('code', ['1302', '1305'])
def test_capacity_failure_uses_one_pinned_groq_route_with_its_own_key(setup, groq, kind, code):
    config, _clock, waits = setup
    replies = [Response(429, code)] * (2 if code == '1305' else 1) + [Response()]
    client, routes = Client(*replies), []
    request(kind, client, config, backup=groq, on_route=routes.append)
    assert routes == [groq] and len(client.calls) == (3 if code == '1305' else 2)
    assert len(waits) == int(code == '1305')
    url, kwargs = client.calls[-1]
    assert url == 'https://api.groq.com/openai/v1/chat/completions'
    assert kwargs['headers']['Authorization'] == 'Bearer groq-synthetic-secret'
    assert client.calls[0][1]['headers']['Authorization'] == 'Bearer private-test-key'
    body = kwargs['json']
    assert body['model'] == 'openai/gpt-oss-120b'
    assert body['messages'] == client.calls[0][1]['json']['messages']
    assert body['include_reasoning'] is False and body['reasoning_effort'] == 'low'
    assert 'thinking' not in body and 'tool_stream' not in body and 'reasoning_format' not in body
    assert 'gateway' not in body and 'max_tokens' not in body and body['max_completion_tokens'] == 2048


@pytest.mark.parametrize('kind', ['sync', 'async'])
@pytest.mark.parametrize('status,code', [(401, '1000'), (429, '1113'), (429, '1308'), (429, '1313'), (500, '1305')])
def test_auth_quota_fair_use_and_wrong_http_status_never_trigger_backup(setup, groq, kind, status, code):
    config, _clock, _waits = setup
    client = Client(Response(status, code), Response())
    with pytest.raises(QuantumError):
        request(kind, client, config, backup=groq)
    assert len(client.calls) == 1


@pytest.mark.parametrize('kind', ['sync', 'async'])
def test_started_stream_does_not_fall_back_even_if_error_has_capacity_code(setup, groq, kind):
    config, _clock, waits = setup
    client = Client(Response(), Response())
    def failed(_response):
        error = QuantumError('quantum_overloaded')
        error.before_response = True
        raise error
    with pytest.raises(QuantumError):
        request(kind, client, config, backup=groq, consume=failed)
    assert len(client.calls) == 1 and not waits


@pytest.mark.parametrize('kind', ['sync', 'async'])
def test_groq_error_stops_without_another_route_or_retry(setup, groq, kind):
    config, _clock, _waits = setup
    client = Client(Response(429, '1302'), Response(429, 'rate_limit_exceeded'), Response())
    with pytest.raises(QuantumError):
        request(kind, client, config, backup=groq)
    assert len(client.calls) == 2


def test_cooldown_skips_zai_and_reaches_backup_without_wait(setup, groq):
    config, _clock, waits = setup
    with pytest.raises(QuantumError):
        request('sync', Client(Response(429), Response(429)), config)
    waits.clear()
    client = Client(Response())
    request('sync', client, config, backup=groq)
    assert len(client.calls) == 1 and client.calls[0][0].startswith(groq.base_url) and not waits


def test_groq_configuration_is_opt_in_and_never_reuses_another_key(monkeypatch):
    from velia_quantum_provider import groq_backup_configuration, payload
    monkeypatch.delenv('VELIA_QUANTUM_GROQ_BACKUP_ENABLED', raising=False)
    monkeypatch.setenv('GROQ_API_KEY', 'dedicated-groq-key')
    assert groq_backup_configuration() is None
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_BACKUP_ENABLED', 'true')
    config = groq_backup_configuration()
    assert config.api_key == 'dedicated-groq-key' and 'dedicated-groq-key' not in repr(config)
    with pytest.raises(QuantumError):
        payload(config, [{'role':'user','content':'test'}], tools=[{'type':'browser_search'}])
    monkeypatch.delenv('GROQ_API_KEY')
    monkeypatch.setenv('VELIA_QUANTUM_API_KEY', 'zai-must-not-be-sent')
    monkeypatch.setenv('EXPLABS_API_KEY', 'aggregator-must-not-be-sent')
    assert groq_backup_configuration() is None


def test_browser_backup_keeps_the_groq_route_for_search_continuation(setup, groq):
    config, _clock, _waits = setup
    tool = {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function',
        'function':{'name':'web_search','arguments':'{"query":"Python","scope":"general"}'}}]},'finish_reason':'tool_calls'}]}
    client = Client(Response(429, '1302'), Response(body=('data: '+json.dumps(tool)+'\n\ndata: [DONE]\n\n').encode()), Response(body=wire('Готова. [1]')))
    searches = []
    async def search(query, **kwargs):
        searches.append(query)
        return {'results':[{'title':'Docs','url':'https://python.org','snippet':'Public'}]}
    async def run():
        return [event async for event in stream_turn(client, config, [{'role':'user','content':'Найди Python'}], search=search)]
    events = asyncio.run(run())
    assert 'Готова.' in ''.join(e.get('text','') for e in events)
    assert len(client.calls) == 3 and searches == ['Python']
    assert all(call[0].startswith(groq.base_url) for call in client.calls[1:])
    first, continuation = client.calls[1][1]['json'], client.calls[2][1]['json']
    assert first['tools'][0]['function']['name'] == 'web_search' and 'tools' not in continuation
    assert continuation['messages'][-1]['role'] == 'tool'
    assert all('thinking' not in b and 'tool_stream' not in b for b in [first, continuation])


@pytest.mark.parametrize('failure', [None, 'bad_key', 'missing_model', 'rate_limit', 'bad_stream', 'missing_done'])
def test_groq_acceptance_qualifies_key_tool_stream_without_output_leaks(monkeypatch, capsys, failure):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    import velia_quantum_provider as provider
    import velia_quantum_groq_probe as acceptance
    seen = []
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('GROQ_API_KEY', 'groq-probe-fixture-secret')
    async def run():
        async def models(request):
            seen.append(('GET', None))
            assert request.headers['Authorization'] == 'Bearer groq-probe-fixture-secret'
            if failure == 'bad_key':
                return web.json_response({'error':'private account'}, status=401)
            return web.json_response({'data': [] if failure == 'missing_model' else [{'id':provider.GROQ_MODEL}]})
        async def completion(request):
            body = await request.json()
            seen.append(('POST', body))
            assert body['model'] == provider.GROQ_MODEL and body['include_reasoning'] is False
            assert body['max_completion_tokens'] == 512 and 'thinking' not in body
            if failure == 'rate_limit':
                return web.json_response({'error':{'code':'rate_limit_exceeded','message':'private account'}},status=429)
            if failure == 'bad_stream':
                return web.Response(text='private output',content_type='text/plain')
            if body.get('tools'):
                assert body['tool_choice']['function']['name'] == 'web_search'
                tool={'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','type':'function',
                    'function':{'name':'web_search','arguments':'{"query":"VELIA synthetic test","scope":"general"}'}}]},'finish_reason':'tool_calls'}]}
                return web.Response(body=('data: '+json.dumps(tool)+'\n\n' + ('' if failure == 'missing_done' else 'data: [DONE]\n\n')).encode(),content_type='text/event-stream')
            assert body['messages'][-1]['role'] == 'tool'
            return web.Response(body=wire('private model output: тест пройден'),content_type='text/event-stream')
        app = web.Application()
        app.router.add_get('/openai/v1/models', models)
        app.router.add_post('/openai/v1/chat/completions', completion)
        async with TestServer(app) as server:
            monkeypatch.setattr(provider, 'GROQ_URL', str(server.make_url('/openai/v1')))
            assert await acceptance.probe() == int(failure is not None)
    asyncio.run(run())
    assert len(seen) == (3 if failure is None else 1 if failure in {'bad_key','missing_model'} else 2)
    output = capsys.readouterr().out
    assert ('GROQ_PROBE_OK' in output) is (failure is None)
    assert 'private' not in output and 'fixture-secret' not in output and 'api_key' not in output
