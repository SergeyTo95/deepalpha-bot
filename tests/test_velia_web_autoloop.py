from types import SimpleNamespace
import uuid

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from desktop.account_routes import setup_account_routes


@pytest.mark.asyncio
async def test_autoloop_proxy_preserves_auth_csrf_model_and_filters_private_fields():
    calls=[]
    cid,rid=str(uuid.uuid4()),str(uuid.uuid4())
    async def session_for(request):
        return SimpleNamespace(access='server-token',user_id=77,free_api=True,background_tasks=True) if request.headers.get('Cookie')=='fixture' else None
    async def upstream(method,path,**kw):
        calls.append((method,path,kw))
        return 201,{'ok':True,'rule':{'id':rid,'conversation_id':cid,'model':'flash','instruction':'Check',
            'enabled':True,'interval_minutes':15,'max_runs_per_day':1,'secret':'DO-NOT-RETURN','config':{'token':'PRIVATE'}}}
    async def authorize(token,model,**kw):
        assert token=='server-token' and model=='velia-flash'
    app=web.Application()
    setup_account_routes(app,session_for=session_for,same_origin=lambda r:r.headers.get('Origin')=='https://velia.test',
        upstream=upstream,upstream_stream=None,authorize_model=authorize,handlers={},
        json_response=lambda data,status=200:web.json_response(data,status=status))
    async with TestClient(TestServer(app)) as client:
        assert (await client.get('/web-api/v1/autoloop/rules')).status==401
        assert (await client.post('/web-api/v1/autoloop/rules',headers={'Cookie':'fixture'},json={})).status==403
        data={'conversation_id':cid,'model':'velia-flash','instruction':'Check','idempotency_key':'request-001','interval_minutes':15}
        response=await client.post('/web-api/v1/autoloop/rules',headers={'Cookie':'fixture','Origin':'https://velia.test'},json=data)
        assert response.status==201
        value=await response.json()
        assert 'PRIVATE' not in str(value) and 'DO-NOT-RETURN' not in str(value)
        assert calls[0][2]['token']=='server-token' and calls[0][2]['data']['model']=='flash'
        assert calls[0][1]=='/mobile-api/v1/autoloop/rules'
        response=await client.post(f'/web-api/v1/autoloop/rules/{rid}/github-secret',headers={'Cookie':'fixture','Origin':'https://velia.test'},json={})
        assert response.status==400 and len(calls)==1
