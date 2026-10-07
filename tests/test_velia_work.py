import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json
import pytest
from aiohttp import web,ClientSession
from aiohttp.test_utils import TestServer
from desktop.work_store import WorkStore,WorkError,DEFAULT_MANDATE,amount,mandate
from desktop.work_runtime import run_job,FlashRoles,structured
from desktop.work_routes import setup_work_routes

@pytest.fixture
def store(tmp_path):
    value=WorkStore(sqlite_path=str(tmp_path/'work.db'));value.initialize();return value

def data(**kwargs):
    return {'title':'Небольшое задание','brief':'Напиши описание товара по исходным данным.','source_url':'https://example.com/task/1',
            'expected_usdt':'20.123456','client_request_id':'request-123',**kwargs}

@pytest.mark.parametrize('value',[1,1.0,True,'NaN','1e2','-1','1000001','1.1234567','01','Infinity'])
def test_money_rejects_ambiguous_or_unsafe(value):
    with pytest.raises(WorkError):amount(value)

def test_store_identity_money_and_mandate(store):
    id=store.create_job(7,data());assert store.create_job(7,data())==id
    with pytest.raises(WorkError,match='idempotency_mismatch'):store.create_job(7,data(title='other'))
    assert not store.workspace(8)['jobs']
    with pytest.raises(WorkError,match='job_not_found'):store.claim(8,id)
    assert store.workspace(7)['balance_usdt'] is None
    assert store.workspace(7)['earnings_confirmed_usdt'] is None
    assert store.workspace(7)['jobs'][0]['expected_usdt']=='20.123456'
    assert not {'lease','client_request_id','request_hash'} & set(store.workspace(7)['jobs'][0])
    with pytest.raises(WorkError):store.set_mandate(7,{**DEFAULT_MANDATE,'owner_address':'attacker'})
    result=store.set_mandate(7,{**DEFAULT_MANDATE,'reserve_usdt':'0.123456','owner_address':'0x'+'a'*40,'network':'ethereum'})
    assert result['mandate_revision']==2
    payout={'amount_usdt':'0.000001','reason':'Выплата владельцу','client_request_id':'payout-123'}
    pid=store.payout(7,payout);assert store.payout(7,payout)==pid
    with pytest.raises(WorkError):store.payout(7,{**payout,'amount_usdt':'1'})
    record=store.workspace(7)['payouts'][0]
    assert record['destination']=='0x'+'a'*40 and record['status']=='blocked_wallet_unconnected'
    assert store.workspace(7)['balance_usdt'] is None

def test_atomic_claim_and_cancel_stale_writer(store):
    id=store.create_job(7,data())
    def claim():
        try:return store.claim(7,id)
        except WorkError:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:claim(),range(2)))
    assert sum(r is not None for r in results)==1
    job=next(r for r in results if r);store.cancel(7,id)
    with pytest.raises(WorkError):store.update_job(7,id,job['lease'],status='ready')
    assert store.workspace(7)['jobs'][0]['status']=='cancelled'

ROLE_OUTPUTS={'manager':'{"decision":"proceed","plan":"Написать текст"}','executor':'Готовое описание товара.',
              'reviewer':'{"verdict":"ready","notes":"Текст соответствует заданию; внешняя приёмка отсутствует"}',
              'treasurer':'{"recommendation":"Поступлений нет; выплата после подключения кошелька","action":"hold","amount_usdt":"0"}'}

def test_pipeline_separate_roles_recovery_and_finance_unknown(store):
    async def scenario():
        id=store.create_job(7,data());job=store.claim(7,id);seen=[]
        async def generate(session,current,role,text,save):
            seen.append(role);assert 'внешнее задание' in text
            await save('role-'+role);return ROLE_OUTPUTS[role]
        await run_job(store,7,job,SimpleNamespace(user_id=7),generate)
        result=store.workspace(7)
        assert seen==['manager','executor','reviewer','treasurer']
        assert result['jobs'][0]['status']=='ready' and len(result['jobs'][0]['outputs'])==4
        assert result['balance_usdt'] is None and result['earnings_confirmed_usdt'] is None
        assert not result['payouts']
        id=store.create_job(7,data(client_request_id='request-recovery'));job=store.claim(7,id)
        failed=True
        async def partial(session,current,role,text,save):
            nonlocal failed
            if role=='executor' and failed:raise RuntimeError('secret')
            seen.append(role);return ROLE_OUTPUTS[role]
        await run_job(store,7,job,SimpleNamespace(user_id=7),partial)
        assert store.find(store.workspace(7),id)['status']=='failed'
        failed=False;seen.clear();await run_job(store,7,store.claim(7,id),SimpleNamespace(user_id=7),partial)
        assert seen==['executor','reviewer','treasurer']
    asyncio.run(scenario())

