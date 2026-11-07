import asyncio
import hashlib
from types import SimpleNamespace
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.map_routes import setup_map_routes
from desktop.feature_routes import setup_feature_routes


def test_lookup_requires_origin_and_auth_and_caches_user_triggered_search():
    async def scenario():
        calls=[]
        async def lookup(query):
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
