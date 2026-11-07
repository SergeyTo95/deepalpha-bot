"""Private OAuth exchange for browser-owned Google and Shopify connections."""
import asyncio
import base64
import json
import secrets
import hashlib
import hmac
import os
import re
import time
from urllib.parse import urlencode, urlsplit

import aiohttp
from aiohttp import web

PREFIX = '/mobile-api/v1/workspace-oauth'
GOOGLE_SCOPES = 'openid email https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose'
SHOPIFY_SCOPES = 'read_products,write_products,read_orders,read_inventory'


def state_secret(provider):
    return (os.getenv('VELIA_GOOGLE_OAUTH_CLIENT_SECRET') or os.getenv('GOOGLE_CLIENT_SECRET','')) if provider=='google' else os.getenv('VELIA_SHOPIFY_CLIENT_SECRET','')


def issue_state(user_id,provider,shop=''):
    secret=state_secret(provider)
    if not secret:raise ValueError('workspace_app_not_configured')
    payload={'user':int(user_id),'provider':provider,'shop':shop,'expires':int(time.time())+600,'nonce':secrets.token_urlsafe(20)}
    encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
    signature=hmac.new(secret.encode(),encoded.encode(),hashlib.sha256).hexdigest()
    return ('velia_workspace.' if provider=='google' else '')+encoded+'.'+signature


def verify_state(state,user_id,provider,shop=''):
    if not isinstance(state,str) or len(state)>2000:raise ValueError('invalid_workspace_state')
    if provider=='google':
        if not state.startswith('velia_workspace.'):raise ValueError('invalid_workspace_state')
        state=state.removeprefix('velia_workspace.')
    encoded,signature=state.split('.')
    expected=hmac.new(state_secret(provider).encode(),encoded.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature):raise ValueError('invalid_workspace_state')
    value=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
    if value.get('user')!=int(user_id) or value.get('provider')!=provider or value.get('shop')!=shop or not time.time()<value.get('expires',0)<=time.time()+601:
        raise ValueError('invalid_workspace_state')


def google_callback_target(query):
    """Fixed return origin; arbitrary login flows keep their existing callback."""
    if not str(query.get('state','')).startswith('velia_workspace.'):
        return None
    value = os.getenv('VELIA_WORKSPACE_WEB_ORIGIN') or os.getenv('VELIA_DEVELOPER_WEB_CALLBACK_REDIRECT','')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('workspace_origin_missing')
    return parsed.scheme+'://'+parsed.netloc+'/web-api/v1/connections/google/callback?'+urlencode(
        {key:query[key] for key in ['state','code','error'] if key in query})


def config(provider):
    if provider == 'google':
        client = os.getenv('VELIA_GOOGLE_OAUTH_CLIENT_ID') or os.getenv('GOOGLE_CLIENT_ID','')
        secret = os.getenv('VELIA_GOOGLE_OAUTH_CLIENT_SECRET') or os.getenv('GOOGLE_CLIENT_SECRET','')
        callback = os.getenv('VELIA_WORKSPACE_GOOGLE_CALLBACK') or os.getenv('GOOGLE_REDIRECT_URI','')
        scope = GOOGLE_SCOPES
    else:
        client, secret = os.getenv('VELIA_SHOPIFY_CLIENT_ID',''), os.getenv('VELIA_SHOPIFY_CLIENT_SECRET','')
        callback, scope = '', SHOPIFY_SCOPES
    parsed = urlsplit(callback) if callback else None
    ready = bool(client and secret and (provider != 'google' or parsed and parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password))
    return {'configured':ready,'client_id':client if ready else '', 'redirect_uri':callback if ready else '', 'scope':scope}


def shop_domain(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,61}\.myshopify\.com',value):
        raise ValueError('invalid_shop')
    return value


