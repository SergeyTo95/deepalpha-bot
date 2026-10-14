from contextlib import contextmanager
from datetime import timedelta
import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
import pytest
from cryptography.fernet import Fernet
from services import velia_vendor_connection_service as vendor
from services import velia_agent_connector_crypto_service as crypto
from desktop.feature_routes import VENDOR_CONNECTORS,route_allowed


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('VELIA_VENDOR_CONNECTIONS_ENABLED','true')
    monkeypatch.setenv('VELIA_CONNECTIONS_WEB_ORIGIN','https://velia.example')
    monkeypatch.setenv('VELIA_CONNECTOR_FERNET_KEY',Fernet.generate_key().decode())
    for key in vendor.SPECS:
        monkeypatch.setenv('VELIA_'+key.upper()+'_OAUTH_CLIENT_ID','client')
        monkeypatch.setenv('VELIA_'+key.upper()+'_OAUTH_CLIENT_SECRET','secret')
    monkeypatch.setattr(vendor,'ensure_tables',lambda:None)


def database_with(monkeypatch,row):
    statements=[]
    class Cursor:
        def execute(self,*args):statements.append(args)
        def fetchone(self):return row
    @contextmanager
    def db():yield Cursor()
    monkeypatch.setattr(vendor.notion,'database',db)
    return statements


def test_all_vendor_cards_have_routes_and_only_described_reads():
    assert set(VENDOR_CONNECTORS)==set(vendor.SPECS) and len(vendor.SPECS)==16
    for key,spec in vendor.SPECS.items():
        assert route_allowed('POST','agent/connectors/'+key+'/connect')
        assert route_allowed('DELETE','agent/connectors/'+key)
        assert not route_allowed('POST','agent/connectors/'+key+'/complete')
        assert not route_allowed('POST','agent/connectors/'+key+'/send')
        for operation in spec[3]:assert key+'.'+operation in vendor.DESCRIPTIONS
        with pytest.raises(vendor.ConnectionError):vendor.build_read(key,'write',{}, {})


@pytest.mark.parametrize('bad',['','example.com','https://store','127.0.0.1','user@shop','shop/path','shop:443','-shop','shop-','shop?x','shop\\x','SHOP'])
def test_tenant_names_cannot_change_host_or_path(bad):
    for key in vendor.TENANTS:
        with pytest.raises(vendor.ConnectionError):vendor.context_for(key,{'tenant':bad})


@pytest.mark.parametrize('key',[k for k in vendor.SPECS if k!='zotero'])
def test_authorization_is_bound_encrypted_and_uses_only_provider_host(configured,monkeypatch,key):
    statements=database_with(monkeypatch,None)
    result=vendor.create_authorization_url(7,key,{'tenant':'my-store'} if key in vendor.TENANTS else {})
    query=parse_qs(urlsplit(result['url']).query);saved=json.loads(crypto.decrypt_secret(statements[-1][1][3]))
    assert saved['user_id']==7 and saved['connector']==key
    assert saved['verifier'] not in result['url'] and 'secret' not in result['url']
    assert query['state'] and query['redirect_uri']==[vendor.callback_url(key)]
    if vendor.SPECS[key][5]:assert query['code_challenge_method']==['s256' if key=='canva' else 'S256']
    if key in vendor.TENANTS:assert urlsplit(result['url']).hostname=='my-store.'+vendor.TENANTS[key]


def test_oauth_state_owner_expiry_and_single_use(configured,monkeypatch):
    data={'user_id':7,'connector':'slack','context':{},'verifier':'v'}
    encoded=crypto.encrypt_secret(json.dumps(data));rows={(7,'slack'):encoded}
    class Cursor:
        def execute(self,sql,args):
            assert 'DELETE' in sql and 'expires_at>' in sql
            self.row=rows.pop((args[1],args[2]),None)
        def fetchone(self):return (self.row,) if self.row else None
    @contextmanager
    def db():yield Cursor()
    monkeypatch.setattr(vendor.notion,'database',db)
    with pytest.raises(vendor.ConnectionError):vendor.consume_state(8,'slack','s'*43)
    with pytest.raises(vendor.ConnectionError):vendor.consume_state(7,'jira','s'*43)
    assert vendor.consume_state(7,'slack','s'*43)==data
    with pytest.raises(vendor.ConnectionError):vendor.consume_state(7,'slack','s'*43)