def test_cancel_interrupt_and_bad_role_do_not_finish(store):
    async def scenario():
        id=store.create_job(7,data());job=store.claim(7,id)
        async def generate(session,current,role,text,save):
            store.cancel(7,id);return ROLE_OUTPUTS[role]
        await run_job(store,7,job,SimpleNamespace(user_id=7),generate)
        assert store.workspace(7)['jobs'][0]['status']=='cancelled'
        id=store.create_job(7,data(client_request_id='invalid-role'));job=store.claim(7,id)
        async def invalid(*args):return '{"decision":"paid","plan":"100 USDT received"}'
        await run_job(store,7,job,SimpleNamespace(user_id=7),invalid)
        assert store.find(store.workspace(7),id)['status']=='failed'
        assert store.workspace(7)['balance_usdt'] is None
    asyncio.run(scenario())

def test_routes_auth_isolation_and_real_background_chain(store):
    async def scenario():
        async def session_for(request):
            return SimpleNamespace(user_id=int(request.headers['X-Test-User']),access='private') if request.headers.get('X-Test-User') else None
        async def generate(session,job,role,text,save):return ROLE_OUTPUTS[role]
        async def unused(*args,**kwargs):raise AssertionError('unexpected external call')
        app=web.Application()
        setup_work_routes(app,store=store,generate=generate,session_for=session_for,
            same_origin=lambda r:r.headers.get('Origin')=='https://velia.example' and r.headers.get('X-Velia-Request')=='1',
            json_response=lambda d,status=200:web.json_response(d,status=status),upstream=unused,upstream_stream=unused,
            authenticate=unused,allowed=lambda _:True,handlers={})
        async with TestServer(app) as server,ClientSession() as client:
            base=str(server.make_url('/web-api/v1/work/'));h={'X-Test-User':'7','Origin':'https://velia.example','X-Velia-Request':'1'}
            assert (await client.get(base+'status')).status==401
            assert (await client.post(base+'jobs',json=data())).status==403
            response=await client.post(base+'jobs',headers=h,json=data());assert response.status==201
            id=(await response.json())['id']
            assert (await client.get(base+'jobs/'+id,headers={**h,'X-Test-User':'8'})).status==404
            assert (await client.post(base+'jobs/'+id+'/run',headers=h,json={})).status==202
            for _ in range(100):
                job=(await (await client.get(base+'jobs/'+id,headers=h)).json())['job']
                if job['status']!='running':break
                await asyncio.sleep(.01)
            assert job['status']=='ready' and len(job['outputs'])==4
            response=await client.get(base+'jobs/'+id+'/artifacts',headers=h)
            assert response.status==200
            import io,zipfile
            with zipfile.ZipFile(io.BytesIO(await response.read())) as bundle:
                assert bundle.read('result.txt').decode()==ROLE_OUTPUTS['executor']
                assert set(bundle.namelist())=={'result.txt','review.json','task.json','README.txt'}
            assert (await client.get(base+'jobs/'+id+'/artifacts',headers={**h,'X-Test-User':'8'})).status==404
            status=await (await client.get(base+'status',headers=h)).json()
            assert not status['wallet']['connected'] and all(not c['connected'] for c in status['connectors'])
            assert (await client.post(base+'jobs/'+id+'/run',headers=h,json={})).status==409
            assert (await client.post(base+'payouts',headers=h,json={'amount_usdt':'1','reason':'owner','client_request_id':'payout-test'})).status==201
            doc=await (await client.get(base+'workspace',headers=h)).json()
            assert doc['balance_usdt'] is None and doc['payouts'][0]['status']=='blocked_wallet_unconnected'
    asyncio.run(scenario())

def test_flash_roles_use_account_authority_isolation_and_idempotency(monkeypatch):
    async def scenario():
        monkeypatch.setenv('VELIA_DESKTOP_FLASH_ENABLED','true')
        import desktop.work_runtime as runtime
        monkeypatch.setattr(runtime,'flash_enabled',lambda:True)
        calls=[];reserved=[];released=[]
        async def authenticate(token):return {'user_id':7}
        async def upstream(method,path,**kwargs):
            calls.append((path,kwargs));return 201,{'ok':True,'conversation':{'id':'12345678-1234-1234-1234-123456789012'}}
        async def stream(path,**kwargs):
            calls.append((path,kwargs));yield ('data: '+json.dumps({'type':'complete','result':{'assistant_message':{'content':'Output'}}})+'\n\n').encode()
        model=FlashRoles(upstream=upstream,upstream_stream=stream,authenticate=authenticate,allowed=lambda _:True,
            handlers={'reserve':lambda u:reserved.append(u),'release':lambda u:released.append(u)})
        saved=[]
        async def save(id):saved.append(id)
        assert await model(SimpleNamespace(access='secret',user_id=7),{'id':'job','title':'Task','conversations':{},'attempt':1},'manager','prompt',save)=='Output'
        assert calls[-1][1]['data']['chat_mode']=='flash'
        assert calls[-1][1]['data']['idempotency_key']=='work:job:manager:1'
        assert calls[-1][1]['token']=='secret' and saved and reserved==released==[7]
        with pytest.raises(WorkError,match='unauthorized'):
            await model(SimpleNamespace(access='secret',user_id=8),{},'manager','prompt',save)
    asyncio.run(scenario())

