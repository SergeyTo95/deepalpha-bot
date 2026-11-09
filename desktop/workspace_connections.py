"""Session-owned Google REST and Shopify Admin tools; credentials never enter plans."""
import asyncio
import base64
from contextlib import contextmanager
from contextvars import ContextVar
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import hashlib
import json
import math
import os
import re
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit

from aiohttp import ClientSession, ClientTimeout, ClientError
from desktop.figma_connector import FigmaConnector, FigmaError
from desktop.figma_oauth import FigmaOAuth


def tool(name, description, properties, required=()):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}

S={'type':'string','maxLength':20000}
TIME={'type':'string','minLength':20,'maxLength':80}
ZONE={'type':'string','maxLength':100}
GOOGLE_OAUTH_SCOPES = ('openid email https://www.googleapis.com/auth/drive.readonly '
    'https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/spreadsheets '
    'https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/gmail.readonly '
    'https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/calendar.events '
    'https://www.googleapis.com/auth/calendar.freebusy')
ROWS={'type':'array','maxItems':200,'items':{'type':'array','maxItems':50,'items':{'type':['string','number','boolean','null']}}}
GOOGLE_TOOLS=[
 tool('drive_search','Find files by name on Google Drive',{'query':S},['query']),
 tool('drive_read','Read a text file or Google document; use sheets_read for spreadsheets',{'file_id':S},['file_id']),
 tool('sheets_create','Create a spreadsheet with rows. Safe formulas are supported.',{'title':S,'rows':ROWS},['title','rows']),
 tool('sheets_read','Read cells from a spreadsheet',{'spreadsheet_id':S,'range':S},['spreadsheet_id','range']),
 tool('sheets_write','Write cells in an existing spreadsheet',{'spreadsheet_id':S,'range':S,'rows':ROWS},['spreadsheet_id','range','rows']),
 tool('docs_create','Create a Google document with text',{'title':S,'text':S},['title','text']),
 tool('gmail_search','Search mail; returns message IDs for gmail_read',{'query':S},['query']),
 tool('gmail_read','Read a message as text',{'message_id':S},['message_id']),
 tool('gmail_draft','Create an email draft. Does not send email.',{'to':S,'subject':S,'body':S},['to','subject','body']),
 tool('calendar_events_list','List primary Google Calendar events; RFC3339 time_min/time_max include UTC offsets.',{'time_min':TIME,'time_max':TIME,'max_results':{'type':'integer','minimum':1,'maximum':50}}),
 tool('calendar_freebusy','Check primary Google Calendar busy intervals without changing the calendar.',{'time_min':TIME,'time_max':TIME,'time_zone':ZONE},['time_min','time_max']),
 tool('calendar_event_create','Create one primary Google Calendar event after explicit user confirmation; no invitations are sent.',{'title':{'type':'string','maxLength':300},'start':TIME,'end':TIME,'time_zone':ZONE,'location':{'type':'string','maxLength':500},'description':{'type':'string','maxLength':2000}},['title','start','end'])]
SHOPIFY_TOOLS=[
 tool('shopify_products','List up to 30 products with descriptions and inventory totals',{}),
 tool('shopify_orders','List up to 30 recent orders without customer personal data',{}),
 tool('shopify_product_update','Update a product title or description on explicit request',{'product_id':S,'title':S,'description_html':S},['product_id'])]


def text(data,key,limit=20000):
    value=data.get(key)
    if not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value:
        raise FigmaError('connection_invalid_arguments')
    return value


def identifier(data,key):
    value=text(data,key,200)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',value):
        raise FigmaError('connection_invalid_arguments')
    return value


