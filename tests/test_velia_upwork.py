import asyncio
import json
from urllib.parse import urlsplit,parse_qs
import pytest
from cryptography.fernet import Fernet
from desktop.work_store import WorkStore,WorkError
from desktop.upwork_connector import UpworkConnector,RESOURCE,provider_url

@pytest.fixture
def connector(tmp_path):
    store=WorkStore(sqlite_path=str(tmp_path/'upwork.db'));store.initialize()
    calls=[]
    async def request(method,url,**kw):
        calls.append((method,url,kw))
        if 'oauth-protected-resource' in url:return {'resource':RESOURCE,'authorization_servers':['https://mcp.upwork.com'],'scopes_supported':['read','write']},{}
        if 'oauth-authorization-server' in url:return {'issuer':'https://mcp.upwork.com','code_challenge_methods_supported':['S256'],
            'authorization_endpoint':'https://mcp.upwork.com/authorize','token_endpoint':'https://mcp.upwork.com/token','registration_endpoint':'https://mcp.upwork.com/register'},{}
        if url.endswith('/register'):return {'client_id':'client-1','token_endpoint_auth_method':'none'},{}
        if url.endswith('/token'):return {'access_token':'secret-access','refresh_token':'secret-refresh','token_type':'Bearer','expires_in':3600},{}
        payload=kw['data'];name=payload['method']
        if name=='notifications/initialized':return {},{}
        result={'protocolVersion':'2025-11-25'} if name=='initialize' else {'tools':[{'name':'job_search','inputSchema':{'type':'object'}}]}
        return {'jsonrpc':'2.0','id':payload['id'],'result':result},{'Mcp-Session-Id':'session-one'}
    value=UpworkConnector(store,Fernet.generate_key().decode(),'https://velia.example',request=request)
    value.calls=calls
    return value

@pytest.mark.parametrize('url',['http://mcp.upwork.com','https://upwork.com.attacker.test','https://upwork.com@attacker.test',
    'https://127.0.0.1','https://mcp.upwork.com:8080/path','https://mcp.upwork.com/path#secret'])
def test_reject_nonprovider_destination(url):
    with pytest.raises(WorkError):provider_url(url)


def test_oauth_owner_state_pkce_catalog_and_encryption(connector):
    async def scenario():
        url=await connector.start(7);query=parse_qs(urlsplit(url).query)
        assert query['code_challenge_method']==['S256'] and query['resource']==[RESOURCE]
        assert query['redirect_uri']==['https://velia.example/web-api/v1/work/upwork/callback']
        state=query['state'][0]
        with pytest.raises(WorkError):await connector.callback_exchange(8,state,'code')
        with pytest.raises(WorkError):await connector.callback_exchange(7,state,'code','https://evil.test')
        assert await connector.callback_exchange(7,state,'code','https://mcp.upwork.com')==1
        status=await connector.status(7);assert status['connected'] and status['tool_count']==1
        assert not (await connector.status(8))['connected']
        with pytest.raises(WorkError):await connector.callback_exchange(7,state,'code')
        assert not {'access_token','refresh_token','metadata'} & set(status)
        assert 'secret-access' not in json.dumps(connector.vault.store.workspace(7))
        raw=connector.vault.store.connector_transaction(7,lambda blob:(blob,blob))
        assert 'secret-access' not in raw and 'secret-refresh' not in raw
        methods=[kw['data']['method'] for method,url,kw in connector.calls if url==RESOURCE]
        assert methods==['initialize','notifications/initialized','tools/list']
        token_request=next(kw['form'] for method,url,kw in connector.calls if url.endswith('/token'))
        assert token_request['code_verifier'] and token_request['resource']==RESOURCE
        await connector.verify(7)
        with pytest.raises(WorkError,match='rate_limit'):await connector.verify(7)
        connector.vault.disconnect(7)
        assert not (await connector.status(7))['connected']
    asyncio.run(scenario())


def test_discovery_denial_keeps_connection_unconnected_and_rate_limits(connector):
    async def deny(*args,**kw):raise WorkError('upwork_access_denied',503)
    connector.request=deny
    async def scenario():
        with pytest.raises(WorkError,match='discovery_unavailable'):await connector.start(7)
        assert not (await connector.status(7))['connected']
        with pytest.raises(WorkError,match='rate_limit'):await connector.start(7)
    asyncio.run(scenario())


