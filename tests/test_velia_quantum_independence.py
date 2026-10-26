"""Quantum may interpret/search even when every Flash route is unavailable."""
import asyncio

import pytest

from desktop.guest_store import GuestStore
from test_velia_web_chat import fixture, login, headers


@pytest.mark.parametrize("action", ["direct", "search"])
@pytest.mark.parametrize("saved", [True, False])
def test_quantum_owns_interpretation_and_search_with_flash_unavailable(monkeypatch, tmp_path, action, saved):
    def forbidden(*args, **kwargs):
        pytest.fail("Quantum reached Flash or Decision routing")
    monkeypatch.setattr("desktop.web_search.understand", forbidden)
    monkeypatch.setattr("desktop.decision_router.try_direct", forbidden)
    async def run():
        plan={"action":action,"quote":"","candidate":"","query":"Python release" if action=="search" else "",
            "source_scope":"general","task_query":"Python release" if action=="search" else "","context":[]}
        async with fixture(monkeypatch, with_search=True, quantum_api=True, without_flash=True,
                features={"chat_quantum":True}, intent=plan,
                guest_store=GuestStore(sqlite_path=tmp_path/"quantum-own.db")) as (server, client, state):
            cookie, _, _=await login(server,client)
            question="Найди текущую версию Python" if action=="search" else "Объясни разницу между списком и множеством."
            if saved:
                async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Quantum"}) as response:
                    cid=(await response.json())["conversation"]["id"]
                endpoint=f"/web-api/v1/conversations/{cid}/messages/stream"
                body={"content":question,"model":"velia-quantum","idempotency_key":"quantum-own-01"}
            else:
                endpoint="/web-api/v1/chat/completions"
                body={"model":"velia-quantum","stream":True,"messages":[{"role":"user","content":question}]}
            async with client.post(server.make_url(endpoint),headers=headers(cookie),json=body) as response:
                wire=await response.text()
                assert response.status==200 and "[DONE]" in wire
                assert "fixture-quantum-key" not in wire and "private-thought" not in wire
                assert ("https://www.python.org/downloads/" in wire)==(action=="search")
            assert len(state["quantum_intent_payloads"])==1
            assert state["intent_payloads"]==state["forbidden_model_calls"]==state["review_payloads"]==[]
            assert len(state["search_queries"])==(1 if action=="search" else 0)
            request=state["quantum_intent_payloads"][0]
            assert request["model"]=="owned-quantum" and request["tool_choice"]=="required"
            assert "chat_template_kwargs" not in request and "thinking_budget_tokens" not in request
            if saved: assert state["account_calls"][-1]["chat_mode"]=="quantum"
    asyncio.run(run())


@pytest.mark.parametrize("model", ["velia-flash", "velia-quantum"])
@pytest.mark.parametrize("action", ["direct", "search"])
def test_voice_and_files_follow_selected_model_without_decision_fallback(monkeypatch, tmp_path, model, action):
    def forbidden(*args, **kwargs):
        pytest.fail("Selected chat used Decision or an unrelated model")
    monkeypatch.setattr("desktop.decision_router.try_direct", forbidden)
    if model=="velia-quantum": monkeypatch.setattr("desktop.web_search.understand", forbidden)
    async def run():
        async with fixture(monkeypatch, with_search=True, quantum_api=model=="velia-quantum",
                without_flash=model=="velia-quantum", features={"chat_quantum":True,"chat_quantum_attachments":True},
                intent={"action":action,"quote":"","query":"Python release" if action=="search" else ""},
                guest_store=GuestStore(sqlite_path=tmp_path/"chosen-voice.db")) as (server, client, state):
            cookie, profile, _=await login(server,client)
            assert profile["quantum_attachments"] is True
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Selected voice"}) as response:
                cid=(await response.json())["conversation"]["id"]
            data={"content":"Найди текущую версию Python" if action=="search" else "Объясни двоичный поиск.",
                "model":model,"idempotency_key":"selected-voice-001","voice_turn":True,"attachment_ids":[]}
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"),headers=headers(cookie),json=data) as response:
                wire=await response.text()
                assert response.status==200 and "[DONE]" in wire
                assert ("https://www.python.org/downloads/" in wire)==(action=="search")
            request=state["account_calls"][-1]
            assert request["chat_mode"]==model.removeprefix("velia-") and request["voice_turn"] is True
            assert request["attachment_ids"]==[]
            assert state["forbidden_model_calls"]==[] and state["agent_calls"]==[]
            own=state["quantum_intent_payloads"] if model=="velia-quantum" else state["intent_payloads"]
            other=state["intent_payloads"] if model=="velia-quantum" else state["quantum_intent_payloads"]
            assert len(own)==1 and other==[]
            data.update(content="Перескажи прикреплённый документ.", idempotency_key="selected-file-001",
                medical_no_search=True, attachment_ids=["11111111-1111-1111-1111-111111111111"])
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"),headers=headers(cookie),json=data) as response:
                assert response.status==200 and "[DONE]" in await response.text()
            assert state["account_calls"][-1]["chat_mode"]==model.removeprefix("velia-")
            assert state["account_calls"][-1]["attachment_ids"]==data["attachment_ids"]
            assert len(own)==1 and other==[]
    asyncio.run(run())


