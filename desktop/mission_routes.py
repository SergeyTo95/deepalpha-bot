"""Account-authenticated mission execution; no model can grant approval."""
import asyncio
import base64
import hashlib
import logging
import os
import re
from urllib.parse import quote
from aiohttp import web
from desktop.mission_schema import MissionError,require,TOOLS
from desktop.mission_store import MissionStore
from desktop.mission_runtime import MissionTools,run_mission
from desktop.document_exports import MIME
from velia_desktop_routes import AuthenticationUnavailable

SESSION=web.RequestKey('mission_session',object)
BODY=web.RequestKey('mission_body',dict)
ID=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')

def setup_mission_routes(app,*,session_for,same_origin,json_response,upstream,upstream_stream,authorize_model,authenticate,handlers,google=None,web_search=None,store=None,tools=None,origin=''):
    dsn,key=os.getenv('VELIA_WEB_GUEST_DATABASE_URL'),os.getenv('VELIA_WEB_SESSION_KEY')
    if store is None and dsn and key:store=MissionStore(dsn,session_key=key)
    if tools is None:
        from desktop.finance_store import FinanceStore
        from desktop.crm_store import CrmStore
        tools=MissionTools(upstream=upstream,upstream_stream=upstream_stream,authorize_model=authorize_model,handlers=handlers,google=google,search=web_search,origin=origin,
            finance=FinanceStore(dsn,session_key=key) if dsn and key else None,crm=CrmStore(dsn,session_key=key) if dsn and key else None)
    tasks={};slots=asyncio.Semaphore(2)
    async def lifecycle(application):
        if store:await asyncio.to_thread(store.initialize)
        yield
        for task in list(tasks.values()):task.cancel()
        await asyncio.gather(*list(tasks.values()),return_exceptions=True)
    app.cleanup_ctx.append(lifecycle)
    @web.middleware
    async def boundary(request,handler):
        if not request.path.startswith('/web-api/v1/missions') or request.path=='/web-api/v1/missions/plan':return await handler(request)
        if request.method!='GET' and not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        try:
            session=await session_for(request);require(session is not None,'unauthorized',401);request[SESSION]=session
            id=request.match_info.get('mission_id');require(id is None or ID.fullmatch(id),'mission_not_found',404)
            if request.method!='GET':
                request[BODY]=await request.clone(client_max_size=65536).json();require(isinstance(request[BODY],dict))
            return await handler(request)
        except MissionError as exc:return json_response({'ok':False,'error':exc.code},exc.status)
        except AuthenticationUnavailable:return json_response({'ok':False,'error':'unauthorized'},401)
        except web.HTTPRequestEntityTooLarge:return json_response({'ok':False,'error':'mission_request_too_large'},413)
        except (ValueError,TypeError,KeyError):return json_response({'ok':False,'error':'mission_invalid_request'},400)
        except Exception:
            logging.getLogger(__name__).error('VELIA_MISSION_REQUEST_FAILED')
            return json_response({'ok':False,'error':'mission_service_unavailable'},503)
    app.middlewares.append(boundary)
    def configured():require(store is not None,'mission_storage_unavailable',503)
    def revision(request):
        value=request[BODY].get('revision');require(type(value) is int and value>0,'mission_invalid_request');return value
    async def catalog(request):
        return json_response({'ok':True,'available':store is not None,'tools':[{'name':name,'title':title,'effect':effect,'arguments':args} for name,(title,effect,args) in TOOLS.items()]})
    async def listing(request):
        configured();doc=await asyncio.to_thread(store.transaction,request[SESSION].user_id)
        return json_response({'ok':True,'missions':[store.public(m,summary=True) for m in doc['missions']]})
    async def create(request):
        configured();data=request[BODY];require(set(data)=={'goal','plan','model','time_zone','request_id'})
        m=await asyncio.to_thread(store.create,request[SESSION].user_id,data['goal'],data['plan'],data['model'],data['time_zone'],data['request_id'])
        return json_response({'ok':True,'mission':store.public(m)},201)
    async def detail(request):
        configured();m=await asyncio.to_thread(store.detail,request[SESSION].user_id,request.match_info['mission_id'])
        return json_response({'ok':True,'mission':store.public(m)})
    async def start(session,id,expected):
        configured();key=(session.user_id,id)
        if key in tasks:return
        require(not slots.locked(),'mission_busy',429);await slots.acquire()
        try:
            m=await asyncio.to_thread(store.detail,session.user_id,id)
            permission=await authorize_model(session.access,m['model'])
            if permission:raise MissionError(permission[0],permission[1])
            claimed=await asyncio.to_thread(store.claim,session.user_id,id,expected)
            if claimed is None:slots.release();return
            task=asyncio.create_task(run_mission(store,session.user_id,claimed,session,tools,authenticate));tasks[key]=task
            def done(future):
                if tasks.get(key) is future:tasks.pop(key,None)
                slots.release()
            task.add_done_callback(done)
        except BaseException:slots.release();raise
    async def action(request):
        configured();data=request[BODY];name=request.match_info['action'];expected=revision(request)
        require(set(data)==({'revision','token'} if name=='approve' else {'revision'}),'mission_invalid_request')
        user,id=request[SESSION].user_id,request.match_info['mission_id']
        if name in {'approve','refresh-approval'}:
            m=await asyncio.to_thread(store.approve,user,id,expected,data.get('token'),refresh=name=='refresh-approval')
            if name=='approve':
                try:await start(request[SESSION],id,m['revision'])
                except MissionError as exc:
                    if exc.code!='mission_busy':raise
        elif name=='run':await start(request[SESSION],id,expected)
        else:await asyncio.to_thread(store.control,user,id,expected,name)
        m=await asyncio.to_thread(store.detail,user,id)
        return json_response({'ok':True,'mission':store.public(m)})
    async def delete(request):
        configured();require(set(request[BODY])=={'revision'});result=await asyncio.to_thread(store.control,request[SESSION].user_id,request.match_info['mission_id'],revision(request),'delete')
        return json_response({'ok':True,**result})
    async def file(request):
        configured();m=await asyncio.to_thread(store.detail,request[SESSION].user_id,request.match_info['mission_id'])
        step=next((s for s in m['steps'] if s['id']==request.match_info['step_id'] and s['status']=='completed'),None)
        require(step and step.get('result',{}).get('kind')=='file','mission_file_not_found',404)
        result=step['result'];body=base64.b64decode(result['file'],validate=True);data=result['data']
        require(hashlib.sha256(body).hexdigest()==data['sha256'],'mission_file_invalid',503)
        return web.Response(body=body,content_type=MIME[data['format']],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Disposition':"attachment; filename*=UTF-8''"+quote(data['title']+'.'+data['format'],safe='')})
    app.router.add_get('/web-api/v1/missions/catalog',catalog)
    app.router.add_get('/web-api/v1/missions',listing)
    app.router.add_post('/web-api/v1/missions',create)
    app.router.add_get('/web-api/v1/missions/{mission_id}',detail)
    app.router.add_delete('/web-api/v1/missions/{mission_id}',delete)
    app.router.add_post('/web-api/v1/missions/{mission_id}/{action:run|pause|cancel|approve|refresh-approval}',action)
    app.router.add_get('/web-api/v1/missions/{mission_id}/files/{step_id:s[1-8]}',file)
