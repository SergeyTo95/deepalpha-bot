"""Account-authenticated mission execution; no model can grant approval."""
import asyncio
import base64
import hashlib
import logging
import json
import os
import re
from urllib.parse import quote
from aiohttp import web
from desktop.mission_schema import MissionError,require,TOOLS
from desktop.mission_store import MissionStore
from desktop.mission_runtime import MissionTools,run_mission
from desktop.mission_delivery import MissionDelivery
from desktop.document_exports import MIME
from velia_desktop_routes import AuthenticationUnavailable

SESSION=web.RequestKey('mission_session',object)
BODY=web.RequestKey('mission_body',dict)
ID=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')

def setup_mission_routes(app,*,session_for,same_origin,json_response,upstream,upstream_stream,authorize_model,authenticate,handlers,google=None,web_search=None,store=None,tools=None,origin='',session_identity=None):
    dsn,key=os.getenv('VELIA_WEB_GUEST_DATABASE_URL'),os.getenv('VELIA_WEB_SESSION_KEY')
    if store is None and dsn and key:store=MissionStore(dsn,session_key=key)
    if tools is None:
        from desktop.finance_store import FinanceStore
        from desktop.crm_store import CrmStore
        tools=MissionTools(upstream=upstream,upstream_stream=upstream_stream,authorize_model=authorize_model,handlers=handlers,google=google,search=web_search,origin=origin,
            finance=FinanceStore(dsn,session_key=key) if dsn and key else None,crm=CrmStore(dsn,session_key=key) if dsn and key else None)
    tasks={};slots=asyncio.Semaphore(1);owners={};deliveries={};start_guard=asyncio.Lock()
    delivery=MissionDelivery(store,upstream,origin) if store else None
    def flush(session):
        if not delivery or session.user_id in deliveries:return
        task=asyncio.create_task(delivery.flush(session));deliveries[session.user_id]=task
        def finished(future):
            deliveries.pop(session.user_id,None)
            if not future.cancelled():future.exception()
        task.add_done_callback(finished)
    async def lifecycle(application):
        if store:await asyncio.to_thread(store.initialize)
        yield
        pending=list(tasks.values())+list(deliveries.values())
        for task in pending:task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
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
                account=hashlib.sha256((origin+':'+str(session.user_id)).encode()).hexdigest()[:24]
                require(request[BODY].pop('account',None)==account,'mission_account_changed',403)
            response=await handler(request)
            if isinstance(response,web.Response) and response.content_type=='application/json':
                value=json.loads(response.body);value['account']=hashlib.sha256((origin+':'+str(session.user_id)).encode()).hexdigest()[:24]
                response.body=json.dumps(value,ensure_ascii=False).encode()
            return response
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
        flush(request[SESSION])
        return json_response({'ok':True,'missions':[store.public(m,summary=True) for m in doc['missions']]})
    async def create(request):
        configured();data=request[BODY];require(set(data)=={'goal','plan','model','time_zone','request_id'})
        m=await asyncio.to_thread(store.create,request[SESSION].user_id,data['goal'],data['plan'],data['model'],data['time_zone'],data['request_id'])
        return json_response({'ok':True,'mission':store.public(m)},201)
    async def detail(request):
        configured();m=await asyncio.to_thread(store.detail,request[SESSION].user_id,request.match_info['mission_id'])
        return json_response({'ok':True,'mission':store.public(m)})
    async def worker(session,queued):
        user,id,lease=session.user_id,queued['id'],queued['lease'];lock=owners.setdefault(user,asyncio.Lock());held=[]
        try:
            for gate in (lock,slots):
                while True:
                    try:await asyncio.wait_for(gate.acquire(),30);held.append(gate);break
                    except asyncio.TimeoutError:
                        await asyncio.to_thread(store.checkpoint,user,id,lease,lambda m:m.update(lease_until=store.clock()+180))
            while True:
                try:m=await asyncio.to_thread(store.activate,user,id,lease);lease=m['lease'];break
                except MissionError as exc:
                    if exc.code!='mission_busy':raise
                    await asyncio.sleep(5)
                    await asyncio.to_thread(store.checkpoint,user,id,lease,lambda m:m.update(lease_until=store.clock()+180))
            async def authenticated(_):return await session_identity(session) if session_identity else await authenticate(session.access)
            identity=await authenticated(session.access);require(identity and identity['user_id']==user,'unauthorized',401)
            if m.get('needs_plan'):
                async def save(cid):await asyncio.to_thread(store.checkpoint,user,id,lease,lambda m:m.update(conversation_id=cid))
                result=await asyncio.wait_for(tools.plan(session,m,save),100)
                m=await asyncio.to_thread(store.planned,user,id,lease,result)
                if m['status']!='running':return
            await run_mission(store,user,m,session,tools,authenticated)
        except BaseException as exc:
            def fail(m):m.update(status='paused' if m['status']=='pausing' else 'cancelled' if m['status']=='cancelling' else 'interrupted' if isinstance(exc,asyncio.CancelledError) else 'failed',planning_error=getattr(exc,'code','mission_step_failed'),lease=None,lease_until=0)
            try:await asyncio.to_thread(store.checkpoint,user,id,lease,fail)
            except Exception:pass
            if isinstance(exc,asyncio.CancelledError):raise
        finally:
            for gate in reversed(held):gate.release()
            # The result is already durable before any notification is attempted.
            if not asyncio.current_task().cancelling():flush(session)
    async def start(session,id,expected,*,submitted=False):
        configured();key=(session.user_id,id)
        if key in tasks:return
        require(len(tasks)<64,'mission_busy',429)
        m=await asyncio.to_thread(store.detail,session.user_id,id)
        if submitted and m['status']!='draft':return
        permission=await authorize_model(session.access,m['model'])
        if permission:raise MissionError(permission[0],permission[1])
        async with start_guard:
            if key in tasks:return
            require(len(tasks)<64,'mission_busy',429)
            queued=await asyncio.to_thread(store.enqueue,session.user_id,id,expected,draft_only=submitted)
            if queued['status'] not in {'queued'}:return
            session.background_jobs=getattr(session,'background_jobs',0)+1
            task=asyncio.create_task(worker(session,queued));tasks[key]=task
            def done(future):
                session.background_jobs=max(0,session.background_jobs-1)
                if tasks.get(key) is future:tasks.pop(key,None)
                if not any(k[0]==session.user_id for k in tasks):owners.pop(session.user_id,None)
                if not future.cancelled():future.exception()
            task.add_done_callback(done)
    async def submit(request):
        configured();data=request[BODY]
        require(set(data)=={'goal','model','time_zone','request_id','source','context'},'mission_invalid_request')
        source=data['source'];require(isinstance(source,dict),'mission_invalid_request')
        if source.get('remote'):
            require(isinstance(source.get('chat_id'),str) and ID.fullmatch(source['chat_id']),'mission_invalid_request')
            status,value=await upstream('GET','/mobile-api/v1/conversations/'+source['chat_id']+'/messages?limit=1',token=request[SESSION].access)
            require(status==200 and value.get('ok') is True,'mission_source_unavailable',404)
        m=await asyncio.to_thread(store.create,request[SESSION].user_id,data['goal'],None,data['model'],data['time_zone'],data['request_id'],source=source,context=data['context'])
        if m['status']=='draft':await start(request[SESSION],m['id'],m['revision'],submitted=True)
        m=await asyncio.to_thread(store.detail,request[SESSION].user_id,m['id'])
        return json_response({'ok':True,'mission':store.public(m)},201)
    async def inbox(request):
        configured();return json_response({'ok':True,'notifications':await asyncio.to_thread(store.inbox,request[SESSION].user_id)})
    async def detach_chat(request):
        configured();data=request[BODY];require(set(data)=={'chat_id'} and isinstance(data['chat_id'],str) and ID.fullmatch(data['chat_id']),'mission_invalid_request')
        def detach(doc):
            for m in doc['missions']:
                if (m.get('source') or {}).get('chat_id')==data['chat_id']:
                    m['source']['detached']=True;store.touch(m,store.clock())
        await asyncio.to_thread(store.transaction,request[SESSION].user_id,detach)
        return json_response({'ok':True})
    async def read_notice(request):
        configured();id=request.match_info['notice_id'];require(ID.fullmatch(id) and not request[BODY],'mission_invalid_request')
        await asyncio.to_thread(store.notice,request[SESSION].user_id,id,lambda m,n:n.update(read=True))
        flush(request[SESSION])
        return json_response({'ok':True})
    async def answer(request):
        configured();data=request[BODY];require(set(data)=={'revision','answer','request_id'},'mission_invalid_request')
        m=await asyncio.to_thread(store.answer,request[SESSION].user_id,request.match_info['mission_id'],revision(request),data['answer'],data['request_id'])
        if m['status']=='paused':await start(request[SESSION],m['id'],m['revision'])
        m=await asyncio.to_thread(store.detail,request[SESSION].user_id,m['id'])
        return json_response({'ok':True,'mission':store.public(m)})
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
    app.router.add_get('/web-api/v1/missions/inbox',inbox)
    app.router.add_post('/web-api/v1/missions/inbox/{notice_id}/read',read_notice)
    app.router.add_post('/web-api/v1/missions/submit',submit)
    app.router.add_post('/web-api/v1/missions/detach-chat',detach_chat)
    app.router.add_post('/web-api/v1/missions/{mission_id}/answer',answer)
    app.router.add_get('/web-api/v1/missions/catalog',catalog)
    app.router.add_get('/web-api/v1/missions',listing)
    app.router.add_post('/web-api/v1/missions',create)
    app.router.add_get('/web-api/v1/missions/{mission_id}',detail)
    app.router.add_delete('/web-api/v1/missions/{mission_id}',delete)
    app.router.add_post('/web-api/v1/missions/{mission_id}/{action:run|pause|cancel|approve|refresh-approval}',action)
    app.router.add_get('/web-api/v1/missions/{mission_id}/files/{step_id:s[1-8]}',file)
