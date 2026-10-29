import asyncio
import json
import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import velia_quantum_provider as quantum
from velia_desktop_routes import setup_velia_desktop_routes, validate_payload
from test_velia_quantum_provider import enabled, wire


@pytest.mark.parametrize("target", ["planner", "desktop", "desktop_stream"])
def test_zai_browser_paths_use_only_free_model_and_documented_tool_mode(enabled, monkeypatch, target):
    from desktop.quantum_intent import understand_quantum
    seen = []
    async def run():
        async def provider(request):
            body = await request.json()
            seen.append(body)
            assert request.headers["Authorization"] == "Bearer quantum-test-secret"
            assert body["model"] == "glm-4.7-flash" and body["thinking"] == {"type": "disabled"}
            assert "gateway" not in body and "reasoning_effort" not in body
            if body.get("tools"):
                assert body["tool_choice"] == "auto"
                plan = {"action": "direct", "quote": "", "candidate": "", "query": "",
                    "source_scope": "general", "task_query": "", "context": []}
                return web.json_response({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
                    {"type": "function", "function": {"name": "understand_request", "arguments": plan}}]}}]})
            if body.get("stream"):
                return web.Response(body=wire(), content_type="text/event-stream")
            return web.json_response({"choices": [{"message": {"content": "Поняла"}, "finish_reason": "stop"}]})
        upstream = web.Application()
        upstream.router.add_post("/api/paas/v4/chat/completions", provider)
        async with TestServer(upstream) as host:
            base = str(host.make_url("/api/paas/v4"))
            monkeypatch.setattr(quantum, "ZAI_URL", base)
            monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "zai")
            monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", base)
            if target == "planner":
                assert (await understand_quantum([{"role": "user", "content": "説明してください"}]))["action"] == "direct"
            else:
                monkeypatch.setenv("VELIA_DESKTOP_API_ENABLED", "true")
                monkeypatch.setenv("VELIA_DESKTOP_PREVIEW_USER_IDS", "7")
                app = web.Application()
                setup_velia_desktop_routes(app, lambda _: {"user_id": 7})
                async with TestServer(app) as server, ClientSession() as client:
                    async with client.post(server.make_url("/desktop-api/v1/chat/completions"),
                            headers={"Authorization": "Bearer va_test"}, json={"model": quantum.MODEL_ID,
                                "stream": target == "desktop_stream", "messages": [{"role": "user", "content": "こんにちは"}]}) as response:
                        assert response.status == 200
                        if target == "desktop_stream":
                            assert "Поняла" in await response.text()
                        else:
                            assert (await response.json())["choices"][0]["message"]["content"] == "Поняла"
        assert len(seen) == 1
    asyncio.run(run())


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


@pytest.mark.parametrize("mode,model",[("flash","glm-4.5-flash"),("quantum","glm-4.7-flash"),("pro","glm-4.7-flash")])
def test_free_browser_tiers_use_selected_native_model_without_planning(monkeypatch,mode,model):
    from test_velia_web_chat import fixture,headers,login
    import velia_free_models as free
    import velia_quantum_turn as native
    async def run():
        async with fixture(monkeypatch,features={"chat_quantum":True,"chat_quantum_native_tools":True}) as (server,client,state):
            monkeypatch.setenv("VELIA_FREE_API_TIERS_ENABLED","true")
            monkeypatch.setenv("VELIA_ZAI_API_KEY","fixture-zai")
            monkeypatch.delenv("GROQ_API_KEY", raising=False)
            seen=[]
            async def stream(client,config,messages,**kwargs):
                seen.append((config,messages,kwargs))
                yield {"text":"Поняла. 日本語 العربية"}
            monkeypatch.setattr(native,"stream_turn",stream)
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/chat/completions"),headers=headers(cookie),
                    json={"model":"velia-"+mode,"stream":True,"voice_turn":True,"web_search":True,
                          "messages":[{"role":"user","content":"Привет"}]}) as response:
                assert response.status==200
                text=await response.text()
                assert "Поняла" in text and "[DONE]" in text and model not in text
            assert len(seen)==1 and seen[0][0].model==model
            config=seen[0][0]
            assert config.provider=="zai" and config.thinking_enabled is (mode=="pro")
            assert quantum.payload(config,seen[0][1],stream=True)["thinking"]=={"type":"enabled" if mode=="pro" else "disabled"}
            assert seen[0][2]["max_tokens"]==(4096 if mode=="pro" else 1024)
            assert seen[0][1][-1]["content"]=="Привет"
            assert state["payloads"]==state["intent_payloads"]==state["review_payloads"]==state["search_queries"]==[]
    asyncio.run(run())


