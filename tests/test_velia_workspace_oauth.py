import asyncio
import hashlib
import hmac
import time
from urllib.parse import urlencode
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from types import SimpleNamespace
from services import velia_workspace_oauth_routes as oauth


def test_google_config_and_callback_do_not_expose_secrets_or_accept_external_return(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID','public-client');monkeypatch.setenv('GOOGLE_CLIENT_SECRET','private-secret')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI','https://backend.example/api/auth/google/callback')
    monkeypatch.setenv('VELIA_DEVELOPER_WEB_CALLBACK_REDIRECT','https://velia.example/?connection=github')
    assert oauth.config('google')['configured']
    assert 'private-secret' not in str(oauth.config('google'))
    assert oauth.google_callback_target({'state':'login-state','code':'abc'}) is None
    result=oauth.google_callback_target({'state':'velia_workspace.random','code':'abc','redirect':'https://evil.example'})
    assert result.startswith('https://velia.example/web-api/v1/connections/google/callback?')
    assert 'evil.example' not in result


def test_shopify_callback_hmac_is_required_and_shop_is_fixed():
    query={'shop':'user-shop.myshopify.com','timestamp':str(int(time.time())),'code':'code','state':'state'}
    query['hmac']=hmac.new(b'secret',urlencode(sorted(query.items())).encode(),hashlib.sha256).hexdigest()
    assert oauth.shopify_callback_valid(query,'secret','user-shop.myshopify.com')
    assert not oauth.shopify_callback_valid({**query,'code':'changed'},'secret','user-shop.myshopify.com')
    assert not oauth.shopify_callback_valid(query,'secret','other-shop.myshopify.com')
    assert not oauth.shopify_callback_valid({**query,'timestamp':'0'},'secret','user-shop.myshopify.com')
    for value in ['evil.example','127.0.0.1','user-shop.myshopify.com@evil.example','user-shop.myshopify.com/path']:
        try:oauth.shop_domain(value);assert False
        except ValueError:pass


def test_exchange_requires_auth_and_never_returns_refresh_token(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID','public');monkeypatch.setenv('GOOGLE_CLIENT_SECRET','private-secret')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI','https://backend.example/api/auth/google/callback')
    async def scenario():
        async def exchange(provider,data):return {'access_token':'account-owned-token','token_type':'Bearer','expires_in':3600}
        monkeypatch.setattr(oauth,'exchange',exchange)
        module=SimpleNamespace(_mobile_api_available=lambda:True,_require_mobile_auth=lambda request:{'user_id':7} if request.headers.get('X-Account') else None,
            _json_response=lambda value,status=200:web.json_response(value,status=status))
        app=web.Application();oauth.setup_workspace_oauth_routes(app,module)
        async with TestServer(app) as server, ClientSession() as client:
            url=server.make_url('/mobile-api/v1/workspace-oauth/google/token')
            assert (await client.post(url,json={'code':'code'})).status==401
            begin=await client.post(server.make_url('/mobile-api/v1/workspace-oauth/google/begin'),json={},headers={'X-Account':'7'})
            state=(await begin.json())['state']
            response=await client.post(url,json={'code':'code','state':state},headers={'X-Account':'7'})
            assert response.status==200 and response.headers['Cache-Control']=='no-store'
            assert 'refresh_token' not in await response.json()
    asyncio.run(scenario())


def test_google_exchange_keeps_client_secret_private_and_drops_refresh_token(monkeypatch):
    import json
    monkeypatch.setenv('GOOGLE_CLIENT_ID','public');monkeypatch.setenv('GOOGLE_CLIENT_SECRET','private-client-secret')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI','https://backend.example/api/auth/google/callback')
    calls=[]
    class Content:
        async def read(self,limit):return json.dumps({'access_token':'account-owned-access','refresh_token':'do-not-forward','expires_in':3600}).encode()
    class Response:
        status=200;content=Content()
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    class Session:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def post(self,url,**kwargs):calls.append((url,kwargs));return Response()
    monkeypatch.setattr(oauth.aiohttp,'ClientSession',Session)
    result=asyncio.run(oauth.exchange('google',{'code':'auth-code','code_verifier':'v'*64}))
    assert calls[0][0]=='https://oauth2.googleapis.com/token'
    assert calls[0][1]['data']['client_secret']=='private-client-secret'
    assert result['access_token']=='account-owned-access' and 'refresh_token' not in result
    assert 'private-client-secret' not in str(result)


def test_provider_state_cannot_exchange_for_a_different_velia_user(monkeypatch):
    import pytest
    monkeypatch.setenv('VELIA_SHOPIFY_CLIENT_SECRET','operator-secret')
    state=oauth.issue_state(7,'shopify','user-shop.myshopify.com')
    oauth.verify_state(state,7,'shopify','user-shop.myshopify.com')
    for user,shop in [(8,'user-shop.myshopify.com'),(7,'other-shop.myshopify.com')]:
        with pytest.raises(ValueError):oauth.verify_state(state,user,'shopify',shop)
    with pytest.raises(ValueError):oauth.verify_state(state+'changed',7,'shopify','user-shop.myshopify.com')


def test_workspace_google_scopes_include_calendar_without_full_calendar_account_access(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'test-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'test-secret')
    monkeypatch.setenv('GOOGLE_REDIRECT_URI', 'https://backend.example/auth/google/callback')
    scopes = set(oauth.config('google')['scope'].split())
    assert 'https://www.googleapis.com/auth/calendar.events' in scopes
    assert 'https://www.googleapis.com/auth/calendar.freebusy' in scopes
    assert 'https://www.googleapis.com/auth/gmail.compose' in scopes
    assert 'https://www.googleapis.com/auth/calendar' not in scopes
