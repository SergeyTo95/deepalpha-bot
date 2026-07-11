import sqlite3, uuid
from datetime import datetime, timedelta
import pytest

import services.polywar_service as polywar
import services.polywar_map_service as m
import services.polywar_governance_service as gov
import services.polywar_capital_service as caps

@pytest.fixture
def polydb(monkeypatch):
    uri=f"file:polywar_gov_{uuid.uuid4().hex}?mode=memory&cache=shared"; keeper=sqlite3.connect(uri,uri=True,check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={}
    def connect(): c=sqlite3.connect(uri,uri=True,check_same_thread=False); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(polywar,'get_connection',connect); monkeypatch.setattr(polywar,'get_setting',lambda k,d='': settings.get(k,d)); monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); c.close(); yield connect,settings; keeper.close()

def join(uid,fid): return polywar.join_faction(uid,fid)

def contribute(connect,sid,*uids):
    c=connect()
    for uid in uids: c.execute('update polywar_players set faction_contribution=10 where user_id=? and season_id=?',(uid,sid))
    c.commit(); c.close()

def elect_now(connect,sid,fid,commander):
    c=connect(); now=datetime.utcnow(); c.execute('update polywar_faction_season_stats set commander_user_id=?, commander_since=?, commander_term_ends_at=? where season_id=? and faction_id=?',(commander,now,now+timedelta(hours=5),sid,fid)); c.commit(); c.close()

def test_election_nomination_vote_tiebreak_and_multiple_finalized(polydb):
    connect,_=polydb; st=join(101,1); join(102,1); sid=st['season']['id']; contribute(connect,sid,101,102)
    g=gov.get_governance(101); assert g['active_election']
    assert gov.nominate(101,'alpha',True)['current_user_is_candidate']
    dup=gov.nominate(101,'alpha',True); assert dup.get('duplicate') is True
    gov.nominate(102,'beta',True); gov.vote(101,102); same=gov.vote(101,102); assert same.get('duplicate') is True
    changed=gov.vote(101,101); assert changed['current_user_vote']==101
    c=connect(); c.execute('update polywar_commander_elections set ends_at=? where season_id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); c.close()
    final=gov.get_governance(101); assert final['commander']['commander_user_id']==101
    c=connect(); c.execute("insert into polywar_commander_elections (season_id,faction_id,status,starts_at,ends_at,finalized_at,created_at) values (?,?,?,?,?,?,?)",(sid,1,'finalized',datetime.utcnow(),datetime.utcnow(),datetime.utcnow(),datetime.utcnow())); c.commit(); n=c.execute("select count(*) from polywar_commander_elections where season_id=? and faction_id=? and status='finalized'",(sid,1)).fetchone()[0]; c.close(); assert n>=2

def test_nomination_strict_statement_withdraw_and_cross_faction_vote(polydb):
    connect,_=polydb; st=join(111,1); join(112,1); join(113,2); sid=st['season']['id']; contribute(connect,sid,111,112,113)
    gov.get_governance(111)
    with pytest.raises(ValueError, match='invalid_statement'): gov.nominate(111,'x'*999,True)
    gov.nominate(111,'ok',True); w=gov.nominate(111,'',False); assert w['current_user_is_candidate'] is False
    w2=gov.nominate(111,'',False); assert w2.get('duplicate') is True
    gov.get_governance(113)
    with pytest.raises(ValueError): gov.vote(113,111)

def test_commander_term_expiry_creates_event_and_new_election(polydb):
    connect,_=polydb; st=join(121,1); join(122,1); sid=st['season']['id']; elect_now(connect,sid,1,121)
    c=connect(); c.execute('update polywar_faction_season_stats set commander_term_ends_at=? where season_id=? and faction_id=1',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); c.close()
    g=gov.get_governance(121); assert not g['commander']['commander_user_id'] and g['active_election']
    c=connect(); assert c.execute("select count(*) from polywar_events where season_id=? and event_type='commander_term_ended'",(sid,)).fetchone()[0]==1; c.close()

def test_order_create_update_cancel_limit_visibility_and_target_validation(polydb):
    connect,settings=polydb; settings['polywar_commander_order_limit']='1'; st=join(131,1); join(132,1); join(133,2); sid=st['season']['id']; elect_now(connect,sid,1,131)
    bx,by=m.faction_base_positions()[2]; c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close(); caps.get_capitals(131)
    with pytest.raises(ValueError, match='invalid_order_target'): gov.upsert_order(131,None,'rally',bx,by,'bad',True)
    created=gov.upsert_order(131,None,'siege',bx,by,'take capital',True); assert len(created['orders'])==1; oid=created['orders'][0]['id']
    with pytest.raises(ValueError, match='order_limit'): gov.upsert_order(131,None,'siege',bx,by,'second',True)
    updated=gov.upsert_order(131,oid,'siege',bx,by,'updated',True); assert updated['orders'][0]['message']=='updated'
    cancelled=gov.upsert_order(131,oid,'siege',bx,by,'',False); assert cancelled['orders']==[]
    dup=gov.upsert_order(131,oid,'siege',bx,by,'',False); assert dup.get('duplicate') is True
    assert gov.get_governance(133).get('orders',[])==[]

def test_frontend_phase5_real_ui_source():
    js=open('webapp/polywar.js',encoding='utf-8').read()
    for token in ['polywarCapitalUi','/api/polywar/capitals','Siege capital','polywarGovernanceUi','data-polywar-vote','data-polywar-goto-order']:
        assert token in js
