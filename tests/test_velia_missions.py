"""Persistent mission state and actual tool adapter contracts, without provider credentials."""
import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace
import uuid
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from desktop.mission_schema import MissionError,validate_plan,digest
from desktop.mission_store import MissionStore
from desktop.mission_runtime import MissionTools,run_mission
from desktop.mission_routes import setup_mission_routes

SESSION=SimpleNamespace(user_id=7,id='session',access='fixture-access')
CAL={'title':'Обсуждение Velia','start':'2026-11-20T15:00:00+03:00','end':'2026-11-20T15:30:00+03:00','time_zone':'Europe/Istanbul'}
def step(name='text.draft',id='s1',**args):
    return {'id':id,'title':name,'tool':name,'arguments':args or {'instruction':'План проекта Velia'}}
def plan(*steps):return {'title':'План Velia','steps':list(steps or [step()])}
def create(store,p=None,key='fixture-request',user=7,goal='Подготовь план Velia'):
    return store.create(user,goal,p or plan(),'velia-flash','Europe/Istanbul',key)
@pytest.fixture
def store(tmp_path):
    s=MissionStore(sqlite_path=tmp_path/'missions.db',session_key='x'*48);s.initialize();return s
async def identity(_):return {'user_id':7}
async def permitted(*_):return None
async def noop(*_,**__):return 200,{'ok':True}
def tools(**kwargs):return MissionTools(upstream=noop,upstream_stream=None,authorize_model=permitted,handlers={'reserve':lambda _:None,'release':lambda _:None},**kwargs)
class FakeTools:
    def __init__(self,fail=None,preflight=None):self.calls=[];self.fail=fail;self.before=preflight
    async def preview(self,s,m,st):return {'action':st['tool'],**st['arguments']}
    async def preflight(self,s,m,st):
        if self.before:await self.before(s,m,st)
    async def execute(self,s,m,st,save):
        self.calls.append(st['id'])
        if self.fail==st['id']:raise RuntimeError('private provider detail')
        return {'kind':'draft' if st['tool']=='text.draft' else 'data','data':{'text':'План готов'},'evidence':{'tool':st['tool']}}
async def run(store,m,t):
    claimed=store.claim(7,m['id'],m['revision']);await run_mission(store,7,claimed,SESSION,t,identity);return store.detail(7,m['id'])

def test_fixed_plan_validation_and_email_from_goal():
    assert validate_plan(plan(), 'Цель')
    for change in [lambda p:p['steps'][0].update(tool='shell.exec'),lambda p:p['steps'][0]['arguments'].update(approved=True),lambda p:p['steps'].append(deepcopy(p['steps'][0])),lambda p:p.update(confirmed=True)]:
        p=plan();change(p)
        with pytest.raises(MissionError):validate_plan(p,'Цель')
    with pytest.raises(MissionError):validate_plan(plan(step('document.export',format='pdf',title='Файл',source_step='s2'),step(id='s2')),'Цель')
    p=plan(step(),step('gmail.draft','s2',to='test@example.com',subject='План',source_step='s1'))
    with pytest.raises(MissionError,match='mission_recipient_required'):validate_plan(p,'Найди адрес сам')
    assert validate_plan(p,'Подготовь для test@example.com письмо')
    with pytest.raises(MissionError):validate_plan(plan(step('calendar.create',**{**CAL,'end':CAL['start']})),'Встреча')
    with pytest.raises(MissionError,match='mission_step_limit'):validate_plan(plan(step(),step(id='s2'),step(id='s3')),'План')

