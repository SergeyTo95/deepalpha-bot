import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import requests
from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.test_utils import TestServer
import velia_quantum_provider as provider
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


def request(kind, client, config, *, retry=True, consume=None, backup=None, on_route=None, reserves=()):
    body = {"model": config.model, "messages": [{"role": "user", "content": "private prompt"}]}
    if kind == "sync":
        with transport.completion(client, config, body, timeout=(3, 15), retry_overload=retry, backup=backup, on_route=on_route, reserves=reserves) as response:
            if consume:
                consume(response)
    else:
        async def run():
            async with transport.completion_async(client, config, body, retry_overload=retry, backup=backup, on_route=on_route, reserves=reserves) as response:
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


@pytest.fixture
def voice_routes(monkeypatch):
    import velia_free_models as free
    monkeypatch.setenv('VELIA_FREE_API_TIERS_ENABLED','true')
    monkeypatch.setenv('VELIA_VOICE_RESERVES_ENABLED','true')
    monkeypatch.setenv('GROQ_API_KEY','groq-private-key')
    monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT','true')
    monkeypatch.setenv('VELIA_VOICE_CF_API_TOKEN','cf-private-key')
    monkeypatch.setenv('VELIA_VOICE_CF_ACCOUNT_ID','a'*32)
    monkeypatch.setenv('VELIA_VOICE_CF_FREE_ACCOUNT','true')
    monkeypatch.setenv('VELIA_VOICE_OPENROUTER_API_KEY','or-private-key')
    transport._VOICE_CIRCUITS.clear()
    yield free.voice_reserves()
    transport._VOICE_CIRCUITS.clear()


class GuardedClient(Client):
    def __init__(self,*replies,cap=0):
        super().__init__(*replies);self.cap=cap;self.guards=[]
    def get(self,url,**kwargs):
        self.guards.append((url,kwargs))
        return Response(body=json.dumps({'data':{'limit':self.cap}}).encode())


def test_voice_reserves_need_explicit_free_plan_and_ignore_untrusted_model_overrides(voice_routes,monkeypatch):
    import velia_free_models as free
    assert [r.model for r in voice_routes]==['openai/gpt-oss-20b','@cf/qwen/qwen3-30b-a3b-fp8','openrouter/free']
    monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT','false')
    monkeypatch.setenv('VELIA_VOICE_CF_FREE_ACCOUNT','false')
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_BACKUP_ENABLED','true')
    monkeypatch.setenv('VELIA_VOICE_GROQ_MODEL','paid-model')
    assert [r.provider for r in free.voice_reserves()]==['voice_openrouter']
    monkeypatch.delenv('VELIA_VOICE_OPENROUTER_API_KEY')
    assert free.voice_reserves()==()
    monkeypatch.setenv('VELIA_VOICE_RESERVES_ENABLED','false')
    assert free.voice_reserves()==()


@pytest.mark.parametrize('kind',['sync','async'])
def test_voice_rejected_quota_uses_bounded_chain_preserves_context_and_scopes_credentials(setup,voice_routes,kind):
    config,_,waits=setup
    client=GuardedClient(Response(429,'1308'),Response(429,'rate_limit_exceeded'),Response(503,'busy'),Response())
    selected=[]
    request(kind,client,config,reserves=voice_routes,on_route=selected.append)
    assert len(client.calls)==4 and not waits and selected==[voice_routes[-1]]
    bodies=[kwargs['json'] for _,kwargs in client.calls]
    assert all(body['messages']==bodies[0]['messages'] for body in bodies)
    assert bodies[1]['max_completion_tokens']==1024 and bodies[1]['reasoning_effort']=='low'
    assert bodies[-1]['model']=='openrouter/free'
    assert bodies[-1]['provider']['max_price']=={'prompt':0,'completion':0}
    for (url,kwargs),route in zip(client.calls,[config,*voice_routes]):
        assert url==route.base_url+'/chat/completions'
        assert kwargs['headers']['Authorization']=='Bearer '+route.api_key
        assert kwargs['allow_redirects'] is False
    assert len(client.guards)==1 and client.guards[0][0]=='https://openrouter.ai/api/v1/key'


