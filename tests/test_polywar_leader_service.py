import sqlite3, uuid
from datetime import datetime, timedelta
import pytest

import services.polywar_service as polywar
import services.polywar_leader_service as leaders
import services.polywar_governance_service as gov

@pytest.fixture
def polydb(monkeypatch):
    uri=f"file:polywar_leader_{uuid.uuid4().hex}?mode=memory&cache=shared"; keeper=sqlite3.connect(uri,uri=True,check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={'polywar_leader_refresh_seconds':'0'}
    def connect(): c=sqlite3.connect(uri,uri=True,check_same_thread=False); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(polywar,'get_connection',connect); monkeypatch.setattr(polywar,'get_setting',lambda k,d='': settings.get(k,d)); monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); c.close(); yield connect,settings; keeper.close()

def _join(uid,fid): return polywar.join_faction(uid,fid)
def _set(connect,sid,uid,contrib=0,last=None,joined=None,fid=None,banned=None):
    c=connect(); last=last or datetime.utcnow(); joined=joined or last-timedelta(minutes=1)
    if fid is not None: c.execute('update polywar_players set faction_id=? where user_id=? and season_id=?',(fid,uid,sid))
    c.execute('update polywar_players set faction_contribution=?, last_active_at=?, joined_at=? where user_id=? and season_id=?',(contrib,last,joined,uid,sid))
    if banned is not None: c.execute('insert or ignore into users (user_id,username,first_name,is_banned) values (?,?,?,?)',(uid,'u'+str(uid),'User',1 if banned else 0)); c.execute('update users set is_banned=? where user_id=?',(1 if banned else 0,uid))
    c.commit(); c.close()

def test_highest_contribution_each_faction_and_ignores_system(polydb):
    connect,_=polydb; st=_join(1,1); _join(2,1); _join(3,2); sid=st['season']['id']; _set(connect,sid,1,5); _set(connect,sid,2,9); _set(connect,sid,3,7)
    c=connect(); leaders.refresh_all_faction_leaders_in_transaction(c,sid,force=True) if False else None; l1=leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True); l2=leaders.refresh_faction_leader_in_transaction(c,sid,2,force=True); c.commit()
    assert l1['user_id']==2 and l2['user_id']==3
    assert leaders.refresh_faction_leader_in_transaction(c,sid,8,force=True) is None; c.close()

def test_season_banned_inactive_zero_and_vacant(polydb):
    connect,_=polydb; st=_join(10,1); _join(11,1); sid=st['season']['id']; old=datetime.utcnow()-timedelta(days=8)
    _set(connect,sid,10,100,last=old); _set(connect,sid,11,0,banned=False)
    c=connect(); assert leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True)['user_id']==11; c.commit(); c.close()
    _set(connect,sid,11,0,banned=True); c=connect(); assert leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True) is None; c.close()

def test_tie_breaking_deterministic(polydb):
    connect,_=polydb; st=_join(20,1); _join(21,1); _join(22,1); sid=st['season']['id']; now=datetime.utcnow()
    _set(connect,sid,20,5,last=now,joined=now-timedelta(days=1)); _set(connect,sid,21,5,last=now+timedelta(seconds=1),joined=now-timedelta(days=2)); _set(connect,sid,22,5,last=now,joined=now-timedelta(days=3))
    c=connect(); assert leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True)['user_id']==21; c.commit(); c.close()
    _set(connect,sid,21,5,last=now,joined=now-timedelta(days=1)); c=connect(); assert leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True)['user_id']==22; c.commit(); c.close()
    _set(connect,sid,20,5,last=now,joined=now-timedelta(days=3)); c=connect(); assert leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True)['user_id']==20
    assert leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True)['user_id']==20; c.close()

def test_leader_change_history_and_order_permission(polydb):
    connect,_=polydb; st=_join(30,1); _join(31,1); sid=st['season']['id']; _set(connect,sid,30,3); _set(connect,sid,31,1)
    assert gov.get_governance(30)['current_user_is_leader'] is True
    with pytest.raises(ValueError, match='leader_required'): gov.upsert_order(31,None,'rally',0,0,'',True)
    _set(connect,sid,31,9); assert gov.get_governance(31)['current_user_is_leader'] is True
    with pytest.raises(ValueError, match='leader_required'): gov.upsert_order(30,None,'rally',0,0,'',True)
    c=connect(); n=c.execute("select count(*) from polywar_faction_leader_history where season_id=? and faction_id=1",(sid,)).fetchone()[0]; assert n==2
    before=c.execute("select count(*) from polywar_events where event_type like 'faction_leader_%'").fetchone()[0]; leaders.refresh_faction_leader_in_transaction(c,sid,1,force=True); after=c.execute("select count(*) from polywar_events where event_type like 'faction_leader_%'").fetchone()[0]; assert before==after; c.close()
