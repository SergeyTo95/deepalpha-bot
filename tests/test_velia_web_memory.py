import asyncio


def test_memory_recall_requires_account_uses_selected_model_and_never_searches_web(monkeypatch,tmp_path):
    from test_velia_web_chat import fixture,headers,login
    from desktop.guest_store import GuestStore
    async def scenario():
        async with fixture(monkeypatch,quantum_api=True,features={'chat_quantum':True},with_search=True,guest_store=GuestStore(sqlite_path=tmp_path/'memory-quota.db')) as (server,client,state):
            body={'model':'velia-pro','messages':[{'role':'user','content':'Выбери точную цитату из сохранённой памяти'}],'stream':True,'web_search':True}
            url=server.make_url('/web-api/v1/memory/plan')
            assert (await client.post(url,headers=headers(),json=body)).status==401
            cookie,_,_=await login(server,client)
            assert (await client.post(url,headers=headers(cookie,Origin='https://evil.example'),json=body)).status==403
            for model in ['velia-flash','velia-quantum','velia-pro']:
                response=await client.post(url,headers=headers(cookie),json={**body,'model':model})
                value=await response.text();assert response.status==200 and '[DONE]' in value,(model,response.status,value)
            assert len(state['payloads'])==3 and not state['search_queries'] and not state['intent_payloads']
            for name in ['memory.mjs','memory-schema.mjs','memory-ui.mjs']:
                response=await client.get(server.make_url('/web/'+name));assert response.status==200
                assert response.headers['X-Content-Type-Options']=='nosniff'
    asyncio.run(scenario())
