import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import time
import pytest
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.crm import CrmError, initial, validate
from desktop.crm_mail import bounds, mail_entry, prepare_mail, import_mail
from desktop.crm_routes import setup_crm_routes
from desktop.crm_store import CrmStore
from desktop.figma_connector import FigmaError

STAMP=int(datetime(2026,10,1,22,30,tzinfo=timezone.utc).timestamp()*1000)
CONTACT={'id':'anna','name':'Анна','email':'anna@example.org'}


def message(id='msg1',direction='inbound',**overrides):
    return {'id':id,'internalDate':str(STAMP),'labelIds':['SENT'] if direction=='outbound' else ['INBOX','UNREAD'],
            'snippet':'Текст &amp; важные детали','payload':{'headers':[
                {'name':'From','value':'me@example.org' if direction=='outbound' else 'Анна <anna@example.org>'},
                {'name':'To','value':'anna@example.org' if direction=='outbound' else 'me@example.org'},
                {'name':'Subject','value':'Презентация'},
                {'name':'Date','value':'Thu, 01 Jan 1970 00:00:00 +0000'}]},**overrides}


class Google:
    def __init__(self,messages=None):
        self.connection={'id':'google-owned','lock':asyncio.Lock()}
        self.messages=messages if messages is not None else [message(),message('msg2','outbound')]
        self.calls=[];self.restored=[]
    async def restore(self,owner):self.restored.append(owner)
    def get(self,owner,id=None):
        if self.connection is None or id is not None and self.connection['id']!=id:raise FigmaError('figma_connect_required',409)
        return self.connection
    async def api_request(self,connection,method,path,**kwargs):
        assert method=='GET'
        self.calls.append((path,kwargs))
        if path.endswith('/profile'):return {'emailAddress':'me@example.org'}
        if path.endswith('/messages'):return {'messages':[{'id':m['id']} for m in self.messages]}
        return next(m for m in self.messages if path.endswith('/'+m['id']))


def store_at(tmp_path):
    store=CrmStore(sqlite_path=tmp_path/'crm.db',session_key='k'*44);store.initialize()
    saved,_=store.commit(7,{'type':'add_contact','contact':{'name':'Анна','email':'anna@example.org'}},'','anna')
    return store,saved['revision']


def test_provider_dates_labels_exact_addresses_and_no_inferred_reply():
    start,end=bounds('2026-10-02','2026-10-02','Europe/Istanbul')
    row=mail_entry(message(),CONTACT,'me@example.org',start,end,'Europe/Istanbul')
    assert row['date']=='2026-10-02' and row['summary']=='Презентация · Текст & важные детали'
    assert row['direction']=='inbound' and row['requires_reply'] is False and 'reply_to' not in row
    assert mail_entry(message(direction='outbound'),CONTACT,'me@example.org',start,end,'Europe/Istanbul')['direction']=='outbound'
    for label in ['DRAFT','SPAM','TRASH']:
        assert mail_entry(message(labelIds=[label]),CONTACT,'me@example.org',start,end,'Europe/Istanbul') is None
    wrong=message();wrong['payload']['headers'][0]['value']='other@example.org'
    assert mail_entry(wrong,CONTACT,'me@example.org',start,end,'Europe/Istanbul') is None
    wrong=message(direction='outbound');wrong['payload']['headers'][0]['value']='alias@example.org'
    assert mail_entry(wrong,CONTACT,'me@example.org',start,end,'Europe/Istanbul') is None
    assert mail_entry(message(),CONTACT,'me@example.org',start+86400,end+86400,'Europe/Istanbul') is None
    assert mail_entry(message(),CONTACT,'me@example.org',start,end,'Europe/Istanbul',now=STAMP/1000-1) is None
    assert len(mail_entry(message(snippet='💡'*4000),CONTACT,'me@example.org',start,end,'Europe/Istanbul')['summary'].encode())<=2000
    with pytest.raises(CrmError):bounds('2026-10-03','2026-10-02','UTC')


