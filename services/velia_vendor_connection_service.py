"""Bounded, read-only vendor adapters. OAuth apps are disabled until configured.

Provider URLs and GraphQL queries are owned by this module, never by a model.
Tenant names and OAuth1 credentials are encrypted with the account binding.
"""
import base64
from datetime import timedelta, datetime
from functools import partial
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode, quote, parse_qsl
import requests
from services import velia_cloud_connection_service as cloud
from services import velia_notion_connection_service as notion
from services import velia_agent_connector_crypto_service as crypto

ConnectionError = notion.ConnectionError
# authorize endpoint, token endpoint, scopes, operations, token encoding, PKCE
SPECS = {
 'todoist':('https://app.todoist.com/oauth/authorize','https://api.todoist.com/oauth/access_token','data:read',('projects','tasks'),'form',True),
 'dropbox':('https://www.dropbox.com/oauth2/authorize','https://api.dropboxapi.com/oauth2/token','files.metadata.read',('files',),'form',True),
 'trello':('https://auth.atlassian.com/authorize','https://auth.atlassian.com/oauth/token','read:member:trello read:board:trello offline_access',('boards',),'json',True),
 'slack':('https://slack.com/oauth/v2/authorize','https://slack.com/api/oauth.v2.access','channels:read,channels:history',('channels','history'),'form',False),
 'hubspot':('https://app.hubspot.com/oauth/authorize','https://api.hubapi.com/oauth/v1/token','crm.objects.contacts.read',('contacts',),'form',False),
 'canva':('https://www.canva.com/api/oauth/authorize','https://api.canva.com/rest/v1/oauth/token','design:meta:read',('designs',),'basic',True),
 'zotero':('https://www.zotero.org/oauth/authorize','https://www.zotero.org/oauth/access','',('items',),'oauth1',False),
 'zoom':('https://zoom.us/oauth/authorize','https://zoom.us/oauth/token','meeting:read:list_meetings',('meetings',),'basic',False),
 'figma':('https://www.figma.com/oauth','https://api.figma.com/v1/oauth/token','file_metadata:read file_comments:read',('file','comments'),'basic',True),
 'asana':('https://app.asana.com/-/oauth_authorize','https://app.asana.com/-/oauth_token','workspaces:read projects:read tasks:read',('workspaces','projects','tasks'),'form',True),
 'clickup':('https://app.clickup.com/api','https://api.clickup.com/api/v2/oauth/token','',('workspaces','tasks'),'json',False),
 'monday':('https://auth.monday.com/oauth2/authorize','https://auth.monday.com/oauth_ms/oauth/token','boards:read',('boards',),'json',True),
 'jira':('https://auth.atlassian.com/authorize','https://auth.atlassian.com/oauth/token','read:jira-work offline_access',('sites','projects','issues'),'json',False),
 'shopify':('/admin/oauth/authorize','/admin/oauth/access_token','read_products',('products',),'json',False),
 'wordpress':('https://public-api.wordpress.com/oauth2/authorize','https://public-api.wordpress.com/oauth2/token','sites posts',('sites','posts'),'form',False),
 'zendesk':('/oauth/authorizations','/oauth/tokens','tickets:read',('tickets',),'json',True),
}
PERPETUAL = {'clickup','wordpress','zotero','shopify','slack'}
TENANTS = {'shopify':'myshopify.com','zendesk':'zendesk.com'}


def credentials(connector):
    if connector not in SPECS:raise ConnectionError('unsupported_connection',404)
    prefix='VELIA_'+connector.upper()+'_OAUTH_'
    return os.getenv(prefix+'CLIENT_ID',''),os.getenv(prefix+'CLIENT_SECRET','')


def configured(connector):
    return bool(os.getenv('VELIA_VENDOR_CONNECTIONS_ENABLED','').lower() in ('1','true')
                and all(credentials(connector)) and cloud.origin() and crypto.configured())


def require_ready(connector):
    if not configured(connector):raise ConnectionError('connection_not_configured',503)


def callback_url(connector):
    if connector not in SPECS or not cloud.origin():raise ConnectionError('connection_not_configured',503)
    return cloud.origin()+'/web-api/v1/connectors/'+connector+'/callback'