def test_owner_encryption_idempotency_and_stale_revision(store):
    m=create(store);assert create(store)['id']==m['id']
    with pytest.raises(MissionError,match='mission_request_conflict'):create(store,goal='Другая цель')
    with pytest.raises(MissionError,match='mission_not_found'):store.detail(8,m['id'])
    assert store.transaction(8)['missions']==[]
    assert store.owner(7)!=store.owner(8)
    with sqlite3.connect(store.sqlite_path) as conn:
        raw=conn.execute('SELECT encrypted FROM velia_mission_workspace WHERE owner_hash=?',(store.owner(7),)).fetchone()[0]
    assert 'Подготовь' not in raw and 'План' not in raw
    claimed=store.claim(7,m['id'],1);assert claimed['lease']
    assert store.claim(7,m['id'],1) is None
    with pytest.raises(MissionError,match='mission_revision_conflict'):store.control(7,m['id'],1,'cancel')
    public=store.public(claimed);assert 'lease' not in public and 'conversation_id' not in public

@pytest.mark.parametrize('action',['pause','cancel'])
def test_pause_or_cancel_at_dispatch_barrier_prevents_effect(store,action):
    async def scenario():
        m=create(store)
        async def stop(s,m,st):store.control(7,m['id'],store.detail(7,m['id'])['revision'],action)
        t=FakeTools(preflight=stop);done=await run(store,m,t)
        assert t.calls==[] and done['status']==('paused' if action=='pause' else 'cancelled')
        assert done['steps'][0]['status']=='pending'
    asyncio.run(scenario())

def test_resume_skips_completed_steps_and_failure_does_not_expose_exception(store):
    async def scenario():
        m=create(store,plan(step(),step('notebook.save','s2',source_step='s1',title='План')));t=FakeTools(fail='s2');failed=await run(store,m,t)
        assert failed['status']=='failed' and failed['steps'][0]['status']=='completed'
        assert failed['steps'][1]['error']=='mission_step_failed'
        t.fail=None;done=await run(store,failed,t)
        assert done['status']=='completed' and t.calls==['s1','s2','s2']
    asyncio.run(scenario())

def test_external_action_waits_for_exact_single_use_approval_and_uncertain_is_not_replayed(store):
    async def scenario():
        m=create(store,plan(step('calendar.create',**CAL)));t=FakeTools();waiting=await run(store,m,t)
        assert waiting['status']=='awaiting_approval' and t.calls==[]
        approval=waiting['steps'][0]['approval'];assert approval['hash']==digest(approval['preview'])
        with pytest.raises(MissionError,match='mission_approval_invalid'):store.approve(7,m['id'],waiting['revision'],'wrong')
        approved=store.approve(7,m['id'],waiting['revision'],approval['token'])
        with pytest.raises(MissionError):store.approve(7,m['id'],waiting['revision'],approval['token'])
        t.fail='s1';uncertain=await run(store,approved,t)
        assert uncertain['status']=='uncertain' and t.calls==['s1']
        with pytest.raises(MissionError):store.claim(7,m['id'],uncertain['revision'])
        with pytest.raises(MissionError,match='mission_execution_uncertain'):store.control(7,m['id'],uncertain['revision'],'pause')
    asyncio.run(scenario())

def test_expired_approval_refresh_rotates_token(store):
    async def scenario():
        m=await run(store,create(store,plan(step('calendar.create',**CAL))),FakeTools());old=m['steps'][0]['approval'];store.clock=lambda:time.time()+601
        with pytest.raises(MissionError,match='mission_approval_expired'):store.approve(7,m['id'],m['revision'],old['token'])
        refreshed=store.approve(7,m['id'],m['revision'],None,refresh=True)
        assert refreshed['steps'][0]['approval']['token']!=old['token']
        with pytest.raises(MissionError):store.approve(7,m['id'],refreshed['revision'],old['token'])
    asyncio.run(scenario())

@pytest.mark.parametrize('external,dispatched,expected',[(False,True,'interrupted'),(True,False,'interrupted'),(True,True,'uncertain')])
def test_restart_recovers_read_steps_but_never_replays_dispatched_external_write(store,external,dispatched,expected):
    m=create(store,plan(step('calendar.create',**CAL)) if external else None);m=store.claim(7,m['id'],1);store.begin(7,m['id'],m['lease'])
    if dispatched:store.ready(7,m['id'],m['lease'],'s1')
    store.clock=lambda:time.time()+181;recovered=store.detail(7,m['id']);assert recovered['status']==expected
    assert recovered['steps'][0]['status']==('uncertain' if expected=='uncertain' else 'pending')