def safe_cell(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) and not re.fullmatch(r'-?\d+(?:\.\d+)?',value):
        if not value.startswith('='):return "'"+value
        functions=re.findall(r'([A-Za-z_][A-Za-z0-9_.]*)\s*\(',value)
        allowed={'SUM','AVERAGE','COUNT','COUNTA','MIN','MAX','IF','ROUND','ROUNDUP','ROUNDDOWN','ABS','SUMIF','COUNTIF','IFERROR','AND','OR','NOT'}
        if any(name.upper() not in allowed for name in functions) or any(c in value for c in '[]!|\\\r\n') or not re.fullmatch(r'[=A-Za-z0-9_ +*/^%<>,.:;()"$&-]{1,2000}',value):
            return "'"+value
    return value


def rows(data):
    values=data.get('rows')
    if not isinstance(values,list) or not 1<=len(values)<=200:
        raise FigmaError('connection_invalid_arguments')
    for row in values:
        if not isinstance(row,list) or not 1<=len(row)<=50 or any(not isinstance(cell,(str,int,float,bool,type(None))) or isinstance(cell,float) and not math.isfinite(cell) or isinstance(cell,str) and len(cell)>2000 for cell in row):
            raise FigmaError('connection_invalid_arguments')
    return [[safe_cell(cell) for cell in row] for row in values]



def calendar_instant(value):
    if not isinstance(value,str) or not 20<=len(value)<=80:
        raise FigmaError('connection_calendar_time_invalid')
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        raise FigmaError('connection_calendar_time_invalid') from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FigmaError('connection_calendar_timezone_required')
    return parsed


def calendar_window(arguments, default=True):
    now=datetime.now(timezone.utc)
    first=arguments.get('time_min') or (now.isoformat() if default else '')
    last=arguments.get('time_max') or ((now+timedelta(days=7)).isoformat() if default else '')
    start,end=calendar_instant(first),calendar_instant(last)
    if not start < end <= start+timedelta(days=31):
        raise FigmaError('connection_calendar_range_invalid')
    return first,last


def calendar_zone(arguments):
    name=arguments.get('time_zone')
    if name is None:return None
    if not isinstance(name,str) or not 1<=len(name)<=100:
        raise FigmaError('connection_calendar_timezone_invalid')
    try:ZoneInfo(name)
    except (ZoneInfoNotFoundError,ValueError):
        raise FigmaError('connection_calendar_timezone_invalid') from None
    return name


def calendar_event_view(event):
    value=lambda key,limit: str(event.get(key) or '')[:limit]
    return {'id':value('id',200),'title':value('summary',300),
            'start':event.get('start',{}),'end':event.get('end',{}),
            'location':value('location',500),'status':value('status',30),
            'url':value('htmlLink',1500)}


def calendar_create_payload(a):
    """Validate a proposed Calendar write before allocating a write receipt.

    This preflight performs no Google API calls, so invalid times/arguments can
    fail definitively without claiming an ambiguous remote write occurred.
    """
    if not isinstance(a, dict):
        raise FigmaError('connection_invalid_arguments')
    allowed={'title','start','end','time_zone','location','description'}
    if set(a)-allowed:
        raise FigmaError('connection_invalid_arguments')
    title=text(a,'title',300)
    start,end=calendar_instant(a.get('start')),calendar_instant(a.get('end'))
    if not start < end <= start+timedelta(hours=24):
        raise FigmaError('connection_calendar_range_invalid')
    zone=calendar_zone(a)
    if zone:
        target_zone=ZoneInfo(zone)
        if (start.utcoffset()!=start.astimezone(target_zone).utcoffset()
                or end.utcoffset()!=end.astimezone(target_zone).utcoffset()):
            raise FigmaError('connection_calendar_timezone_offset_conflict')
    begin={'dateTime':a['start']}
    finish={'dateTime':a['end']}
    if zone:begin['timeZone']=finish['timeZone']=zone
    body={'summary':title,'start':begin,'end':finish}
    if 'location' in a:body['location']=text(a,'location',500)
    if 'description' in a:body['description']=text(a,'description',2000)
    return body


