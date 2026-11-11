import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.map_routing import FreeRouter,RouteError,route_input,best_order,extract_route,setup_routing_routes

POINTS=[[30.7,36.9],[30.8,36.91],[30.9,36.92],[31,36.93]]


def response(points=POINTS):
    return {'features':[{'geometry':{'type':'LineString','coordinates':points},'properties':{'summary':{'distance':12000,'duration':1800},'segments':[{'distance':4000,'duration':600} for _ in range(len(points)-1)]}}]}


def test_strict_inputs_and_directed_optimization_fixed_endpoints():
    assert route_input({'coordinates':POINTS})==(POINTS,'driving',False)
    for bad in [{'coordinates':POINTS,'url':'https://evil.example'}, {'coordinates':POINTS,'mode':'transit'}, {'coordinates':POINTS,'optimize':1},
                {'coordinates':[[True,36],[30,37]]},{'coordinates':[[30,float('nan')],[31,37]]},{'coordinates':[[181,36],[31,37]]}, {'coordinates':[POINTS[0],POINTS[0]]}]:
        with pytest.raises(RouteError):route_input(bad)
    matrix=[[0,100,1,50],[50,0,100,1],[50,1,0,100],[50,50,50,0]]
    assert best_order(matrix,4)==[0,2,1,3]
    matrix[0][2]=None;assert best_order(matrix,4)==[0,1,2,3]
    matrix[0][1]=None
    with pytest.raises(RouteError,match='route_no_path'):best_order(matrix,4)
    with pytest.raises(RouteError):best_order([[0,float('nan')],[1,0]],2)


def test_invalid_provider_shapes_geometry_and_metrics_are_rejected():
    result=extract_route(response(),POINTS,[0,1,2,3],'driving',False)
    assert result['traffic'] is False and result['duration_s']==1800 and result['license']=='CC-BY-SA-4.0'
    for invalid in [None,{'features':[None]}, {'features':[{'geometry':None}]}, {'features':[]},
                    {'features':[{'geometry':{'type':'LineString','coordinates':[[999,0],[0,0]]},'properties':{'summary':{'distance':3,'duration':4},'segments':[]}}]}]:
        with pytest.raises(RouteError):extract_route(invalid,POINTS,[0,1,2,3],'driving',False)
    invalid=response();invalid['features'][0]['properties']['summary']['duration']=float('inf')
    with pytest.raises(RouteError):extract_route(invalid,POINTS,[0,1,2,3],'driving',False)


def test_fixed_host_payload_scoped_cache_limits_and_no_fallback():
    async def scenario():
        calls=[];tick=[0]
        def clock():tick[0]+=3;return tick[0]
        async def request(path,body):
            calls.append((path,body))
            if path.startswith('/matrix'):return {'durations':[[0,100,1,50],[50,0,100,1],[50,1,0,100],[50,50,50,0]]}
            result=response(body['coordinates'])
            # Real ORS resets segments when instructions=false, even with geometry.
            if not body.get('instructions'):result['features'][0]['properties'].pop('segments')
            return result
        router=FreeRouter(api_key='private-free-key',request=request,clock=clock)
        data={'coordinates':POINTS,'mode':'driving','optimize':True}
        result=await router.route(7,data)
        assert result['order']==[0,2,1,3] and not result['cached']
        assert calls[1][1]['coordinates']==[POINTS[i] for i in result['order']]
        assert set(calls[0][1])=={'locations','metrics'}
        assert set(calls[1][1])=={'coordinates','instructions','geometry_simplify'}
        assert calls[1][1]['instructions'] is True
        assert 'private-free-key' not in str(result)
        assert (await router.route(7,data))['cached'] and len(calls)==2
        await router.route(8,data);assert len(calls)==4
        router.cache.clear();router.counts['directions']=800
        with pytest.raises(RouteError,match='route_free_limit'):await router.route(7,{'coordinates':POINTS})
        assert len(calls)==4
        missing=FreeRouter(api_key='',request=request)
        with pytest.raises(RouteError,match='route_key_required'):await missing.route(7,data)
        assert len(calls)==4
    asyncio.run(scenario())