@pytest.mark.parametrize('kind',['sync','async'])
@pytest.mark.parametrize('status,code',[(401,'auth'),(403,'auth'),(400,'bad_request'),(429,'1301')])
def test_voice_never_retries_auth_validation_or_content_rejections(setup,voice_routes,kind,status,code):
    config,_,_=setup;client=GuardedClient(Response(status,code),Response())
    with pytest.raises(QuantumError):request(kind,client,config,reserves=voice_routes)
    assert len(client.calls)==1


@pytest.mark.parametrize('kind',['sync','async'])
def test_voice_stream_failure_and_network_failure_never_restart_a_turn(setup,voice_routes,kind):
    config,_,_=setup
    def failed(_response):raise QuantumError('quantum_quota_exhausted',429)
    client=GuardedClient(Response(),Response())
    with pytest.raises(QuantumError):request(kind,client,config,reserves=voice_routes,consume=failed)
    assert len(client.calls)==1
    client=GuardedClient(OSError('network'),Response())
    with pytest.raises(OSError):request(kind,client,config,reserves=voice_routes)
    assert len(client.calls)==1


@pytest.mark.parametrize('kind',['sync','async'])
@pytest.mark.parametrize('cap',[None,True,1,-1])
def test_openrouter_voice_requires_a_numeric_hard_zero_spend_cap(setup,voice_routes,kind,cap):
    config,_,_=setup;client=GuardedClient(Response(429,'1308'),cap=cap)
    with pytest.raises(QuantumError,match='free_guard_required'):
        request(kind,client,config,reserves=(voice_routes[-1],))
    assert len(client.calls)==1 and len(client.guards)==1


@pytest.mark.parametrize('kind',['sync','async'])
def test_voice_cooldown_honors_retry_after_and_does_not_poison_other_keys(setup,voice_routes,kind):
    from dataclasses import replace
    config,clock,_=setup
    client=GuardedClient(Response(429,'1308',retry_after='120'),Response())
    request(kind,client,config,reserves=voice_routes[:1])
    clock[0]+=31
    client=GuardedClient(Response())
    request(kind,client,config,reserves=voice_routes[:1])
    assert client.calls[0][0]==voice_routes[0].base_url+'/chat/completions'
    client=GuardedClient(Response())
    request(kind,client,replace(config,api_key='other-user-key'),reserves=voice_routes[:1])
    assert client.calls[0][0]==config.base_url+'/chat/completions'


def test_voice_chain_rejects_tool_continuations_and_foreign_routes(setup,voice_routes):
    from dataclasses import replace
    config,_,_=setup
    with pytest.raises(QuantumError,match='free_guard_required'):
        transport._voice_candidates(config,voice_routes,{'messages':[{'role':'tool','content':'already executed'}]})
    with pytest.raises(QuantumError,match='free_guard_required'):
        transport._voice_candidates(config,[replace(voice_routes[0],base_url='https://attacker.example')],{'messages':[]})


# Voice admission deadlines must not truncate or replay accepted streams.


@pytest.fixture
def routes(monkeypatch):
    import velia_free_models as free
    monkeypatch.setenv('VELIA_FREE_API_TIERS_ENABLED', 'true')
    monkeypatch.setenv('VELIA_VOICE_RESERVES_ENABLED', 'true')
    monkeypatch.setenv('VELIA_VOICE_GROQ_FREE_ACCOUNT', 'true')
    monkeypatch.setenv('GROQ_API_KEY', 'private-reserve-key')
    transport._VOICE_CIRCUITS.clear()
    transport._CIRCUITS.clear()
    primary = QuantumConfig('zai', ZAI_URL, 'glm-4.5-flash', 'private-primary-key', 1024)
    yield primary, free.voice_reserves()[:1]
    transport._VOICE_CIRCUITS.clear()
    transport._CIRCUITS.clear()


def body(config):
    return {'model': config.model, 'messages': [{'role': 'user', 'content': 'private context'}]}