@pytest.mark.parametrize("model", ["velia-flash", "velia-quantum"])
@pytest.mark.parametrize("question", ["Как дела?", "Расскажи какую-нибудь интересную историю без разницы какую"])
def test_simple_voice_turn_uses_one_native_answer_without_planning_or_history_restore(monkeypatch, tmp_path, model, question):
    from desktop.web_search import WebSearch
    def forbidden(*args, **kwargs):
        pytest.fail("Self-contained voice turn waited for preparation")
    monkeypatch.setattr(WebSearch,"account_question",forbidden)
    monkeypatch.setattr(WebSearch,"restore_messages",forbidden)
    async def run():
        async with fixture(monkeypatch,with_search=True,quantum_api=model=="velia-quantum",
                without_flash=model=="velia-quantum",features={"chat_quantum":True},
                guest_store=GuestStore(sqlite_path=tmp_path/"direct-voice.db")) as (server,client,state):
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Voice"}) as response:
                cid=(await response.json())["conversation"]["id"]
            prior=[{"role":"user","content":"Мой рассказ про лес.","status":"completed"},
                {"role":"assistant","content":"Поняла.","status":"completed"}]
            state["conversations"][cid]["messages"]=list(prior)
            identities_before=sum(row['path']=='/mobile-api/v1/me' for row in state['seen'])
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"),headers=headers(cookie),
                    json={"content":question,"model":model,"idempotency_key":"direct-voice-001","voice_turn":True,"web_search":True}) as response:
                assert response.status==200 and "[DONE]" in await response.text()
            assert len(state["account_calls"])==1
            assert state["account_calls"][0]["chat_mode"]==model.removeprefix("velia-")
            assert state["account_calls"][0]["voice_turn"] is True
            assert state["account_calls"][0]["content"]==question
            assert sum(row['path']=='/mobile-api/v1/me' for row in state['seen'])==identities_before+1
            assert state["conversations"][cid]["messages"][:2]==prior
            assert state["intent_payloads"]==state["quantum_intent_payloads"]==state["search_queries"]==[]
    asyncio.run(run())


@pytest.mark.parametrize("mode", [None,"flash","quantum"])
def test_file_upload_refuses_legacy_backend_instead_of_using_its_other_model(monkeypatch, mode):
    from aiohttp import FormData
    async def run():
        async with fixture(monkeypatch, features={"chat_quantum":True,"chat_quantum_attachments":True}) as (server,client,state):
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Files"}) as response:
                cid=(await response.json())["conversation"]["id"]
            form=FormData();form.add_field("file",b"Invoice 42",filename="invoice.txt",content_type="text/plain")
            extra={"X-Velia-Chat-Mode":mode} if mode else {}
            async with client.post(server.make_url(f"/web-api/v1/features/conversations/{cid}/attachments"),
                    headers=headers(cookie,**extra),data=form) as response:
                assert response.status==(503 if mode else 409)
                assert (await response.json())["error"]=="selected_model_files_unavailable"
            assert state["account_calls"]==state["intent_payloads"]==state["quantum_intent_payloads"]==[]
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["flash","quantum"])
def test_rolling_backend_cannot_fall_back_to_legacy_file_vision(monkeypatch, mode):
    from aiohttp import FormData
    async def run():
        async with fixture(monkeypatch, features={"chat_quantum":True,"chat_quantum_attachments":True,
                "chat_selected_model_inputs":True}) as (server,client,state):
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Files"}) as response:
                cid=(await response.json())["conversation"]["id"]
            form=FormData();form.add_field("file",b"Invoice 42",filename="invoice.txt",content_type="text/plain")
            async with client.post(server.make_url(f"/web-api/v1/features/conversations/{cid}/attachments"),
                    headers=headers(cookie,**{"X-Velia-Chat-Mode":mode}),data=form) as response:
                assert response.status==503
                assert (await response.json())["error"]=="selected_model_files_unavailable"
            assert state.get("legacy_upload_calls",[])==[]
    asyncio.run(run())


