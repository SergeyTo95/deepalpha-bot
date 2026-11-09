import asyncio
import json
from urllib.parse import urlsplit, parse_qs
from types import SimpleNamespace

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import desktop.workspace_connections as workspace_module
from desktop.figma_routes import setup_figma_routes
import pytest
from desktop.workspace_connections import WorkspaceConnector, WorkspaceOAuth, safe_cell, GOOGLE_TOOLS
from desktop.figma_connector import FigmaError


@pytest.mark.parametrize('provider',['google','shopify'])
def test_native_oauth_is_owner_bound_and_disconnect_cancels_pending(provider,monkeypatch):
    if provider=='google':
        monkeypatch.setenv('VELIA_GOOGLE_OAUTH_CLIENT_ID','public')
        monkeypatch.setenv('VELIA_GOOGLE_OAUTH_CLIENT_SECRET','private-local-only')
    async def scenario():
        calls=[]
        async def upstream(method,path,*,token,data=None):
            if provider=='google':raise AssertionError('Google OAuth must never call legacy backend')
            assert token=='account-access';calls.append((method,path,data))
            if path.endswith('/begin'):return 200,{'ok':True,'configured':True,'client_id':'public','redirect_uri':'https://backend.example/api/auth/google/callback','scope':'read','state':'signed-state'}
            return 200,{'ok':True,'access_token':'private-access-token','expires_in':3600}
        bridge=WorkspaceConnector(provider);oauth=WorkspaceOAuth(bridge,'https://velia.example',provider,upstream);bridge.oauth=oauth
        if provider=='google':
            async def google_exchange(data):
                assert data['code']=='code' and len(data['code_verifier'])>=43
                return {'ok':True,'access_token':'private-access-token','expires_in':3600}
            oauth.google_exchange=google_exchange
        owner=('7','session-7')
        with oauth.authentication('account-access'):
            start=await oauth.start(owner,options={'shop':'user-shop.myshopify.com'})
            query=parse_qs(urlsplit(start['authorization_url']).query);state=query['state'][0]
            assert query['client_id']==['public'] and 'private-access-token' not in start['authorization_url']
            if provider=='google':
                assert state.startswith('velia_workspace.') and query['code_challenge_method']==['S256']
                assert query['redirect_uri']==['https://velia.example/web-api/v1/velia/oauth/google/callback']
                assert 'private-local-only' not in start['authorization_url']
            with pytest.raises(FigmaError):await oauth.complete(('8','session-8'),state,'code')
            result=await oauth.complete(owner,state,'code',callback_query=[('state',state),('code','code')])
            assert result['connected'] and bridge.connections[owner]['shop']=='user-shop.myshopify.com'
            with pytest.raises(FigmaError):await oauth.complete(owner,state,'code')
            await oauth.start(owner,options={'shop':'user-shop.myshopify.com'})
            bridge.disconnect(owner)
            assert not oauth.pending and not bridge.connections
        assert sum(path.endswith('/token') for _,path,_ in calls)==(0 if provider=='google' else 1)
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
            started.set();await resume.wait()
            return 200,{'ok':True,'configured':True,'client_id':'public','redirect_uri':'https://backend.example/callback','scope':'read','state':'signed-state'}
        bridge=WorkspaceConnector('shopify');oauth=WorkspaceOAuth(bridge,'https://velia.example','shopify',upstream);bridge.oauth=oauth
        owner=('7','s')
        task=asyncio.create_task(oauth.start(owner,options={'shop':'user-shop.myshopify.com'}))
        await started.wait();bridge.disconnect(owner);resume.set()
        with pytest.raises(FigmaError,match='cancelled'):await task
        assert not oauth.pending and not bridge.connections
    asyncio.run(scenario())


# Calendar regression checks run in the existing gateway Docker qualification.
def execute(name, arguments, payload):
    calls = []
    async def fake(connection, method, path, *, data=None, params=None, plain=False):
        calls.append((method, path, data, params))
        return payload
    connector = WorkspaceConnector('google', request=fake)
    result = asyncio.run(connector.execute({'token': 'test-only'}, name, arguments))
    return result, calls