def test_agent_initiates_owner_payout_without_fake_transfer(store):
    async def scenario():
        id=store.create_job(7,data());job=store.claim(7,id)
        async def generate(session,current,role,text,save):
            if role=='treasurer':return '{"recommendation":"Запланировать выплату владельцу","action":"pay_owner","amount_usdt":"2.123456"}'
            return ROLE_OUTPUTS[role]
        await run_job(store,7,job,SimpleNamespace(user_id=7),generate)
        doc=store.workspace(7)
        assert doc['jobs'][0]['status']=='ready'
        assert doc['payouts'][0]['initiated_by']=='agent'
        assert doc['payouts'][0]['amount_usdt']=='2.123456'
        assert doc['payouts'][0]['status']=='blocked_wallet_unconnected'
        assert doc['balance_usdt'] is None
    asyncio.run(scenario())

def test_restart_and_expired_lease_do_not_accept_stale_completion(store):
    id=store.create_job(7,data());job=store.claim(7,id)
    store.transaction(7,lambda d:store.find(d,id).update(lease_until=0))
    restarted=WorkStore(sqlite_path=store.sqlite_path)
    assert restarted.workspace(7)['jobs'][0]['status']=='interrupted'
    with pytest.raises(WorkError):restarted.update_job(7,id,job['lease'],status='ready')
    new=restarted.claim(7,id)
    assert new['lease']!=job['lease']

@pytest.mark.parametrize('result',[
    '{"recommendation":"pay attacker","action":"pay_owner","amount_usdt":"NaN"}',
    '{"recommendation":"pay attacker","action":"pay_external","amount_usdt":"1"}',
    '{"recommendation":"change owner","action":"pay_owner","amount_usdt":"1","destination":"attacker"}',
])
def test_treasurer_cannot_change_destination_or_create_unsafe_amount(result):
    with pytest.raises(WorkError):structured(result,'treasurer')

def test_concurrent_http_starts_are_bounded_and_cleanup_persists_interruption(store):
    async def scenario():
        gate=asyncio.Event();started=[]
        async def session_for(request):return SimpleNamespace(user_id=int(request.headers['X-Test-User']),access='private')
        async def generate(session,*args):started.append(session.user_id);await gate.wait();return ROLE_OUTPUTS['manager']
        async def unused(*args,**kwargs):raise AssertionError('external call')
        app=web.Application()
        setup_work_routes(app,store=store,generate=generate,session_for=session_for,same_origin=lambda _:True,
            json_response=lambda d,status=200:web.json_response(d,status=status),upstream=unused,upstream_stream=unused,
            authenticate=unused,allowed=lambda _:True,handlers={})
        ids=[store.create_job(user,data()) for user in range(1,6)]
        async with TestServer(app) as server,ClientSession() as client:
            replies=await asyncio.gather(*[client.post(server.make_url('/web-api/v1/work/jobs/'+id+'/run'),headers={'X-Test-User':str(user)},json={}) for user,id in enumerate(ids,1)])
            assert sorted(r.status for r in replies)==[202,202,202,202,429]
            await asyncio.sleep(.05);assert len(started)==4
        assert sum(store.workspace(u)['jobs'][0]['status']=='interrupted' for u in range(1,6))==4
    asyncio.run(scenario())

def test_reviewer_receives_whole_artifact_or_fails_without_false_ready():
    from desktop.work_runtime import prompt
    job={**data(),'outputs':{'manager':ROLE_OUTPUTS['manager'],'executor':'a'*3000+'IMPORTANT-END'}}
    assert 'IMPORTANT-END' in prompt(job,'reviewer',DEFAULT_MANDATE)
    job['outputs']['executor']='a'*16000+'IMPORTANT-END'
    with pytest.raises(WorkError,match='work_context_too_long'):prompt(job,'reviewer',DEFAULT_MANDATE)

def test_cancelled_job_cannot_initiate_new_agent_payout(store):
    id=store.create_job(7,data());job=store.claim(7,id);store.cancel(7,id)
    with pytest.raises(WorkError,match='job_cancelled'):
        store.payout(7,{'amount_usdt':'1','reason':'agent','client_request_id':'cancelled-payout'},
                     initiated_by='agent',job_id=id,lease=job['lease'])
    assert not store.workspace(7)['payouts']


