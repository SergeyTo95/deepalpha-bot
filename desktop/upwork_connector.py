"""Official Upwork OAuth and read-only MCP capability qualification.

No tool calls, proposals, contracts or money operations are made here.
"""
import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlsplit, urlencode
from aiohttp import ClientSession, ClientTimeout, ClientError
from cryptography.fernet import Fernet, InvalidToken
from desktop.work_store import WorkError

RESOURCE='https://mcp.upwork.com/mcp'
PROTOCOLS={'2025-03-26','2025-06-18','2025-11-25'}


def provider_url(value):
    if not isinstance(value,str):raise WorkError('upwork_metadata_invalid',502)
    try:
        u=urlsplit(value)
        if (u.scheme!='https' or not u.hostname or not (u.hostname=='upwork.com' or u.hostname.endswith('.upwork.com'))
                or u.username or u.password or u.port not in {None,443} or u.fragment):raise ValueError()
    except ValueError:raise WorkError('upwork_metadata_invalid',502)
    return value


class Vault:
    def __init__(self,store,key):self.store,self.cipher=store,Fernet(key.encode())
    def transaction(self,user,change):
        def apply(blob):
            try:doc=json.loads(self.cipher.decrypt(blob.encode())) if blob else {}
            except (InvalidToken,ValueError):raise WorkError('upwork_reconnect_required',409)
            result=change(doc)
            return self.cipher.encrypt(json.dumps(doc).encode()).decode(),result
        return self.store.connector_transaction(user,apply)
    def read(self,user):return self.transaction(user,lambda doc:dict(doc))
    def disconnect(self,user):
        # Clearing also repairs a connection encrypted with a retired key.
        return self.store.connector_transaction(user,lambda blob:('',None))


