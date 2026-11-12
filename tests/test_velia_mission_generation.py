"""Gateway model transport → durable background task → real PDF regression."""
import asyncio
import base64
from io import BytesIO
import json
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pypdf import PdfReader
import velia_free_models as free_models
import velia_quantum_provider as quantum
from desktop.mission_generation import generate_text
from desktop.mission_runtime import MissionTools,run_mission
from desktop.mission_schema import MissionError
from desktop.mission_store import MissionStore
from test_velia_document_missions import GOAL,BODY,SESSION,allowed,identity


def wire(text=BODY,reason='stop',*,done=True,tool=False):
    delta={'content':text,'reasoning_content':'PRIVATE_REASONING'}
    if tool:delta['tool_calls']=[{'index':0,'function':{'name':'send_email','arguments':'{}'}}]
    value='data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':reason}]},ensure_ascii=False)+'\n\n'
    return (value+('data: [DONE]\n\n' if done else '')).encode()


@pytest.mark.parametrize('model',['velia-flash','velia-quantum','velia-pro'])
@pytest.mark.parametrize('ack_first',[False,True])
def test_gateway_selected_free_model_finishes_failed_task_and_pdf_without_native_chat(tmp_path,monkeypatch,model,ack_first):
    async def scenario():
        observed=[];released=[];reserved=[]
        async def provider(request):
            body=await request.json();observed.append(body)
            assert request.headers['Authorization']=='Bearer fixture-zai-key'
            assert body['messages'][0]['role']=='system'
            assert 'готовый результат' in body['messages'][0]['content']
            assert '3–5 коротких предложений' not in body['messages'][0]['content']
            assert not body.get('tools') and not body.get('tool_choice')
            assert body['thinking']['type']==('enabled' if model=='velia-pro' else 'disabled')
            return web.Response(body=wire('Я подготовлю план. Давайте сначала соберём информацию.' if ack_first and len(observed)==1 else BODY),content_type='text/event-stream')
        server=web.Application();server.router.add_post('/v4/chat/completions',provider)
        async with TestServer(server) as host:
            base=str(host.make_url('/v4'))
            monkeypatch.setattr(quantum,'ZAI_URL',base);monkeypatch.setattr(free_models,'ZAI_URL',base)
            monkeypatch.setenv('VELIA_FREE_API_TIERS_ENABLED','true');monkeypatch.setenv('VELIA_ZAI_API_KEY','fixture-zai-key')
            async def unexpected(*_,**__):raise AssertionError('mission must not enter native conversation/agent routing')
            t=MissionTools(upstream=unexpected,upstream_stream=unexpected,authorize_model=allowed,
                handlers={'generate_background':generate_text,'reserve':lambda _:None,'release':lambda _:None,
                    'reserve_background':reserved.append,'release_background':released.append})
            store=MissionStore(sqlite_path=tmp_path/'gateway.db',session_key='x'*48);store.initialize()
            p=await t.plan(SESSION,{'goal':GOAL,'model':model},unexpected)
            m=store.create(7,GOAL,p,model,'Europe/Istanbul','gateway-regression')
            # Simulate the already-failed two-step task shown in the screenshot.
            claimed=store.claim(7,m['id'],m['revision'])
            def fail(v):
                v['steps'][0].update(status='failed',error='mission_draft_incomplete')
                v.update(status='failed',lease=None,lease_until=0)
            store.checkpoint(7,m['id'],claimed['lease'],fail)
            failed=store.detail(7,m['id']);queued=store.enqueue(7,m['id'],failed['revision'])
            active=store.activate(7,m['id'],queued['lease'])
            await run_mission(store,7,active,SESSION,t,identity)
            completed=store.detail(7,m['id'])
            assert completed['status']=='completed' and len(observed)==(2 if ack_first else 1)
            result=completed['steps'][1]['result'];pdf=base64.b64decode(result['file'])
            text='\n'.join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
            for day in range(1,31):assert f'День {day}:' in text
            assert 'PRIVATE_REASONING' not in text
            assert observed[0]['model']==free_models.TIERS[model.removeprefix('velia-')][1]
            assert reserved==released==[7]*(2 if ack_first else 1)
    asyncio.run(scenario())


@pytest.mark.parametrize('body,error',[
    (wire(reason='length'),'mission_model_incomplete'),
    (wire(done=False),'quantum_invalid_response'),
    (wire(tool=True),'mission_model_unavailable'),
])
def test_mission_transport_rejects_truncation_incomplete_stream_and_unrequested_tools(monkeypatch,body,error):
    async def scenario():
        async def provider(request):return web.Response(body=body,content_type='text/event-stream')
        server=web.Application();server.router.add_post('/v1/chat/completions',provider)
        async with TestServer(server) as host:
            monkeypatch.setenv('VELIA_FREE_API_TIERS_ENABLED','false')
            monkeypatch.setenv('KIMI_BASE_URL',str(host.make_url('/v1')));monkeypatch.setenv('KIMI_API_KEY','fixture-pro-key')
            with pytest.raises((MissionError,quantum.QuantumError),match=error):
                await generate_text('velia-pro',[{'role':'user','content':'Подготовь план'}])
    asyncio.run(scenario())