@pytest.mark.parametrize("status,code", [(429,"quantum_quota_exhausted"),(402,"quantum_access_required"),(503,"quantum_provider_error")])
def test_quantum_preparation_failure_never_falls_back_or_generates(monkeypatch, tmp_path, status, code):
    async def run():
        async with fixture(monkeypatch, with_search=True, quantum_api=True, without_flash=True,
                features={"chat_quantum":True}, intent_status=status,
                guest_store=GuestStore(sqlite_path=tmp_path/"quantum-error.db")) as (server, client, state):
            cookie, _, _=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Quantum"}) as response:
                cid=(await response.json())["conversation"]["id"]
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"),headers=headers(cookie),
                    json={"content":"Объясни разницу между списком и множеством.","model":"velia-quantum","idempotency_key":"quantum-error-01"}) as response:
                wire=await response.text()
                assert code in wire and "[DONE]" not in wire and "fixture failure" not in wire
            assert len(state["quantum_intent_payloads"])==1
            assert state["intent_payloads"]==state["forbidden_model_calls"]==state["account_calls"]==[]
    asyncio.run(run())


@pytest.mark.parametrize("case", ["redirect", "wrong_tool", "oversized", "truncated", "malformed"])
def test_quantum_plan_rejects_invalid_provider_results_without_retry(monkeypatch, case):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from desktop.quantum_intent import understand_quantum
    from velia_quantum_provider import QuantumError
    async def run():
        calls=[]
        async def provider(request):
            calls.append(request.path)
            if case=="redirect":
                raise web.HTTPTemporaryRedirect(location="/followed")
            if case in {"oversized","malformed"}:
                return web.Response(body=b" "*65537 if case=="oversized" else b"{",content_type="application/json")
            return web.json_response({"choices":[{"message":{"tool_calls":[{"function":{
                "name":"not_allowed" if case=="wrong_tool" else "understand_request",
                "arguments":'{"action":"direct","quote":"","query":""}'}}]},
                "finish_reason":"length" if case=="truncated" else "tool_calls"}]})
        app=web.Application()
        app.router.add_post("/v1/chat/completions",provider)
        app.router.add_route("*","/followed",provider)
        async with TestServer(app) as server:
            for key,value in {"VELIA_QUANTUM_ENABLED":"true","VELIA_QUANTUM_PROVIDER":"self_hosted",
                    "VELIA_QUANTUM_BASE_URL":str(server.make_url("/v1")),"VELIA_QUANTUM_MODEL":"owned-quantum",
                    "VELIA_QUANTUM_API_KEY":"dedicated-test-key"}.items():
                monkeypatch.setenv(key,value)
            with pytest.raises(QuantumError) as error:
                await understand_quantum([{"role":"user","content":"Объясни двоичный поиск."}])
            assert error.value.code==("quantum_provider_error" if case=="redirect" else "quantum_invalid_response")
        assert calls==["/v1/chat/completions"]
    asyncio.run(run())