class WorkspaceConnector(FigmaConnector):
    def __init__(self, provider, request=None, *, persistent_store=None):
        super().__init__(pair_required=False)
        self.provider=provider
        self.api_request=request or self.api
        self.tools={item['name']:item for item in (GOOGLE_TOOLS if provider=='google' else SHOPIFY_TOOLS)}
        self.read_only_tools=({'drive_search','drive_read','sheets_read','gmail_search','gmail_read',
                               'calendar_events_list','calendar_freebusy'} if provider=='google' else
                              {'shopify_products','shopify_orders'})
        self.persistent_store=persistent_store if provider=='google' else None
        self.restore_lock=asyncio.Lock()

    async def call(self, owner, data):
        if self.provider=='google' and isinstance(data,dict) and data.get('name')=='calendar_event_create':
            calendar_create_payload(data.get('arguments'))
        return await super().call(owner,data)

    async def connect(self,owner,token,*,oauth_expires=None,refresh_token=None,persist=True):
        result=await super().connect(owner,token,oauth_expires=oauth_expires)
        if persist and self.persistent_store is not None:
            if not oauth_expires:
                self.connections.pop(owner,None)
                raise FigmaError('connection_storage_unavailable',503)
            try:
                await asyncio.to_thread(self.persistent_store.save,owner[0],
                    access_token=token,refresh_token=refresh_token,expires_at=oauth_expires,
                    client_id=self.oauth.google_config()['client_id'])
            except Exception:
                self.connections.pop(owner,None)
                raise FigmaError('connection_storage_unavailable',503) from None
        return result

    async def restore(self,owner):
        """Restore Google access after Railway cold starts or a new browser session."""
        if self.persistent_store is None or self.provider!='google':
            return
        if owner in self.connections:
            self.prune()
            if owner in self.connections:
                return
        async with self.restore_lock:
            self.prune()
            if owner in self.connections:
                return
            client_id=self.oauth.google_config()['client_id']
            if not client_id:
                return
            try:
                saved=await asyncio.to_thread(self.persistent_store.load,owner[0],client_id=client_id)
            except Exception:
                raise FigmaError('connection_storage_unavailable',503) from None
            if not saved:
                return
            expires=float(saved['expires_at'])
            access=saved['access_token']
            refresh=saved.get('refresh_token')
            if expires<=time.time()+90:
                if not refresh:
                    await self.forget(owner)
                    return
                try:
                    renewed=await self.oauth.google_refresh(refresh)
                except FigmaError as error:
                    if error.code=='connection_authorization_revoked':
                        await self.forget(owner)
                        return
                    raise
                access=renewed['access_token']
                expires=time.time()+min(renewed['expires_in'],3600)
                await self.connect(owner,access,oauth_expires=expires,
                                   refresh_token=renewed.get('refresh_token') or refresh)
            else:
                await self.connect(owner,access,oauth_expires=expires,persist=False)

    async def forget(self,owner):
        self.disconnect(owner)
        if self.persistent_store is not None:
            try:
                await asyncio.to_thread(self.persistent_store.delete,owner[0])
            except Exception:
                raise FigmaError('connection_storage_unavailable',503) from None

    async def api(self, connection, method, path, *, data=None, params=None, plain=False):
        if self.provider=='google':
            if path.startswith('/gmail/'):
                origin='https://gmail.googleapis.com'
            elif path.startswith('/v4/spreadsheets'):
                origin='https://sheets.googleapis.com'
            elif path.startswith('/v1/documents'):
                origin='https://docs.googleapis.com'
            elif path=='/v1/userinfo':
                origin='https://openidconnect.googleapis.com'
            else:
                origin='https://www.googleapis.com'
            headers={'Authorization':'Bearer '+connection['token']}
        else:
            shop=connection.get('shop','')
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,61}\.myshopify\.com',shop):
                raise FigmaError('connection_connect_required',409)
            origin='https://'+shop
            headers={'X-Shopify-Access-Token':connection['token']}
        try:
            async with ClientSession(timeout=ClientTimeout(total=30),trust_env=False) as client:
                async with client.request(method,origin+path,params=params,json=data,headers=headers,allow_redirects=False) as response:
                    if response.status not in {200,201}:
                        calendar=self.provider=='google' and path.startswith('/calendar/v3/')
                        reason=''
                        if calendar and response.status in {400,401,403,429}:
                            # Google's error reason can identify disabled APIs without exposing
                            # sensitive messages, calendar contents, or OAuth credentials.
                            raw=await response.content.read(8193)
                            if len(raw)<=8192:
                                try:
                                    fault=json.loads(raw)
                                    error=fault.get('error',{}) if isinstance(fault,dict) else {}
                                    if isinstance(error,dict):
                                        candidates=[detail.get('reason') for detail in error.get('details',[])
                                                    if isinstance(detail,dict)]
                                        candidates += [item.get('reason') for item in error.get('errors',[])
                                                       if isinstance(item,dict)]
                                        if 'SERVICE_DISABLED' in candidates or 'accessNotConfigured' in candidates:
                                            reason='connection_calendar_api_disabled'
                                        elif 'ACCESS_TOKEN_SCOPE_INSUFFICIENT' in candidates or 'insufficientPermissions' in candidates:
                                            reason='connection_calendar_permissions_required'
                                        elif any(x in candidates for x in ('rateLimitExceeded','userRateLimitExceeded','quotaExceeded')):
                                            reason='connection_calendar_rate_limited'
                                except (ValueError,TypeError,UnicodeError):
                                    pass
                        if reason:
                            raise FigmaError(reason,429 if reason.endswith('rate_limited') else 503 if reason.endswith('api_disabled') else 403)
                        if response.status in {401,403}:
                            raise FigmaError('connection_access_denied',403)
                        if calendar and response.status==400:
                            raise FigmaError('connection_calendar_request_invalid',400)
                        if calendar and response.status==429:
                            raise FigmaError('connection_calendar_rate_limited',429)
                        raise FigmaError('connection_unavailable',503)
                    body=await response.content.read(1000001)
                    if len(body)>1000000:
                        raise FigmaError('connection_response_too_large',502)
                    return body.decode()[:24000] if plain else json.loads(body)
        except (ClientError,TimeoutError,ValueError,UnicodeError):
            raise FigmaError('connection_unavailable',503) from None

    async def rpc(self, connection, method, params=None, *, notification=False):
        if method=='initialize':return {'protocolVersion':'2025-03-26'}
        if method=='notifications/initialized':return {}
        if method=='tools/list':return {'tools':list(self.tools.values())}
        if method=='ping':
            if self.provider=='google':await self.api_request(connection,'GET','/v1/userinfo')
            else:await self.graphql(connection,'{shop{name}}')
            return {}
        name=params['name']; data=params['arguments']
        schema=self.tools.get(name,{}).get('inputSchema',{})
        if not schema or set(data)-set(schema['properties']) or set(schema['required'])-set(data):
            raise FigmaError('connection_invalid_arguments')
        result=await self.execute(connection,name,data)
        return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}]}

    async def graphql(self,c,query,variables=None):
        result=await self.api_request(c,'POST','/admin/api/2026-10/graphql.json',data={'query':query,'variables':variables or {}})
        if result.get('errors'):
            raise FigmaError('connection_operation_failed',400)
        return result.get('data',{})

    async def execute(self,c,name,a):
        api=self.api_request
        if name=='drive_search':
            query=text(a,'query',500).replace('\\','\\\\').replace("'","\\'")
            return await api(c,'GET','/drive/v3/files',params={'q':"trashed = false and name contains '"+query+"'",'pageSize':30,'fields':'files(id,name,mimeType,webViewLink),nextPageToken'})
        if name=='drive_read':
            file=identifier(a,'file_id');path='/drive/v3/files/'+file
            meta=await api(c,'GET',path,params={'fields':'id,name,mimeType'})
            mime=meta.get('mimeType','')
            if mime=='application/vnd.google-apps.document':
                content=await api(c,'GET',path+'/export',params={'mimeType':'text/plain'},plain=True)
            elif mime.startswith('text/') or mime in {'application/json','application/xml'}:
                content=await api(c,'GET',path,params={'alt':'media'},plain=True)
            else:raise FigmaError('connection_file_type_unsupported',400)
            return {'file':meta,'text':content}
        if name=='sheets_create':
            values=rows(a)
            result=await api(c,'POST','/v4/spreadsheets',data={'properties':{'title':text(a,'title',200)},'sheets':[{'properties':{'title':'Лист 1'}}]})
            sid=identifier({'id':result['spreadsheetId']},'id')
            await api(c,'PUT','/v4/spreadsheets/'+sid+'/values/A1',params={'valueInputOption':'USER_ENTERED'},data={'values':values})
            return {'spreadsheet_id':sid,'url':result.get('spreadsheetUrl')}
        if name in {'sheets_read','sheets_write'}:
            sid=identifier(a,'spreadsheet_id');target=text(a,'range',200)
            path='/v4/spreadsheets/'+sid+'/values/'+quote(target,safe='')
            if name=='sheets_read':return await api(c,'GET',path)
            return await api(c,'PUT',path,params={'valueInputOption':'USER_ENTERED'},data={'values':rows(a)})
        if name=='docs_create':
            content=text(a,'text');result=await api(c,'POST','/v1/documents',data={'title':text(a,'title',200)})
            did=identifier({'id':result['documentId']},'id')
            await api(c,'POST','/v1/documents/'+did+':batchUpdate',data={'requests':[{'insertText':{'location':{'index':1},'text':content}}]})
            return {'document_id':did,'url':'https://docs.google.com/document/d/'+did+'/edit'}
        if name=='gmail_search':return await api(c,'GET','/gmail/v1/users/me/messages',params={'q':text(a,'query',500),'maxResults':30})
        if name=='gmail_read':
            result=await api(c,'GET','/gmail/v1/users/me/messages/'+identifier(a,'message_id'),params={'format':'full'})
            def body(part):
                value=''
                if part.get('mimeType')=='text/plain':
                    raw=part.get('body',{}).get('data','');value=base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)).decode(errors='replace')[:20000]
                return value+''.join(body(child) for child in part.get('parts',[])[:20])
            payload=result.get('payload',{})
            headers={item['name']:item['value'] for item in payload.get('headers',[]) if item.get('name','').lower() in {'from','to','subject','date'}}
            return {'id':result['id'],'headers':headers,'text':body(payload)[:20000] or result.get('snippet','')}
        if name=='gmail_draft':
            recipient=text(a,'to',320)
            if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',recipient):raise FigmaError('connection_invalid_arguments')
            subject=text(a,'subject',300)
            if '\r' in subject or '\n' in subject:raise FigmaError('connection_invalid_arguments')
            message=EmailMessage();message['To']=recipient;message['Subject']=subject;message.set_content(text(a,'body'))
            result=await api(c,'POST','/gmail/v1/users/me/drafts',data={'message':{'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()}})
            return {'draft_id':result.get('id'),'sent':False}
        if name=='calendar_events_list':
            first,last=calendar_window(a)
            amount=a.get('max_results',30)
            if isinstance(amount,bool) or not isinstance(amount,int) or not 1<=amount<=50:
                raise FigmaError('connection_invalid_arguments')
            result=await api(c,'GET','/calendar/v3/calendars/primary/events',params={
                'timeMin':first,'timeMax':last,'maxResults':amount,
                'singleEvents':'true','orderBy':'startTime',
                'fields':'items(id,summary,start,end,location,status,htmlLink),timeZone,nextPageToken'})
            values=result.get('items',[])
            if not isinstance(values,list):raise FigmaError('connection_unavailable',503)
            return {'events':[calendar_event_view(event) for event in values[:50] if isinstance(event,dict)],
                    'time_zone':str(result.get('timeZone') or '')[:100],
                    'more':bool(result.get('nextPageToken'))}
        if name=='calendar_freebusy':
            first,last=calendar_window(a,default=False)
            zone=calendar_zone(a)
            body={'timeMin':first,'timeMax':last,'items':[{'id':'primary'}]}
            if zone:body['timeZone']=zone
            result=await api(c,'POST','/calendar/v3/freeBusy',data=body)
            calendars=result.get('calendars',{})
            if not isinstance(calendars,dict) or len(calendars)!=1:
                raise FigmaError('connection_calendar_availability_unavailable',502)
            primary=next(iter(calendars.values()))
            if not isinstance(primary,dict) or primary.get('errors'):
                raise FigmaError('connection_calendar_availability_unavailable',502)
            windows=primary.get('busy',[])
            if not isinstance(windows,list):raise FigmaError('connection_unavailable',503)
            return {'busy':[{'start':w.get('start'),'end':w.get('end')} for w in windows[:100] if isinstance(w,dict)],
                    'time_min':first,'time_max':last,'time_zone':zone or str(result.get('timeZone') or '')[:100],
                    'truncated':len(windows)>100}
        if name=='calendar_event_create':
            body=calendar_create_payload(a)
            result=await api(c,'POST','/calendar/v3/calendars/primary/events',
                params={'sendUpdates':'none'},data=body)
            if not isinstance(result.get('id'),str) or not result['id']:
                raise FigmaError('connection_calendar_creation_unconfirmed',502)
            return {'event':calendar_event_view(result),'created':True,
                    'invitations_sent':False}
        if name=='shopify_products':return await self.graphql(c,'{products(first:30){nodes{id title description totalInventory status}}}')
        if name=='shopify_orders':return await self.graphql(c,'{orders(first:30,sortKey:CREATED_AT,reverse:true){nodes{id name createdAt displayFinancialStatus currentTotalPriceSet{shopMoney{amount currencyCode}}}}}')
        if name=='shopify_product_update':
            pid=text(a,'product_id',100)
            if not re.fullmatch(r'gid://shopify/Product/[0-9]+',pid) or not ({'title','description_html'} & set(a)):
                raise FigmaError('connection_invalid_arguments')
            update={'id':pid}
            if 'title' in a:update['title']=text(a,'title',200)
            if 'description_html' in a:update['descriptionHtml']=text(a,'description_html')
            result=await self.graphql(c,'mutation($product:ProductUpdateInput!){productUpdate(product:$product){product{id title} userErrors{field message}}}',{'product':update})
            if result.get('productUpdate',{}).get('userErrors'):raise FigmaError('connection_operation_failed')
            return result
        raise FigmaError('connection_unknown_tool')


class WorkspaceOAuth(FigmaOAuth):
    def __init__(self,bridge,origin,provider,upstream):
        super().__init__(bridge,origin)
        self.provider=provider;self.upstream=upstream
        self.settings={"issuer":"https://accounts.google.com" if provider=="google" else "https://accounts.shopify.com"}
        self.return_callback=(origin.rstrip('/')+'/web-api/v1/velia/oauth/google/callback' if provider=='google'
                              else origin.rstrip('/')+'/web-api/v1/connections/'+provider+'/callback')
        self.access=ContextVar('workspace_access',default=None)
    @contextmanager
    def authentication(self,access):
        token=self.access.set(access)
        try:yield
        finally:self.access.reset(token)
    def google_config(self):
        """Google client credentials live exclusively in the Velia gateway, not DeepAlpha."""
        client_id=os.getenv('VELIA_GOOGLE_OAUTH_CLIENT_ID','').strip()
        secret=os.getenv('VELIA_GOOGLE_OAUTH_CLIENT_SECRET','')
        target=urlsplit(self.return_callback)
        valid=bool(client_id and secret and target.scheme=='https' and target.hostname
                   and not target.username and not target.password and not target.fragment
                   and target.path=='/web-api/v1/velia/oauth/google/callback')
        return {'ok':True,'configured':valid,'client_id':client_id if valid else '',
                'redirect_uri':self.return_callback if valid else '', 'scope':GOOGLE_OAUTH_SCOPES}

    async def _google_token(self,body,*,refresh=False):
        """Exchange/renew tokens only over Google's fixed HTTPS endpoint."""
        try:
            async with ClientSession(timeout=ClientTimeout(total=20),trust_env=False) as client:
                async with client.post('https://oauth2.googleapis.com/token',data=body,allow_redirects=False) as response:
                    if response.status!=200:
                        if refresh and response.status in (400,401):
                            # Only invalid_grant proves the user's refresh credential was revoked.
                            detail=await response.content.read(4097)
                            if len(detail)<=4096:
                                try:
                                    failure=json.loads(detail)
                                    if isinstance(failure,dict) and failure.get('error')=='invalid_grant':
                                        raise FigmaError('connection_authorization_revoked',409)
                                except (ValueError,UnicodeError):
                                    pass
                        raise FigmaError('connection_authorization_failed' if response.status<500 else 'connection_authorization_unavailable',
                                         400 if response.status<500 else 503)
                    payload=await response.content.read(64001)
        except (ClientError, OSError, asyncio.TimeoutError):
            raise FigmaError('connection_authorization_unavailable',503) from None
        if len(payload)>64000:raise FigmaError('connection_authorization_failed',400)
        try:result=json.loads(payload)
        except (UnicodeError, ValueError):raise FigmaError('connection_authorization_failed',400) from None
        if not isinstance(result,dict):raise FigmaError('connection_authorization_failed',400)
        token=result.get('access_token')
        lifetime=result.get('expires_in')
        if (not isinstance(token,str) or not 10<=len(token)<=4096 or any(ord(char)<33 or ord(char)>126 for char in token)
                or isinstance(lifetime,bool) or not isinstance(lifetime,(int,float)) or not math.isfinite(lifetime)
                or not 60<=lifetime<=90000):
            raise FigmaError('connection_authorization_failed',400)
        new_refresh=result.get('refresh_token')
        if new_refresh is not None and (
                not isinstance(new_refresh,str) or not 10<=len(new_refresh)<=4096
                or any(ord(char)<33 or ord(char)>126 for char in new_refresh)):
            raise FigmaError('connection_authorization_failed',400)
        # Never leak tokens to the browser; the OAuth completion stores them server-side.
        return {'ok':True,'access_token':token,'refresh_token':new_refresh,'expires_in':lifetime}

    async def google_exchange(self,data):
        """Exchange a session-bound PKCE code directly from the Velia gateway."""
        settings=self.google_config()
        if not settings['configured']:raise FigmaError('connection_app_not_configured',503)
        code=data.get('code') if isinstance(data,dict) else None
        verifier=data.get('code_verifier') if isinstance(data,dict) else None
        if not isinstance(code,str) or not 1<=len(code)<=2000 or not isinstance(verifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',verifier):
            raise FigmaError('connection_invalid_callback')
        body={'grant_type':'authorization_code','code':code,'code_verifier':verifier,
              'client_id':settings['client_id'],'client_secret':os.environ['VELIA_GOOGLE_OAUTH_CLIENT_SECRET'],
              'redirect_uri':settings['redirect_uri']}
        return await self._google_token(body)

    async def google_refresh(self,refresh_token):
        """Refresh silently after a cold start; invalid grants require reconnect."""
        settings=self.google_config()
        if not settings['configured']:raise FigmaError('connection_app_not_configured',503)
        if not isinstance(refresh_token,str) or not 10<=len(refresh_token)<=4096:
            raise FigmaError('connection_authorization_revoked',409)
        body={'grant_type':'refresh_token','refresh_token':refresh_token,
              'client_id':settings['client_id'],'client_secret':os.environ['VELIA_GOOGLE_OAUTH_CLIENT_SECRET']}
        return await self._google_token(body,refresh=True)

    async def backend(self,action,data=None):
        if self.provider=='google':
            if action=='config':return self.google_config()
            if action=='begin':
                result=self.google_config()
                if not result['configured']:raise FigmaError('connection_app_not_configured',503)
                result['state']=data['state']
                return result
            if action=='token':return await self.google_exchange(data)
            raise FigmaError('connection_authorization_unavailable',503)
        status,result=await self.upstream('GET' if data is None else 'POST','/mobile-api/v1/workspace-oauth/'+self.provider+'/'+action,token=self.access.get(),data=data)
        if status!=200 or not result.get('ok'):raise FigmaError('connection_authorization_unavailable',503)
        return result
    async def start(self,owner,*,options=None):
        self.prune()
        if owner in self.pending and self.pending[owner]['started']>time.monotonic()-10 or owner not in self.pending and len(self.pending)>=256:
            raise FigmaError('connection_busy',429)
        shop=(options or {}).get('shop','')
        if self.provider=='shopify' and (not isinstance(shop,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,61}\.myshopify\.com',shop)):
            raise FigmaError('connection_shop_required')
        state=('velia_workspace.' if self.provider=='google' else '')+secrets.token_urlsafe(32)
        flow={'state':state,'verifier':secrets.token_urlsafe(48),'expires':time.monotonic()+600,'started':time.monotonic(),'status':'starting','shop':shop}
        self.pending[owner]=flow
        try:
            settings=await self.backend('begin',{'shop':shop,'state':state})
            if self.pending.get(owner) is not flow:raise FigmaError('connection_authorization_cancelled',409)
            if not settings.get('configured'):raise FigmaError('connection_app_not_configured',503)
            state=settings['state'];flow['state']=state
            flow.update({'status':'pending','client_id':settings['client_id'],'redirect_uri':settings['redirect_uri'] if self.provider=='google' else self.return_callback})
            params={'client_id':flow['client_id'],'redirect_uri':flow['redirect_uri'],'response_type':'code','scope':settings['scope'],'state':state}
            if self.provider=='google':
                params.update({'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(flow['verifier'].encode()).digest()).decode().rstrip('='),'code_challenge_method':'S256','prompt':'consent select_account','access_type':'offline'})
            origin='https://accounts.google.com/o/oauth2/v2/auth' if self.provider=='google' else 'https://'+shop+'/admin/oauth/authorize'
            return {'authorization_url':origin+'?'+urlencode(params)}
        except BaseException:
            if self.pending.get(owner) is flow:self.pending.pop(owner,None)
            raise
    async def complete(self,owner,state,code,*,callback_query=None):
        self.prune();flow=self.pending.get(owner)
        if not flow or flow['status']!='pending' or not isinstance(state,str) or not secrets.compare_digest(flow['state'],state) or not isinstance(code,str) or not 1<=len(code)<=2000:
            raise FigmaError('connection_invalid_callback')
        flow['status']='exchanging'
        try:
            pairs=callback_query or []
            if len({key for key,value in pairs})!=len(pairs):raise FigmaError('connection_invalid_callback')
            result=await self.backend('token',{'code':code,'code_verifier':flow['verifier'],'shop':flow['shop'],'state':state,'callback_query':dict(pairs)})
            if self.pending.get(owner) is not flow:raise FigmaError('connection_authorization_cancelled',409)
            lifetime=result.get('expires_in')
            if isinstance(lifetime,bool) or not isinstance(lifetime,(int,float)) or not math.isfinite(lifetime) or lifetime<60:raise FigmaError('connection_invalid_callback')
            paired=await self.bridge.connect(owner,result.get('access_token'),oauth_expires=time.time()+min(lifetime,3600 if self.provider=='google' else 86400),
                **({'refresh_token':result.get('refresh_token')} if self.provider=='google' else {}))
            connection=self.bridge.connections.get(owner)
            if self.pending.get(owner) is not flow:
                if connection and connection['id']==paired['connection_id']:self.bridge.connections.pop(owner,None)
                raise FigmaError('connection_authorization_cancelled',409)
            connection['shop']=flow['shop']
            return paired
        finally:
            if self.pending.get(owner) is flow:self.pending.pop(owner,None)