def test_disabled_free_model_cannot_fall_back_to_paid_or_native_sender(monkeypatch):
    monkeypatch.setenv('VELIA_FREE_API_TIERS_ENABLED','true')
    monkeypatch.delenv('VELIA_ZAI_API_KEY',raising=False);monkeypatch.delenv('VELIA_QUANTUM_API_KEY',raising=False)
    monkeypatch.setenv('KIMI_API_KEY','must-not-be-used')
    with pytest.raises((MissionError,quantum.QuantumError)):
        asyncio.run(generate_text('velia-pro',[{'role':'user','content':'Подготовь план'}]))


def test_background_auth_failure_precedes_generation_and_budget_reservation():
    async def scenario():
        calls=[]
        async def refused(*_):return 'pro_tokens_required',402
        async def unexpected(*_,**__):raise AssertionError('unauthorized model generation')
        t=MissionTools(upstream=unexpected,upstream_stream=unexpected,authorize_model=refused,
            handlers={'generate_background':unexpected,'reserve':calls.append,'release':calls.append})
        with pytest.raises(MissionError,match='pro_tokens_required'):
            await t.draft(SESSION,{'model':'velia-pro'},{},unexpected)
        assert calls==[]
    asyncio.run(scenario())


def test_native_conversation_is_not_required_even_for_an_existing_task(monkeypatch):
    async def scenario():
        seen=[];released=[]
        async def generate(model,messages,**kw):seen.append((model,messages,kw));return BODY
        async def unexpected(*_,**__):raise AssertionError('must not reuse failed native conversation')
        t=MissionTools(upstream=unexpected,upstream_stream=unexpected,authorize_model=allowed,
            handlers={'generate_background':generate,'reserve':lambda _:None,'release':released.append})
        step={'id':'s1','tool':'text.draft','arguments':{'instruction':'План на 30 дней'}}
        m={'model':'velia-flash','goal':GOAL,'time_zone':'Europe/Istanbul','conversation_id':'old-native-conversation',
            'steps':[step],'context':'Контекст предыдущего диалога'}
        result=await t.execute(SESSION,m,step,unexpected)
        assert result['data']['text']==BODY and released==[7] and len(seen)==1
        assert 'Контекст предыдущего диалога' in seen[0][1][-1]['content']
    asyncio.run(scenario())


def test_two_short_completions_stop_without_third_attempt_or_file():
    async def scenario():
        calls=[];reserved=[];released=[]
        async def short(model,messages,**kw):calls.append(messages);return 'Я подготовлю план.'
        async def unexpected(*_,**__):raise AssertionError('native conversation must not be called')
        t=MissionTools(upstream=unexpected,upstream_stream=unexpected,authorize_model=allowed,
            handlers={'generate_background':short,'reserve':reserved.append,'release':released.append})
        step={'id':'s1','tool':'text.draft','arguments':{'instruction':'План на 30 дней'}}
        m={'model':'velia-flash','goal':GOAL,'time_zone':'Europe/Istanbul','steps':[step]}
        with pytest.raises(MissionError,match='mission_draft_incomplete'):
            await t.execute(SESSION,m,step,unexpected)
        assert len(calls)==2 and reserved==released==[7,7]
        assert 'Предыдущая попытка' in calls[1][-1]['content']
    asyncio.run(scenario())


def test_gateway_wires_the_same_selected_model_generator_into_background_handlers(monkeypatch):
    import desktop.gateway as gateway
    observed=[]
    monkeypatch.setenv('VELIA_WEB_ENABLED','true');monkeypatch.setenv('VELIA_WEB_GUEST_ENABLED','false')
    monkeypatch.delenv('VELIA_WEB_SEARCH_ENABLED',raising=False)
    monkeypatch.setattr(gateway,'setup_web_routes',lambda *a,**kw:observed.append(kw['handlers']))
    gateway.create_app(gateway.GatewayConfig('https://identity.example','https://browser.example'),
        check_identity=False,web_origin='https://browser.example')
    assert observed[0]['generate_background'] is generate_text