@pytest.mark.parametrize('kind', ['sync', 'async'])
def test_header_timeout_moves_once_to_reserve_preserving_context(routes, kind, caplog):
    primary, reserves = routes
    error = requests.ReadTimeout('private error') if kind == 'sync' else asyncio.TimeoutError('private error')
    client, selected = Client(error, Response()), []
    if kind == 'sync':
        with transport.completion(client, primary, body(primary), timeout=(5, 60), reserves=reserves, on_route=selected.append):
            pass
        assert client.calls[0][1]['timeout'] == (3, 12)
    else:
        async def run():
            async with transport.completion_async(client, primary, body(primary), reserves=reserves, on_route=selected.append):
                pass
        asyncio.run(run())
    assert selected == [reserves[0]] and len(client.calls) == 2
    assert client.calls[1][1]['json']['messages'] == body(primary)['messages']
    assert client.calls[1][1]['headers']['Authorization'] == 'Bearer private-reserve-key'
    assert 'private' not in caplog.text
    # A failing route is skipped on the next turn, rather than adding another wait.
    assert not transport._voice_ready(primary)


@pytest.mark.parametrize('kind', ['sync', 'async'])
def test_missing_reserve_reports_timeout_without_repeating_primary(routes, kind):
    primary, _ = routes
    error = requests.ConnectTimeout() if kind == 'sync' else asyncio.TimeoutError()
    client = Client(error, Response())
    with pytest.raises(QuantumError, match='quantum_timeout'):
        if kind == 'sync':
            with transport.completion(client, primary, body(primary), timeout=(5, 60), voice_turn=True):
                pass
        else:
            async def run():
                async with transport.completion_async(client, primary, body(primary), voice_turn=True):
                    pass
            asyncio.run(run())
    assert len(client.calls) == 1


@pytest.mark.parametrize('kind', ['sync', 'async'])
def test_timeout_after_headers_never_restarts_stream(routes, kind):
    primary, reserves = routes
    client = Client(Response(), Response())
    error = requests.ReadTimeout() if kind == 'sync' else asyncio.TimeoutError()
    with pytest.raises(type(error)):
        if kind == 'sync':
            with transport.completion(client, primary, body(primary), timeout=(5, 60), reserves=reserves):
                raise error
        else:
            async def run():
                async with transport.completion_async(client, primary, body(primary), reserves=reserves):
                    raise error
            asyncio.run(run())
    assert len(client.calls) == 1
    assert transport._voice_ready(primary)


def test_async_cancellation_does_not_start_reserve(routes):
    primary, reserves = routes
    client = Client(asyncio.CancelledError(), Response())
    async def run():
        async with transport.completion_async(client, primary, body(primary), reserves=reserves):
            pass
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert len(client.calls) == 1


