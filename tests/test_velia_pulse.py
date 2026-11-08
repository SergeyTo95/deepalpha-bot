import asyncio
import json
import sqlite3
import uuid
from datetime import datetime,timezone
from types import SimpleNamespace
from aiohttp import web,ClientSession
from aiohttp.test_utils import TestServer
import pytest
from services import velia_pulse_service as pulse
from services import velia_pulse_routes as routes

BASE=int(datetime(2026,11,8,9,tzinfo=timezone.utc).timestamp())
@pytest.fixture
def database(monkeypatch,tmp_path):
    path=str(tmp_path/'pulse.sqlite');clock=[BASE]
    class Cursor:
        def __init__(self,c):self.c=c
        def execute(self,sql,args=()):return self.c.execute(sql.replace('%s','?').replace(' FOR UPDATE SKIP LOCKED','').replace(' FOR UPDATE',''),args)
        def fetchone(self):return self.c.fetchone()
        def fetchall(self):return self.c.fetchall()
        def close(self):self.c.close()
    class Connection:
        def __init__(self):self.c=sqlite3.connect(path)
        def cursor(self):return Cursor(self.c.cursor())
        def commit(self):self.c.commit()
        def rollback(self):self.c.rollback()
        def close(self):self.c.close()
    monkeypatch.setattr(pulse,'get_connection',Connection);monkeypatch.setattr(pulse,'_READY',False);monkeypatch.setattr(pulse,'now',lambda:clock[0]);monkeypatch.setenv('MASTER_ENCRYPTION_KEY','stable-pulse-test-secret-never-production');monkeypatch.setenv('VELIA_PULSE_ENABLED','false')
    return path,clock

def draft(**patch):return {'id':str(uuid.uuid4()),'title':'Проверить оплату 5000 лир','timezone':'UTC','schedule':{'kind':'once','date':'2026-11-08','time':'10:00'},**patch}
def complete(item,**patch):return {'action':'complete','revision':item['revision'],'occurrence_at':item.get('action_at',item['due_at']),**patch}

def test_timezone_dst_recurrence_and_quiet_hours():
    with pytest.raises(pulse.PulseError):pulse.local_epoch('2026-03-08','02:30','America/New_York')
    first=pulse.local_epoch('2026-11-01','01:30','America/New_York');assert datetime.fromtimestamp(first,timezone.utc).hour==5
    after=pulse.local_epoch('2026-03-07','09:00','America/New_York')
    assert pulse.next_local({'kind':'daily','time':'09:00'},'America/New_York',after)-after==23*3600
    prefs={**pulse.DEFAULTS,'timezone':'Europe/Istanbul'};at=pulse.local_epoch('2026-11-08','23:00','Europe/Istanbul')
    assert pulse.quiet_time(at,prefs)==pulse.local_epoch('2026-11-09','08:00','Europe/Istanbul')
    assert pulse.quiet_time(at,prefs,False)==at

@pytest.mark.parametrize('second',[-1,60,True,1.5,'23'])
def test_invalid_seconds_are_rejected(second):
    with pytest.raises(pulse.PulseError):pulse.local_epoch('2026-11-08','09:01','UTC',second)

def test_second_precise_snooze_and_minute_only_compatibility(database):
    _,clock=database;clock[0]+=23;item=pulse.create(7,draft())
    moved=pulse.action(7,item['item_id'],{**complete(item),'action':'snooze','date':'2026-11-08','time':'09:01','second':23})
    assert moved['due_at']==clock[0]+60 and moved['schedule']['second']==23
    moved=pulse.action(7,item['item_id'],{**complete(moved),'action':'snooze','date':'2026-11-08','time':'09:02'})
    assert moved['due_at']==BASE+120 and 'second' not in moved['schedule']

def test_idempotent_creation_ownership_and_encrypted_storage(database):
    path,clock=database;data=draft();item=pulse.create(7,data);assert pulse.create(7,data)['item_id']==item['item_id'];assert not pulse.listing(8)['items']
    with pytest.raises(pulse.PulseError) as error:pulse.action(8,item['item_id'],complete(item))
    assert error.value.status==404
    with pytest.raises(pulse.PulseError):pulse.create(7,{**data,'title':'Изменённое условие'})
    with sqlite3.connect(path) as c:stored=c.execute('SELECT payload FROM velia_pulse_items').fetchone()[0]
    assert '5000' not in stored and pulse.unpack(7,stored)['title']==data['title']
    with pytest.raises(Exception):pulse.unpack(8,stored)