def test_missing_google_fails_safely_before_dispatch_and_approval_is_rechecked(store):
    async def scenario():
        t=tools();m=await run(store,create(store,plan(step('calendar.create',**CAL))),t)
        m=store.approve(7,m['id'],m['revision'],m['steps'][0]['approval']['token']);failed=await run(store,m,t)
        assert failed['status']=='failed' and failed['steps'][0]['error']=='mission_google_required'
        assert failed['steps'][0]['dispatched'] is False
        altered=deepcopy(m);altered['steps'][0]['arguments']['title']='Подменённый заголовок'
        with pytest.raises(MissionError,match='mission_approval_required'):await t.execute(SESSION,altered,altered['steps'][0],noop)
    asyncio.run(scenario())

@pytest.mark.parametrize('fmt',['pdf','docx','pptx','xlsx'])
def test_real_file_bytes_and_digest(fmt):
    async def scenario():
        p=plan(step(),step('document.export','s2',source_step='s1',title='Velia',format=fmt));m={'steps':p['steps']}
        m['steps'][0].update(status='completed',result={'kind':'draft','data':{'text':'# Velia\nПлан развития проекта.'}})
        result=await tools().execute(SESSION,m,m['steps'][1],noop);body=base64.b64decode(result['file'])
        assert body.startswith(b'%PDF' if fmt=='pdf' else b'PK')
        assert len(body)==result['data']['bytes'] and hashlib.sha256(body).hexdigest()==result['data']['sha256']
    asyncio.run(scenario())

def test_native_generation_uses_selected_model_and_canonical_completion():
    async def scenario():
        seen=[];released=[];cid=str(uuid.uuid4());saved=[]
        async def upstream(method,path,**kw):seen.append((method,path,kw));return 201,{'ok':True,'conversation':{'id':cid}}
        async def stream(path,**kw):
            seen.append(('STREAM',path,kw));yield b'data: '+json.dumps({'type':'delta','text':'wrong partial'}).encode()+b'\n\n'
            yield b'data: '+json.dumps({'type':'complete','result':{'assistant_message':{'content':'Проверенный черновик'}}}).encode()+b'\n\n'
        t=MissionTools(upstream=upstream,upstream_stream=stream,authorize_model=permitted,handlers={'reserve':lambda _:None,'release':released.append})
        async def save(cid):saved.append(cid)
        for model in ['velia-flash','velia-quantum','velia-pro']:
            m={'id':str(uuid.uuid4()),'title':'План','goal':'Подготовь план','time_zone':'UTC','model':model,'steps':plan()['steps']}
            result=await t.execute(SESSION,m,m['steps'][0],save);assert result['data']['text']=='Проверенный черновик'
            payload=seen[-1][2]['data'];assert payload['chat_mode']==model.removeprefix('velia-') and payload['web_search'] is False
            assert payload['idempotency_key'].endswith(':s1')
        assert released==[7,7,7] and saved==[cid,cid,cid]
    asyncio.run(scenario())