def test_callback_is_single_use_after_provider_failure(connector):
    original=connector.request
    async def scenario():
        state=parse_qs(urlsplit(await connector.start(7)).query)['state'][0]
        async def fail(method,url,**kw):
            if url.endswith('/token'):raise WorkError('upwork_access_denied',503)
            return await original(method,url,**kw)
        connector.request=fail
        with pytest.raises(WorkError):await connector.callback_exchange(7,state,'code')
        assert not (await connector.status(7))['connected']
        with pytest.raises(WorkError,match='callback_invalid'):await connector.callback_exchange(7,state,'code')
    asyncio.run(scenario())


def test_refresh_uses_resource_binding_and_discovery_rejects_foreign_issuer(connector):
    async def scenario():
        state=parse_qs(urlsplit(await connector.start(7)).query)['state'][0]
        await connector.callback_exchange(7,state,'code')
        connector.vault.transaction(7,lambda doc:doc.update(expires_at=0))
        assert not (await connector.status(7))['connected']
        assert (await connector.verify(7))['connected']
        forms=[kw['form'] for method,url,kw in connector.calls if url.endswith('/token')]
        assert forms[-1]['grant_type']=='refresh_token' and forms[-1]['resource']==RESOURCE
    asyncio.run(scenario())


def test_disconnect_during_exchange_cannot_restore_access(connector):
    async def scenario():
        state=parse_qs(urlsplit(await connector.start(7)).query)['state'][0]
        original=connector.request;entered=asyncio.Event();resume=asyncio.Event()
        async def paused(method,url,**kw):
            if url.endswith('/token'):entered.set();await resume.wait()
            return await original(method,url,**kw)
        connector.request=paused
        task=asyncio.create_task(connector.callback_exchange(7,state,'code'))
        await entered.wait();connector.vault.disconnect(7);resume.set()
        with pytest.raises(WorkError,match='connection_cancelled'):await task
        assert not (await connector.status(7))['connected']
        assert not connector.vault.read(7)
    asyncio.run(scenario())


def test_expired_callback_and_untrusted_discovery(connector):
    async def scenario():
        state=parse_qs(urlsplit(await connector.start(7)).query)['state'][0]
        connector.vault.transaction(7,lambda doc:doc.update(expires_at=0))
        with pytest.raises(WorkError,match='callback_invalid'):await connector.callback_exchange(7,state,'code')
        async def hostile(method,url,**kw):return {'resource':RESOURCE,'authorization_servers':['https://attacker.test']},{}
        connector.metadata=None;connector.request=hostile
        with pytest.raises(WorkError,match='metadata_invalid'):await connector.discover()
    asyncio.run(scenario())


def test_oauth_routes_keep_auth_origin_and_credentials_private(connector):
    from aiohttp import web,ClientSession
    from aiohttp.test_utils import TestServer
    from types import SimpleNamespace
    from desktop.work_routes import setup_work_routes
    async def scenario():
        async def session_for(request):return SimpleNamespace(user_id=7,access='owner') if request.headers.get('X-Owner') else None
        async def unused(*args,**kw):raise AssertionError('unexpected call')
        app=web.Application()
        setup_work_routes(app,store=connector.vault.store,upwork=connector,session_for=session_for,
            same_origin=lambda r:r.headers.get('Origin')=='https://velia.example',json_response=lambda d,status=200:web.json_response(d,status=status),
            upstream=unused,upstream_stream=unused,authenticate=unused,allowed=lambda _:True,handlers={})
        async with TestServer(app) as server,ClientSession() as client:
            base=str(server.make_url('/web-api/v1/work/'));h={'X-Owner':'1','Origin':'https://velia.example'}
            assert (await client.post(base+'upwork/connect',json={})).status==403
            assert (await client.post(base+'upwork/connect',headers={'Origin':h['Origin']},json={})).status==401
            response=await client.post(base+'upwork/connect',headers=h,json={})
            assert response.status==200
            url=(await response.json())['authorization_url'];state=parse_qs(urlsplit(url).query)['state'][0]
            assert (await client.get(base+'upwork/callback',params={'state':state,'code':'code'})).status==401
            response=await client.get(base+'upwork/callback',params={'state':state,'code':'code'},headers=h,allow_redirects=False)
            assert response.status==303 and response.headers['Location']=='/'
            assert response.headers['Referrer-Policy']=='no-referrer'
            body=await (await client.get(base+'status',headers=h)).json()
            assert body['connectors'][0]['connected']
            assert 'secret-access' not in json.dumps(body) and 'secret-refresh' not in json.dumps(body)
            assert (await client.post(base+'upwork/disconnect',headers=h,json={})).status==200
    asyncio.run(scenario())