def test_token_row_swaps_and_refresh_preserves_tenant(configured,monkeypatch):
    encoded,expiry=vendor.token_data(7,'zendesk',{'access_token':'old','refresh_token':'old-refresh','expires_in':1},{'tenant':'my-team'})
    statements=database_with(monkeypatch,(encoded,vendor.cloud.now()-timedelta(seconds=1)))
    with pytest.raises(vendor.ConnectionError):vendor.access_credentials(8,'zendesk')
    with pytest.raises(vendor.ConnectionError):vendor.access_credentials(7,'shopify')
    calls=[]
    def exchange(key,data,context):
        calls.append((key,data,context));return {'access_token':'new','refresh_token':'rotated','expires_in':3600}
    monkeypatch.setattr(vendor,'token_request',exchange)
    value=vendor.access_credentials(7,'zendesk')
    assert value['context']=={'tenant':'my-team'} and value['refresh_token']=='rotated'
    assert calls[0][2]=={'tenant':'my-team'}
    assert json.loads(crypto.decrypt_secret(statements[-1][1][0]))==value


def test_nonexpiring_and_expiring_without_refresh_need_honest_lifetimes(configured,monkeypatch):
    encoded,expiry=vendor.token_data(7,'clickup',{'access_token':'a'}, {})
    assert expiry is None
    database_with(monkeypatch,(encoded,expiry))
    assert vendor.access_credentials(7,'clickup')['access_token']=='a'
    database_with(monkeypatch,(encoded,vendor.cloud.now()-timedelta(seconds=1)))
    with pytest.raises(vendor.ConnectionError,match='reconnect_required'):vendor.access_credentials(7,'clickup')
    with pytest.raises(vendor.ConnectionError,match='token_expiry_missing'):vendor.token_data(7,'zoom',{'access_token':'a'}, {})
    claims=base64.urlsafe_b64encode(json.dumps({'exp':int(time.time())+3600}).encode()).decode().rstrip('=')
    _,expiry=vendor.token_data(7,'monday',{'access_token':'h.'+claims+'.s','refresh_token':'r'}, {})
    assert expiry>vendor.cloud.now()


@pytest.mark.parametrize('key',list(vendor.SPECS))
def test_fixed_requests_bounded_reads_no_urls_from_model(key):
    args={'channel_id':'C123','file_key':'file123','workspace_id':'123','project_id':'123','list_id':'123','cloud_id':'cloud-123','site_id':'123','query':'test'}
    context={'tenant':'my-team','user_id':'123','site_id':'123'}
    for operation in vendor.SPECS[key][3]:
        method,url,params,body,headers=vendor.build_read(key,operation,args,context)
        assert url.startswith('https://') and method in ('GET','POST')
        if method=='POST':
            assert key in ('dropbox','monday','shopify')
            assert 'mutation' not in json.dumps(body) and 'test' not in json.dumps(body)
        if key in ('shopify','zendesk'):assert urlsplit(url).hostname=='my-team.'+vendor.TENANTS[key]
    for key,operation,field in [('slack','history','channel_id'),('figma','file','file_key'),('jira','issues','cloud_id'),('wordpress','posts','site_id')]:
        with pytest.raises(vendor.ConnectionError):vendor.build_read(key,operation,{field:'https://evil.example'},context)