def test_notebook_encryption_search_respects_disabled_memory_and_save_is_idempotent():
    async def scenario():
        key=b'x'*32;owner='fixture-owner';entries=[{'id':'n1','kind':'note','title':'Velia','content':'Данные проекта','tags':[]},{'id':'memory:settings','kind':'memory-settings','enabled':False},{'id':'m1','kind':'memory','title':'Velia','content':'Скрытая память','memory':{'enabled':True}}]
        state={'revision':1,'entries':entries};calls=[]
        async def upstream(method,path,**kw):
            assert path=='/mobile-api/v1/notebook'
            if method=='PUT':
                calls.append(kw['data']);d=kw['data'];assert d['revision']==state['revision'];assert d['account']==hashlib.sha256(b'https://velyoncore.ai:7').hexdigest()[:24]
                r=d['record'];state['entries']=json.loads(AESGCM(key).decrypt(base64.b64decode(r['iv']),base64.b64decode(r['data']),('velia-notebook-v1:'+owner).encode()))['entries'];state['revision']+=1;return 200,{'ok':True}
            iv=b'1'*12;raw=json.dumps({'version':1,'entries':state['entries']}).encode();return 200,{'ok':True,'owner':owner,'revision':state['revision'],'encryption_key':key.hex(),'record':{'version':1,'iv':base64.b64encode(iv).decode(),'data':base64.b64encode(AESGCM(key).encrypt(iv,raw,('velia-notebook-v1:'+owner).encode())).decode()}}
        t=tools(origin='https://velyoncore.ai');t.upstream=upstream
        result=await t.execute(SESSION,{},step('notebook.search',query='Velia'),noop);assert [r['id'] for r in result['data']['records']]==['n1']
        m={'id':str(uuid.uuid4()),'steps':plan(step(),step('notebook.save','s2',source_step='s1',title='Сохранённый план'))['steps']};m['steps'][0].update(status='completed',result={'kind':'draft','data':{'text':'План'}})
        for _ in range(2):assert (await t.execute(SESSION,m,m['steps'][1],noop))['data']['saved']
        assert len(calls)==1 and state['entries'][0]['content']=='План'
    asyncio.run(scenario())


def test_actual_google_bridge_receipt_and_no_send_tool():
    from desktop.workspace_connections import WorkspaceConnector
    async def scenario():
        calls=[]
        async def api(c,method,path,**kw):
            calls.append((method,path,kw))
            if path=='/v1/userinfo':return {'sub':'fixture','email':'fixture@example.com'}
            if path.endswith('/drafts'):return {'id':'draft123'}
            return {'id':'event123','summary':CAL['title'],'start':{'dateTime':CAL['start']},'end':{'dateTime':CAL['end']}}
        bridge=WorkspaceConnector('google',api);await bridge.connect(('7','session'),'fixture-access-token',oauth_expires=time.time()+3600)
        t=tools(google=bridge);m={'id':str(uuid.uuid4()),'steps':plan(step('calendar.create',**CAL))['steps']};st=m['steps'][0]
        st['approved']={'hash':digest(await t.preview(SESSION,m,st)),'until':time.time()+100,'at':time.time()}
        for _ in range(2):assert (await t.execute(SESSION,m,st,noop))['data']['created']
        assert len([c for c in calls if c[0]=='POST'])==1
        assert calls[-1][2]['params']['sendUpdates']=='none'
        assert 'gmail_send' not in bridge.tools
    asyncio.run(scenario())