def test_provider_http_contract_retains_segments_and_discards_navigation_steps(monkeypatch):
    async def scenario():
        async def directions(req):
            assert req.headers['Authorization']=='test-key'
            body=await req.json();result=response(body['coordinates'])
            props=result['features'][0]['properties']
            if body.get('instructions'):
                for segment in props['segments']:segment['steps']=[{'instruction':'Private street name'}]
            else:props.pop('segments')
            return web.json_response(result,content_type='application/geo+json')
        app=web.Application();app.router.add_post('/directions/driving-car/geojson',directions)
        async with TestServer(app) as server:
            monkeypatch.setattr('desktop.map_routing.API',str(server.make_url('')).rstrip('/'))
            router=FreeRouter(api_key='test-key')
            result=await router.route(7,{'coordinates':POINTS[:2]})
            assert result['distance_m']==12000 and result['duration_s']==1800
            assert len(result['legs'])==1 and result['geometry']==POINTS[:2]
            assert 'Private street' not in str(result) and 'steps' not in str(result)
    asyncio.run(scenario())


def test_provider_html_redirects_and_auth_errors_never_escape_as_raw_text(monkeypatch):
    async def scenario():
        status=[200];hits=[]
        async def broken(req):
            hits.append(req.path)
            return web.Response(text='<!DOCTYPE html><html>private provider error</html>',status=status[0],headers={'Location':'/must-not-follow'},content_type='text/html')
        app=web.Application();app.router.add_post('/broken',broken)
        async with TestServer(app) as server:
            monkeypatch.setattr('desktop.map_routing.API',str(server.make_url('')).rstrip('/'))
            router=FreeRouter(api_key='test-key')
            for http,code in [(200,'route_invalid_response'),(502,'route_unavailable'),(302,'route_unavailable'),(401,'route_access_denied'),(403,'route_access_denied'),(429,'route_free_limit')]:
                status[0]=http
                with pytest.raises(RouteError,match=code) as error:await router.remote('/broken',{})
                assert '<' not in str(error.value) and 'private' not in str(error.value)
            assert hits==['/broken']*6
    asyncio.run(scenario())


def test_provider_failure_cooldown_and_abort_release_slot():
    async def scenario():
        calls=[]
        async def request(path,body):calls.append(path);raise RouteError('route_free_limit',429)
        router=FreeRouter(api_key='key',request=request)
        for _ in range(2):
            with pytest.raises(RouteError,match='route_free_limit'):await router.route(7,{'coordinates':POINTS})
        assert len(calls)==1 and not router.lock.locked()
        entered=asyncio.Event()
        async def blocked(*args):entered.set();await asyncio.Event().wait()
        router=FreeRouter(api_key='key',request=blocked)
        task=asyncio.create_task(router.route(7,{'coordinates':POINTS}));await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not router.lock.locked()
    asyncio.run(scenario())


def test_http_auth_origin_size_and_no_unknown_provider_or_pii_fields():
    async def scenario():
        calls=[]
        async def request(path,body):calls.append((path,body));return response(body['coordinates'])
        router=FreeRouter(api_key='key',request=request)
        async def session(req):return SimpleNamespace(user_id=7) if req.headers.get('X-User') else None
        app=web.Application();setup_routing_routes(app,session_for=session,same_origin=lambda r:r.headers.get('Origin')=='https://velia.example',json_response=lambda result,status=200:web.json_response(result,status=status,headers={'Cache-Control':'no-store'}),router=router)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/web-api/v1/map/route');headers={'Origin':'https://velia.example','X-User':'7'}
            assert (await client.post(url,json={'coordinates':POINTS})).status==403
            assert (await client.post(url,json={'coordinates':POINTS},headers={'Origin':'https://velia.example'})).status==401
            assert (await client.post(url,json={'coordinates':POINTS,'contact':'Private name'},headers=headers)).status==400
            assert not calls
            result=await client.post(url,json={'coordinates':POINTS},headers=headers)
            assert result.status==200 and result.headers['Cache-Control']=='no-store'
            assert (await result.json())['route']['distance_m']==12000
            assert (await client.post(url,json={'coordinates':POINTS,'x':'a'*5000},headers=headers)).status==413
            assert len(calls)==1
    asyncio.run(scenario())
