import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace
import pytest
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.crm import CrmError, initial, validate, query, apply_edit
from desktop.crm_store import CrmStore
from desktop.crm_routes import setup_crm_routes

NOW=datetime(2026,11,11,12,tzinfo=timezone.utc)


def workspace():
    doc=initial()
    for id,name,role in [('anna','Анна','investor'),('boris','Борис','partner'),('new','Новый','investor')]:
        doc,_=apply_edit(doc,{'type':'add_contact','contact':{'name':name,'company':id.upper(),'roles':[role]}},id,now=NOW)
    return doc


def interaction(doc,id,contact='anna',direction='inbound',date='2026-10-01',**kwargs):
    return apply_edit(doc,{'type':'add_interaction','interaction':{'contact_id':contact,'direction':direction,'date':date,
        'channel':'email','summary':'Тестовое сообщение',**kwargs}},id,now=NOW)[0]


def test_waiting_replies_are_not_inferred_from_any_outbound_or_draft():
    doc=interaction(workspace(),'incoming',requires_reply=True)
    doc=interaction(doc,'draft',direction='note',date='2026-11-10')
    assert query(doc,{'view':'waiting_reply','role':'investor'},now=NOW)['total']==1
    doc=interaction(doc,'unrelated',direction='outbound',date='2026-11-10')
    assert query(doc,{'view':'waiting_reply'},now=NOW)['contacts'][0]['waiting_count']==1
    doc=interaction(doc,'actual-reply',direction='outbound',date='2026-11-10',reply_to='incoming')
    assert query(doc,{'view':'waiting_reply'},now=NOW)['total']==0
    with pytest.raises(CrmError,match='crm_invalid_reply'):
        interaction(doc,'wrong',contact='boris',direction='outbound',date='2026-11-10',reply_to='incoming')


def test_stale_unknown_history_and_old_unanswered_are_separate():
    doc=interaction(workspace(),'meeting',direction='meeting',date='2026-10-01')
    doc=interaction(doc,'note',direction='note',date='2026-11-11')
    stale=query(doc,{'view':'stale','role':'investor','stale_days':14},now=NOW)
    assert stale['total']==1 and stale['contacts'][0]['days_since_contact']==41
    assert stale['unknown_history_count']==1
    assert query(doc,{'view':'waiting_reply'},now=NOW)['total']==0
    doc=interaction(doc,'recent',contact='new',date='2026-11-10',requires_reply=True)
    doc=interaction(doc,'old',date='2026-10-15',requires_reply=True)
    result=query(doc,{'view':'waiting_reply','stale_days':14},now=NOW)
    assert result['total']==1 and result['contacts'][0]['id']=='anna'


def test_batch_contact_interaction_deal_and_followup_is_atomic():
    edit={'type':'batch','edits':[
        {'type':'add_contact','contact':{'name':'Инвестор','roles':['investor'],'email':'anna@example.org'}},
        {'type':'add_deal','deal':{'title':'Инвестиции Velia','contact_ids':['$0'],'stage':'negotiation','project':'Velia','value':'10000.50','currency':'USD'}},
        {'type':'add_interaction','interaction':{'contact_id':'$0','date':'2026-11-01','direction':'meeting','channel':'meeting','summary':'Договорились выслать отчёт'}},
        {'type':'add_task','task':{'contact_id':'$0','deal_id':'$1','title':'Выслать отчёт','due_date':'2026-11-10'}}]}
    before=initial();doc,change=apply_edit(before,edit,'request',now=NOW)
    assert before==initial() and len(change['changes'])==4
    card=query(doc,{'view':'card','contact_id':'request:0'},now=NOW)['contacts'][0]
    assert card['overdue_count']==1 and card['next_actions'][0]['title']=='Выслать отчёт'
    assert query(doc,{'view':'pipeline','project':'Velia'},now=NOW)['deals'][0]['value']=='10000.50'
    invalid=deepcopy(edit);invalid['edits'][3]['task']['contact_id']='$7'
    with pytest.raises(CrmError):apply_edit(before,invalid,'bad',now=NOW)
    assert before==initial()


