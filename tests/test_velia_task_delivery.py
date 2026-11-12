"""Automatic task acceptance, background queue and existing Pulse transport."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import hashlib
from types import SimpleNamespace
import uuid
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer,TestClient
from desktop.mission_store import MissionStore
from desktop.mission_schema import MissionError
from desktop.mission_routes import setup_mission_routes
from desktop.mission_delivery import MissionDelivery,planning_prompt,parse_plan
from desktop.mission_runtime import MissionTools

USER=SimpleNamespace(user_id=7,id='fixture-session',access='fixture-access')
CHAT=str(uuid.uuid4())
PLAN={'title':'Исследование','steps':[{'id':'s1','title':'Подготовить анализ','tool':'text.draft','arguments':{'instruction':'Подготовить анализ'}}]}
def payload(key='fixture-submit'):
    return {'account':hashlib.sha256(b'https://velia.example:7').hexdigest()[:24],'goal':'Подготовь подробный анализ проекта Velia','model':'velia-flash','time_zone':'Europe/Istanbul','request_id':key,'source':{'chat_id':CHAT,'title':'Исходный чат','remote':False},'context':'user: Velia — ИИ помощник'}
@pytest.fixture
def store(tmp_path):
    s=MissionStore(sqlite_path=tmp_path/'tasks.db',session_key='x'*48);s.initialize();return s
async def permitted(*_):return None
async def identity(_):return {'user_id':7}
class Planner:
    def __init__(self):self.calls=[];self.started=asyncio.Event();self.proceed=asyncio.Event();self.proceed.set();self.question=False
    async def plan(self,s,m,save):
        self.calls.append(('plan',m['model']));self.started.set();await self.proceed.wait()
        return {'question':'Какой период учесть?'} if self.question else deepcopy(PLAN)
    async def preflight(self,*_):pass
    async def execute(self,s,m,step,save):
        self.calls.append(('execute',step['id']));return {'kind':'draft','data':{'text':'Проверенный результат'},'evidence':{'kind':'generated_draft'}}
@asynccontextmanager
async def fixture(store,t=None,upstream=None):
    t=t or Planner();seen=[]
    async def default(method,path,**kw):
        seen.append((method,path,kw))
        if method=='POST' and path.endswith('/pulse'):return 201,{'ok':True,'item':{'item_id':kw['data']['id']}}
        return 200,{'ok':True}
    async def session(req):return SimpleNamespace(**{**vars(USER),'user_id':int(req.headers['X-User'])}) if req.headers.get('X-User') else None
    app=web.Application();setup_mission_routes(app,session_for=session,same_origin=lambda r:r.headers.get('Origin')=='https://velia.example',json_response=lambda d,s=200:web.json_response(d,status=s),upstream=upstream or default,upstream_stream=None,authorize_model=permitted,authenticate=identity,handlers={},store=store,tools=t,origin='https://velia.example')
    async with TestClient(TestServer(app)) as raw:
        class Client:
            async def post(self,url,*,json,headers):
                account=hashlib.sha256(('https://velia.example:'+headers.get('X-User','')).encode()).hexdigest()[:24]
                return await raw.post(url,json={'account':account,**json},headers=headers)
            async def get(self,*args,**kwargs):return await raw.get(*args,**kwargs)
        yield Client(),t,seen
HEAD={'X-User':'7','Origin':'https://velia.example'}
async def wait_for(store,id,status):
    for _ in range(200):
        m=store.detail(7,id)
        if m['status']==status:return m
        await asyncio.sleep(.01)
    raise AssertionError(m)

def test_submit_returns_before_planning_and_finishes_without_browser_poll(store):
    async def scenario():
        t=Planner();t.proceed.clear()
        async with fixture(store,t) as (client,t,seen):
            response=await asyncio.wait_for(client.post('/web-api/v1/missions/submit',json=payload(),headers=HEAD),.5)
            assert response.status==201;initial=(await response.json())['mission'];assert initial['status'] in {'queued','planning'} and not initial['steps']
            await t.started.wait();assert not any(x[0]=='execute' for x in t.calls)
            # No further browser requests are required for execution or delivery.
            t.proceed.set();done=await wait_for(store,initial['id'],'completed')
            assert done['source']['chat_id']==CHAT and done['steps'][0]['result']['data']['text']=='Проверенный результат'
            for _ in range(100):
                if store.inbox(7)[0]['delivery']=='accepted':break
                await asyncio.sleep(.01)
            assert store.inbox(7)[0]['delivery']=='accepted'
            pulse=[x for x in seen if x[1]=='/mobile-api/v1/pulse'];assert len(pulse)==1
            assert pulse[0][2]['data']['respect_quiet'] is True and pulse[0][2]['data']['followup_minutes']==0
            again=await client.post('/web-api/v1/missions/submit',json=payload(),headers=HEAD);assert again.status==201
            assert (await again.json())['mission']['id']==initial['id'] and t.calls==[('plan','velia-flash'),('execute','s1')]
    asyncio.run(scenario())


def test_two_tasks_queue_without_rejecting_second_and_cancelled_queue_never_dispatches(store):
    async def scenario():
        t=Planner();t.proceed.clear()
        async with fixture(store,t) as (client,t,_):
            one=(await (await client.post('/web-api/v1/missions/submit',json=payload('one'),headers=HEAD)).json())['mission'];await t.started.wait()
            response=await client.post('/web-api/v1/missions/submit',json=payload('two'),headers=HEAD);assert response.status==201
            two=(await response.json())['mission'];assert two['status']=='queued'
            cancel=await client.post('/web-api/v1/missions/'+two['id']+'/cancel',json={'revision':two['revision']},headers=HEAD);assert cancel.status==200
            t.proceed.set();await wait_for(store,one['id'],'completed');await asyncio.sleep(.03)
            assert store.detail(7,two['id'])['status']=='cancelled' and len([c for c in t.calls if c[0]=='plan'])==1
    asyncio.run(scenario())


def test_simultaneous_duplicate_submits_execute_once(store):
    async def scenario():
        async with fixture(store) as (client,t,_):
            responses=await asyncio.gather(*[client.post('/web-api/v1/missions/submit',json=payload(),headers=HEAD) for _ in range(3)])
            values=[await r.json() for r in responses];assert all(r.status==201 for r in responses),values
            assert len({v['mission']['id'] for v in values})==1
            await wait_for(store,values[0]['mission']['id'],'completed');assert len([c for c in t.calls if c[0]=='execute'])==1
    asyncio.run(scenario())


def test_question_is_not_completion_and_answer_is_owned_idempotent(store):
    async def scenario():
        t=Planner();t.question=True
        async with fixture(store,t) as (client,t,_):
            m=(await (await client.post('/web-api/v1/missions/submit',json=payload(),headers=HEAD)).json())['mission'];m=await wait_for(store,m['id'],'waiting_input')
            notices=store.inbox(7);assert [n['task_status'] for n in notices]==['waiting_input'] and not any(c[0]=='execute' for c in t.calls)
            assert (await client.get('/web-api/v1/missions/inbox',headers={'X-User':'8'})).status==200
            assert (await (await client.get('/web-api/v1/missions/inbox',headers={'X-User':'8'})).json())['notifications']==[]
            id=notices[0]['id'];assert (await client.post('/web-api/v1/missions/inbox/'+id+'/read',json={},headers={**HEAD,'X-User':'8'})).status==404
            assert (await client.post('/web-api/v1/missions/inbox/'+id+'/read',json={},headers=HEAD)).status==200
            assert store.inbox(7)[0]['status']=='read'
            t.question=False;data={'revision':m['revision'],'answer':'Ноябрь 2026','request_id':'fixture-answer'}
            url='/web-api/v1/missions/'+m['id']+'/answer';r=await client.post(url,json=data,headers=HEAD);assert r.status==200,await r.text()
            done=await wait_for(store,m['id'],'completed');assert 'Ноябрь 2026' in done['goal'] and done['plan_generation']==1
            r=await client.post(url,json=data,headers=HEAD);assert r.status==200 and len([c for c in t.calls if c[0]=='execute'])==1
            assert (await client.post(url,json={**data,'answer':'Другое'},headers=HEAD)).status==409
    asyncio.run(scenario())


def test_remote_source_is_verified_and_chat_deletion_detaches_recovery(store):
    async def scenario():
        seen=[]
        async def upstream(method,path,**kw):
            seen.append((method,path));return 404,{'ok':False}
        async with fixture(store,upstream=upstream) as (client,t,_):
            body=payload();body['source']['remote']=True
            assert (await client.post('/web-api/v1/missions/submit',json=body,headers=HEAD)).status==404
            assert store.transaction(7)['missions']==[] and seen[0][0]=='GET'
        async with fixture(store) as (client,t,_):
            m=(await (await client.post('/web-api/v1/missions/submit',json=payload(),headers=HEAD)).json())['mission']
            assert (await client.post('/web-api/v1/missions/detach-chat',json={'chat_id':CHAT},headers=HEAD)).status==200
            assert store.detail(7,m['id'])['source']['detached'] is True
    asyncio.run(scenario())


def completed(store):
    m=store.create(7,'План',PLAN,'velia-flash','Europe/Istanbul','notice-test');m=store.claim(7,m['id'],1)
    store.checkpoint(7,m['id'],m['lease'],lambda x:x.update(status='completed',lease=None,lease_until=0));return m
@pytest.mark.parametrize('response,expected',[(201,'accepted'),(429,'failed'),(503,'unknown')])
def test_delivery_receipts_are_separate_and_never_rerun_completed_task(store,response,expected):
    async def scenario():
        completed(store);calls=[]
        async def upstream(method,path,**kw):calls.append(kw['data']);return response,{'ok':response==201,'item':{'item_id':kw['data']['id']}}
        d=MissionDelivery(store,upstream,'https://velia.example');await asyncio.gather(d.flush(USER),d.flush(USER));await d.flush(USER)
        assert len(calls)==1 and store.inbox(7)[0]['delivery']==expected
        assert store.transaction(7)['missions'][0]['status']=='completed'
        assert '_payload' not in store.inbox(7)[0]
    asyncio.run(scenario())


def test_crashed_delivery_is_unknown_and_is_not_automatically_resent(store):
    m=completed(store);notice=store.inbox(7)[0];store.notice(7,notice['id'],lambda m,n:n.update(delivery='sending',_sending_at=store.clock()-31))
    assert store.inbox(7)[0]['delivery']=='unknown'


def test_new_worker_rotates_queue_lease_so_an_old_worker_cannot_corrupt_execution(store):
    m=store.create(7,'План',None,'velia-flash','UTC','lease-test',source=payload()['source']);queued=store.enqueue(7,m['id'],1);active=store.activate(7,m['id'],queued['lease'])
    assert active['lease']!=queued['lease'] and active['status']=='planning'
    with pytest.raises(MissionError,match='mission_lease_lost'):store.activate(7,m['id'],queued['lease'])
    with pytest.raises(MissionError,match='mission_lease_lost'):store.checkpoint(7,m['id'],queued['lease'],lambda m:m.update(status='failed'))
    assert store.detail(7,m['id'])['status']=='planning'


def test_plan_is_bounded_typed_and_does_not_accept_model_granted_approval():
    m={**payload(),'context':'x'*2000};m['goal']='г'*6000
    assert len(planning_prompt(m))<=11500
    for raw in ['{"approved":true}',json.dumps({**PLAN,'confirmed':True}),'```json\n{"tool":"shell.exec"}\n```']:
        with pytest.raises(MissionError):parse_plan(raw,'Цель')
    assert parse_plan('```json\n'+json.dumps(PLAN)+'\n```','Цель')==PLAN


def test_background_and_foreground_have_bounded_distinct_admission_lanes():
    from velia_desktop_routes import setup_velia_desktop_routes
    async def auth(*_):return None
    handlers=setup_velia_desktop_routes(web.Application(),auth)
    assert handlers['reserve_background'](7) is None
    assert handlers['reserve'](7) is None
    assert handlers['reserve_background'](8) is not None
    assert handlers['reserve'](8) is not None
    handlers['release'](7)
    assert handlers['reserve'](8) is None
    handlers['release'](8);handlers['release_background'](7)
    assert handlers['reserve_background'](8) is None;handlers['release_background'](8)


def test_browser_account_guard_blocks_wrong_cookie_before_any_creation_or_model_call(store):
    async def scenario():
        async with fixture(store) as (client,t,seen):
            response=await client.post('/web-api/v1/missions/submit',json={**payload(),'account':'another-account'},headers=HEAD)
            assert response.status==403 and (await response.json())['error']=='mission_account_changed'
            assert store.transaction(7)['missions']==[] and t.calls==[] and seen==[]
            r=await client.get('/web-api/v1/missions',headers=HEAD)
            assert (await r.json())['account']==hashlib.sha256(b'https://velia.example:7').hexdigest()[:24]
    asyncio.run(scenario())


def test_reading_task_notice_releases_transport_quota_without_changing_the_task(store):
    async def scenario():
        m=completed(store);n=store.inbox(7)[0];store.notice(7,n['id'],lambda m,n:n.update(read=True,delivery='accepted'))
        seen=[]
        async def upstream(method,path,**kw):
            seen.append((method,path,kw))
            if method=='GET':return 200,{'ok':True,'items':[{'item_id':n['id'],'state':'active','revision':3,'due_at':100}]}
            return 200,{'ok':True}
        d=MissionDelivery(store,upstream,'https://velia.example');await d.flush(USER);await d.flush(USER)
        writes=[c for c in seen if c[0]=='POST'];assert len(writes)==1 and writes[0][2]['data']['action']=='complete'
        assert store.inbox(7)[0]['transport_cleared'] is True and store.detail(7,m['id'])['status']=='completed'
    asyncio.run(scenario())


def test_closed_tab_job_can_refresh_its_existing_session_without_duplicate_rotation(monkeypatch):
    import time
    import desktop.mission_routes as routes
    from desktop.web_routes import Session
    from test_velia_web_chat import fixture,TOKEN,REFRESH,ROTATED
    captured={};original=routes.setup_mission_routes
    def setup(*args,**kwargs):captured['identity']=kwargs['session_identity'];return original(*args,**kwargs)
    monkeypatch.setattr(routes,'setup_mission_routes',setup)
    async def scenario():
        async with fixture(monkeypatch) as (_,_,state):
            session=Session(id=str(uuid.uuid4()),access=TOKEN,refresh=REFRESH,device='fixture-device',user_id=7,access_until=time.time()+5,expires=time.time()+3600)
            first,second=await asyncio.gather(captured['identity'](session),captured['identity'](session))
            assert first['user_id']==7 and second['user_id']==7 and session.access==ROTATED and state['refreshes']==1
            session.expires=time.time()-1;assert await captured['identity'](session) is None and state['refreshes']==1
    asyncio.run(scenario())