@pytest.mark.parametrize('finish',['tool_calls','stop'])
@pytest.mark.parametrize('argument_format',['json','object'])
@pytest.mark.parametrize('quote',['',None])
def test_complete_quantum_tool_envelopes_accept_null_optional_metadata_without_a_second_call(monkeypatch, finish, argument_format,quote):
    import json
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from desktop.quantum_intent import understand_quantum
    from desktop.request_intent import FULL_TOOL
    async def run():
        calls=[]
        async def provider(request):
            calls.append(await request.json())
            args={'action':'direct','quote':quote,'query':None,'candidate':None,'source_scope':None,'task_query':None,'context':[]}
            return web.json_response({'choices':[{'finish_reason':finish,'message':{'tool_calls':[{'type':'function','function':{
                'name':'understand_request','arguments':json.dumps(args) if argument_format=='json' else args}}]}}]})
        app=web.Application();app.router.add_post('/v1/chat/completions',provider)
        async with TestServer(app) as server:
            for key,value in {'VELIA_QUANTUM_ENABLED':'true','VELIA_QUANTUM_PROVIDER':'self_hosted',
                    'VELIA_QUANTUM_BASE_URL':str(server.make_url('/v1')),'VELIA_QUANTUM_MODEL':'owned',
                    'VELIA_QUANTUM_API_KEY':'fixture'}.items():monkeypatch.setenv(key,value)
            assert await understand_quantum([{'role':'user','content':'Объясни двоичный поиск.'}])=={'action':'direct','query':'','context':[]}
        assert len(calls)==1 and calls[0]['tools']==[FULL_TOOL]
    asyncio.run(run())


@pytest.mark.parametrize('task',['',None,'  '])
def test_general_search_accepts_omitted_empty_quote_without_changing_retrieval(monkeypatch,tmp_path,task):
    async def run():
        plan={'action':'search','candidate':'','query':'latest stable Python version',
            'source_scope':'general','task_query':task,'context':[]}
        async with fixture(monkeypatch,with_search=True,quantum_api=True,without_flash=True,
                features={'chat_quantum':True},intent=plan,
                search_response={'results':[{'title':'Python releases','url':'https://www.python.org/downloads/',
                    'content':'Stable release information.'}]},
                guest_store=GuestStore(sqlite_path=tmp_path/'omitted-quote.db')) as (server,client,state):
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url('/web-api/v1/conversations'),headers=headers(cookie),json={'title':'Versions'}) as response:
                cid=(await response.json())['conversation']['id']
            async with client.post(server.make_url(f'/web-api/v1/conversations/{cid}/messages/stream'),headers=headers(cookie),
                    json={'content':'Найди последнюю стабильную версию Python.','model':'velia-quantum',
                        'idempotency_key':'empty-metadata-search'}) as response:
                assert response.status==200 and '[DONE]' in await response.text()
            assert state['search_queries']==['latest stable Python version']
            assert len(state['quantum_intent_payloads'])==1 and len(state['account_calls'])==1
            assert state['account_calls'][0]['chat_mode']=='quantum'
            assert state['intent_payloads']==state['forbidden_model_calls']==[]
    asyncio.run(run())


@pytest.mark.parametrize('overrides',[
    {'candidate':'different term'},
    {'query':''},
    {'query':None},
    {'source_scope':'official_health','task_query':''},
    {'source_scope':'official_health','task_query':None},
    {'context':[{'quote':'invented condition','kind':'condition','status':'stated'}]},
])
def test_empty_general_metadata_compatibility_still_rejects_invalid_plans(overrides):
    from desktop.quantum_intent import planning_decision
    args={'action':'search','candidate':'','query':'latest stable Python version',
        'source_scope':'general','task_query':'','context':[],**overrides}
    result={'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{
        'name':'understand_request','arguments':args}}]}}]}
    with pytest.raises((ValueError,TypeError)):planning_decision(result,'Найди последнюю стабильную версию Python.')


@pytest.mark.parametrize('args',[
    {'action':'search','quote':'','query':None},
    {'action':'direct','quote':'диабет','candidate':'рак','query':''},
    {'action':'direct','quote':'','query':'','context':[{'quote':'астма','kind':'condition','status':'stated'}]},
    {'action':'direct','quote':'','query':'','source_scope':'official_health'},
    {'action':'direct','quote':'','query':'','unknown':'field'},
])
def test_quantum_envelope_compatibility_does_not_repair_queries_quotes_or_medical_evidence(args):
    from desktop.quantum_intent import planning_decision
    result={'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{
        'name':'understand_request','arguments':args}}]}}]}
    with pytest.raises(ValueError):planning_decision(result,'Расскажи про лес.')


