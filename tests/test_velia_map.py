import asyncio
import hashlib
from types import SimpleNamespace
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.map_routes import setup_map_routes
from desktop.map_routes import search_filters,photon_params,extract_places
from desktop.feature_routes import setup_feature_routes


def test_lookup_requires_origin_and_auth_and_caches_user_triggered_search():
    async def scenario():
        calls=[]
        async def lookup(query,filters):
            calls.append(query)
            return {'features':[{'geometry':{'coordinates':[30.7133,36.8969]},'properties':{'name':'Antalya','country':'Türkiye'}}]}
        async def session(request):return SimpleNamespace(user_id=7) if request.headers.get('X-Account') else None
        app=web.Application();setup_map_routes(app,session_for=session,same_origin=lambda request:request.headers.get('Origin')=='https://velia.example',json_response=lambda result,status=200:web.json_response(result,status=status),origin='https://velia.example',lookup=lookup)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/web-api/v1/map/search');headers={'Origin':'https://velia.example','X-Account':'7'}
            assert (await client.post(url,json={'query':'Antalya'})).status==403
            assert (await client.post(url,json={'query':'Antalya'},headers={'Origin':'https://velia.example'})).status==401
            response=await client.post(url,json={'query':'Antalya'},headers=headers);value=await response.json()
            assert value['places'][0]['lat']==36.8969 and value['places'][0]['lon']==30.7133
            assert (await client.post(url,json={'query':'ANTALYA'},headers=headers)).status==200
            assert calls==['Antalya']
            assert (await client.post(url,json={'query':[]},headers=headers)).status==400
    asyncio.run(scenario())


def test_country_and_category_filters_exclude_namesakes_and_survive_missing_provider_filters():
    import pytest
    filters=search_filters({'category':'airport','country_code':'tr'})
    params=photon_params('Antalya Airport',filters)
    assert ('countrycode','TR') in params and ('osm_tag','aeroway:aerodrome') in params
    def feature(name,country,key,value):
        return {'geometry':{'coordinates':[30.7981914,36.8999425]},'properties':{'name':name,'countrycode':country,'country':country,'osm_key':key,'osm_value':value}}
    result=extract_places({'features':[feature('Antalya','RU','amenity','restaurant'),feature('Antalya Airport Rent a car','TR','amenity','car_rental'),feature('Antalya Airport','TR','aeroway','aerodrome'),feature('Antalya','TR','place','city')]},filters)
    assert len(result)==1 and result[0]['title']=='Antalya Airport' and result[0]['category']=='airport' and result[0]['country_code']=='TR'
    for value in [{'country_code':'Turkey'},{'category':'invented'},{'country_code':[]},{'category':False}]:
        with pytest.raises(ValueError):search_filters(value)


def test_cache_separates_country_and_category_constrained_queries():
    async def scenario():
        calls=[]
        async def lookup(query,filters):
            calls.append(filters.copy())
            return {'features':[{'geometry':{'coordinates':[30.8,36.9]},'properties':{'name':'Antalya Airport','countrycode':'TR','osm_key':'aeroway','osm_value':'aerodrome'}}]}
        async def session(request):return SimpleNamespace(user_id=7)
        app=web.Application();setup_map_routes(app,session_for=session,same_origin=lambda request:True,json_response=lambda result,status=200:web.json_response(result,status=status),origin='https://velia.example',lookup=lookup)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/web-api/v1/map/search')
            for body in [{'query':'Antalya'},{'query':'Antalya','category':'airport','country_code':'TR'},{'query':'ANTALYA','category':'airport','country_code':'tr'}]:
                response=await client.post(url,json=body);assert response.status==200
            assert len(calls)==2 and calls[0]!=calls[1]
    asyncio.run(scenario())


def test_map_planner_uses_authenticated_selected_model_without_web_search(monkeypatch,tmp_path):
    from test_velia_web_chat import fixture,headers,login
    from desktop.guest_store import GuestStore
    async def scenario():
        async with fixture(monkeypatch,quantum_api=True,features={'chat_quantum':True},with_search=True,guest_store=GuestStore(sqlite_path=tmp_path/'map-quota.db')) as (server,client,state):
            body={'model':'velia-pro','messages':[{'role':'user','content':'Верни JSON для карты'}],'stream':True,'web_search':False}
            url=server.make_url('/web-api/v1/map/plan')
            assert (await client.post(url,headers=headers(),json=body)).status==401
            cookie,_,_=await login(server,client)
            assert (await client.post(url,headers=headers(cookie,Origin='https://evil.example'),json=body)).status==403
            for model in ['velia-flash','velia-quantum','velia-pro']:
                response=await client.post(url,headers=headers(cookie),json={**body,'model':model})
                value=await response.text();assert response.status==200 and '[DONE]' in value,(model,response.status,value)
            assert len(state['payloads'])==3 and not state['search_queries'] and not state['intent_payloads']
    asyncio.run(scenario())


def test_notebook_gateway_binds_snapshot_to_current_account():
    async def scenario():
        calls=[];origin='https://velia.example';account=hashlib.sha256((origin+':7').encode()).hexdigest()[:24]
        async def session(request):return SimpleNamespace(user_id=7,access='account-access')
        async def upstream(method,path,**kwargs):calls.append((method,path,kwargs));return 200,{'ok':True,'owner':'7','revision':0,'encryption_key':'1'*64,'record':None}
        app=web.Application();setup_feature_routes(app,session_for=session,same_origin=lambda request:True,upstream=upstream,json_response=lambda result,status=200:web.json_response(result,status=status),origin=origin)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/web-api/v1/features/notebook')
            response=await client.get(url);assert (await response.json())['account']==account
            response=await client.put(url,json={'account':'b'*24,'owner':'7'})
            assert response.status==403 and len(calls)==1
            response=await client.put(url,json={'account':account,'owner':'7','revision':0,'record':{}})
            assert response.status==200 and len(calls)==2
    asyncio.run(scenario())