@pytest.mark.parametrize('close_connection', [False, True])
def test_real_requests_restores_body_timeout_after_fast_headers(routes, monkeypatch, close_connection):
    """A slow body can outlive the admission deadline, including HTTP close."""
    primary, _ = routes
    monkeypatch.setattr(transport, 'VOICE_HEADER_TIMEOUT_SECONDS', .08)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            payload = wire('Full answer')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            if close_connection:
                self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.flush()
            time.sleep(.18)
            self.wfile.write(payload)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = replace(primary, base_url=f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setattr(provider, 'ZAI_URL', local.base_url)
    try:
        with requests.Session() as client:
            client.trust_env = False
            with transport.completion(client, local, body(local), timeout=(1, 1), voice_turn=True) as response:
                assert b'Full answer' in b''.join(response.iter_content(1))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_async_deadline_bounds_headers_only(routes, monkeypatch):
    primary, _ = routes
    monkeypatch.setattr(transport, 'VOICE_HEADER_TIMEOUT_SECONDS', .05)
    async def run():
        mode = ['headers']
        async def handle(request):
            if mode[0] == 'headers':
                await asyncio.sleep(.15)
                return web.Response(body=wire())
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            await asyncio.sleep(.15)
            await response.write(wire('Complete reasoning'))
            return response
        app = web.Application()
        app.router.add_post('/chat/completions', handle)
        async with TestServer(app) as server:
            local = replace(primary, base_url=str(server.make_url('')).rstrip('/'))
            monkeypatch.setattr(provider, 'ZAI_URL', local.base_url)
            async with ClientSession(timeout=ClientTimeout(total=2, sock_read=1)) as client:
                with pytest.raises(QuantumError, match='quantum_timeout'):
                    async with transport.completion_async(client, local, body(local), voice_turn=True):
                        pytest.fail('Late headers were accepted')
                transport._VOICE_CIRCUITS.clear()
                mode[0] = 'body'
                async with transport.completion_async(client, local, body(local), voice_turn=True) as response:
                    assert b'Complete reasoning' in await response.read()
    asyncio.run(run())


def test_real_requests_header_timeout_reaches_reserve(routes, monkeypatch):
    import velia_free_models as free
    primary, reserves = routes
    monkeypatch.setattr(transport, 'VOICE_HEADER_TIMEOUT_SECONDS', .05)
    seen = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            seen.append(self.path)
            if self.path.startswith('/primary'):
                time.sleep(.2)
            payload = wire('Reserve answer')
            try:
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    local = replace(primary, base_url=origin+'/primary')
    monkeypatch.setattr(provider, 'ZAI_URL', local.base_url)
    backup = replace(reserves[0], base_url=origin+'/reserve')
    monkeypatch.setattr(free, 'voice_reserves', lambda: (backup,))
    try:
        with requests.Session() as client:
            client.trust_env = False
            with transport.completion(client, local, body(local), timeout=(1, 1), reserves=(backup,)) as response:
                assert b'Reserve answer' in response.content
        assert seen == ['/primary/chat/completions', '/reserve/chat/completions']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('mode', ['flash', 'quantum', 'pro'])
@pytest.mark.parametrize('reserve_available', [True, False])
def test_account_generator_uses_timeout_boundary_for_each_selected_mode(routes, monkeypatch, mode, reserve_available, caplog):
    """Exercise the deployed account generator without loading unrelated DB code."""
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    import velia_free_models as free
    monkeypatch.setitem(sys.modules, 'services.velia_chat_streaming_runtime_patch',
                        SimpleNamespace(run_streaming_send=lambda *_a, **_k: None))
    spec = importlib.util.spec_from_file_location('voice_account_generator_test',
        Path(__file__).resolve().parents[1]/'services/velia_quantum_service.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('VELIA_ZAI_API_KEY', 'private-primary-key')
    if not reserve_available:
        monkeypatch.delenv('GROQ_API_KEY')
    # No other machine-wide reserve settings may influence this regression.
    monkeypatch.delenv('VELIA_VOICE_CF_API_TOKEN', raising=False)
    monkeypatch.delenv('VELIA_VOICE_OPENROUTER_API_KEY', raising=False)
    client = Client(requests.ReadTimeout('private exception'), Response(body=wire('Готова помочь.')))
    monkeypatch.setattr(module.requests, 'Session', lambda: client)
    emitted = []
    history = [{'role': 'user', 'content': 'Сохрани контекст'},
               {'role': 'assistant', 'content': 'Предложила решение'},
               {'role': 'user', 'content': 'Продолжай'}]
    caplog.set_level('INFO', logger=module.LOG.name)
    result = module.generate('unused prompt', chat_mode=mode, voice_turn=True,
        request_id='same-request', messages=history, on_delta=emitted.append)
    assert result['request_id'] == 'same-request' and result['model'] == 'velia-'+mode
    assert client.calls[0][1]['timeout'] == (3, 12)
    primary_body = client.calls[0][1]['json']
    assert primary_body['model'] == free.TIERS[mode][1]
    assert primary_body['messages'][1:] == history
    assert primary_body['thinking']['type'] == ('enabled' if mode == 'pro' else 'disabled')
    if reserve_available:
        assert result['ok'] and result['fallback_used'] and result['text'] == 'Готова помочь.'
        assert emitted == ['Готова помочь.'] and len(client.calls) == 2
        assert client.calls[1][1]['json']['messages'] == primary_body['messages']
    else:
        assert not result['ok'] and result['reason'] == mode+'_timeout'
        assert not emitted and len(client.calls) == 1
    assert 'mode='+mode in caplog.text and 'private' not in caplog.text


def test_async_voice_generator_without_reserve_uses_short_entry_timeout(routes, monkeypatch):
    from velia_quantum_turn import stream_turn
    primary, _ = routes
    monkeypatch.delenv('GROQ_API_KEY')
    monkeypatch.delenv('VELIA_VOICE_CF_API_TOKEN', raising=False)
    monkeypatch.delenv('VELIA_VOICE_OPENROUTER_API_KEY', raising=False)
    client = Client(asyncio.TimeoutError(), Response())
    async def run():
        return [event async for event in stream_turn(client, primary,
            [{'role': 'user', 'content': 'Continue'}], voice_turn=True)]
    with pytest.raises(QuantumError, match='quantum_timeout'):
        asyncio.run(run())
    assert len(client.calls) == 1
