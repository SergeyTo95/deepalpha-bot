import asyncio
from types import SimpleNamespace
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.platform_status import observe_platform, setup_platform_status, DOMAINS

def test_observation_is_bounded_read_only_and_no_private_data():
    async def scenario():
        active=0;peak=0;seen=[]
        replies=[(200,{'ok':True,'access_token':'secret'}),(200,{'ok':True,'enabled':False}),(403,{}),(404,{}),(200,{'ok':False}),(200,{'ok':True})]
        async def upstream(method,path,**kwargs):
            nonlocal active,peak
            i=next(i for i,(_,p) in enumerate(DOMAINS) if path.endswith(p))
            seen.append((method,path,kwargs));active+=1;peak=max(peak,active)
            await asyncio.sleep(.001);active-=1
            return replies[i]
        result=await observe_platform(upstream,'private-token')
        assert peak<=2 and len(seen)==6
        assert all(m=='GET' and k=={'token':'private-token','data':None} for m,p,k in seen)
        assert [x['state'] for x in result['observations']]==['responding','disabled','access_denied','not_exposed','unavailable','responding']
        assert 'secret' not in str(result) and 'private-token' not in str(result)
        assert not result['task_execution_performed']
        assert all(not x['execution_verified'] for x in result['observations'])
    asyncio.run(scenario())

def test_timeout_and_error_do_not_break_other_domains():
    async def scenario():
        async def upstream(method,path,**kwargs):
            if path.endswith('plugins'):raise RuntimeError('secret-provider-url')
            await asyncio.sleep(.02)
        result=await observe_platform(upstream,'token',timeout=.001)
        assert result['observations'][0]['state']=='unavailable'
        assert all(x['state']=='timeout' for x in result['observations'][1:])
        assert 'secret' not in str(result)
    asyncio.run(scenario())

def test_owner_session_required_and_arbitrary_probe_rejected():
    async def scenario():
        seen=[]
        async def session_for(request):return SimpleNamespace(access='owner-token') if request.headers.get('X-Test') else None
        async def upstream(*args,**kwargs):seen.append(args);return 200,{'ok':True}
        app=web.Application();setup_platform_status(app,session_for=session_for,upstream=upstream,json_response=lambda d,status:web.json_response(d,status=status))
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/web-api/v1/platform/status')
            assert (await client.get(url)).status==401
            assert not seen
            assert (await client.get(str(url)+'?url=https://private',headers={'X-Test':'1'})).status==400
            assert not seen
            assert (await client.get(url,headers={'X-Test':'1'})).status==200
            assert len(seen)==6
    asyncio.run(scenario())

def test_existing_capability_search_filters_are_route_scoped():
    from desktop.feature_routes import setup_feature_routes
    async def scenario():
        seen=[]
        async def session_for(request):return SimpleNamespace(access='token',user_id=7)
        async def upstream(method,path,**kwargs):seen.append(path);return 200,{'ok':True,'capabilities':[]}
        app=web.Application();setup_feature_routes(app,session_for=session_for,same_origin=lambda r:True,upstream=upstream,json_response=lambda d,status=200:web.json_response(d,status=status))
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/web-api/v1/features/agents/capabilities')
            assert (await client.get(url,params={'q':'исследования','category':'Research'})).status==200
            assert 'q=' in seen[-1] and 'category=Research' in seen[-1]
            count=len(seen)
            assert (await client.get(server.make_url('/web-api/v1/features/profile?q=secret'))).status==400
            assert len(seen)==count
    asyncio.run(scenario())

def test_declared_worker_and_plugin_blockers_are_not_reported_ready():
    from desktop.platform_status import readiness
    assert readiness('software',{'worker_enabled':False,'coding_enabled':True,'write_enabled':False,'worker_ready':False})==['worker_enabled','write_enabled','worker_ready']
    assert readiness('software',{'worker_ready':True})==[]
    assert readiness('plugins',{'plugins':{'research':{'available':False},'file_analyst':{'available':True},'private':{'available':False,'api_key':'secret'}}})==['research_unavailable']
    assert readiness('plugins',{'plugins':None})==[]

def test_partial_status_uses_fixed_public_reasons():
    async def scenario():
        async def upstream(method,path,**kwargs):
            return 200,{'ok':True,'enabled':True,'worker_ready':False,'error':'secret'}
        result=await observe_platform(upstream,'token')
        software=next(x for x in result['observations'] if x['domain']=='software')
        assert software['state']=='partial'
        assert software['missing_prerequisites']==['worker_ready']
        assert 'secret' not in str(result)
    asyncio.run(scenario())