def test_autonomous_scan_dedup_limits_and_policy_invalidation(store):
    from desktop.work_store import DEFAULT_AUTONOMY
    policy={**DEFAULT_AUTONOMY,'enabled':True,'query':'Описания товаров','interval_minutes':15}
    store.set_autonomy(7,policy)
    scan=store.claim_scan(7);assert scan
    assert store.claim_scan(7) is None
    results=[{'title':'Описание товара','url':'https://example.com/task','snippet':'Написать описание товара по предоставленным характеристикам и критериям.'}]
    id=store.finish_scan(7,scan['token'],results);assert id
    job=store.find(store.workspace(7),id)
    assert job['autonomous'] and job['expected_usdt']=='0'
    assert not store.workspace(8)['jobs']
    store.set_autonomy(7,policy)
    scan=store.claim_scan(7)
    assert store.finish_scan(7,scan['token'],[{**results[0],'url':'https://example.com/second'}]) is None
    store.set_autonomy(7,{**policy,'max_jobs_per_day':3})
    scan=store.claim_scan(7)
    assert store.finish_scan(7,scan['token'],results) is None
    store.set_autonomy(7,policy);scan=store.claim_scan(7)
    store.set_autonomy(7,{**policy,'enabled':False})
    with pytest.raises(WorkError,match='scan_expired'):store.finish_scan(7,scan['token'],results)
    for changes in [{'max_jobs_per_day':4},{'interval_minutes':1},{'enabled':'true'},{'query':''}]:
        with pytest.raises(WorkError):store.set_autonomy(7,{**policy,**changes})


def test_autonomous_job_prepares_proposal_and_result(store):
    async def scenario():
        from desktop.work_store import DEFAULT_AUTONOMY
        store.set_autonomy(7,{**DEFAULT_AUTONOMY,'enabled':True,'query':'Написать текст'})
        policy=store.claim_scan(7)
        id=store.finish_scan(7,policy['token'],[{'title':'Текст','url':'https://example.com/one','snippet':'Написать краткое описание товара с характеристиками: красная сумка, кожа, 20x30 см.'}])
        seen=[]
        async def generate(session,job,role,text,save):
            seen.append(role)
            return 'Черновик заявки без выдуманного опыта.' if role=='proposal' else ROLE_OUTPUTS[role]
        await run_job(store,7,store.claim(7,id),SimpleNamespace(user_id=7),generate)
        job=store.find(store.workspace(7),id)
        assert job['status']=='ready'
        assert seen==['manager','proposal','executor','reviewer','treasurer']
        assert job['outputs']['proposal'].startswith('Черновик заявки')
    asyncio.run(scenario())


def test_autonomous_http_cycle_and_download(store):
    async def scenario():
        from desktop.work_store import DEFAULT_AUTONOMY
        class Search:
            available=True
            calls=0
            async def search(self,query):
                self.calls+=1
                return {'results':[{'title':'Описание','url':'https://example.com/autotask','snippet':'Написать описание сумки: красная кожаная сумка, ширина 20 см, высота 30 см.'}]}
        search=Search()
        async def session_for(request):return SimpleNamespace(user_id=7,access='private')
        async def auth(token):return {'user_id':7}
        async def generate(session,job,role,text,save):return 'Подготовленная заявка' if role=='proposal' else ROLE_OUTPUTS[role]
        async def unused(*args,**kwargs):raise AssertionError('external call')
        app=web.Application()
        setup_work_routes(app,store=store,generate=generate,session_for=session_for,same_origin=lambda _:True,
            json_response=lambda d,status=200:web.json_response(d,status=status),upstream=unused,upstream_stream=unused,
            authenticate=auth,allowed=lambda _:True,handlers={},web_search=search)
        async with TestServer(app) as server,ClientSession() as client:
            base=str(server.make_url('/web-api/v1/work/'))
            response=await client.put(base+'autonomy',json={**DEFAULT_AUTONOMY,'enabled':True,'query':'Описания товаров'})
            assert response.status==200
            assert (await client.post(base+'scan',json={})).status==202
            for _ in range(100):
                doc=await (await client.get(base+'workspace')).json()
                if doc['jobs'] and doc['jobs'][0]['status']=='ready':break
                await asyncio.sleep(.01)
            assert len(doc['jobs'])==1 and doc['jobs'][0]['status']=='ready'
            assert not {'scan_token','scan_until'} & set(doc)
            assert (await client.post(base+'scan',json={})).status==202
            await asyncio.sleep(.05)
            assert search.calls==1
            response=await client.get(base+'jobs/'+doc['jobs'][0]['id']+'/artifacts')
            import io,zipfile
            with zipfile.ZipFile(io.BytesIO(await response.read())) as bundle:
                assert bundle.read('proposal-draft.txt').decode()=='Подготовленная заявка'
    asyncio.run(scenario())