class UpworkConnector:
    def __init__(self,store,key,origin,request=None):
        u=urlsplit(origin)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Trusted HTTPS origin required')
        self.callback=origin.rstrip('/')+'/web-api/v1/work/upwork/callback'
        self.origin=origin.rstrip('/')
        self.vault=Vault(store,key);self.request=request or self.http
        self.locks={};self.metadata=None;self.metadata_until=0

    async def http(self,method,url,*,headers=None,data=None,form=None,notification=False):
        provider_url(url)
        try:
            async with ClientSession(timeout=ClientTimeout(total=20)) as client:
                async with client.request(method,url,headers=headers,json=data,data=form,allow_redirects=False) as response:
                    if response.status not in {200,201,202,204}:
                        raise WorkError('upwork_access_denied' if response.status in {401,403} else 'upwork_service_unavailable',503)
                    chunks=[];size=0
                    async for chunk in response.content.iter_chunked(65536):
                        size+=len(chunk)
                        if size>512000:raise WorkError('upwork_response_too_large',502)
                        chunks.append(chunk)
                    body=b''.join(chunks).decode()
                    if notification:return {},dict(response.headers)
                    if 'text/event-stream' in response.headers.get('Content-Type',''):
                        frames=[]
                        for frame in body.replace('\r\n','\n').split('\n\n'):
                            lines=[line[5:].lstrip() for line in frame.split('\n') if line.startswith('data:')]
                            if lines:frames.append(json.loads('\n'.join(lines)))
                        values=[v for v in frames if isinstance(v,dict) and 'id' in v]
                        if len(values)!=1:raise WorkError('upwork_protocol_error',502)
                        return values[0],dict(response.headers)
                    value=json.loads(body)
                    if not isinstance(value,dict):raise WorkError('upwork_protocol_error',502)
                    return value,dict(response.headers)
        except (ClientError,asyncio.TimeoutError,ValueError,UnicodeError):raise WorkError('upwork_service_unavailable',503)

    async def discover(self):
        if self.metadata and self.metadata_until>time.time():return self.metadata
        resource=None
        for url in ['https://mcp.upwork.com/.well-known/oauth-protected-resource/mcp',
                    'https://mcp.upwork.com/.well-known/oauth-protected-resource']:
            try:resource,_=await self.request('GET',url);break
            except WorkError:continue
        if not resource or resource.get('resource')!=RESOURCE:raise WorkError('upwork_discovery_unavailable',503)
        issuers=resource.get('authorization_servers')
        if not isinstance(issuers,list) or not issuers:raise WorkError('upwork_metadata_invalid',502)
        issuer=provider_url(issuers[0]);u=urlsplit(issuer)
        if u.query:raise WorkError('upwork_metadata_invalid',502)
        metadata=None
        for url in [u.scheme+'://'+u.netloc+'/.well-known/oauth-authorization-server'+u.path.rstrip('/'),
                    issuer.rstrip('/')+'/.well-known/openid-configuration']:
            try:metadata,_=await self.request('GET',url);break
            except WorkError:continue
        if not metadata:raise WorkError('upwork_discovery_unavailable',503)
        if metadata.get('issuer')!=issuer or 'S256' not in metadata.get('code_challenge_methods_supported',[]):
            raise WorkError('upwork_metadata_invalid',502)
        for field in ['authorization_endpoint','token_endpoint','registration_endpoint']:provider_url(metadata.get(field))
        scopes=resource.get('scopes_supported',[])
        if not isinstance(scopes,list) or len(scopes)>100 or any(not isinstance(s,str) or not s or len(s)>100 or any(ord(c)<33 or ord(c)>126 for c in s) for s in scopes):raise WorkError('upwork_metadata_invalid',502)
        self.metadata={**metadata,'scope':' '.join(scopes)};self.metadata_until=time.time()+300
        return self.metadata

    async def start(self,user):
        async with self.locks.setdefault(user,asyncio.Lock()):
            old=await asyncio.to_thread(self.vault.read,user)
            if old.get('started_at',0)>time.time()-60:raise WorkError('upwork_connect_rate_limit',429)
            await asyncio.to_thread(self.vault.transaction,user,lambda doc:doc.update(started_at=time.time()))
            metadata=await self.discover()
            registration,_=await self.request('POST',metadata['registration_endpoint'],data={
                'client_name':'VELIA Work','redirect_uris':[self.callback],
                'grant_types':['authorization_code','refresh_token'],'response_types':['code'],'token_endpoint_auth_method':'none'})
            client=registration.get('client_id')
            if not isinstance(client,str) or not client or len(client)>500 or registration.get('token_endpoint_auth_method','none')!='none':
                raise WorkError('upwork_registration_invalid',502)
            state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            pending={'status':'pending','state_hash':hashlib.sha256(state.encode()).hexdigest(),'verifier':verifier,
                'client_id':client,'metadata':metadata,'started_at':time.time(),'expires_at':time.time()+600,'nonce':secrets.token_hex(16)}
            def save(doc):doc.clear();doc.update(pending)
            await asyncio.to_thread(self.vault.transaction,user,save)
            params={'response_type':'code','client_id':client,'redirect_uri':self.callback,'state':state,
                'code_challenge':challenge,'code_challenge_method':'S256','resource':RESOURCE}
            if metadata['scope']:params['scope']=metadata['scope']
            separator='&' if '?' in metadata['authorization_endpoint'] else '?'
            return metadata['authorization_endpoint']+separator+urlencode(params)

    async def callback_exchange(self,user,state,code,issuer=None):
        if not isinstance(state,str) or not 20<=len(state)<=200 or not isinstance(code,str) or not 1<=len(code)<=2048:
            raise WorkError('upwork_callback_invalid')
        async with self.locks.setdefault(user,asyncio.Lock()):
            def consume(doc):
                if (doc.get('status')!='pending' or doc.get('expires_at',0)<time.time()
                    or not secrets.compare_digest(doc.get('state_hash',''),hashlib.sha256(state.encode()).hexdigest())
                    or (issuer is not None and issuer!=doc['metadata']['issuer'])):
                    raise WorkError('upwork_callback_invalid')
                snapshot=dict(doc);doc['status']='exchanging';doc.pop('state_hash',None);return snapshot
            pending=await asyncio.to_thread(self.vault.transaction,user,consume)
            try:
                token,_=await self.request('POST',pending['metadata']['token_endpoint'],form={
                    'grant_type':'authorization_code','client_id':pending['client_id'],'redirect_uri':self.callback,
                    'code':code,'code_verifier':pending['verifier'],'resource':RESOURCE})
                if (not isinstance(token.get('access_token'),str) or not 1<=len(token['access_token'])<=16000
                    or any(ord(c)<33 or ord(c)>126 for c in token['access_token'])
                    or str(token.get('token_type','')).lower()!='bearer'):
                    raise WorkError('upwork_token_invalid',502)
                tools=await self.catalog(token['access_token'])
                def save(doc):
                    if doc.get('nonce')!=pending['nonce'] or doc.get('status')!='exchanging':raise WorkError('upwork_connection_cancelled',409)
                    doc.clear();doc.update(status='connected',access_token=token['access_token'],refresh_token=token.get('refresh_token'),
                        client_id=pending['client_id'],metadata=pending['metadata'],tools=tools,verified_at=time.time(),nonce=pending['nonce'],
                        expires_at=time.time()+max(0,min(float(token.get('expires_in',300)),86400)))
                await asyncio.to_thread(self.vault.transaction,user,save)
                return len(tools)
            except Exception:
                def fail(doc):
                    if doc.get('nonce')==pending['nonce']:doc.clear();doc.update(status='reconnect_required')
                await asyncio.to_thread(self.vault.transaction,user,fail)
                raise

    async def catalog(self,token):
        headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
        sequence=0
        async def rpc(method,params=None,notification=False):
            nonlocal sequence
            sequence+=1;payload={'jsonrpc':'2.0','method':method}
            if not notification:payload['id']=sequence
            if params is not None:payload['params']=params
            value,response_headers=await self.request('POST',RESOURCE,headers=dict(headers),data=payload,notification=notification)
            sid=response_headers.get('Mcp-Session-Id') or response_headers.get('mcp-session-id')
            if sid:
                if not isinstance(sid,str) or len(sid)>500 or any(ord(c)<32 for c in sid):raise WorkError('upwork_protocol_error',502)
                headers['Mcp-Session-Id']=sid
            if notification:return None
            if value.get('jsonrpc')!='2.0' or value.get('id')!=sequence or value.get('error') or not isinstance(value.get('result'),dict):
                raise WorkError('upwork_protocol_error',502)
            return value['result']
        initialized=await rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'VELIA Work','version':'1.0'}})
        protocol=initialized.get('protocolVersion')
        if protocol not in PROTOCOLS:raise WorkError('upwork_protocol_unsupported',502)
        headers['MCP-Protocol-Version']=protocol
        await rpc('notifications/initialized',notification=True)
        tools=[];cursor=None
        for _ in range(5):
            page=await rpc('tools/list',{'cursor':cursor} if cursor else {})
            items=page.get('tools')
            if not isinstance(items,list) or any(not isinstance(t,dict) or not isinstance(t.get('name'),str) or not isinstance(t.get('inputSchema'),dict) for t in items):
                raise WorkError('upwork_protocol_error',502)
            tools.extend(items)
            if len(tools)>250 or len(json.dumps(tools))>512000:raise WorkError('upwork_catalog_too_large',502)
            cursor=page.get('nextCursor')
            if not cursor:break
            if not isinstance(cursor,str) or len(cursor)>2000:raise WorkError('upwork_protocol_error',502)
        else:raise WorkError('upwork_catalog_too_large',502)
        if not tools:raise WorkError('upwork_tools_unavailable',503)
        return tools

    async def status(self,user):
        try:doc=await asyncio.to_thread(self.vault.read,user)
        except WorkError:return {'connected':False,'status':'reconnect_required','tool_count':0}
        connected=doc.get('status')=='connected' and doc.get('expires_at',0)>time.time()+30
        return {'connected':connected,'status':'connected' if connected else 'reconnect_required' if doc.get('status')=='connected' else doc.get('status','disconnected'),
                'tool_count':len(doc.get('tools',[])) if connected else 0,'verified_at':doc.get('verified_at')}

    async def capabilities(self,user):
        doc=await asyncio.to_thread(self.vault.read,user)
        if doc.get('status')!='connected' or doc.get('expires_at',0)<=time.time()+30:
            raise WorkError('upwork_reconnect_required',409)
        return {'tools':doc.get('tools',[]),'verified_at':doc.get('verified_at'),'execution_enabled':False}

    async def verify(self,user):
        async with self.locks.setdefault(user,asyncio.Lock()):
            doc=await asyncio.to_thread(self.vault.read,user)
            if doc.get('status')!='connected':raise WorkError('upwork_reconnect_required',409)
            if doc.get('last_check',0)>time.time()-60:raise WorkError('upwork_connect_rate_limit',429)
            def mark(value):
                if value.get('nonce')!=doc['nonce']:raise WorkError('upwork_connection_cancelled',409)
                value['last_check']=time.time()
            await asyncio.to_thread(self.vault.transaction,user,mark)
            token=doc['access_token'];expiry=doc.get('expires_at',0);refresh=doc.get('refresh_token')
            if expiry<=time.time()+30:
                if not isinstance(refresh,str) or not refresh:raise WorkError('upwork_reconnect_required',409)
                updated,_=await self.request('POST',doc['metadata']['token_endpoint'],form={
                    'grant_type':'refresh_token','refresh_token':refresh,'client_id':doc['client_id'],'resource':RESOURCE})
                token=updated.get('access_token')
                if not isinstance(token,str) or not token or len(token)>16000 or any(ord(c)<33 or ord(c)>126 for c in token) or str(updated.get('token_type','')).lower()!='bearer':
                    raise WorkError('upwork_token_invalid',502)
                refresh=updated.get('refresh_token',refresh)
                expiry=time.time()+max(0,min(float(updated.get('expires_in',300)),86400))
            tools=await self.catalog(token)
            def save(value):
                if value.get('nonce')!=doc['nonce'] or value.get('status')!='connected':raise WorkError('upwork_connection_cancelled',409)
                value.update(access_token=token,refresh_token=refresh,expires_at=expiry,tools=tools,verified_at=time.time())
            await asyncio.to_thread(self.vault.transaction,user,save)
            return await self.status(user)