def test_free_pro_preserves_existing_account_token_access_rule(monkeypatch):
    from test_velia_web_chat import fixture,headers,login
    async def run():
        async with fixture(monkeypatch,credits=0) as (server,client,state):
            monkeypatch.setenv("VELIA_FREE_API_TIERS_ENABLED","true")
            monkeypatch.delenv("GROQ_API_KEY", raising=False)
            monkeypatch.setenv("VELIA_ZAI_API_KEY","fixture-zai")
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/chat/completions"),headers=headers(cookie),
                    json={"model":"velia-pro","stream":True,"messages":[{"role":"user","content":"Hello"}]}) as response:
                assert response.status==402 and (await response.json())["error"]["message"]=="pro_tokens_required"
            assert state["payloads"]==[]
    asyncio.run(run())


@pytest.mark.parametrize("mode",["flash","quantum","pro"])
def test_free_saved_voice_turn_goes_directly_to_selected_account_model(monkeypatch,mode):
    import uuid
    from test_velia_web_chat import fixture,headers,login
    async def run():
        features={"chat_quantum":True,"chat_quantum_native_tools":True,"chat_free_api_tiers":True}
        async with fixture(monkeypatch,features=features) as (server,client,state):
            monkeypatch.setenv("VELIA_FREE_API_TIERS_ENABLED","true")
            monkeypatch.setenv("VELIA_ZAI_API_KEY","fixture-zai")
            monkeypatch.delenv("GROQ_API_KEY", raising=False)
            cookie,profile,_=await login(server,client)
            assert profile["free_api_tiers"] is True
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Fixture"}) as response:
                cid=(await response.json())["conversation"]["id"]
            async with client.post(server.make_url("/web-api/v1/conversations/"+cid+"/messages/stream"),headers=headers(cookie),
                    json={"model":"velia-"+mode,"content":"Сохрани буквально эту фразу", "idempotency_key":"free-account-voice", "voice_turn":True, "web_search":True}) as response:
                assert response.status==200
                assert "[DONE]" in await response.text()
            call=state["account_calls"][0]
            assert call["content"]=="Сохрани буквально эту фразу" and call["chat_mode"]==mode
            assert call["voice_turn"] is True and call["web_search"] is True
            assert state["intent_payloads"]==state["review_payloads"]==state["payloads"]==[]
    asyncio.run(run())


def test_free_saved_pro_cannot_reach_legacy_paid_backend_during_mixed_deployment(monkeypatch):
    from test_velia_web_chat import fixture,headers,login
    async def run():
        async with fixture(monkeypatch) as (server,client,state):
            monkeypatch.setenv("VELIA_FREE_API_TIERS_ENABLED","true")
            monkeypatch.delenv("GROQ_API_KEY", raising=False)
            monkeypatch.setenv("VELIA_ZAI_API_KEY","fixture-zai")
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Fixture"}) as response:
                cid=(await response.json())["conversation"]["id"]
            async with client.post(server.make_url("/web-api/v1/conversations/"+cid+"/messages/stream"),headers=headers(cookie),
                    json={"model":"velia-pro","content":"Fixture", "idempotency_key":"free-account-pro"}) as response:
                assert response.status==503
            assert state["account_calls"]==state["payloads"]==[]
    asyncio.run(run())
