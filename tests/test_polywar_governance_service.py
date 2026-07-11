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

def test_governance_context_keeps_transaction_active_and_pg_insert_source(polydb):
    connect,_=polydb; st=join(201,1); sid=st['season']['id']; c=connect(); gov._begin(c,c.cursor()); p=gov._governance_context_in_transaction(c,201,sid); assert p['user_id']==201 and c.in_transaction; c.rollback(); c.close()
    src=open('services/polywar_governance_service.py',encoding='utf-8').read(); assert 'ON CONFLICT DO NOTHING' in src and 'INSERT OR IGNORE INTO polywar_commander_elections' in src and 'except Exception:\n        pass' not in src

def test_concurrent_finalization_and_term_expiry_single_events(polydb):
    connect,_=polydb; st=join(211,1); join(212,1); sid=st['season']['id']; contribute(connect,sid,211,212); gov.get_governance(211); gov.nominate(211,'a',True); gov.vote(212,211)
    c=connect(); c.execute('update polywar_commander_elections set ends_at=? where season_id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); c.close()
    out=[]
    def fin():
        try: out.append(gov.get_governance(211))
        except Exception as e: out.append(e)
    import threading
    ts=[threading.Thread(target=fin) for _ in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute("select count(*) from polywar_events where season_id=? and event_type='commander_elected'",(sid,)).fetchone()[0]==1; c.execute('update polywar_faction_season_stats set commander_term_ends_at=? where season_id=? and faction_id=1',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); c.close()
    ts=[threading.Thread(target=fin) for _ in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute("select count(*) from polywar_events where season_id=? and event_type='commander_term_ended'",(sid,)).fetchone()[0]==1; c.close()

def test_concurrent_order_creation_limit_one(polydb):
    connect,settings=polydb; settings['polywar_commander_order_limit']='1'; st=join(221,1); join(222,1); join(223,2); sid=st['season']['id']; elect_now(connect,sid,1,221); bx,by=m.faction_base_positions()[2]; caps.get_capitals(221); c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close()
    out=[]; import threading
    def create():
        try: out.append(gov.upsert_order(221,None,'siege',bx,by,'msg',True))
        except Exception as e: out.append(e)
    ts=[threading.Thread(target=create) for _ in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute('select count(*) from polywar_faction_orders where season_id=? and faction_id=1 and active=1',(sid,)).fetchone()[0]==1; c.close()

def test_frontend_real_phase5_calls_and_xss_escape():
    js=open('webapp/polywar.js',encoding='utf-8').read()
    for token in ['this.refreshCapitals()','this.refreshGovernance()','polywarCapitalUi.draw(ctx','polywarGovernanceUi.drawOrders','id="polywarGovernancePanel"','actionMode === "siege"','actionMode === "repair_capital"','/api/polywar/action','handlePolywarUiClick','data-polywar-vote']:
        assert token in js
    assert 'esc(c.statement' in js and 'esc(o.message' in js
    assert '<img src=x onerror=alert(1)>' not in js

def test_committed_mutation_ignores_exhausted_governance_get_limiter(polydb):
    connect,_=polydb; st=join(231,1); join(232,1); sid=st['season']['id']; contribute(connect,sid,231,232)
    gov.get_governance(231); gov.nominate(231,'cmd',True)
    from collections import deque
    import time
    gov._GET_RATE[232]=deque([time.monotonic()] * gov.GET_RATE_MAX)
    voted=gov.vote(232,231)
    assert voted['ok'] and voted['current_user_vote']==231
    c=connect(); assert c.execute('select candidate_user_id from polywar_commander_votes where voter_user_id=?',(232,)).fetchone()[0]==231; c.close()
    elect_now(connect,sid,1,231)
    bx,by=m.faction_base_positions()[2]; caps.get_capitals(231); c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close()
    gov._GET_RATE[231]=deque([time.monotonic()] * gov.GET_RATE_MAX)
    ordered=gov.upsert_order(231,None,'siege',bx,by,'go',True)
    assert ordered['ok'] and len(ordered['orders'])==1

def test_frontend_latest_phase5_source_guards():
    js=open('webapp/polywar.js',encoding='utf-8').read()
    assert js.count('init();') == 1
    for token in ['refresh(this)','refresh(expectedMap = map)','expectedMap !== map','expectedMap?.destroyed','lastServerTimestamp','data-polywar-create-order','data-polywar-update-order','polywarOrderType','polywarOrderMessage','isCommander ?','canSiege','canRepair','controlled_since','siege_percent','No order selected for edit','Choose an order to edit first']:
        assert token in js


def test_frontend_initial_refresh_and_order_update_source_guards():
    js=open('webapp/polywar.js',encoding='utf-8').read()
    assert 'this.refreshCapitals();\n    this.refreshGovernance();' in js
    assert 'polywarCapitalUi.refresh(this)' in js
    assert 'polywarGovernanceUi.refresh(this)' in js
    assert 'const seq = ++this.seq' in js and 'expectedMap !== map' in js and 'expectedMap?.destroyed' in js
    assert 'owner !== map' not in js
    assert "data-polywar-update-order=\"true\" ${this.editingOrderId ? '' : 'disabled'}" in js
    assert "if (!polywarGovernanceUi.editingOrderId)" in js
    assert "const order_id = null" in js
    assert "const order_id = polywarGovernanceUi.editingOrderId" in js
    assert "order_id = updateOrder ? (polywarGovernanceUi.editingOrderId || null) : null" not in js
    assert 'polywarGovernanceUi.setEditingOrder(null)' in js
