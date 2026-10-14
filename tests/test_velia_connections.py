from contextlib import contextmanager
import hashlib
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import pytest
from cryptography.fernet import Fernet
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from services import velia_notion_connection_service as notion
from services import velia_connection_catalog_service as catalog
from services.velia_connection_routes import setup_connection_routes
from desktop.feature_routes import route_allowed, setup_feature_routes


def test_catalog_never_marks_planned_services_available(monkeypatch):
    monkeypatch.setattr(notion,'connection_status',lambda user_id:{'configured':False,'connected':False})
    monkeypatch.setattr(catalog.calendar,'connection_status',lambda user_id:{'configured':True,'connected':True,'access_token':'SECRET'})
    rows=catalog.list_connections(7)
    assert len(rows)==28 and len({r['id'] for r in rows})==28
    assert 'SECRET' not in str(rows)
    assert next(r for r in rows if r['id']=='google-calendar')['connected']
    for row in rows:
        from services import velia_cloud_connection_service as cloud
        if row['id'] in cloud.SPECS:
            assert row['status']=='requires_setup' and not row['available']
        elif row['id'] not in ('notion','google-calendar'):
            assert row['status']=='planned' and not row['available'] and not row['connected']


def test_catalog_provider_failure_remains_unknown_not_connected(monkeypatch):
    monkeypatch.setattr(notion,'connection_status',lambda user_id:(_ for _ in ()).throw(RuntimeError('SECRET')))
    monkeypatch.setattr(catalog.calendar,'connection_status',lambda user_id:{'configured':False})
    row=catalog.list_connections(7)[0]
    assert row['status']=='unavailable' and not row['available'] and 'SECRET' not in str(row)


def test_notion_requires_encryption_and_exact_callback_path(monkeypatch):
    for name,value in {'VELIA_NOTION_ENABLED':'true','VELIA_NOTION_CLIENT_ID':'id','VELIA_NOTION_CLIENT_SECRET':'secret',
      'VELIA_NOTION_OAUTH_CALLBACK_URL':'https://velia.example/web-api/v1/connectors/notion/callback',
      'VELIA_CONNECTOR_FERNET_KEY':Fernet.generate_key().decode()}.items():monkeypatch.setenv(name,value)
    assert notion.configured()
    for url in ('http://velia.example/web-api/v1/connectors/notion/callback','https://u:p@velia.example/web-api/v1/connectors/notion/callback','https://velia.example/unrelated'):
        monkeypatch.setenv('VELIA_NOTION_OAUTH_CALLBACK_URL',url)
        assert not notion.configured()


def test_oauth_state_is_bound_to_owner_expires_and_cannot_be_replayed(monkeypatch):
    state='a'*43;records={hashlib.sha256(state.encode()).hexdigest():(7,datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(minutes=1))}
    class Cursor:
        def execute(self,sql,args):
            self.found=None
            digest,user,now=args
            if digest in records and records[digest][0]==user and records[digest][1]>now:
                records.pop(digest);self.found=(user,)
        def fetchone(self):return self.found
    @contextmanager
    def db():yield Cursor()
    monkeypatch.setattr(notion,'database',db)
    with pytest.raises(notion.ConnectionError):notion.consume_state(8,state)
    notion.consume_state(7,state)
    with pytest.raises(notion.ConnectionError):notion.consume_state(7,state)
    records[hashlib.sha256(state.encode()).hexdigest()]=(7,datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(seconds=1))
    with pytest.raises(notion.ConnectionError):notion.consume_state(7,state)


def test_provider_operations_pin_host_block_redirects_and_validate_ids(monkeypatch):
    calls=[]
    def request(method,url,**kwargs):
        calls.append((url,kwargs));return SimpleNamespace(status_code=200,json=lambda:{'results':[]})
    monkeypatch.setattr(notion.requests,'request',request)
    notion.provider_request('POST','/search',token='fixture',body={'page_size':20})
    assert calls[0][0]=='https://api.notion.com/v1/search' and calls[0][1]['allow_redirects'] is False
    for path in ('https://evil.example','/users','/blocks/../../secret/children','/search?redirect=https://evil.example'):
        with pytest.raises(notion.ConnectionError):notion.provider_request('GET',path,token='fixture')
    with pytest.raises(notion.ConnectionError):notion.read_page(7,{'page_id':'../secret'})
    monkeypatch.setattr(notion,'read_request',lambda user,method,path,body=None: {'results':[{'type':'paragraph','paragraph':{'rich_text':[{'plain_text':'text'}]},'has_children':True}]})
    result=notion.read_page(7,{'page_id':'11111111-1111-1111-1111-111111111111'})
    assert result['partial'] and result['blocks'][0]['text']=='text'


class Auth:
    _mobile_api_available=staticmethod(lambda:True)
    _disabled_response=staticmethod(lambda:web.json_response({'ok':False},status=503))
    _require_mobile_auth=staticmethod(lambda request:{'user_id':7} if request.headers.get('Authorization')=='Bearer fixture' else None)
    _json_response=staticmethod(lambda value,status=200:web.json_response(value,status=status))


@pytest.mark.asyncio
async def test_completion_requires_auth_and_never_returns_tokens(monkeypatch):
    seen=[]
    monkeypatch.setattr(notion,'complete_connection',lambda user,state,code:seen.append((user,state,code)) or {'connected':True})
    app=web.Application();setup_connection_routes(app,Auth)
    async with TestClient(TestServer(app)) as client:
        path='/mobile-api/v1/agent/connectors/notion/complete'
        assert (await client.post(path,json={'state':'state','code':'code'})).status==401
        r=await client.post(path,json={'state':'state','code':'code'},headers={'Authorization':'Bearer fixture'})
        assert await r.json()=={'ok':True,'connected':True}
        assert seen==[(7,'state','code')]


@pytest.mark.asyncio
async def test_web_callback_uses_current_session_and_cleans_url():
    seen=[]
    async def session(request):return SimpleNamespace(access='fixture') if request.headers.get('X-Test-Session') else None
    async def upstream(method,path,**kwargs):seen.append((method,path,kwargs));return 200,{'ok':True,'connected':True}
    app=web.Application();setup_feature_routes(app,session_for=session,same_origin=lambda r:True,upstream=upstream,json_response=lambda data,status=200:web.json_response(data,status=status))
    url='/web-api/v1/connectors/notion/callback?code=fixture&state='+'x'*43
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(url,allow_redirects=False)).status==401
        assert not seen
        response=await client.get(url,headers={'X-Test-Session':'1'},allow_redirects=False)
        assert response.status==303 and response.headers['Location']=='/'
        assert response.headers['Referrer-Policy']=='no-referrer'
        assert seen[0][2]['token']=='fixture'
    assert not route_allowed('POST','agent/connectors/notion/complete')
    assert route_allowed('POST','agent/connectors/notion/connect')
    assert not route_allowed('GET','agent/connectors/notion/connect')


def test_encrypted_credentials_cannot_be_swapped_between_users(monkeypatch):
    import json
    monkeypatch.setenv('VELIA_CONNECTOR_FERNET_KEY',Fernet.generate_key().decode())
    from services import velia_agent_connector_crypto_service as crypto
    cipher=crypto.encrypt_secret(json.dumps({'user_id':7,'access_token':'secret'}))
    class Cursor:
        def execute(self,*args):pass
        def fetchone(self):return (cipher,)
    assert 'secret' not in cipher
    assert notion.tokens_for(7,Cursor())['access_token']=='secret'
    with pytest.raises(notion.ConnectionError):notion.tokens_for(8,Cursor())