def shopify_callback_valid(query, secret, shop):
    if not isinstance(query,dict) or query.get('shop') != shop or not re.fullmatch(r'[a-f0-9]{64}',str(query.get('hmac',''))):
        return False
    try:
        if abs(time.time()-int(query.get('timestamp',0))) > 600:
            return False
    except (TypeError,ValueError):
        return False
    message=urlencode(sorted((key,value) for key,value in query.items() if key not in {'hmac','signature'}))
    return hmac.compare_digest(hmac.new(secret.encode(),message.encode(),hashlib.sha256).hexdigest(),query['hmac'])


async def exchange(provider, data):
    settings=config(provider)
    if not settings['configured']:
        raise ValueError('workspace_app_not_configured')
    code=data.get('code')
    if not isinstance(code,str) or not 1<=len(code)<=2000:
        raise ValueError('invalid_code')
    if provider == 'google':
        verifier=data.get('code_verifier')
        if not isinstance(verifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',verifier):
            raise ValueError('invalid_verifier')
        url='https://oauth2.googleapis.com/token'
        body={'grant_type':'authorization_code','code':code,'code_verifier':verifier,
              'client_id':settings['client_id'],'redirect_uri':settings['redirect_uri'],
              'client_secret':os.getenv('VELIA_GOOGLE_OAUTH_CLIENT_SECRET') or os.getenv('GOOGLE_CLIENT_SECRET','')}
        options={'data':body}
    else:
        shop=shop_domain(data.get('shop'))
        secret=os.getenv('VELIA_SHOPIFY_CLIENT_SECRET','')
        query=data.get('callback_query')
        if not shopify_callback_valid(query,secret,shop) or query.get('code') != code or query.get('state') != data.get('state'):
            raise ValueError('invalid_shopify_callback')
        url='https://'+shop+'/admin/oauth/access_token'
        options={'json':{'client_id':settings['client_id'],'client_secret':secret,'code':code}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),trust_env=False) as client:
        async with client.post(url,allow_redirects=False,**options) as response:
            if response.status != 200:
                raise ValueError('workspace_authorization_failed')
            body=await response.content.read(64001)
            if len(body)>64000:
                raise ValueError('workspace_invalid_response')
            import json
            result=json.loads(body)
    token=result.get('access_token')
    if not isinstance(token,str) or not re.fullmatch(r'[\x21-\x7e]{10,4096}',token):
        raise ValueError('workspace_invalid_response')
    # Refresh tokens stay out of the browser connection path. Reconnect after expiry.
    return {'access_token':token,'token_type':'Bearer','expires_in':result.get('expires_in',3600),'scope':result.get('scope','')}


def setup_workspace_oauth_routes(app, routes_module):
    slots=asyncio.Semaphore(4)
    async def handle(request):
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth=routes_module._require_mobile_auth(request)
        if not auth:
            return routes_module._json_response({'ok':False,'error':'unauthorized'},status=401)
        provider=request.match_info['provider']
        action=request.path.rsplit('/',1)[-1]
        if request.method == 'GET':
            return routes_module._json_response({'ok':True,**config(provider)})
        try:
            if request.content_length and request.content_length>16000 or slots.locked():
                return routes_module._json_response({'ok':False,'error':'workspace_busy'},status=429)
            data=await request.json()
            if not isinstance(data,dict):
                raise ValueError('invalid_request')
            shop=shop_domain(data.get('shop')) if provider=='shopify' else ''
            if action=='begin':
                settings=config(provider)
                if not settings['configured']:raise ValueError('workspace_app_not_configured')
                return routes_module._json_response({'ok':True,**settings,'state':issue_state(auth['user_id'],provider,shop)})
            verify_state(data.get('state'),auth['user_id'],provider,shop)
            async with slots:
                result=await exchange(provider,data)
            response=routes_module._json_response({'ok':True,**result})
            response.headers['Cache-Control']='no-store'
            return response
        except (ValueError,TypeError,aiohttp.ClientError,TimeoutError):
            return routes_module._json_response({'ok':False,'error':'workspace_authorization_failed'},status=400)
    app.router.add_get(PREFIX+'/{provider:google|shopify}/config',handle)
    app.router.add_post(PREFIX+'/{provider:google|shopify}/begin',handle)
    app.router.add_post(PREFIX+'/{provider:google|shopify}/token',handle)
