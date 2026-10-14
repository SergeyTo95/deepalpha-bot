from contextlib import contextmanager
from datetime import timedelta
import json
from urllib.parse import parse_qs,urlsplit
from types import SimpleNamespace
import pytest
from cryptography.fernet import Fernet
from services import velia_cloud_connection_service as cloud
from services import velia_agent_connector_crypto_service as crypto
from desktop.feature_routes import CLOUD_CONNECTORS,route_allowed


@pytest.fixture
def configuration(monkeypatch):
    for key,value in {'VELIA_CLOUD_CONNECTIONS_ENABLED':'true','VELIA_CONNECTIONS_WEB_ORIGIN':'https://velia.example',
      'VELIA_GOOGLE_OAUTH_CLIENT_ID':'google-client','VELIA_GOOGLE_OAUTH_CLIENT_SECRET':'google-secret',
      'VELIA_MICROSOFT_OAUTH_CLIENT_ID':'ms-client','VELIA_MICROSOFT_OAUTH_CLIENT_SECRET':'ms-secret',
      'VELIA_CONNECTOR_FERNET_KEY':Fernet.generate_key().decode()}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(cloud,'ensure_tables',lambda:None)


def test_only_declared_read_operations_and_callbacks_are_exposed():
    assert set(CLOUD_CONNECTORS)==set(cloud.SPECS) and len(cloud.SPECS)==10
    for key in cloud.SPECS:
        assert route_allowed('POST','agent/connectors/'+key+'/connect')
        assert not route_allowed('GET','agent/connectors/'+key+'/connect')
        assert not route_allowed('POST','agent/connectors/'+key+'/complete')
    with pytest.raises(cloud.ConnectionError):cloud.build_read('gmail','send',{})
    with pytest.raises(cloud.ConnectionError):cloud.build_read('unknown','files',{})


@pytest.mark.parametrize('connector',['gmail','outlook'])
def test_authorization_uses_pkce_and_hides_verifier(configuration,monkeypatch,connector):
    seen=[]
    class Cursor:
        def execute(self,sql,args):seen.append((sql,args))
    @contextmanager
    def db():yield Cursor()
    monkeypatch.setattr(cloud.notion,'database',db)
    result=cloud.create_authorization_url(7,connector)
    url=urlsplit(result['url']);query=parse_qs(url.query)
    assert query['code_challenge_method']==['S256']
    assert query['redirect_uri']==['https://velia.example/web-api/v1/connectors/'+connector+'/callback']
    ciphertext=seen[-1][1][3];value=json.loads(crypto.decrypt_secret(ciphertext))
    assert value['user_id']==7 and value['connector']==connector
    assert value['verifier'] not in result['url'] and 'secret' not in result['url']
    assert url.hostname==('accounts.google.com' if connector=='gmail' else 'login.microsoftonline.com')


def test_wrong_owner_or_service_cannot_consume_authorization(configuration,monkeypatch):
    cipher=crypto.encrypt_secret(json.dumps({'user_id':7,'connector':'gmail','verifier':'v'}))
    class Cursor:
        def execute(self,sql,args):self.args=args
        def fetchone(self):return (cipher,)
    @contextmanager
    def db():yield Cursor()
    monkeypatch.setattr(cloud.notion,'database',db)
    assert cloud.consume_state(7,'gmail','s'*43)=='v'
    for user,key in [(8,'gmail'),(7,'outlook')]:
        with pytest.raises(cloud.ConnectionError):cloud.consume_state(user,key,'s'*43)


def test_cross_account_and_cross_service_token_swaps_are_rejected(configuration,monkeypatch):
    encoded,expiry=cloud.token_data(7,'gmail',{'access_token':'a','refresh_token':'r','expires_in':3600})
    class Cursor:
        def execute(self,*args):pass
        def fetchone(self):return (encoded,expiry)
    @contextmanager
    def db():yield Cursor()
    monkeypatch.setattr(cloud.notion,'database',db)
    assert cloud.access_token(7,'gmail')=='a'
    for user,key in [(8,'gmail'),(7,'outlook')]:
        with pytest.raises(cloud.ConnectionError):cloud.access_token(user,key)


def test_token_exchange_blocks_redirects_and_insufficient_scopes(configuration,monkeypatch):
    calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs));return SimpleNamespace(status_code=200,json=lambda:{'access_token':'a','scope':'Mail.ReadBasic'})
    monkeypatch.setattr(cloud.requests,'post',post)
    with pytest.raises(cloud.ConnectionError,match='required_permissions_not_granted'):cloud.token_request('outlook',{'grant_type':'authorization_code'})
    assert calls[0][0]=='https://login.microsoftonline.com/common/oauth2/v2.0/token'
    assert calls[0][1]['allow_redirects'] is False
    assert calls[0][1]['data']['client_secret']=='ms-secret'