def test_review_token_bound_to_session_connection_revision_and_not_repeatable(tmp_path):
    async def scenario():
        store,revision=store_at(tmp_path);google=Google();session=SimpleNamespace(user_id=7,id='owned-session')
        data={'contact_id':'anna','start_date':'2026-10-01','end_date':'2026-10-02','time_zone':'Europe/Istanbul','revision':revision}
        preview=await import_mail('mail-preview',data,session,store,google)
        assert preview['count']==2 and not store.load(7)['document']['interactions']
        token={'token':preview['token'],'confirmed':True}
        with pytest.raises(CrmError,match='crm_confirmation_required'):await import_mail('mail-commit',{**token,'confirmed':False},session,store,google)
        for other in [SimpleNamespace(user_id=8,id=session.id),SimpleNamespace(user_id=7,id='other-session')]:
            with pytest.raises(CrmError,match='crm_mail_preview_expired'):await import_mail('mail-commit',token,other,store,google)
        with pytest.raises(CrmError):await import_mail('mail-commit',{**token,'token':'changed'+token['token']},session,store,google)
        assert not store.load(7)['document']['interactions']
        old=google.connection['id'];google.connection['id']='another-account'
        with pytest.raises(FigmaError):await import_mail('mail-commit',token,session,store,google)
        google.connection['id']=old
        result=await import_mail('mail-commit',token,session,store,google)
        assert result['count']==2
        with pytest.raises(CrmError,match='crm_revision_conflict'):await import_mail('mail-commit',token,session,store,google)
        again=await import_mail('mail-preview',{**data,'revision':result['revision']},session,store,google)
        assert again['count']==0 and again['duplicates']==2 and 'token' not in again
        rows=store.load(7)['document']['interactions']
        assert len(rows)==2 and all(not row['requires_reply'] for row in rows)
        assert google.restored[0]==('7','owned-session')
        assert not store.load(8)['document']['interactions']
        assert 'Презентация' not in (tmp_path/'crm.db').read_bytes().decode(errors='ignore')
        payload=json.loads(store.cipher.decrypt(token['token'].encode()));payload['expires']=time.time()-1
        expired=store.cipher.encrypt(json.dumps(payload).encode()).decode()
        with pytest.raises(CrmError,match='crm_mail_preview_expired'):await import_mail('mail-commit',{'token':expired,'confirmed':True},session,store,google)
    asyncio.run(scenario())


def test_bounded_reads_filters_and_duplicate_provenance(tmp_path):
    async def scenario():
        store,_=store_at(tmp_path);doc=store.load(7)['document']
        google=Google([message('msg'+str(i)) for i in range(15)])
        result=await prepare_mail(google,('7','session'),doc,'anna','2026-10-01','2026-10-02','Europe/Istanbul')
        assert len(result['rows'])==8 and result['more'] and len(google.calls)==10
        q=google.calls[1][1]['params']['q'];assert 'from:anna@example.org' in q and 'after:1790801999' in q
        assert all(call[1].get('params',{}).get('format')=='metadata' for call in google.calls[2:])
        doc['interactions']=[dict(row,id='import:'+str(i)) for i,row in enumerate(result['rows'])]
        duplicate=deepcopy(doc['interactions'][0]);duplicate['id']='copy';doc['interactions'].append(duplicate)
        with pytest.raises(CrmError,match='crm_duplicate_mail'):validate(doc)
        evil=deepcopy(doc);evil['contacts'][0]['email']='anna@example.org} OR from:victim@example.org'
        with pytest.raises(CrmError,match='crm_mail_email_required'):await prepare_mail(google,('7','session'),evil,'anna','2026-10-01','2026-10-02','UTC')
    asyncio.run(scenario())


def test_import_routes_authorization_no_forged_sources_and_atomic_stale_commit(tmp_path):
    async def scenario():
        store,revision=store_at(tmp_path);google=Google()
        async def session(request):return SimpleNamespace(user_id=int(request.headers['X-User']),id='session') if request.headers.get('X-User') else None
        app=web.Application();setup_crm_routes(app,session_for=session,same_origin=lambda r:r.headers.get('Origin')=='https://velia.example',json_response=lambda data,status=200:web.json_response(data,status=status),store=store,google=google)
        async with TestServer(app) as server,ClientSession() as client:
            async def post(action,data,user='7',origin='https://velia.example'):
                response=await client.post(server.make_url('/web-api/v1/crm/'+action),headers={'Origin':origin,**({'X-User':user} if user else {})},json=data)
                return response.status,await response.json()
            data={'contact_id':'anna','start_date':'2026-10-01','end_date':'2026-10-02','time_zone':'Europe/Istanbul','revision':revision}
            assert (await post('mail-preview',data,user=None))[0]==401
            assert (await post('mail-preview',data,origin='https://evil.example'))[0]==403
            assert not google.calls
            status,preview=await post('mail-preview',data);assert status==200
            forged={'edit':{'type':'add_interaction','interaction':{'source':{'provider':'gmail'}}},'revision':revision,'operation_id':'forged'}
            assert (await post('preview',forged))[1]['error']=='crm_mail_source_read_only'
            store.commit(7,{'type':'update_contact','contact_id':'anna','changes':{'company':'Changed'}},revision,'change')
            assert (await post('mail-commit',{'token':preview['token'],'confirmed':True}))[0]==409
            assert not store.load(7)['document']['interactions']
    asyncio.run(scenario())