def test_medical_planning_requires_exact_inflected_evidence_and_logs_only_structure():
    from desktop.quantum_intent import planning_decision
    from desktop.probe_quantum_independence import response_shape
    import json
    question='Как безопасно снизить вес при астме?'
    args={'action':'search','quote':'','candidate':'','query':'healthy weight loss with asthma',
        'source_scope':'official_health','task_query':'healthy weight loss advice',
        'context':[{'quote':'астме','kind':'condition','status':'unspecified'}]}
    result={'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{
        'name':'understand_request','arguments':args}}]}}]}
    decision=planning_decision(result,question)
    assert decision['query']=='healthy weight loss advice' and decision['source_scope']=='official_health'
    assert decision['context']==[{'span':[30,35],'kind':'condition','status':'unspecified'}]
    assert response_shape(result,question)['context_exact']==[True]
    args['context'][0]['quote']='астма'
    with pytest.raises(ValueError):planning_decision(result,question)
    shape=response_shape(result,question)
    assert shape['context_exact']==[False]
    assert 'астма' not in json.dumps(shape,ensure_ascii=False) and 'healthy' not in json.dumps(shape)


def test_quantum_medical_search_preserves_official_sources_without_flash(monkeypatch,tmp_path):
    async def run():
        plan={"action":"search","quote":"","candidate":"","query":"healthy weight loss with asthma",
            "source_scope":"official_health","task_query":"healthy weight loss advice",
            "context":[{"quote":"астма","kind":"condition","status":"stated"}]}
        async with fixture(monkeypatch,with_search=True,quantum_api=True,without_flash=True,
                features={"chat_quantum":True},intent=plan,
                search_response={"results":[{"title":"NHS","url":"https://www.nhs.uk/live-well/healthy-weight/","content":"Gradual physical activity."},
                    {"title":"Impostor","url":"https://nhs.uk.attacker.example/diet","content":"Unverified."}]},
                guest_store=GuestStore(sqlite_path=tmp_path/"quantum-health.db")) as (server,client,state):
            cookie,_,_=await login(server,client)
            async with client.post(server.make_url("/web-api/v1/conversations"),headers=headers(cookie),json={"title":"Health"}) as response:
                cid=(await response.json())["conversation"]["id"]
            async with client.post(server.make_url(f"/web-api/v1/conversations/{cid}/messages/stream"),headers=headers(cookie),
                    json={"content":"У меня астма. Как безопасно похудеть?","model":"velia-quantum","idempotency_key":"quantum-health-01"}) as response:
                wire=await response.text()
                assert response.status==200 and "[DONE]" in wire
                assert "www.nhs.uk" in wire and "attacker.example" not in wire
            assert state["search_queries"]==["healthy weight loss advice"]
            assert state["search_payloads"][0]["include_domains_mode"]=="restrict"
            assert state["intent_payloads"]==state["forbidden_model_calls"]==[]
            assert "астма" in state["account_calls"][-1]["content"]
    asyncio.run(run())


@pytest.mark.parametrize('status,code,expected',[
    (401,'invalid_key','invalid_key'),
    (403,'model_not_granted','model_not_granted'),
    (403,'model_location_not_supported','model_location_not_supported'),
    (402,'free_tier_requires_payment','free_tier_requires_payment'),
    (429,'free_limit_reached','free_limit_reached'),
    (403,'private-provider-secret','unknown_error'),
    (307,'invalid_key','invalid_key'),
])
def test_access_diagnostic_is_one_read_and_exposes_only_allowlisted_codes(monkeypatch,capsys,status,code,expected):
    import json
    from types import SimpleNamespace
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from desktop import probe_quantum_independence as probe
    seen=[];key='xpl_'+'1'*40
    async def run():
        async def models(request):
            seen.append((request.method,request.path))
            assert request.headers['Authorization']=='Bearer '+key
            return web.json_response({'error':{'code':code,'message':'private provider body '+key}},
                status=status,headers={'Location':'/must-not-follow'} if status==307 else {})
        app=web.Application();app.router.add_route('*','/{tail:.*}',models)
        async with TestServer(app) as server:
            base=str(server.make_url('/v1'));monkeypatch.setattr(probe,'EXPERIENTIAL_URL',base)
            monkeypatch.setenv('VELIA_QUANTUM_API_KEY',key)
            config=SimpleNamespace(provider='experiential',base_url=base,model=probe.FREE_MODEL,api_key=key)
            with pytest.raises(RuntimeError,match='quantum_access_failed'):await probe.check_access(config)
    asyncio.run(run())
    output=capsys.readouterr().out
    assert seen==[('GET','/v1/models')]
    record=json.loads(output.removeprefix('VELIA_QUANTUM_ACCESS '))
    assert record=={'ok':False,'status':status,'provider_code':expected,
        'key_source':'VELIA_QUANTUM_API_KEY','key_shape':'expected'}
    assert key not in output and 'private' not in output