def test_duplicate_contact_methods_names_and_corrections():
    doc,_=apply_edit(initial(),{'type':'add_contact','contact':{'name':'Анна','email':'anna@example.org','phone':'+90 (555) 123 4567','telegram':'@anna_test'}},'first',now=NOW)
    for method,value in [('email','ANNA@example.org'),('phone','905551234567'),('telegram','ANNA_TEST')]:
        with pytest.raises(CrmError,match='crm_duplicate_contact'):
            apply_edit(doc,{'type':'add_contact','contact':{'name':'Другая Анна',method:value}},'second',now=NOW)
    doc,_=apply_edit(doc,{'type':'add_contact','contact':{'name':'Анна','company':'Другая компания'}},'second',now=NOW)
    assert query(doc,{'search':'Анна'},now=NOW)['total']==2
    edited,_=apply_edit(doc,{'type':'update_contact','contact_id':'second','changes':{'company':'Фонд B','roles':['investor']}},'edit',now=NOW)
    assert query(edited,{'search':'Фонд B','role':'investor'},now=NOW)['contacts'][0]['id']=='second'


def test_contact_delete_preserves_other_people_and_shared_deal():
    doc=workspace()
    doc=interaction(doc,'incoming',requires_reply=True)
    doc,_=apply_edit(doc,{'type':'add_deal','deal':{'title':'Совместная сделка','contact_ids':['anna','boris'],'stage':'proposal'}},'shared',now=NOW)
    doc,_=apply_edit(doc,{'type':'add_task','task':{'contact_id':'anna','title':'Ответить'}},'task-a',now=NOW)
    doc,_=apply_edit(doc,{'type':'add_task','task':{'contact_id':'boris','title':'Обсудить','deal_id':'shared'}},'task-b',now=NOW)
    after,change=apply_edit(doc,{'type':'delete_contact','contact_id':'anna'},'delete',now=NOW)
    assert change['before']['related_removed']=={'interactions':1,'tasks':1,'deals':0}
    assert after['deals'][0]['contact_ids']==['boris'] and after['tasks'][0]['id']=='task-b'
    assert not after['interactions']


def test_user_zone_date_survives_validation_from_other_zone():
    now=datetime(2026,10,3,22,30,tzinfo=timezone.utc)
    doc,_=apply_edit(workspace(),{'type':'add_interaction','interaction':{'contact_id':'anna','date':'2026-10-04',
        'direction':'meeting','channel':'meeting','summary':'Поздняя встреча'}},'late',time_zone='Europe/Istanbul',now=now)
    assert validate(doc,time_zone='UTC',now=now)['interactions'][0]['time_zone']=='Europe/Istanbul'
    with pytest.raises(CrmError,match='crm_future_interaction'):
        apply_edit(workspace(),{'type':'add_interaction','interaction':{'contact_id':'anna','date':'2026-10-05',
            'direction':'meeting','channel':'meeting','summary':'Будущая запись'}},'future',time_zone='Europe/Istanbul',now=now)


def test_encryption_durability_owner_binding_and_stale_revision(tmp_path):
    path=tmp_path/'crm.db';store=CrmStore(sqlite_path=path,session_key='c'*44);store.initialize()
    edit={'type':'add_contact','contact':{'name':'Private Contact','email':'private@example.org'}}
    preview,_=store.preview(7,edit,'','contact')
    assert len(preview['contacts'])==1 and store.load(7)['document']==initial()
    saved,_=store.commit(7,edit,'','contact')
    assert store.load(8)['document']==initial()
    with pytest.raises(CrmError,match='crm_revision_conflict'):store.commit(7,edit,'','again')
    restarted=CrmStore(sqlite_path=path,session_key='c'*44)
    assert restarted.load(7)['document']['contacts'][0]['email']=='private@example.org'
    with sqlite3.connect(path) as conn:
        raw=conn.execute('SELECT owner_hash,encrypted FROM velia_crm_workspace').fetchone()
        assert 'Private' not in raw[1] and 'private@example.org' not in raw[1] and raw[0]!='7'
        conn.execute('INSERT INTO velia_crm_workspace VALUES (?,?,?)',(store.key(8),saved['revision'],raw[1]))
    with pytest.raises(CrmError,match='crm_storage_unavailable'):store.load(8)


