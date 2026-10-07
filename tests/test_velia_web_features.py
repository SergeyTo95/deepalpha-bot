import asyncio
from types import SimpleNamespace
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.feature_routes import setup_feature_routes, route_allowed, safe_result

def test_feature_paths_are_exact_and_do_not_expose_auth_or_admin():
    assert route_allowed('GET', 'profile')
    assert route_allowed('GET', 'agent/schedules/status')
    assert route_allowed('POST', 'agent/schedules/schedule1/enable')
    assert route_allowed('DELETE', 'agent/schedules/schedule1')
    assert not route_allowed('POST', 'agent/schedules/schedule1/run')
    assert not route_allowed('GET', 'agent/schedules/a/b')
    assert route_allowed('POST', 'research/missions/m1/literature')
    assert not route_allowed('POST', 'profile')
    for path in ('auth/refresh', 'admin', 'profile/../auth/refresh', 'https://example.com', 'research/missions/a/b/literature'):
        assert not route_allowed('GET', path)
        assert not route_allowed('POST', path)

def test_credentials_are_removed_recursively():
    assert safe_result({'ok': True, 'access_token': 'secret', 'nested': [{'api_key':'secret','title':'Keep'}]}) == {'ok':True,'nested':[{'title':'Keep'}]}

def test_feature_relay_checks_origin_identity_ownership_and_passes_idempotency():
    async def scenario():
        seen=[]
        async def session_for(request):
            return SimpleNamespace(access='server-only-token',user_id=7) if request.headers.get('X-Test-Session') else None
        async def upstream(method,path,**kwargs):
            seen.append((method,path,kwargs));return 201,{'ok':True,'project':{'title':'Project'},'refresh_token':'secret'}
        app=web.Application()
        setup_feature_routes(app,origin="https://velia.example.com",session_for=session_for,same_origin=lambda r:r.headers.get('Origin')=='https://velia.example.com' and r.headers.get('X-Velia-Request')=='1' and r.content_type=='application/json',upstream=upstream,json_response=lambda data,status=200:web.json_response(data,status=status))
        async with TestServer(app) as server, ClientSession() as client:
            url=str(server.make_url('/web-api/v1/features/projects'))
            assert (await client.get(url)).status==401
            assert (await client.post(url,json={})).status==403
            headers={'Origin':'https://velia.example.com','X-Velia-Request':'1','X-Test-Session':'yes','Idempotency-Key':'feature-key-123'}
            response=await client.post(url,headers=headers,json={'passport':{'title':'Project'}})
            assert response.status==201
            assert 'refresh_token' not in await response.json()
            assert seen[-1][2]['token']=='server-only-token'
            assert seen[-1][2]['idempotency_key']=='feature-key-123'
            assert (await client.get(url+'?unknown=1',headers=headers)).status==400
            assert (await client.get(str(server.make_url('/web-api/v1/features/media/images/id/content?user_id=8')),headers=headers)).status==403
            assert (await client.get(str(server.make_url('/web-api/v1/features/auth/refresh')),headers=headers)).status==404
    asyncio.run(scenario())

def test_history_attachment_metadata_does_not_expose_originals_or_credentials():
    from desktop.account_routes import message
    item={'id':'11111111-1111-1111-1111-111111111111','name':'photo.jpg','mime_type':'image/jpeg','kind':'image','byte_size':42,'content_bytes':'private','extracted_text':'private','content_url':'https://private','access_token':'secret'}
    result=message({'role':'user','content':'Фото','attachments':[item,{'id':'invalid','name':'bad'}]})
    assert result['attachments']==[{key:item[key] for key in ('id','name','mime_type','kind','byte_size')}]