def context_for(connector,options):
    if not isinstance(options,dict):raise ConnectionError('invalid_request')
    if set(options)-{'tenant'}:raise ConnectionError('invalid_request')
    if connector not in TENANTS:
        if options:raise ConnectionError('invalid_request')
        return {}
    tenant=options.get('tenant','')
    if not isinstance(tenant,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,61}[a-z0-9]|[a-z]',tenant):
        raise ConnectionError('invalid_tenant')
    return {'tenant':tenant}


def endpoint(connector,index,context):
    value=SPECS[connector][index]
    if connector in TENANTS:
        tenant=context_for(connector,{'tenant':context.get('tenant')})['tenant']
        return 'https://'+tenant+'.'+TENANTS[connector]+value
    return value


def ensure_tables():
    with notion.database() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS velia_vendor_oauth_states (
          state_hash TEXT PRIMARY KEY,user_id BIGINT NOT NULL,connector TEXT NOT NULL,
          binding_ciphertext TEXT NOT NULL,expires_at TIMESTAMP NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS velia_vendor_connections (
          user_id BIGINT NOT NULL,connector TEXT NOT NULL,tokens_ciphertext TEXT NOT NULL,
          expires_at TIMESTAMP,updated_at TIMESTAMP NOT NULL DEFAULT NOW(),PRIMARY KEY(user_id,connector))''')


def save_state(user_id,connector,state,binding):
    value={**binding,'user_id':int(user_id),'connector':connector}
    with notion.database() as c:
        c.execute('DELETE FROM velia_vendor_oauth_states WHERE expires_at<=%s',(cloud.now(),))
        c.execute('INSERT INTO velia_vendor_oauth_states VALUES (%s,%s,%s,%s,%s)',
            (hashlib.sha256(state.encode()).hexdigest(),int(user_id),connector,crypto.encrypt_secret(json.dumps(value)),cloud.now()+timedelta(minutes=10)))


def consume_state(user_id,connector,state):
    if not isinstance(state,str) or not 32<=len(state)<=128:raise ConnectionError('invalid_oauth_state')
    with notion.database() as c:
        c.execute('''DELETE FROM velia_vendor_oauth_states WHERE state_hash=%s AND user_id=%s
          AND connector=%s AND expires_at>%s RETURNING binding_ciphertext''',
          (hashlib.sha256(state.encode()).hexdigest(),int(user_id),connector,cloud.now()))
        row=c.fetchone()
        if not row:raise ConnectionError('invalid_oauth_state')
        data=json.loads(crypto.decrypt_secret(row['binding_ciphertext'] if isinstance(row,dict) else row[0]))
        if data.get('user_id')!=int(user_id) or data.get('connector')!=connector:raise ConnectionError('invalid_oauth_state')
        return data


def oauth1_signature(method,url,params,consumer_secret,token_secret=''):
    escape=lambda s:quote(str(s),safe='~')
    pairs=sorted((escape(k),escape(v)) for k,v in params.items() if k!='oauth_signature')
    normalized='&'.join(k+'='+v for k,v in pairs)
    message='&'.join(escape(v) for v in (method.upper(),url,normalized))
    key=escape(consumer_secret)+'&'+escape(token_secret)
    return base64.b64encode(hmac.new(key.encode(),message.encode(),hashlib.sha1).digest()).decode()


def zotero_exchange(url,extra,secret=''):
    key,consumer_secret=credentials('zotero')
    params={'oauth_consumer_key':key,'oauth_nonce':secrets.token_hex(16),'oauth_timestamp':str(int(time.time())),
            'oauth_signature_method':'HMAC-SHA1','oauth_version':'1.0',**extra}
    params['oauth_signature']=oauth1_signature('POST',url,params,consumer_secret,secret)
    try:
        r=requests.post(url,data=params,timeout=(5,25),allow_redirects=False)
        if r.status_code!=200:raise ConnectionError('authorization_failed',409)
        result=dict(parse_qsl(r.text))
    except requests.RequestException:raise ConnectionError('provider_unavailable',502) from None
    if not result.get('oauth_token') or not result.get('oauth_token_secret'):raise ConnectionError('invalid_provider_response',502)
    return result


def create_authorization_url(user_id,connector,options=None):
    require_ready(connector);ensure_tables()
    context=context_for(connector,options or {});state=secrets.token_urlsafe(32)
    verifier=secrets.token_urlsafe(48);client_id,_=credentials(connector)
    binding={'context':context,'verifier':verifier}
    query={'client_id':client_id,'redirect_uri':callback_url(connector),'response_type':'code','state':state}
    scope=SPECS[connector][2]
    if scope:query['scope']=scope
    if SPECS[connector][5]:
        query.update(code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),
                     code_challenge_method='s256' if connector=='canva' else 'S256')
    if connector=='dropbox':query['token_access_type']='offline'
    if connector in ('jira','trello'):query.update(audience='api.atlassian.com',prompt='consent')
    if connector=='zotero':
        result=zotero_exchange('https://www.zotero.org/oauth/request',{'oauth_callback':callback_url(connector)+'?'+urlencode({'state':state})})
        if result.get('oauth_callback_confirmed')!='true':raise ConnectionError('invalid_provider_response',502)
        binding.update(request_token=result['oauth_token'],request_secret=result['oauth_token_secret'])
        query={'oauth_token':result['oauth_token'],'name':'VELIA','library_access':'1','notes_access':'0','write_access':'0','all_groups':'none'}
    save_state(user_id,connector,state,binding)
    return {'url':endpoint(connector,0,context)+'?'+urlencode(query),'expires_in':600}


def token_request(connector,data,context):
    client_id,client_secret=credentials(connector);encoding=SPECS[connector][4]
    payload={**data,'client_id':client_id,'client_secret':client_secret}
    if connector=='zendesk':payload.update(scope='tickets:read',expires_in=86400,refresh_token_expires_in=604800)
    url=endpoint(connector,1,context);kwargs={}
    if encoding=='basic':
        kwargs['auth']=(client_id,client_secret);payload=dict(data)
        if connector=='figma' and data.get('grant_type')=='refresh_token':
            url='https://api.figma.com/v1/oauth/refresh';payload={'refresh_token':data['refresh_token']}
    kwargs['json' if encoding=='json' else 'data']=payload
    try:
        r=requests.post(url,timeout=(5,25),allow_redirects=False,**kwargs)
        if r.status_code!=200:raise ConnectionError('authorization_failed',409)
        result=r.json()
    except (requests.RequestException,ValueError):raise ConnectionError('provider_unavailable',502) from None
    if not isinstance(result,dict) or result.get('ok') is False or not isinstance(result.get('access_token'),str) or not result['access_token']:
        raise ConnectionError('invalid_provider_response',502)
    granted=result.get('scope',result.get('scopes'))
    if isinstance(granted,str):granted=re.split(r'[ ,]+',granted)
    if isinstance(granted,list) and not set(re.split(r'[ ,]+',SPECS[connector][2]))-{''}<=set(granted):
        raise ConnectionError('required_permissions_not_granted',409)
    return result


def token_data(user_id,connector,result,context,previous_refresh=None):
    refresh=result.get('refresh_token') or previous_refresh;seconds=result.get('expires_in');expiry=None
    if connector=='monday' and seconds is None:
        try:
            claims=json.loads(base64.urlsafe_b64decode(result['access_token'].split('.')[1]+'==='))
            seconds=int(claims['exp'])-int(time.time())
        except (ValueError,KeyError,IndexError,TypeError):raise ConnectionError('invalid_provider_response',502) from None
    if seconds is not None:
        try:seconds=int(seconds)
        except (ValueError,TypeError):raise ConnectionError('invalid_provider_response',502) from None
        if not (1<=seconds<=366*86400 or (connector=='todoist' and seconds==315360000)):raise ConnectionError('invalid_provider_response',502)
        expiry=cloud.now()+timedelta(seconds=seconds)
    elif connector not in PERPETUAL:raise ConnectionError('token_expiry_missing',409)
    if refresh is not None and (not isinstance(refresh,str) or not refresh):raise ConnectionError('invalid_provider_response',502)
    value={'user_id':int(user_id),'connector':connector,'access_token':result['access_token'],'refresh_token':refresh,'context':context}
    return crypto.encrypt_secret(json.dumps(value)),expiry


def verify_shopify(query,context):
    if not isinstance(query,dict) or any(not isinstance(v,str) for v in query.values()):raise ConnectionError('invalid_callback')
    if query.get('shop')!=context['tenant']+'.myshopify.com':raise ConnectionError('invalid_callback')
    message='&'.join(k+'='+v for k,v in sorted(query.items()) if k!='hmac')
    expected=hmac.new(credentials('shopify')[1].encode(),message.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,query.get('hmac','')):raise ConnectionError('invalid_callback')


def complete_connection(user_id,connector,state,code,provider=None):
    require_ready(connector);ensure_tables()
    if not isinstance(code,str) or not 1<=len(code)<=2048:raise ConnectionError('invalid_oauth_code')
    if provider is not None and (not isinstance(provider,dict) or len(provider)>16 or any(not isinstance(v,str) or len(v)>4096 for v in provider.values())):raise ConnectionError('invalid_callback')
    binding=consume_state(user_id,connector,state);context=binding['context'];provider=provider or {}
    if connector=='zotero':
        if provider.get('oauth_token')!=binding['request_token']:raise ConnectionError('invalid_callback')
        result=zotero_exchange(SPECS[connector][1],{'oauth_token':binding['request_token'],'oauth_verifier':code},binding['request_secret'])
        uid=result.get('userID','')
        if not re.fullmatch(r'[0-9]{1,20}',uid):raise ConnectionError('invalid_provider_response',502)
        context={'user_id':uid};result={'access_token':result['oauth_token_secret']}
    else:
        if connector=='shopify':
            if provider.get('state')!=state or provider.get('code')!=code:raise ConnectionError('invalid_callback')
            verify_shopify(provider,context)
        data={'grant_type':'authorization_code','code':code,'redirect_uri':callback_url(connector)}
        if SPECS[connector][5]:data['code_verifier']=binding['verifier']
        if connector=='clickup':data={k:v for k,v in data.items() if k=='code'}
        result=token_request(connector,data,context)
        if connector=='wordpress':
            site_id=str(result.get('blog_id',''))
            if not re.fullmatch(r'[1-9][0-9]{0,19}',site_id):raise ConnectionError('invalid_provider_response',502)
            context={'site_id':site_id}
    ciphertext,expiry=token_data(user_id,connector,result,context)
    with notion.database() as c:
        c.execute('''INSERT INTO velia_vendor_connections(user_id,connector,tokens_ciphertext,expires_at)
          VALUES (%s,%s,%s,%s) ON CONFLICT(user_id,connector) DO UPDATE SET
          tokens_ciphertext=EXCLUDED.tokens_ciphertext,expires_at=EXCLUDED.expires_at,updated_at=NOW()''',(int(user_id),connector,ciphertext,expiry))
    return {'connected':True}


def connection_status(user_id,connector):
    result={'configured':configured(connector),'connected':False}
    if not result['configured']:return result
    ensure_tables()
    with notion.database() as c:
        c.execute('SELECT user_id FROM velia_vendor_connections WHERE user_id=%s AND connector=%s',(int(user_id),connector))
        result['connected']=bool(c.fetchone())
    return result


def access_credentials(user_id,connector):
    require_ready(connector);ensure_tables()
    with notion.database() as c:
        c.execute('SELECT tokens_ciphertext,expires_at FROM velia_vendor_connections WHERE user_id=%s AND connector=%s FOR UPDATE',(int(user_id),connector))
        row=c.fetchone()
        if not row:raise ConnectionError('not_connected',409)
        encoded=row['tokens_ciphertext'] if isinstance(row,dict) else row[0]
        expiry=row['expires_at'] if isinstance(row,dict) else row[1]
        value=json.loads(crypto.decrypt_secret(encoded))
        if value.get('user_id')!=int(user_id) or value.get('connector')!=connector:raise ConnectionError('invalid_account_credentials',409)
        if expiry is not None and expiry<=cloud.now()+timedelta(seconds=60):
            if not value.get('refresh_token'):raise ConnectionError('reconnect_required',409)
            result=token_request(connector,{'grant_type':'refresh_token','refresh_token':value['refresh_token']},value['context'])
            ciphertext,expiry=token_data(user_id,connector,result,value['context'],value['refresh_token'])
            c.execute('UPDATE velia_vendor_connections SET tokens_ciphertext=%s,expires_at=%s,updated_at=NOW() WHERE user_id=%s AND connector=%s',(ciphertext,expiry,int(user_id),connector))
            value=json.loads(crypto.decrypt_secret(ciphertext))
        return value


def disconnect(user_id,connector):
    credentials(connector);ensure_tables()
    with notion.database() as c:
        c.execute('DELETE FROM velia_vendor_connections WHERE user_id=%s AND connector=%s',(int(user_id),connector))
        c.execute('DELETE FROM velia_vendor_oauth_states WHERE user_id=%s AND connector=%s',(int(user_id),connector))
    return {'connected':False}


def build_read(connector,operation,args,context):
    if not isinstance(args,dict) or connector not in SPECS or operation not in SPECS[connector][3]:raise ConnectionError('invalid_arguments')
    ident=cloud.identifier;method='GET';params={};body=None;headers={}
    if connector=='todoist':url='https://api.todoist.com/api/v1/'+operation;params={'limit':20}
    elif connector=='dropbox':
        method='POST';url='https://api.dropboxapi.com/2/files/list_folder';body={'path':'','limit':20,'recursive':False,'include_deleted':False}
    elif connector=='trello':url='https://api.trello.com/1/members/me/boards';params={'fields':'id,name,desc,closed','filter':'open'}
    elif connector=='slack':
        url='https://slack.com/api/conversations.'+('list' if operation=='channels' else 'history')
        params={'limit':15}
        if operation=='channels':params['types']='public_channel';params['exclude_archived']='true'
        else:params['channel']=ident(args.get('channel_id'))
    elif connector=='hubspot':url='https://api.hubapi.com/crm/v3/objects/contacts';params={'limit':20,'properties':'firstname,lastname,email'}
    elif connector=='canva':url='https://api.canva.com/rest/v1/designs';params={'query':cloud.text(args.get('query',''))}
    elif connector=='zotero':
        uid=context.get('user_id','')
        if not isinstance(uid,str) or not re.fullmatch(r'[0-9]{1,20}',uid):raise ConnectionError('invalid_account_credentials',409)
        url='https://api.zotero.org/users/'+uid+'/items';params={'limit':20,'itemType':'-note || attachment'};headers={'Zotero-API-Version':'3'}
    elif connector=='zoom':url='https://api.zoom.us/v2/users/me/meetings';params={'page_size':20,'type':'scheduled'}
    elif connector=='figma':
        url='https://api.figma.com/v1/files/'+ident(args.get('file_key'))+('/comments' if operation=='comments' else '/meta')
    elif connector=='asana':
        url='https://app.asana.com/api/1.0/'+operation;params={'limit':20}
        if operation=='projects':params['workspace']=ident(args.get('workspace_id'))
        if operation=='tasks':params['project']=ident(args.get('project_id'))
    elif connector=='clickup':
        url='https://api.clickup.com/api/v2/'+('team' if operation=='workspaces' else 'list/'+ident(args.get('list_id'))+'/task');params={'page':0}
    elif connector=='monday':method='POST';url='https://api.monday.com/v2';body={'query':'query { boards(limit:20) { id name state } }'}
    elif connector=='jira':
        if operation=='sites':url='https://api.atlassian.com/oauth/token/accessible-resources'
        else:
            url='https://api.atlassian.com/ex/jira/'+ident(args.get('cloud_id'))+'/rest/api/3/'
            if operation=='projects':url+='project/search';params={'maxResults':20}
            else:url+='search/jql';params={'maxResults':20,'jql':cloud.text(args.get('query','order by updated DESC'),500),'fields':'summary,status,duedate'}
    elif connector=='shopify':
        host=endpoint(connector,0,context).split('/admin/')[0];method='POST';url=host+'/admin/api/2026-10/graphql.json';body={'query':'query { products(first:20) { nodes { id title status } } }'}
    elif connector=='wordpress':
        site_id=context.get('site_id','')
        if not isinstance(site_id,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',site_id):raise ConnectionError('invalid_account_credentials',409)
        if operation=='posts' and args.get('site_id')!=site_id:raise ConnectionError('resource_not_authorized',403)
        url='https://public-api.wordpress.com/rest/v1.1/sites/'+site_id+('/posts/' if operation=='posts' else '');params={'number':20}
    else:url=endpoint(connector,0,context).split('/oauth/')[0]+'/api/v2/tickets.json';params={'page[size]':20}
    return method,url,params,body,headers


def request_read(method,url,params,body,headers):
    try:
        kwargs={'params':params,'headers':headers,'timeout':(5,25),'allow_redirects':False,'stream':True}
        if body is not None:kwargs['json']=body
        with requests.request(method,url,**kwargs) as r:
            if r.status_code!=200:raise ConnectionError('reconnect_required' if r.status_code in (401,403) else 'provider_request_failed',409 if r.status_code in (401,403) else 502)
            chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>2_000_000:raise ConnectionError('provider_response_too_large',413)
                chunks.append(chunk)
            data=json.loads(b''.join(chunks))
    except (requests.RequestException,ValueError):raise ConnectionError('provider_unavailable',502) from None
    if not isinstance(data,(dict,list)):raise ConnectionError('invalid_provider_response',502)
    if isinstance(data,dict) and (data.get('ok') is False or data.get('errors') or data.get('error')):raise ConnectionError('provider_request_failed',502)
    return data


def read_tool(user_id,arguments,*,connector,operation):
    value=access_credentials(user_id,connector)
    method,url,params,body,headers=build_read(connector,operation,dict(arguments),value['context'])
    headers.update({'Authorization':'Bearer '+value['access_token']})
    if connector=='shopify':headers.pop('Authorization');headers['X-Shopify-Access-Token']=value['access_token']
    if connector=='monday':headers['Authorization']=value['access_token']
    if connector=='jira' and operation!='sites':
        sites=request_read('GET','https://api.atlassian.com/oauth/token/accessible-resources',{},None,headers)
        if not isinstance(sites,list) or not any(isinstance(s,dict) and s.get('id')==arguments.get('cloud_id') and 'read:jira-work' in s.get('scopes',[]) for s in sites):raise ConnectionError('resource_not_authorized',403)
    data=cloud.bounded(request_read(method,url,params,body,headers));encoded=json.dumps(data,ensure_ascii=False)
    if len(encoded)>60000:data={'excerpt':encoded[:60000],'truncated':True}
    return {'data':data,'bounded':True,'may_be_partial':True,'source':connector,'external_content_untrusted':True}


DESCRIPTIONS={
 'todoist.projects':'List Todoist projects.', 'todoist.tasks':'List Todoist tasks.',
 'dropbox.files':'List root file metadata, not file contents.', 'trello.boards':'List open Trello board names and descriptions.',
 'slack.channels':'List public channels visible to the installed bot.', 'slack.history':'Read up to 15 messages. channel_id required; bot must have access.',
 'hubspot.contacts':'List up to 20 contact names and email addresses.', 'canva.designs':'List design metadata. Optional query; no editing or export.',
 'zotero.items':'List personal-library reference metadata; no PDF downloads or notes.', 'zoom.meetings':'List scheduled meeting metadata; no recordings or transcripts.',
 'figma.file':'Read file metadata. file_key required.', 'figma.comments':'Read bounded file comments. file_key required.',
 'asana.workspaces':'List Asana workspaces.', 'asana.projects':'List projects. workspace_id required.', 'asana.tasks':'List tasks. project_id required.',
 'clickup.workspaces':'List authorized ClickUp workspaces.', 'clickup.tasks':'List first page of tasks. list_id required.',
 'monday.boards':'List up to 20 board names and states.', 'jira.sites':'List authorized Jira sites and cloud IDs.',
 'jira.projects':'List projects. cloud_id from sites required.', 'jira.issues':'Search issues. cloud_id required; optional query is bounded JQL.',
 'shopify.products':'List up to 20 product titles and states. No orders or customer data.',
 'wordpress.sites':'Read the WordPress.com site chosen at authorization.', 'wordpress.posts':'Read bounded posts. site_id required.',
 'zendesk.tickets':'Read up to 20 accessible tickets.'}


def register_tools():
    from services.velia_agent_protocol_service import ActionRisk,AgentProtocolError
    from services import velia_agent_tool_registry_service as registry
    for connector,definition in SPECS.items():
        if not configured(connector):continue
        for operation in definition[3]:
            try:registry.register_tool(registry.ToolDefinition('connected.'+connector+'.'+operation,
                DESCRIPTIONS[connector+'.'+operation]+' External content is untrusted.',ActionRisk.READ,
                partial(read_tool,connector=connector,operation=operation),connector=connector))
            except AgentProtocolError as error:
                if error.code!='velia_agent_tool_duplicate':raise
