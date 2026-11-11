import asyncio


def test_inline_app_planning_requires_auth_uses_selected_model_and_disables_search(monkeypatch,tmp_path):
    from test_velia_web_chat import fixture,headers,login
    from desktop.guest_store import GuestStore
    async def scenario():
        async with fixture(monkeypatch,quantum_api=True,features={'chat_quantum':True},with_search=True,guest_store=GuestStore(sqlite_path=tmp_path/'apps-quota.db')) as (server,client,state):
            body={'model':'velia-flash','messages':[{'role':'user','content':'Верни JSON интерактивного калькулятора'}],'stream':True,'web_search':True}
            url=server.make_url('/web-api/v1/apps/plan')
            assert (await client.post(url,headers=headers(),json=body)).status==401
            cookie,_,_=await login(server,client)
            assert (await client.post(url,headers=headers(cookie,Origin='https://evil.example'),json=body)).status==403
            assert (await client.post(url,headers=headers(cookie),json={**body,'script':'fetch("https://evil.example")'})).status==400
            for model in ['velia-flash','velia-quantum','velia-pro']:
                response=await client.post(url,headers=headers(cookie),json={**body,'model':model})
                value=await response.text();assert response.status==200 and '[DONE]' in value,(model,response.status,value)
            assert len(state['payloads'])==3 and not state['search_queries'] and not state['intent_payloads']
            assert state['payloads'][0]['max_tokens']==1536
            for name in ['mini-app-core.mjs','mini-apps.mjs','mini-app-ui.mjs']:
                response=await client.get(server.make_url('/web/'+name));assert response.status==200
                assert response.headers['X-Content-Type-Options']=='nosniff'
    asyncio.run(scenario())
