"""Owner-authenticated work dashboard, durable jobs and Flash role orchestration."""
import asyncio
import os
from collections import deque
import time
from aiohttp import web
from desktop.work_store import WorkStore, WorkError, ROLES
from desktop.work_runtime import FlashRoles, run_job
from velia_desktop_routes import AuthenticationUnavailable

CONNECTORS=[{'id':id,'name':name,'connected':False} for id,name in [('upwork','Upwork'),('laborx','LaborX'),('direct','Прямые заказы')]]

def setup_work_routes(app,*,session_for,same_origin,json_response,upstream,upstream_stream,authenticate,allowed,handlers,
                      web_search=None,store=None,generate=None):
    if store is None and os.getenv('VELIA_WORK_ENABLED')=='true':
        dsn=os.getenv('VELIA_WORK_DATABASE_URL') or os.getenv('VELIA_WEB_GUEST_DATABASE_URL')
        if dsn:store=WorkStore(dsn)
    tasks={};search_rates={};slots=asyncio.Semaphore(4)
    generate=generate or FlashRoles(upstream=upstream,upstream_stream=upstream_stream,authenticate=authenticate,allowed=allowed,handlers=handlers)
    async def lifecycle(application):
        if store:await asyncio.to_thread(store.initialize)
        yield
        for task in list(tasks.values()):task.cancel()
        await asyncio.gather(*list(tasks.values()),return_exceptions=True)
    app.cleanup_ctx.append(lifecycle)

    @web.middleware
    async def boundary(request,handler):
        if not request.path.startswith('/web-api/v1/work/'):
            return await handler(request)
        if request.method!='GET' and not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        try:
            session=await session_for(request)
            if not session:return json_response({'ok':False,'error':'unauthorized'},401)
            request[WORK_SESSION]=session
            if request.path.endswith('/status'):return await handler(request)
            if store is None:raise WorkError('work_not_configured',503)
            if request.method!='GET':
                if request.content_length and request.content_length>16000:raise WorkError('request_too_large',413)
                request[WORK_BODY]=await request.json()
                if not isinstance(request[WORK_BODY],dict):raise WorkError('invalid_request')
            return await handler(request)
        except WorkError as exc:return json_response({'ok':False,'error':exc.code},exc.status)
        except (ValueError,TypeError,UnicodeDecodeError):return json_response({'ok':False,'error':'invalid_request'},400)
        except (AuthenticationUnavailable,OSError,TimeoutError):return json_response({'ok':False,'error':'work_service_unavailable'},503)
    app.middlewares.append(boundary)
    async def status(request):
        return json_response({'ok':True,'available':store is not None,'roles':ROLES,'connectors':CONNECTORS,
                             'wallet':{'connected':False},'search_available':bool(web_search and web_search.available),
                             'execution_mode':'text_drafts','model':'velia-flash'})
    async def workspace(request):
        doc=await asyncio.to_thread(store.workspace,request[WORK_SESSION].user_id)
        for job in doc['jobs']:
            job.pop('outputs',None);job.pop('brief',None)
        return json_response({'ok':True,**doc})
    async def policy(request):
        return json_response({'ok':True,**await asyncio.to_thread(store.set_mandate,request[WORK_SESSION].user_id,request[WORK_BODY])})
    async def create(request):
        id=await asyncio.to_thread(store.create_job,request[WORK_SESSION].user_id,request[WORK_BODY])
        return json_response({'ok':True,'id':id},201)
    async def detail(request):
        doc=await asyncio.to_thread(store.workspace,request[WORK_SESSION].user_id)
        return json_response({'ok':True,'job':store.find(doc,request.match_info['job_id'])})
    async def start(request):
        if request[WORK_BODY]:raise WorkError('invalid_request')
        id=request.match_info['job_id'];session=request[WORK_SESSION]
        if slots.locked():raise WorkError('work_busy',429)
        await slots.acquire()
        try:
            job=await asyncio.to_thread(store.claim,session.user_id,id)
            key=(session.user_id,id)
            task=asyncio.create_task(run_job(store,session.user_id,job,session,generate));tasks[key]=task
            def finished(done):
                if tasks.get(key) is done:tasks.pop(key,None)
                slots.release()
            task.add_done_callback(finished)
        except BaseException:
            slots.release()
            raise
        return json_response({'ok':True,'id':id,'status':'running'},202)
    async def cancel(request):
        if request[WORK_BODY]:raise WorkError('invalid_request')
        key=(request[WORK_SESSION].user_id,request.match_info['job_id'])
        result=await asyncio.to_thread(store.cancel,*key)
        if key in tasks:tasks[key].cancel()
        return json_response({'ok':True,**result})
    async def payout(request):
        id=await asyncio.to_thread(store.payout,request[WORK_SESSION].user_id,request[WORK_BODY])
        return json_response({'ok':True,'id':id,'status':'blocked_wallet_unconnected'},201)
    async def discover(request):
        data=request[WORK_BODY]
        if set(data)!={'query'} or not isinstance(data['query'],str) or not 2<=len(data['query'].strip())<=300:raise WorkError('invalid_query')
        if not web_search or not web_search.available:raise WorkError('search_unavailable',503)
        user=request[WORK_SESSION].user_id;now=time.monotonic()
        for key in list(search_rates):
            if not search_rates[key] or search_rates[key][-1]<now-60:del search_rates[key]
        entries=search_rates.setdefault(user,deque())
        while entries and entries[0]<now-60:entries.popleft()
        if len(entries)>=5:raise WorkError('search_rate_limit',429)
        entries.append(now)
        result=await web_search.search(data['query'])
        return json_response({'ok':True,'results':result['results'],'retrieved_at':result.get('retrieved_at'),'verified_jobs':False})
    app.router.add_get('/web-api/v1/work/status',status)
    app.router.add_get('/web-api/v1/work/workspace',workspace)
    app.router.add_put('/web-api/v1/work/mandate',policy)
    app.router.add_post('/web-api/v1/work/jobs',create)
    app.router.add_get('/web-api/v1/work/jobs/{job_id}',detail)
    app.router.add_post('/web-api/v1/work/jobs/{job_id}/run',start)
    app.router.add_post('/web-api/v1/work/jobs/{job_id}/cancel',cancel)
    app.router.add_post('/web-api/v1/work/payouts',payout)
    app.router.add_post('/web-api/v1/work/discover',discover)

WORK_SESSION=web.RequestKey('velia_work_session',object)
WORK_BODY=web.RequestKey('velia_work_body',dict)