def test_due_delivery_and_completion_are_separate_and_tick_is_idempotent(database):
    _,clock=database;item=pulse.create(7,draft());clock[0]+=3600;pulse.tick();pulse.tick()
    data=pulse.listing(7);assert len(data['notifications'])==1;assert data['items'][0]['state']=='active';assert data['notifications'][0]['delivery']=='off'
    notice=data['notifications'][0];pulse.read_notification(8,notice['id']);assert pulse.listing(7)['notifications'][0]['status']=='unread'
    pulse.read_notification(7,notice['id']);assert pulse.listing(7)['notifications'][0]['status']=='read'
    done=pulse.action(7,item['item_id'],complete(data['items'][0],result='Получено'));assert done['state']=='done'
    pulse.action(7,item['item_id'],complete(data['items'][0]));assert pulse.listing(7)['notifications'][0]['result']=='Получено'

def test_repeat_completion_preserves_series_and_followup_is_bounded(database):
    _,clock=database;item=pulse.create(7,draft(schedule={'kind':'daily','time':'10:00'},followup_minutes=5));clock[0]+=3600;pulse.tick();clock[0]+=301;pulse.tick();pulse.tick()
    data=pulse.listing(7);assert len(data['notifications'])==2;assert data['items'][0]['due_at']>clock[0]
    pulse.action(7,item['item_id'],complete(data['items'][0]));assert pulse.listing(7)['items'][0]['state']=='active'
    clock[0]+=3600;pulse.tick();assert len(pulse.listing(7)['notifications'])==2

def test_snooze_cas_and_early_completion(database):
    _,clock=database;item=pulse.create(7,draft());moved=pulse.action(7,item['item_id'],{'action':'snooze','revision':item['revision'],'occurrence_at':item['due_at'],'date':'2026-11-08','time':'11:00','timezone':'UTC'})
    with pytest.raises(pulse.PulseError):pulse.action(7,item['item_id'],complete(item))
    assert pulse.action(7,item['item_id'],complete(moved))['state']=='done';clock[0]+=7200;pulse.tick();assert pulse.listing(7)['notifications'][0]['status']=='done'

def test_dependency_activates_only_on_confirmation(database):
    _,clock=database;parent=pulse.create(7,draft());child=pulse.create(7,draft(after_id=parent['item_id'],after_minutes=15));assert child['state']=='waiting'
    clock[0]+=3600;pulse.tick();assert next(p for p in pulse.listing(7)['items'] if p['item_id']==child['item_id'])['state']=='waiting'
    live=next(p for p in pulse.listing(7)['items'] if p['item_id']==parent['item_id']);pulse.action(7,parent['item_id'],complete(live))
    child=next(p for p in pulse.listing(7)['items'] if p['item_id']==child['item_id']);assert child['state']=='active' and child['due_at']==clock[0]+900
    with pytest.raises(pulse.PulseError):pulse.create(8,draft(after_id=parent['item_id']))

def test_quiet_time_digest_and_telegram_outbox_claim(database):
    _,clock=database;pulse.settings(7,{'telegram':True,'digest':True,'digest_time':'09:30'})
    item=pulse.create(7,draft());clock[0]+=1800;pulse.tick();notice=pulse.claim_delivery();assert notice['title'].startswith('План на') and not notice['preview'];pulse.delivery_result(7,notice['id'],'sent');assert pulse.claim_delivery() is None
    clock[0]+=1800;pulse.tick();notice=pulse.claim_delivery();assert notice['title']==item['title'];assert pulse.claim_delivery() is None
    clock[0]+=181;pulse.tick();assert next(n for n in pulse.listing(7)['notifications'] if n['id']==notice['id'])['delivery']=='unknown'
    assert pulse.claim_delivery() is None

def test_routes_guard_auth_and_disable_unavailable_telegram(database,monkeypatch):
    monkeypatch.setattr(routes,'telegram_available',lambda:False)
    async def run():
        module=SimpleNamespace(_mobile_api_available=lambda:True,_require_mobile_auth=lambda r:{'user_id':7} if r.headers.get('X-User') else None,_json_response=lambda data,status=200:web.json_response(data,status=status))
        app=web.Application();routes.setup_pulse_routes(app,module)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/mobile-api/v1/pulse');headers={'X-User':'7'}
            assert (await client.get(url)).status==401
            response=await client.post(url,json=draft(),headers=headers);assert response.status==200
            assert response.headers['Cache-Control']=='no-store'
            assert len((await (await client.get(url,headers=headers)).json())['items'])==1
            response=await client.patch(str(url)+'/settings',json={'telegram':True},headers=headers);assert response.status==409
    asyncio.run(run())