@pytest.mark.parametrize('free_mode',[False,True])
def test_provider_done_finishes_without_waiting_for_http_eof(monkeypatch,free_mode):
    """Real chunked SSE response remains open after DONE, as production can."""
    async def scenario():
        release=asyncio.Event()
        async def provider(request):
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
            await response.prepare(request);await response.write(wire())
            await release.wait()  # No HTTP EOF until after the client returns.
            return response
        server=web.Application();server.router.add_post('/v1/chat/completions',provider)
        async with TestServer(server) as host:
            monkeypatch.setenv('VELIA_FREE_API_TIERS_ENABLED','true' if free_mode else 'false')
            base=str(host.make_url('/v1'))
            monkeypatch.setattr(quantum,'ZAI_URL',base);monkeypatch.setattr(free_models,'ZAI_URL',base)
            monkeypatch.setenv('VELIA_ZAI_API_KEY','fixture-zai-key')
            monkeypatch.setenv('KIMI_BASE_URL',str(host.make_url('/v1')));monkeypatch.setenv('KIMI_API_KEY','fixture-pro-key')
            try:
                result=await asyncio.wait_for(generate_text('velia-flash' if free_mode else 'velia-pro',[{'role':'user','content':'Подготовь план'}]),1)
                assert result==BODY and not release.is_set()
            finally:release.set()
    asyncio.run(scenario())


def test_slow_provider_timeout_is_typed_and_not_retried(monkeypatch):
    import desktop.mission_generation as generation
    async def scenario():
        release=asyncio.Event();calls=[]
        async def provider(request):
            calls.append(1)
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
            await response.prepare(request);await release.wait();return response
        server=web.Application();server.router.add_post('/v1/chat/completions',provider)
        async with TestServer(server) as host:
            monkeypatch.setenv('VELIA_FREE_API_TIERS_ENABLED','false')
            monkeypatch.setenv('KIMI_BASE_URL',str(host.make_url('/v1')));monkeypatch.setenv('KIMI_API_KEY','fixture-pro-key')
            monkeypatch.setattr(generation,'MODEL_TIMEOUT_SECONDS',.05)
            try:
                with pytest.raises(MissionError,match='mission_model_timeout'):
                    await generate_text('velia-pro',[{'role':'user','content':'Подготовь план'}])
                assert calls==[1]
            finally:release.set()
    asyncio.run(scenario())


def test_text_heartbeat_preserves_ownership_across_old_deadline_and_stops_after_completion(tmp_path,monkeypatch):
    import desktop.mission_runtime as runtime
    async def scenario():
        clock=[1000.0]
        store=MissionStore(sqlite_path=tmp_path/'heartbeat.db',session_key='x'*48,clock=lambda:clock[0]);store.initialize()
        t=MissionTools(upstream=None,upstream_stream=None,authorize_model=allowed,handlers={})
        p=await t.plan(SESSION,{'goal':GOAL},allowed)
        m=store.create(7,GOAL,p,'velia-flash','Europe/Istanbul','slow-text-test')
        active=store.claim(7,m['id'],m['revision']);monkeypatch.setattr(runtime,'HEARTBEAT_SECONDS',.005)
        async def slow():
            # Advance more than the old 180s lease while polling the same task.
            for _ in range(4):
                clock[0]+=100
                await asyncio.sleep(.025)
                current=store.detail(7,m['id'])
                assert current['status']=='running' and current['lease']==active['lease']
                assert current['lease_until']>clock[0]
            return BODY
        assert await runtime.leased_text_call(store,7,m['id'],active['lease'],slow())==BODY
        revision=store.detail(7,m['id'])['revision'];await asyncio.sleep(.025)
        assert store.detail(7,m['id'])['revision']==revision
    asyncio.run(scenario())


def test_lease_loss_cancels_generation_instead_of_saving_a_stale_result(monkeypatch):
    import desktop.mission_runtime as runtime
    async def scenario():
        stopped=asyncio.Event()
        class LostStore:
            clock=lambda _:1000
            def checkpoint(self,*_):raise MissionError('mission_lease_lost',409)
        async def pending():
            try:await asyncio.Event().wait()
            finally:stopped.set()
        monkeypatch.setattr(runtime,'HEARTBEAT_SECONDS',.005)
        with pytest.raises(MissionError,match='mission_lease_lost'):
            await runtime.leased_text_call(LostStore(),7,'task','old-lease',pending())
        assert stopped.is_set()
    asyncio.run(scenario())


def test_text_step_deadline_cancels_call_and_keeps_typed_timeout(monkeypatch):
    import desktop.mission_runtime as runtime
    async def scenario():
        stopped=asyncio.Event()
        class Store:
            clock=lambda _:1000
            def checkpoint(self,*_):pass
        async def pending():
            try:await asyncio.Event().wait()
            finally:stopped.set()
        monkeypatch.setattr(runtime,'TEXT_STEP_TIMEOUT',.03)
        monkeypatch.setattr(runtime,'HEARTBEAT_SECONDS',.005)
        with pytest.raises(MissionError,match='mission_model_timeout'):
            await runtime.leased_text_call(Store(),7,'task','lease',pending())
        assert stopped.is_set()
    asyncio.run(scenario())