def test_shopify_hmac_tenant_state_and_code_are_verified(configured,monkeypatch):
    query={'code':'abc','state':'s'*43,'shop':'my-store.myshopify.com','timestamp':'123'}
    message='&'.join(k+'='+v for k,v in sorted(query.items()))
    query['hmac']=hmac.new(b'secret',message.encode(),hashlib.sha256).hexdigest()
    vendor.verify_shopify(query,{'tenant':'my-store'})
    for tamper in ({'code':'changed'},{'shop':'other.myshopify.com'},{'hmac':'0'*64}):
        with pytest.raises(vendor.ConnectionError):vendor.verify_shopify({**query,**tamper},{'tenant':'my-store'})
    monkeypatch.setattr(vendor,'consume_state',lambda *a:{'context':{'tenant':'my-store'},'verifier':'v'})
    with pytest.raises(vendor.ConnectionError):vendor.complete_connection(7,'shopify','other-state','abc',query)


def test_oauth1_signature_known_vector():
    # OAuth 1.0 signature example from RFC 5849 / OAuth core example.
    params={'file':'vacation.jpg','size':'original','oauth_consumer_key':'dpf43f3p2l4k3l03','oauth_token':'nnch734d00sl2jdk',
      'oauth_nonce':'kllo9940pd9333jh','oauth_timestamp':'1191242096','oauth_signature_method':'HMAC-SHA1','oauth_version':'1.0'}
    assert vendor.oauth1_signature('GET','http://photos.example.net/photos',params,'kd94hf93k423kf44','pfkkdhi9sl3r4s00')=='tR3+Ty81lMeYAr/Fid0kMTYa/WM='


def test_zotero_uses_verified_request_token_and_api_key_secret(configured,monkeypatch):
    database_with(monkeypatch,None)
    monkeypatch.setattr(vendor,'consume_state',lambda *a:{'context':{},'request_token':'temporary','request_secret':'secret'})
    calls=[]
    def exchange(url,extra,secret):
        calls.append((url,extra,secret));return {'oauth_token':'token','oauth_token_secret':'API-KEY','userID':'123'}
    monkeypatch.setattr(vendor,'zotero_exchange',exchange)
    with pytest.raises(vendor.ConnectionError):vendor.complete_connection(7,'zotero','s'*43,'verifier',{'oauth_token':'wrong'})
    statements=database_with(monkeypatch,None)
    vendor.complete_connection(7,'zotero','s'*43,'verifier',{'oauth_token':'temporary'})
    saved=json.loads(crypto.decrypt_secret(statements[-1][1][2]))
    assert saved['access_token']=='API-KEY' and saved['context']=={'user_id':'123'}
    assert calls[0][1]['oauth_verifier']=='verifier' and calls[0][2]=='secret'


def test_provider_errors_even_on_http200_and_response_cap(monkeypatch):
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,n):yield self.payload
    response=Response();response.payload=b'{"ok":false,"error":"not_in_channel"}'
    monkeypatch.setattr(vendor.requests,'request',lambda *a,**k:response)
    with pytest.raises(vendor.ConnectionError,match='provider_request_failed'):vendor.request_read('GET','https://slack.com/api/conversations.history',{},None,{})
    response.payload=b'{"errors":[{"message":"denied"}]}'
    with pytest.raises(vendor.ConnectionError):vendor.request_read('POST','https://api.monday.com/v2',{}, {}, {})
    response.payload=b'x'*2_000_001
    with pytest.raises(vendor.ConnectionError,match='provider_response_too_large'):vendor.request_read('GET','https://api.example',{},None,{})


def test_jira_rejects_ungranted_cloud_before_read(monkeypatch):
    monkeypatch.setattr(vendor,'access_credentials',lambda *a:{'context':{},'access_token':'a'})
    calls=[]
    def request(*args):calls.append(args);return [{'id':'different','scopes':['read:jira-work']}]
    monkeypatch.setattr(vendor,'request_read',request)
    with pytest.raises(vendor.ConnectionError,match='resource_not_authorized'):vendor.read_tool(7,{'cloud_id':'chosen'},connector='jira',operation='projects')
    assert len(calls)==1 and calls[0][1].endswith('/accessible-resources')