def test_rotated_refresh_token_is_committed_and_old_refresh_is_retained_if_omitted(configuration,monkeypatch):
    encoded,expiry=cloud.token_data(7,'gmail',{'access_token':'old','refresh_token':'old-refresh','expires_in':1})
    statements=[]
    class Cursor:
        def execute(self,sql,args):statements.append((sql,args))
        def fetchone(self):return (encoded,cloud.now()-timedelta(seconds=1))
    @contextmanager
    def db():yield Cursor()
    monkeypatch.setattr(cloud.notion,'database',db)
    monkeypatch.setattr(cloud,'token_request',lambda key,args:{'access_token':'new','refresh_token':'rotated','expires_in':3600})
    assert cloud.access_token(7,'gmail')=='new'
    saved=json.loads(crypto.decrypt_secret(statements[-1][1][0]))
    assert saved['refresh_token']=='rotated' and saved['connector']=='gmail'
    cipher,_=cloud.token_data(7,'gmail',{'access_token':'new','expires_in':3600},'existing')
    assert json.loads(crypto.decrypt_secret(cipher))['refresh_token']=='existing'


def test_resource_ids_query_escaping_and_sheet_bounds():
    for bad in ('../users','https://evil.example','id?token=secret','id/child'):
        with pytest.raises(cloud.ConnectionError):cloud.build_read('gmail','message',{'message_id':bad})
    url,params=cloud.build_read('google-drive','files',{'query':"O'Reilly"})
    assert url=='https://www.googleapis.com/drive/v3/files' and "O\\'Reilly" in params['q']
    assert cloud.build_read('microsoft-todo','tasks',{'list_id':'ABC='})[0].endswith('/ABC=/tasks')
    for area in ('A:Z','A1:ZZ9999','Z100:A1','https://evil.example'):
        with pytest.raises(cloud.ConnectionError):cloud.build_read('google-sheets','values',{'spreadsheet_id':'doc','range':area})
    assert cloud.build_read('google-sheets','values',{'spreadsheet_id':'doc','range':'Sheet1!A1:Z100'})[0].endswith('Sheet1%21A1%3AZ100')


def test_event_reads_require_timezone_and_bounded_interval():
    for args in ({'start':'2026-01-01','end':'2026-01-02'},
                 {'start':'2026-01-01T00:00:00Z','end':'2027-01-01T00:00:00Z'}):
        with pytest.raises(cloud.ConnectionError):cloud.build_read('outlook','events',args)
    url,params=cloud.build_read('outlook','events',{'start':'2026-01-01T00:00:00Z','end':'2026-01-02T00:00:00Z'})
    assert url.endswith('/me/calendarView') and params['$top']==20


def test_every_operation_uses_fixed_provider_host_and_bounded_output():
    args={'document_id':'doc','spreadsheet_id':'sheet','range':'A1:B3','list_id':'list','message_id':'message','course_id':'course',
          'start':'2026-01-01T00:00:00Z','end':'2026-01-02T00:00:00Z'}
    for key,(_,_,ops) in cloud.SPECS.items():
        for operation in ops:
            url,_=cloud.build_read(key,operation,args)
            assert urlsplit(url).hostname in ('www.googleapis.com','docs.googleapis.com','sheets.googleapis.com','classroom.googleapis.com','graph.microsoft.com')
    result=cloud.bounded({'access_token':'secret','@odata.nextLink':'https://evil.example','rows':['x'*10000]*200})
    assert 'secret' not in str(result) and '@odata.nextLink' not in result
    assert len(result['rows'])==100 and len(result['rows'][0])==4000


def test_cloud_registry_contains_only_read_tools(configuration):
    from services import velia_agent_tool_registry_service as registry
    from services.velia_agent_protocol_service import ActionRisk
    registry.clear_registry_for_tests()
    cloud.register_tools()
    tools=list(registry._REGISTRY.list())
    assert len(tools)==17
    assert all(tool.risk is ActionRisk.READ and not tool.requires_approval for tool in tools)
    assert {tool.connector for tool in tools}==set(cloud.SPECS)


def test_large_provider_responses_are_stopped_before_json_decode(configuration,monkeypatch):
    monkeypatch.setattr(cloud,'access_token',lambda user,key:'fixture')
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,size):yield b'x'*2_000_001
    monkeypatch.setattr(cloud.requests,'get',lambda url,**kwargs:Response())
    with pytest.raises(cloud.ConnectionError,match='provider_response_too_large'):
        cloud.read_tool(7,{},connector='onedrive',operation='files')


@pytest.mark.asyncio
async def test_cloud_callback_uses_current_session_and_only_internal_completion():
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from desktop.feature_routes import setup_feature_routes
    seen=[]
    async def session(request):return SimpleNamespace(access='fixture') if request.headers.get('X-Test') else None
    async def upstream(method,path,**kwargs):seen.append((path,kwargs));return 200,{'ok':True}
    app=web.Application();setup_feature_routes(app,session_for=session,same_origin=lambda r:True,upstream=upstream,
                    json_response=lambda data,status=200:web.json_response(data,status=status))
    async with TestClient(TestServer(app)) as client:
        path='/web-api/v1/connectors/outlook/callback?code=fixture&state='+'a'*43
        assert (await client.get(path,allow_redirects=False)).status==401
        assert not seen
        r=await client.get(path,headers={'X-Test':'1'},allow_redirects=False)
        assert r.status==303 and r.headers['Location']=='/'
        assert seen[-1][0]=='/mobile-api/v1/agent/connectors/outlook/complete'
        assert seen[-1][1]['token']=='fixture'
        assert (await client.get('/web-api/v1/connectors/unlisted/callback')).status==404