def test_crm_routes_auth_origin_preview_consent_and_isolation(tmp_path):
    async def scenario():
        store=CrmStore(sqlite_path=tmp_path/'routes.db',session_key='r'*44)
        async def session(request):return SimpleNamespace(user_id=int(request.headers['X-User'])) if request.headers.get('X-User') else None
        app=web.Application();setup_crm_routes(app,session_for=session,same_origin=lambda r:r.headers.get('Origin')=='https://velia.example',
            json_response=lambda data,status=200:web.json_response(data,status=status),store=store)
        async with TestServer(app) as server,ClientSession() as client:
            async def post(action,data,user='7',origin='https://velia.example'):
                headers={'Origin':origin}
                if user:headers['X-User']=user
                response=await client.post(server.make_url('/web-api/v1/crm/'+action),headers=headers,json=data)
                return response.status,await response.json()
            assert (await post('context',{},user=None))[0]==401
            assert (await post('context',{},origin='https://evil.example'))[0]==403
            edit={'edit':{'type':'add_contact','contact':{'name':'Test Investor','roles':['investor']}},'revision':'','operation_id':'test-contact'}
            assert (await post('preview',edit))[0]==200
            assert (await post('context',{}))[1]['counts']['contacts']==0
            assert (await post('mutate',edit))[0]==409
            status,saved=await post('mutate',{**edit,'confirmed':True});assert status==200
            assert (await post('mutate',{**edit,'confirmed':True}))[0]==409
            assert (await post('context',{},user='8'))[1]['counts']['contacts']==0
            assert (await post('query',{'query':{'contact_id':'test-contact','view':'card'}},user='8'))[0]==404
            assert (await post('query',{'user_id':'7'},user='8'))[0]==400
            assert (await post('query',{'query':{'role':'investor'}}))[1]['result']['contacts'][0]['name']=='Test Investor'
            assert (await post('preview',{'edit':{'type':'update_contact','contact_id':'test-contact','changes':{'name':'Changed'}},'revision':saved['revision'],'operation_id':'edit','query':{'view':'unknown'}}))[0]==400
            assert (await post('query',{'query':{'view':'card','contact_id':'test-contact'}}))[1]['result']['contacts'][0]['name']=='Test Investor'
    asyncio.run(scenario())


def test_planner_uses_each_selected_model_and_disables_search(monkeypatch,tmp_path):
    from test_velia_web_chat import fixture,headers,login
    from desktop.guest_store import GuestStore
    async def scenario():
        async with fixture(monkeypatch,quantum_api=True,features={'chat_quantum':True},with_search=True,
            guest_store=GuestStore(sqlite_path=tmp_path/'quota.db')) as (server,client,state):
            url=server.make_url('/web-api/v1/crm/plan')
            body={'model':'velia-flash','messages':[{'role':'user','content':'Найди контакты CRM'}],'stream':True,'web_search':False}
            assert (await client.post(url,headers=headers(),json=body)).status==401
            cookie,_,_=await login(server,client)
            for model in ('velia-flash','velia-quantum','velia-pro'):
                response=await client.post(url,headers=headers(cookie),json={**body,'model':model})
                assert response.status==200 and '[DONE]' in await response.text()
            assert len(state['payloads'])==3 and not state['search_queries'] and not state['intent_payloads']
            asset=await client.get(server.make_url('/web/crm.mjs'));assert asset.status==200
    asyncio.run(scenario())


def test_archived_contacts_can_be_found_and_restored():
    doc,_=apply_edit(workspace(),{'type':'update_contact','contact_id':'anna','changes':{'status':'archived'}},'archive',now=NOW)
    assert query(doc,{'role':'investor'},now=NOW)['total']==1
    assert query(doc,{'status':'archived','search':'Анна'},now=NOW)['contacts'][0]['id']=='anna'
    assert query(doc,{'view':'card','contact_id':'anna'},now=NOW)['contacts'][0]['status']=='archived'
    restored,_=apply_edit(doc,{'type':'update_contact','contact_id':'anna','changes':{'status':'active'}},'restore',now=NOW)
    assert query(restored,{'role':'investor'},now=NOW)['total']==2