def test_mission_http_auth_origin_owner_download_and_run(tmp_path):
    async def scenario():
        store=MissionStore(sqlite_path=tmp_path/'http.db',session_key='x'*48);t=FakeTools();t.execute=tools().execute
        async def session(req):return SimpleNamespace(**{**vars(SESSION),'user_id':int(req.headers['X-User'])}) if req.headers.get('X-User') else None
        app=web.Application();setup_mission_routes(app,session_for=session,same_origin=lambda req:req.headers.get('Origin')=='https://velia.example',json_response=lambda d,s=200:web.json_response(d,status=s),upstream=noop,upstream_stream=None,authorize_model=permitted,authenticate=identity,handlers={},store=store,tools=t)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/web-api/v1/missions')).status==401
            h={'Origin':'https://velia.example','X-User':'7'};account=hashlib.sha256(b':7').hexdigest()[:24];p=plan(step(),step('document.export','s2',source_step='s1',title='Файл',format='pdf'))
            body={'account':account,'goal':'План','plan':p,'model':'velia-flash','time_zone':'UTC','request_id':'http-request'}
            assert (await client.post('/web-api/v1/missions',json=body,headers={'X-User':'7'})).status==403
            assert (await client.post('/web-api/v1/missions',json={**body,'approved':True},headers=h)).status==400
            response=await client.post('/web-api/v1/missions',json=body,headers=h);value=await response.json();assert response.status==201,value
            m=value['mission'];url='/web-api/v1/missions/'+m['id']
            assert (await client.get(url,headers={'X-User':'8'})).status==404
            # Persist an actual draft, then run the real PDF adapter.
            claimed=store.claim(7,m['id'],1);store.begin(7,m['id'],claimed['lease']);store.checkpoint(7,m['id'],claimed['lease'],lambda target:target['steps'][0].update(status='completed',result={'kind':'draft','data':{'text':'План Velia'},'evidence':{}}))
            store.control(7,m['id'],store.detail(7,m['id'])['revision'],'pause')
            m=store.detail(7,m['id']);response=await client.post(url+'/run',headers=h,json={'revision':m['revision'],'account':account});assert response.status==200,await response.text()
            for _ in range(100):
                await asyncio.sleep(.01);m=store.detail(7,m['id'])
                if m['status'] not in {'running','pausing'}:break
            assert m['status']=='completed',m
            detail=await (await client.get(url,headers=h)).json();assert 'file' not in detail['mission']['steps'][1]['result']
            download=await client.get(url+'/files/s2',headers=h);assert download.status==200 and (await download.read()).startswith(b'%PDF')
            assert download.headers['Cache-Control']=='private, no-store'
            assert (await client.get(url+'/files/s2',headers={'X-User':'8'})).status==404
            assert (await client.delete(url,headers=h,json={'revision':m['revision'],'account':account})).status==200
            assert (await client.get(url,headers=h)).status==404
    asyncio.run(scenario())


def test_mission_planner_uses_selected_model_disables_search_and_static_assets(monkeypatch,tmp_path):
    from test_velia_web_chat import fixture,headers,login
    from desktop.guest_store import GuestStore
    async def scenario():
        async with fixture(monkeypatch,quantum_api=True,features={'chat_quantum':True},with_search=True,guest_store=GuestStore(sqlite_path=tmp_path/'quota.db')) as (server,client,state):
            url=server.make_url('/web-api/v1/missions/plan');body={'model':'velia-flash','messages':[{'role':'user','content':'План миссии JSON'}],'stream':True,'web_search':True}
            assert (await client.post(url,headers=headers(),json=body)).status==401
            cookie,_,_=await login(server,client)
            assert (await client.post(url,headers=headers(cookie,Origin='https://evil.example'),json=body)).status==403
            for model in ['velia-flash','velia-quantum','velia-pro']:
                r=await client.post(url,headers=headers(cookie),json={**body,'model':model});assert r.status==200 and '[DONE]' in await r.text()
            assert not state['search_queries'] and not state['intent_payloads']
            assert state['payloads'][0]['max_tokens']==1536
            for name in ['missions.mjs','mission-ui.mjs']:
                r=await client.get(server.make_url('/web/'+name));assert r.status==200 and r.headers['X-Content-Type-Options']=='nosniff'
    asyncio.run(scenario())


def test_cancel_during_external_write_preserves_receipt_and_stops_next_step(store):
    async def scenario():
        started,release=asyncio.Event(),asyncio.Event()
        class Blocking(FakeTools):
            async def execute(self,s,m,st,save):
                self.calls.append(st['id']);started.set();await release.wait();return {'kind':'data','data':{'created':True},'evidence':{'kind':'provider_receipt'}}
        t=Blocking();m=await run(store,create(store,plan(step('calendar.create',**CAL),step(id='s2'))),t)
        m=store.approve(7,m['id'],m['revision'],m['steps'][0]['approval']['token']);claimed=store.claim(7,m['id'],m['revision'])
        task=asyncio.create_task(run_mission(store,7,claimed,SESSION,t,identity));await started.wait()
        store.control(7,m['id'],store.detail(7,m['id'])['revision'],'cancel');release.set();await task
        done=store.detail(7,m['id']);assert done['status']=='cancelled' and done['steps'][0]['status']=='completed' and done['steps'][0]['result']['data']['created']
        assert done['steps'][1]['status']=='pending' and t.calls==['s1']
    asyncio.run(scenario())


