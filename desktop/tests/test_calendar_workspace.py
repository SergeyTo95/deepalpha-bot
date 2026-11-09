"""Google Calendar uses the existing owner-scoped Google connection and grants."""
import asyncio

import pytest

from desktop.figma_connector import FigmaError
from desktop.workspace_connections import GOOGLE_TOOLS, WorkspaceConnector


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
