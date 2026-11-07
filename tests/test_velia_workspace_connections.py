import asyncio
import json
from urllib.parse import urlsplit, parse_qs
import pytest
from desktop.workspace_connections import WorkspaceConnector, WorkspaceOAuth, safe_cell
from desktop.figma_connector import FigmaError


@pytest.mark.parametrize('provider',['google','shopify'])
def test_native_oauth_is_owner_bound_and_disconnect_cancels_pending(provider):
    async def scenario():
        calls=[]
        async def upstream(method,path,*,token,data=None):
            assert token=='account-access';calls.append((method,path,data))
            if path.endswith('/begin'):return 200,{'ok':True,'configured':True,'client_id':'public','redirect_uri':'https://backend.example/api/auth/google/callback','scope':'read','state':'velia_workspace.signed-state' if provider=='google' else 'signed-state'}
            return 200,{'ok':True,'access_token':'private-access-token','expires_in':3600}
        bridge=WorkspaceConnector(provider);oauth=WorkspaceOAuth(bridge,'https://velia.example',provider,upstream);bridge.oauth=oauth
        owner=('7','session-7')
        with oauth.authentication('account-access'):
            start=await oauth.start(owner,options={'shop':'user-shop.myshopify.com'})
            query=parse_qs(urlsplit(start['authorization_url']).query);state=query['state'][0]
            assert query['client_id']==['public'] and 'private-access-token' not in start['authorization_url']
            if provider=='google':assert state.startswith('velia_workspace.') and query['code_challenge_method']==['S256']
            with pytest.raises(FigmaError):await oauth.complete(('8','session-8'),state,'code')
            result=await oauth.complete(owner,state,'code',callback_query=[('state',state),('code','code')])
            assert result['connected'] and bridge.connections[owner]['shop']=='user-shop.myshopify.com'
            with pytest.raises(FigmaError):await oauth.complete(owner,state,'code')
            await oauth.start(owner,options={'shop':'user-shop.myshopify.com'})
            bridge.disconnect(owner)
            assert not oauth.pending and not bridge.connections
        assert sum(path.endswith('/token') for _,path,_ in calls)==1
    asyncio.run(scenario())


def test_google_draft_and_sheet_calls_have_receipts_and_do_not_send_mail():
    async def scenario():
        calls=[]
        async def api(connection,method,path,**kwargs):
            assert connection['token']=='private-access-token';calls.append((method,path,kwargs))
            if path=='/v1/userinfo':return {'email':'user@example.com'}
            if path=='/v4/spreadsheets':return {'spreadsheetId':'sheet-1','spreadsheetUrl':'https://docs.google.com/spreadsheets/d/sheet-1'}
            if path.endswith('/drafts'):return {'id':'draft-1'}
            return {'updatedCells':2}
        bridge=WorkspaceConnector('google',api);owner=('7','s');linked=await bridge.connect(owner,'private-access-token',oauth_expires=4000000000)
        assert (await bridge.verify(owner))['connected']
        base={'connection_id':linked['connection_id'],'request_id':'request-1234','run_id':'run-12345','operation_id':'operation-1234'}
        args={**base,'name':'gmail_draft','arguments':{'to':'client@example.com','subject':'Предложение','body':'Добрый день'}}
        result=await bridge.call(owner,args);assert 'draft-1' in result['text'] and 'false' in result['text']
        assert await bridge.call(owner,args)==result
        await bridge.call(owner,{**base,'operation_id':'operation-2345','name':'sheets_create','arguments':{'title':'Доход','rows':[['A','B'],['Итог','=SUM(B1:B2)']]}})
        assert len([path for _,path,_ in calls if path.endswith('/drafts')])==1
        assert not any('send' in path for _,path,_ in calls)
        assert calls[-1][2]['data']['values'][1][1]=='=SUM(B1:B2)'
        with pytest.raises(FigmaError):await bridge.call(('8','s'),args)
        bridge.disconnect(owner)
        with pytest.raises(FigmaError):await bridge.call(owner,args)
    asyncio.run(scenario())


def test_invalid_rows_do_not_create_an_empty_spreadsheet():
    async def scenario():
        async def api(*args,**kwargs):raise AssertionError('Invalid request must not reach Google')
        bridge=WorkspaceConnector('google',api)
        with pytest.raises(FigmaError):await bridge.execute({},'sheets_create',{'title':'x','rows':[]})
    asyncio.run(scenario())


def test_formulas_cannot_fetch_urls_or_execute_external_commands():
    assert safe_cell('=SUM(B2:B6)')=='=SUM(B2:B6)'
    for value in ['=IMPORTXML("https://example.com","a")','=WEBSERVICE("x")',"=cmd|' /C calc'!A0",'+IMPORTDATA("https://example.com")','\t=IMPORTDATA("x")']:
        assert safe_cell(value).startswith("'")


def test_disconnect_during_authorization_prevents_new_pending_state():
    async def scenario():
        started=asyncio.Event();resume=asyncio.Event()
        async def upstream(*args,**kwargs):
            started.set();await resume.wait();return 200,{'ok':True,'configured':True,'client_id':'public','redirect_uri':'https://backend.example/callback','scope':'read'}
        bridge=WorkspaceConnector('google');oauth=WorkspaceOAuth(bridge,'https://velia.example','google',upstream);bridge.oauth=oauth
        owner=('7','s')
        task=asyncio.create_task(oauth.start(owner));await started.wait();bridge.disconnect(owner);resume.set()
        with pytest.raises(FigmaError,match='cancelled'):await task
        assert not oauth.pending and not bridge.connections
    asyncio.run(scenario())