def test_default_timezone_and_opt_out_recalculate_pending_delivery(database):
    _,clock=database;pulse.listing(7)
    item=pulse.create(7,draft(timezone='Europe/Istanbul',schedule={'kind':'once','date':'2026-11-08','time':'23:00'}))
    assert pulse.settings(7)['timezone']=='Europe/Istanbul'
    assert item['notify_at']>item['due_at']
    pulse.settings(7,{'quiet':False});assert pulse.listing(7)['items'][0]['notify_at']==item['due_at']

def test_cancelled_parent_cancels_waiting_chain_and_delete_does_not_cross_users(database):
    parent=pulse.create(7,draft());child=pulse.create(7,draft(after_id=parent['item_id']));grand=pulse.create(7,draft(after_id=child['item_id']))
    pulse.action(7,parent['item_id'],{'action':'cancel','revision':parent['revision']})
    assert all(p['state']=='cancelled' for p in pulse.listing(7)['items'])
    with pytest.raises(pulse.PulseError):pulse.delete(8,parent['item_id'])
    pulse.delete(7,parent['item_id']);assert len(pulse.listing(7)['items'])==2

def test_private_telegram_preview_uses_only_the_authenticated_user(database,monkeypatch):
    from services import velia_telegram_runtime as runtime
    import db.database as db
    sent=[]
    class FakeRuntime:
        def __init__(self,*args):pass
        async def send(self,user,text,**kwargs):sent.append((user,text,kwargs))
    monkeypatch.setattr(routes,'telegram_available',lambda:True);monkeypatch.setattr(routes.telegram,'web_url',lambda:'https://velia.example/');monkeypatch.setattr(routes.telegram,'dedicated_token',lambda:'fake-token');monkeypatch.setattr(runtime,'Runtime',FakeRuntime);monkeypatch.setattr(db,'is_user_banned',lambda user:False)
    async def run():
        notice={'user_id':7,'id':str(uuid.uuid4()),'title':'Оплата 5000 лир','preview':False}
        assert await routes._deliver(None,notice)=='sent';assert sent[0][0]==7 and '5000' not in sent[0][1]
        assert 'pulse=' in sent[0][2]['reply_markup']['inline_keyboard'][0][0]['url']
    asyncio.run(run())

def test_worker_lifecycle_ticks_without_telegram_and_stops_cleanly(monkeypatch):
    import time
    ticks=[]
    monkeypatch.setattr(pulse,'enabled',lambda:True);monkeypatch.setattr(pulse,'tick',lambda:ticks.append(time.monotonic()));monkeypatch.setattr(routes,'telegram_available',lambda:False)
    async def run():
        app=web.Application();app.cleanup_ctx.append(routes.lifecycle)
        async with TestServer(app):
            for _ in range(20):
                if ticks:break
                await asyncio.sleep(.01)
            assert ticks
    asyncio.run(run())

def test_read_but_uncompleted_tasks_remain_actionable_after_notification_retention(database):
    _,clock=database;item=pulse.create(7,draft());clock[0]+=3600;pulse.tick();pulse.read_notification(7,pulse.listing(7)['notifications'][0]['id'])
    clock[0]+=91*86400;pulse.tick();state=pulse.listing(7);assert state['notifications'][0]['status']=='read';assert state['items'][0]['action_at'] is not None
    done=pulse.action(7,item['item_id'],complete(state['items'][0],result='Позднее выполнение'));assert done['result']=='Позднее выполнение'
    clock[0]+=91*86400;pulse.tick();assert pulse.listing(7)['items'][0]['result']=='Позднее выполнение'

def test_create_retry_after_due_time_returns_original_without_rescheduling(database):
    _,clock=database;data=draft();item=pulse.create(7,data);clock[0]+=3601;pulse.tick()
    same=pulse.create(7,data);assert same['item_id']==item['item_id'];assert same['due_at'] is None;assert len(pulse.listing(7)['notifications'])==1
    with pytest.raises(pulse.PulseError):pulse.create(7,draft())


def test_overdue_digest_in_dst_gap_does_not_stall_other_users(database):
    _,clock=database;clock[0]=pulse.local_epoch('2027-03-13','00:00','UTC')
    pulse.settings(7,{'timezone':'America/New_York','quiet':False,'digest':True,'digest_time':'02:30'})
    pulse.create(8,draft(schedule={'kind':'once','date':'2027-03-14','time':'11:00'}))
    clock[0]=pulse.local_epoch('2027-03-14','16:00','UTC');pulse.tick();pulse.tick()
    digest=pulse.listing(7)['notifications'];assert len(digest)==1
    assert digest[0]['occurrence_at']==pulse.local_epoch('2027-03-14','03:00','America/New_York')
    assert len(pulse.listing(8)['notifications'])==1