def test_calendar_tools_are_part_of_google_without_displacing_gmail():
    names = {item['name'] for item in GOOGLE_TOOLS}
    assert {'gmail_search', 'gmail_read', 'gmail_draft'} <= names
    assert {'calendar_events_list', 'calendar_freebusy', 'calendar_event_create'} <= names


def test_calendar_list_is_bounded_and_discards_unrequested_personal_data():
    args={'time_min':'2027-01-01T09:00:00+03:00', 'time_max':'2027-01-02T09:00:00+03:00'}
    result, calls=execute('calendar_events_list',args,{'items':[{
        'id':'event-1','summary':'Meeting','start':{'dateTime':args['time_min']},
        'end':{'dateTime':args['time_max']},'description':'private','attendees':[{'email':'private@example.com'}],
        'htmlLink':'https://calendar.google.com/calendar/event?eid=123'}], 'timeZone':'Europe/Istanbul'})
    assert len(result['events'])==1
    assert 'description' not in result['events'][0] and 'attendees' not in result['events'][0]
    assert calls[0][0:2]==('GET','/calendar/v3/calendars/primary/events')
    assert calls[0][3]['singleEvents']=='true'
    assert calls[0][3]['maxResults']<=50


def test_calendar_freebusy_reads_one_primary_calendar_without_writing_events():
    args={'time_min':'2027-01-01T09:00:00+03:00','time_max':'2027-01-02T09:00:00+03:00','time_zone':'Europe/Istanbul'}
    result, calls=execute('calendar_freebusy',args,{'calendars':{'primary':{'busy':[{'start':args['time_min'],'end':args['time_max']}]}}})
    assert len(result['busy'])==1
    assert calls[0][0:2]==('POST','/calendar/v3/freeBusy')
    assert calls[0][2]['items']==[{'id':'primary'}]
    assert calls[0][2]['timeZone']=='Europe/Istanbul'


def test_calendar_create_is_one_event_without_guest_invitations():
    args={'title':'Investor discussion','start':'2027-01-01T12:00:00+03:00',
          'end':'2027-01-01T13:00:00+03:00','time_zone':'Europe/Istanbul','location':'Office'}
    result, calls=execute('calendar_event_create',args,{'id':'created-1','summary':args['title']})
    assert result['created'] and result['invitations_sent'] is False
    assert calls[0][0:2]==('POST','/calendar/v3/calendars/primary/events')
    assert calls[0][3]=={'sendUpdates':'none'}
    assert calls[0][2]['start']['timeZone']=='Europe/Istanbul'
    assert 'attendees' not in calls[0][2]


@pytest.mark.parametrize('arguments',[
    {'time_min':'2027-01-01T12:00:00','time_max':'2027-01-02T12:00:00+03:00'},
    {'time_min':'2027-01-02T12:00:00+03:00','time_max':'2027-01-01T12:00:00+03:00'},
    {'time_min':'2027-01-01T12:00:00+03:00','time_max':'2027-04-01T12:00:00+03:00'},
    {'time_min':'2027-01-01T12:00:00+03:00','time_max':'2027-01-02T12:00:00+03:00','time_zone':'Invalid/Somewhere'}
])
def test_bad_calendar_ranges_fail_before_request(arguments):
    name='calendar_freebusy' if 'time_zone' in arguments else 'calendar_events_list'
    with pytest.raises(FigmaError):
        execute(name,arguments,{})


def test_calendar_create_rejects_naive_or_reverse_times():
    for start,end in [
        ('2027-01-01T12:00:00','2027-01-01T13:00:00+03:00'),
        ('2027-01-01T13:00:00+03:00','2027-01-01T12:00:00+03:00'),
        ('2027-01-01T12:00:00+03:00','2027-01-03T13:00:00+03:00')]:
        with pytest.raises(FigmaError):
            execute('calendar_event_create',{'title':'Meeting','start':start,'end':end},{})

def test_calendar_create_rejects_daylight_saving_offset_conflict():
    with pytest.raises(FigmaError):
        execute('calendar_event_create',{'title':'DST gap',
            'start':'2027-03-14T02:30:00-05:00','end':'2027-03-14T03:30:00-05:00',
            'time_zone':'America/New_York'}, {})