@pytest.mark.parametrize('dedicated',[True,False])
def test_access_diagnostic_authenticates_without_assuming_free_alias_is_listed(monkeypatch,capsys,dedicated):
    import json
    from types import SimpleNamespace
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from desktop import probe_quantum_independence as probe
    seen=[];key='xpl_'+'2'*40
    async def run():
        async def models(request):
            seen.append(request.method)
            return web.json_response({'data':[{'id':'glm-5.3-uncensored'},{'id':'private-owned-model'}]})
        app=web.Application();app.router.add_get('/v1/models',models)
        monkeypatch.delenv('VELIA_QUANTUM_API_KEY',raising=False)
        monkeypatch.setenv('VELIA_QUANTUM_API_KEY' if dedicated else 'EXPLABS_API_KEY',key)
        async with TestServer(app) as server:
            base=str(server.make_url('/v1'));monkeypatch.setattr(probe,'EXPERIENTIAL_URL',base)
            await probe.check_access(SimpleNamespace(provider='experiential',base_url=base,model=probe.FREE_MODEL,api_key=key))
    asyncio.run(run())
    output=capsys.readouterr().out;record=json.loads(output.removeprefix('VELIA_QUANTUM_ACCESS '))
    assert seen==['GET'] and record['ok'] and record['status']==200
    assert record['base_model_listed'] and not record['free_alias_listed']
    assert record['key_source']==('VELIA_QUANTUM_API_KEY' if dedicated else 'EXPLABS_API_KEY')
    assert key not in output and 'private-owned-model' not in output


def test_access_diagnostic_never_sends_an_unresolved_railway_reference(monkeypatch,capsys):
    from types import SimpleNamespace
    from desktop import probe_quantum_independence as probe
    def forbidden(*args,**kwargs):pytest.fail('unresolved reference reached HTTP')
    monkeypatch.setattr(probe,'ClientSession',forbidden)
    config=SimpleNamespace(provider='experiential',base_url=probe.EXPERIENTIAL_URL,
        model=probe.FREE_MODEL,api_key='${{deepalpha-bot.EXPLABS_API_KEY}}')
    with pytest.raises(RuntimeError,match='quantum_access_failed'):asyncio.run(probe.check_access(config))
    output=capsys.readouterr().out
    assert 'unresolved_key_reference' in output and 'deepalpha-bot' not in output


@pytest.mark.parametrize('status,code',[(401,'invalid_key'),(403,'model_not_granted')])
def test_planning_logs_exact_provider_rejection_without_secrets_or_a_retry(monkeypatch,caplog,status,code):
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from desktop.quantum_intent import understand_quantum
    from velia_quantum_provider import QuantumError
    seen=[]
    async def run():
        async def provider(request):
            seen.append(request.method)
            return web.json_response({'error':{'code':code,'message':'private provider body'}},status=status)
        app=web.Application();app.router.add_post('/v1/chat/completions',provider)
        async with TestServer(app) as server:
            for key,value in {'VELIA_QUANTUM_ENABLED':'true','VELIA_QUANTUM_PROVIDER':'self_hosted',
                    'VELIA_QUANTUM_BASE_URL':str(server.make_url('/v1')),'VELIA_QUANTUM_MODEL':'owned',
                    'VELIA_QUANTUM_API_KEY':'private-fixture-key'}.items():monkeypatch.setenv(key,value)
            with pytest.raises(QuantumError) as error:await understand_quantum([{'role':'user','content':'private-user-question'}])
            assert error.value.code=='quantum_unavailable'
    asyncio.run(run())
    assert seen==['POST']
    assert f'status={status} provider_code={code}' in caplog.text
    assert 'private' not in caplog.text
