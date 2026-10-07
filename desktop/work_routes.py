"""Owner-authenticated work dashboard, durable jobs and Flash role orchestration."""
import asyncio
import os
from collections import deque
import time
import io
import zipfile
import json
from aiohttp import web
from desktop.work_store import WorkStore, WorkError, ROLES, DEFAULT_AUTONOMY
from desktop.work_runtime import FlashRoles, run_job
from desktop.upwork_connector import UpworkConnector
from velia_desktop_routes import AuthenticationUnavailable

CONNECTORS=[{'id':id,'name':name,'connected':False} for id,name in [('upwork','Upwork'),('laborx','LaborX'),('direct','Прямые заказы')]]

def setup_work_routes(app,*,session_for,same_origin,json_response,upstream,upstream_stream,authenticate,allowed,handlers,
                      web_search=None,store=None,generate=None,origin=None,upwork=None):
    if store is None and os.getenv('VELIA_WORK_ENABLED')=='true':
        dsn=os.getenv('VELIA_WORK_DATABASE_URL') or os.getenv('VELIA_WEB_GUEST_DATABASE_URL')
        if dsn:store=WorkStore(dsn)
    if upwork is None and store and origin and os.getenv('VELIA_WEB_SESSION_KEY'):
        upwork=UpworkConnector(store,os.environ['VELIA_WEB_SESSION_KEY'],origin)
    tasks={};search_rates={};slots=asyncio.Semaphore(4);sessions={};scanning=set();connector_slots=asyncio.Semaphore(4)
    generate=generate or FlashRoles(upstream=upstream,upstream_stream=upstream_stream,authenticate=authenticate,allowed=allowed,handlers=handlers)
    async def lifecycle(application):
        if store:await asyncio.to_thread(store.initialize)
        scheduler=asyncio.create_task(schedule()) if store else None
        yield
        if scheduler:
            scheduler.cancel()
            await asyncio.gather(scheduler,return_exceptions=True)
        for task in list(tasks.values()):task.cancel()
        await asyncio.gather(*list(tasks.values()),return_exceptions=True)
    app.cleanup_ctx.append(lifecycle)
    async def callback_headers(request,response):
        if request.path=='/web-api/v1/work/upwork/callback':
            response.headers['Cache-Control']='no-store'
            response.headers['Referrer-Policy']='no-referrer'
    app.on_response_prepare.append(callback_headers)

    @web.middleware
    async def boundary(request,handler):
        if not request.path.startswith('/web-api/v1/work/'):
            return await handler(request)
        if request.method!='GET' and not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        try:
            session=await session_for(request)
            if not session:return json_response({'ok':False,'error':'unauthorized'},401)
            request[WORK_SESSION]=session
            if store:sessions[session.user_id]=session
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
        connectors=[dict(c) for c in CONNECTORS]
        if upwork:
            connection=await upwork.status(request[WORK_SESSION].user_id)
            connectors[0].update(connection)
        return json_response({'ok':True,'available':store is not None,'roles':ROLES,'connectors':connectors,'upwork_available':upwork is not None,
                             'wallet':{'connected':False},'search_available':bool(web_search and web_search.available),
                             'execution_mode':'text_drafts','model':'velia-flash'})
    async def workspace(request):
        doc=await asyncio.to_thread(store.workspace,request[WORK_SESSION].user_id)
        doc['autonomy']=doc.get('autonomy',DEFAULT_AUTONOMY)
        for key in ('scan_token','scan_until'):doc.pop(key,None)
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
        take_search_slot(request[WORK_SESSION].user_id)
        result=await web_search.search(data['query'])
        return json_response({'ok':True,'results':result['results'],'retrieved_at':result.get('retrieved_at'),'verified_jobs':False})
    def take_search_slot(user):
        now=time.monotonic()
        for key in list(search_rates):
            if not search_rates[key] or search_rates[key][-1]<now-60:del search_rates[key]
        entries=search_rates.setdefault(user,deque())
        while entries and entries[0]<now-60:entries.popleft()
        if len(entries)>=5:raise WorkError('search_rate_limit',429)
        entries.append(now)
    async def autonomy(request):
        value=await asyncio.to_thread(store.set_autonomy,request[WORK_SESSION].user_id,request[WORK_BODY])
        return json_response({'ok':True,'autonomy':value})
    async def scan(session):
        user=session.user_id
        if user in scanning or not web_search or not web_search.available or slots.locked():return False
        scanning.add(user)
        await slots.acquire()
        policy=None
        try:
            policy=await asyncio.to_thread(store.claim_scan,user)
            if not policy:return False
            identity=await authenticate(session.access)
            if not identity or identity['user_id']!=user or not allowed(user):
                await asyncio.to_thread(store.finish_scan,user,policy['token'],[],error='authentication_required')
                sessions.pop(user,None);return False
            take_search_slot(user)
            result=await asyncio.wait_for(web_search.search(policy['query']),timeout=45)
            id=await asyncio.to_thread(store.finish_scan,user,policy['token'],result.get('results',[]))
            if id:
                job=await asyncio.to_thread(store.claim,user,id)
                key=(user,id);task=asyncio.create_task(run_job(store,user,job,session,generate));tasks[key]=task
                try:await task
                finally:
                    if tasks.get(key) is task:tasks.pop(key,None)
            return True
        except asyncio.CancelledError:raise
        except Exception:
            if policy:
                try:await asyncio.to_thread(store.finish_scan,user,policy['token'],[],error='autonomy_scan_failed')
                except WorkError:pass
            return False
        finally:
            slots.release();scanning.discard(user)
    async def schedule():
        while True:
            await asyncio.sleep(30)
            # Sessions stay in memory only. After restart the owner signs in again.
            await asyncio.gather(*(scan(session) for session in list(sessions.values())),return_exceptions=True)
    async def scan_now(request):
        if request[WORK_BODY]:raise WorkError('invalid_request')
        session=request[WORK_SESSION]
        if session.user_id in scanning:raise WorkError('work_busy',429)
        task=asyncio.create_task(scan(session));key=(session.user_id,'scan');tasks[key]=task
        task.add_done_callback(lambda done:tasks.pop(key,None) if tasks.get(key) is done else None)
        return json_response({'ok':True,'status':'scheduled'},202)
    async def artifacts(request):
        doc=await asyncio.to_thread(store.workspace,request[WORK_SESSION].user_id)
        job=store.find(doc,request.match_info['job_id'])
        if job['status']!='ready' or not job['outputs'].get('executor'):raise WorkError('artifact_not_ready',409)
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('result.txt',job['outputs']['executor'])
            if job['outputs'].get('proposal'):archive.writestr('proposal-draft.txt',job['outputs']['proposal'])
            archive.writestr('review.json',job['outputs'].get('reviewer','{}'))
            archive.writestr('task.json',json.dumps({k:job[k] for k in ['id','title','brief','source_url']},ensure_ascii=False,indent=2))
            archive.writestr('README.txt','Prepared text artifacts. No code was executed. No marketplace submission or payment is confirmed.')
        return web.Response(body=buffer.getvalue(),content_type='application/zip',headers={
            'Content-Disposition':'attachment; filename="velia-work-'+job['id']+'.zip"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    def require_upwork():
        if not upwork:raise WorkError('upwork_not_configured',503)
    async def connect_upwork(request):
        require_upwork()
        if request[WORK_BODY]:raise WorkError('invalid_request')
        if connector_slots.locked():raise WorkError('upwork_connect_rate_limit',429)
        async with connector_slots:url=await upwork.start(request[WORK_SESSION].user_id)
        return json_response({'ok':True,'authorization_url':url})
    async def callback_upwork(request):
        require_upwork()
        if request.query.get('error'):raise WorkError('upwork_authorization_declined')
        if connector_slots.locked():raise WorkError('upwork_connect_rate_limit',429)
        async with connector_slots:
            await upwork.callback_exchange(request[WORK_SESSION].user_id,request.query.get('state'),request.query.get('code'),request.query.get('iss'))
        return web.Response(status=303,headers={'Location':'/','Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    async def disconnect_upwork(request):
        require_upwork()
        if request[WORK_BODY]:raise WorkError('invalid_request')
        await asyncio.to_thread(upwork.vault.disconnect,request[WORK_SESSION].user_id)
        return json_response({'ok':True,'connected':False})
    async def verify_upwork(request):
        require_upwork()
        if request[WORK_BODY]:raise WorkError('invalid_request')
        if connector_slots.locked():raise WorkError('upwork_connect_rate_limit',429)
        async with connector_slots:result=await upwork.verify(request[WORK_SESSION].user_id)
        return json_response({'ok':True,**result})
    app.router.add_get('/web-api/v1/work/status',status)
    app.router.add_get('/web-api/v1/work/workspace',workspace)
    app.router.add_put('/web-api/v1/work/mandate',policy)
    app.router.add_post('/web-api/v1/work/jobs',create)
    app.router.add_get('/web-api/v1/work/jobs/{job_id}',detail)
    app.router.add_post('/web-api/v1/work/jobs/{job_id}/run',start)
    app.router.add_post('/web-api/v1/work/jobs/{job_id}/cancel',cancel)
    app.router.add_post('/web-api/v1/work/payouts',payout)
    app.router.add_post('/web-api/v1/work/discover',discover)
    app.router.add_put('/web-api/v1/work/autonomy',autonomy)
    app.router.add_post('/web-api/v1/work/scan',scan_now)
    app.router.add_get('/web-api/v1/work/jobs/{job_id}/artifacts',artifacts)
    app.router.add_post('/web-api/v1/work/upwork/connect',connect_upwork)
    app.router.add_get('/web-api/v1/work/upwork/callback',callback_upwork)
    app.router.add_post('/web-api/v1/work/upwork/disconnect',disconnect_upwork)
    app.router.add_post('/web-api/v1/work/upwork/verify',verify_upwork)

WORK_SESSION=web.RequestKey('velia_work_session',object)
WORK_BODY=web.RequestKey('velia_work_body',dict)
