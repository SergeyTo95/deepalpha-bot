import asyncio
import json
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import velia_quantum_provider as quantum
from velia_desktop_routes import setup_velia_desktop_routes, validate_payload
from test_velia_quantum_provider import enabled, wire


@pytest.mark.parametrize("status,code", [(402, "quantum_access_required"), (429, "quantum_quota_exhausted"),
    (401, "quantum_unavailable"), (503, "quantum_provider_error"), (307, "quantum_provider_error")])
def test_gateway_quantum_route_uses_own_key_and_never_retries(enabled, monkeypatch, status, code):
    async def run():
        calls = []
        async def provider(request):
            calls.append((request.headers.get("Authorization"), await request.json()))
            return web.json_response({"error": "PRIVATE"}, status=status)
        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", provider)
        async with TestServer(upstream) as host:
            monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
            monkeypatch.setenv("VELIA_QUANTUM_MODEL", "future-owned-model")
            monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", str(host.make_url("/v1")))
            monkeypatch.setenv("VELIA_DESKTOP_API_ENABLED", "true")
            monkeypatch.setenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "7")
            monkeypatch.setenv("KIMI_BASE_URL", "http://127.0.0.1:1/must-not-be-contacted")
            app = web.Application()
            setup_velia_desktop_routes(app, lambda _: {"user_id": 7})
            async with TestServer(app) as server, ClientSession() as client:
                async with client.post(server.make_url("/desktop-api/v1/chat/completions"),
                        headers={"Authorization": "Bearer va_test"},
                        json={"model": quantum.MODEL_ID, "messages": [{"role": "user", "content": "hi"}]}) as response:
                    assert response.status != 200
                    assert (await response.json())["error"]["message"] == code
        assert len(calls) == 1 and calls[0][0] == "Bearer quantum-test-secret"
        assert calls[0][1]["model"] == "future-owned-model"
    asyncio.run(run())


def test_gateway_quantum_stream_and_tool_round_trip(enabled, monkeypatch):
    async def run():
        calls = []
        async def provider(request):
            data = await request.json()
            calls.append(data)
            return web.Response(body=wire(), content_type="text/event-stream")
        upstream = web.Application()
        upstream.router.add_post("/v1/chat/completions", provider)
        async with TestServer(upstream) as host:
            monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
            monkeypatch.setenv("VELIA_QUANTUM_MODEL", "owned-model")
            monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", str(host.make_url("/v1")))
            monkeypatch.setenv("VELIA_DESKTOP_API_ENABLED", "true")
            monkeypatch.setenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "7")
            app = web.Application()
            setup_velia_desktop_routes(app, lambda _: {"user_id": 7})
            async with TestServer(app) as server, ClientSession() as client:
                data = {"model": quantum.MODEL_ID, "stream": True, "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "read", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "call_1", "content": "read result"}]}
                async with client.post(server.make_url("/desktop-api/v1/chat/completions"),
                        headers={"Authorization": "Bearer va_test"}, json=data) as response:
                    text = await response.text()
                    assert response.status == 200 and "Поняла" in text and "[DONE]" in text
                    assert "PRIVATE" not in text and "owned-model" not in text
            assert calls[0]["messages"][-1]["tool_call_id"] == "call_1"
    asyncio.run(run())


@pytest.mark.parametrize('target',['planner','desktop','desktop_stream'])
@pytest.mark.parametrize('cap,status',[(0,200),(None,200),(1,200),(0,403),(0,307)])
def test_every_external_browser_path_checks_zero_spend_before_canonical_glm(enabled,monkeypatch,caplog,target,cap,status):
    from desktop.quantum_intent import understand_quantum
    seen=[];payloads=[]
    async def run():
        async def key_info(request):
            seen.append((request.method,request.path))
            assert request.headers['Authorization']=='Bearer quantum-test-secret'
            return web.json_response({'data':{'limit':cap,'label':'private label'}},status=status,
                headers={'Location':'/must-not-follow'} if status==307 else {})
        async def provider(request):
            seen.append((request.method,request.path));body=await request.json();payloads.append(body)
            if body.get('tools'):
                plan={'action':'direct','quote':'','candidate':'','query':'','source_scope':'general','task_query':'','context':[]}
                return web.json_response({'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[
                    {'type':'function','function':{'name':'understand_request','arguments':json.dumps(plan)}}]}}]})
            if body.get('stream'):return web.Response(body=wire(),content_type='text/event-stream')
            return web.json_response({'choices':[{'message':{'content':'Поняла'},'finish_reason':'stop'}]})
        upstream=web.Application();upstream.router.add_get('/api/v1/key',key_info)
        upstream.router.add_post('/v1/chat/completions',provider)
        safe=cap==0 and status==200
        async with TestServer(upstream) as host:
            monkeypatch.setattr(quantum,'EXPERIENTIAL_URL',str(host.make_url('/v1')))
            monkeypatch.setattr(quantum,'KEY_INFO_URL',str(host.make_url('/api/v1/key')))
            monkeypatch.setenv('VELIA_QUANTUM_BASE_URL',str(host.make_url('/v1')))
            if target=='planner':
                if safe:assert (await understand_quantum([{'role':'user','content':'Объясни двоичный поиск.'}]))['action']=='direct'
                else:
                    with pytest.raises(quantum.QuantumError,match='quantum_free_guard_required'):
                        await understand_quantum([{'role':'user','content':'private user prompt'}])
            else:
                monkeypatch.setenv('VELIA_DESKTOP_API_ENABLED','true')
                monkeypatch.setenv('VELIA_DESKTOP_PREVIEW_USER_IDS','7')
                app=web.Application();setup_velia_desktop_routes(app,lambda _:{'user_id':7})
                async with TestServer(app) as server,ClientSession() as client:
                    async with client.post(server.make_url('/desktop-api/v1/chat/completions'),
                            headers={'Authorization':'Bearer va_test'},json={'model':quantum.MODEL_ID,
                            'stream':target=='desktop_stream','messages':[{'role':'user','content':'hello'}]}) as response:
                        text=await response.text()
                        assert response.status==(200 if safe else 503)
                        if not safe:assert 'quantum_free_guard_required' in text
                        elif target=='desktop_stream':assert 'Поняла' in text
                        else:assert json.loads(text)['choices'][0]['message']['content']=='Поняла'
        assert seen==([('GET','/api/v1/key'),('POST','/v1/chat/completions')] if safe else [('GET','/api/v1/key')])
        if safe:
            assert len(payloads)==1 and payloads[0]['model']=='glm-5.3-uncensored'
            assert payloads[0]['gateway']=={'retry':{'max_attempts_per_route':1,'max_total_attempts':1},'routing':{'allow_fallbacks':False}}
    asyncio.run(run())
    assert 'private' not in caplog.text and 'quantum-test-secret' not in caplog.text