def test_basic_json_form_exchange_and_figma_refresh_endpoint(configured,monkeypatch):
    calls=[]
    def post(url,**kwargs):calls.append((url,kwargs));return SimpleNamespace(status_code=200,json=lambda:{'access_token':'a','expires_in':3600})
    monkeypatch.setattr(vendor.requests,'post',post)
    for key in ('canva','jira','asana','figma'):
        vendor.token_request(key,{'grant_type':'refresh_token','refresh_token':'r'}, {})
    assert calls[0][1]['auth']==('client','secret') and 'client_secret' not in calls[0][1]['data']
    assert 'json' in calls[1][1] and 'data' in calls[2][1]
    assert calls[3][0]=='https://api.figma.com/v1/oauth/refresh' and calls[3][1]['data']=={'refresh_token':'r'}
    assert all(not kwargs['allow_redirects'] for _,kwargs in calls)


def test_registered_tools_are_reads(configured,monkeypatch):
    from services import velia_agent_tool_registry_service as registry
    from services.velia_agent_protocol_service import ActionRisk
    definitions=[];monkeypatch.setattr(registry,'register_tool',definitions.append)
    vendor.register_tools()
    assert len(definitions)==sum(len(s[3]) for s in vendor.SPECS.values())
    assert all(d.risk==ActionRisk.READ for d in definitions)


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['shopify','zotero','slack'])
async def test_vendor_callback_preserves_provider_proof_and_requires_session(key):
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from desktop.feature_routes import setup_feature_routes
    seen=[]
    async def session(request):return SimpleNamespace(access='fixture') if request.headers.get('X-Test-Session') else None
    async def upstream(method,path,**kwargs):seen.append((path,kwargs));return 200,{'ok':True}
    app=web.Application();setup_feature_routes(app,session_for=session,same_origin=lambda r:True,upstream=upstream,json_response=lambda d,status=200:web.json_response(d,status=status))
    query={'state':'s'*43}
    if key=='zotero':query.update(oauth_token='temporary',oauth_verifier='verifier')
    else:query['code']='fixture'
    if key=='shopify':query.update(shop='my-store.myshopify.com',hmac='0'*64,timestamp='123')
    async with TestClient(TestServer(app)) as client:
        url='/web-api/v1/connectors/'+key+'/callback'
        assert (await client.get(url,params=query,allow_redirects=False)).status==401
        assert not seen
        r=await client.get(url,params=query,headers={'X-Test-Session':'1'},allow_redirects=False)
        assert r.status==303 and r.headers['Location']=='/' and r.headers['Cache-Control']=='no-store'
        assert seen[0][1]['token']=='fixture'
        data=seen[0][1]['data']
        assert data['code']==('verifier' if key=='zotero' else 'fixture')
        if key in ('zotero','shopify'):assert data['provider']==query
        r=await client.get(url+'?state='+query['state']+'&state='+query['state']+'&code=fixture',headers={'X-Test-Session':'1'},allow_redirects=False)
        assert r.status==400 and len(seen)==1


def test_wordpress_cannot_read_a_different_site():
    _,url,_,_,_=vendor.build_read('wordpress','sites',{}, {'site_id':'123'})
    assert url.endswith('/sites/123')
    with pytest.raises(vendor.ConnectionError,match='resource_not_authorized'):
        vendor.build_read('wordpress','posts',{'site_id':'456'}, {'site_id':'123'})


def test_legacy_todoist_expiry_and_dot_path_rejection(configured):
    _,expiry=vendor.token_data(7,'todoist',{'access_token':'a','expires_in':315360000}, {})
    assert expiry>vendor.cloud.now()+timedelta(days=365)
    for value in ('.','..'):
        with pytest.raises(vendor.ConnectionError):vendor.cloud.identifier(value)
