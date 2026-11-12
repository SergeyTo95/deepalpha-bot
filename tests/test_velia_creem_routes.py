import asyncio
import base64
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from services.velia_creem_routes import setup_creem_routes
from services.velia_creem_billing import MAX_BODY


def test_checkout_identity_and_webhook_body_boundary():
    async def scenario():
        calls=[]
        class Billing:
            def checkout(self,*args):
                calls.append(('checkout',args)); return {'ok':True}
            def portal(self,*args):
                calls.append(('portal',args)); return {'ok':True}
            def webhook(self,*args):
                calls.append(('webhook',args)); return {'ok':True}
        app=web.Application()
        setup_creem_routes(app,require_auth=lambda r:{'user_id':7} if r.headers.get('Authorization')=='Bearer owner' else None,
            api_available=lambda:True,billing=Billing())
        async with TestServer(app) as server,ClientSession() as client:
            prefix='/mobile-api/v1/economy/creem/'
            url=server.make_url(prefix+'checkout')
            assert (await client.post(url,json={'plan':'plus','attempt':'a'})).status==401
            headers={'Authorization':'Bearer owner'}
            assert (await client.post(url,headers=headers,json={'plan':'plus','attempt':'a','user_id':8})).status==400
            assert not calls
            assert (await client.post(url,headers=headers,json={'plan':'plus','attempt':'a'})).status==200
            assert calls.pop()==('checkout',(7,'plus','a'))
            assert (await client.post(server.make_url(prefix+'portal'),headers=headers,json={'customer_id':'cust_other'})).status==400
            assert (await client.post(server.make_url(prefix+'portal'),headers=headers,json={})).status==200
            assert calls.pop()==('portal',(7,))
            raw=b'{ "id": "evt_signed" }\n'
            envelope={'raw_body':base64.b64encode(raw).decode(),'signature':'a'*64}
            assert (await client.post(server.make_url(prefix+'webhook'),json=envelope)).status==200
            kind,args=calls.pop()
            assert kind=='webhook' and args[:2]==(raw,'a'*64)
            envelope['raw_body']='%invalid'
            assert (await client.post(server.make_url(prefix+'webhook'),json=envelope)).status==400
            envelope['raw_body']='x'*(MAX_BODY*2)
            assert (await client.post(server.make_url(prefix+'webhook'),json=envelope)).status==413
            assert not calls
    asyncio.run(scenario())
