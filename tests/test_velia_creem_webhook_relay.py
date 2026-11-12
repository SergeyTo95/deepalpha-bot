import asyncio
import base64
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from desktop.creem_webhook_relay import PATH, MAX_BODY, setup_creem_webhook_relay


def test_live_webhook_relay_preserves_signed_bytes_and_has_no_fulfillment_of_its_own():
    async def scenario():
        calls=[]
        async def upstream(method,path,**options):
            calls.append((method,path,options))
            return 200, {'ok':True,'customer_email':'private@example.com','api_key':'secret'}
        app=web.Application()
        setup_creem_webhook_relay(app,upstream=upstream,json_response=lambda d,s=200:web.json_response(d,status=s))
        async with TestServer(app) as server,ClientSession() as client:
            assert (await client.post(server.make_url(PATH),data=b'{}')).status==401
            assert calls==[]
            raw=b'{ "eventType": "checkout.completed", "id": "evt_a" }\n'
            response=await client.post(server.make_url(PATH),data=raw,headers={'creem-signature':'a'*64})
            assert response.status==200 and await response.json()=={'ok':True}
            method,path,options=calls[0]
            assert method=='POST' and path=='/mobile-api/v1/economy/creem/webhook'
            assert set(options)=={'data'}
            assert base64.b64decode(options['data']['raw_body'])==raw
            assert options['data']['signature']=='a'*64
            assert (await client.post(server.make_url(PATH),data=b'x'*(MAX_BODY+1),headers={'creem-signature':'a'*64})).status==413
            assert len(calls)==1
    asyncio.run(scenario())