def test_workspace_reserves_room_for_checkpoints_before_dispatch(store):
    first=create(store);store.transaction(7,lambda doc:doc['missions'][0].update(extra='x'*(2500*1024)))
    second=create(store,key='other-request')
    with pytest.raises(MissionError,match='mission_storage_full'):store.claim(7,second['id'],1)
    assert store.detail(7,second['id'])['status']=='draft'


def test_review_requires_current_approval_and_reauth_failure_stops_execution(store):
    async def scenario():
        t=FakeTools();m=await run(store,create(store,plan(step(),step('review','s2',source_step='s1'))),t)
        assert m['status']=='awaiting_approval' and t.calls==['s1']
        m=store.approve(7,m['id'],m['revision'],m['steps'][1]['approval']['token']);done=await run(store,m,t)
        assert done['status']=='completed' and t.calls==['s1','s2']
        other=create(store,key='auth-request');claimed=store.claim(7,other['id'],1)
        async def revoked(_):return None
        await run_mission(store,7,claimed,SESSION,t,revoked)
        assert store.detail(7,other['id'])['status']=='failed' and t.calls==['s1','s2']
    asyncio.run(scenario())


def test_actual_finance_and_crm_tools_read_saved_owner_records(tmp_path):
    from desktop.finance_store import FinanceStore
    from desktop.crm_store import CrmStore
    from test_velia_finance import snapshot
    async def scenario():
        finance=FinanceStore(sqlite_path=tmp_path/'finance.db',session_key='x'*48);finance.initialize();saved=finance.save(7,snapshot(),'')
        crm=CrmStore(sqlite_path=tmp_path/'crm.db',session_key='x'*48);crm.initialize();crm.commit(7,{'type':'add_contact','contact':{'name':'Анна','company':'Velia'}},'','fixture-contact')
        t=tools(finance=finance,crm=crm);m={'time_zone':'Europe/Istanbul'}
        result=await t.execute(SESSION,m,step('finance.report',reserve_months=6),noop)
        assert result['data']['rows'][0]['available']=='850' and result['evidence']['revision']==saved['revision']
        contacts=await t.execute(SESSION,m,step('crm.search',query='Анна'),noop)
        assert contacts['data']['total']==1 and contacts['data']['contacts'][0]['name']=='Анна'
        other=SimpleNamespace(**{**vars(SESSION),'user_id':8})
        assert (await t.execute(other,m,step('crm.search',query='Анна'),noop))['data']['total']==0
    asyncio.run(scenario())


def test_draft_source_budget_fits_native_input_limit_and_marks_truncation():
    async def scenario():
        observed=[]
        async def stream(path,**kw):
            observed.append(kw['data']['content']);yield b'data: '+json.dumps({'type':'complete','result':{'assistant_message':{'content':'Сводка'}}}).encode()+b'\n\n'
        t=tools();t.stream=stream
        prior=[{'id':'s'+str(i),'tool':'web.search','status':'completed','result':{'data':{'content':'Данные'*5000},'evidence':{'kind':'web_sources','tool':'web.search','checked_at':time.time()}}} for i in range(1,7)]
        draft=step(id='s7',instruction='План'*500);m={'id':str(uuid.uuid4()),'title':'План','goal':'Цель'*1500,'time_zone':'UTC','model':'velia-pro','conversation_id':str(uuid.uuid4()),'steps':[*prior,draft]}
        assert (await t.execute(SESSION,m,draft,noop))['data']['text']=='Сводка'
        assert len(observed[0])<=11500 and '"truncated": true' in observed[0]
    asyncio.run(scenario())
