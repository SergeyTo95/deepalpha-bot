import sqlite3, uuid, threading
from datetime import datetime, timedelta
import pytest

import services.polywar_service as polywar
import services.polywar_map_service as m
import services.polywar_combat_service as combat
import services.polywar_capital_service as caps

@pytest.fixture
def polydb(monkeypatch):
    uri=f"file:polywar_capitals_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri,uri=True,check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={}
    def connect():
        c=sqlite3.connect(uri,uri=True,check_same_thread=False); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(polywar,'get_connection',connect)
    monkeypatch.setattr(polywar,'get_setting',lambda k,d='': settings.get(k,d))
    monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); c.close(); yield connect,settings; keeper.close()

def join(uid,fid): return polywar.join_faction(uid,fid)

def full_energy(connect,sid,*uids):
    c=connect()
    for uid in uids: c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=? and season_id=?',(uid,sid))
    c.commit(); c.close()

def test_seven_capitals_once_and_no_duplicate_stats(polydb):
    connect,_=polydb; st=join(1,1); sid=st['season']['id']; c=connect()
    caps.ensure_capitals_initialized(c,sid); first=c.execute('select count(*) from polywar_capitals where season_id=?',(sid,)).fetchone()[0]; counts1=[tuple(r) for r in c.execute('select faction_id,controlled_capitals_count from polywar_faction_season_stats where season_id=? order by faction_id',(sid,))]
    caps.ensure_capitals_initialized(c,sid); second=c.execute('select count(*) from polywar_capitals where season_id=?',(sid,)).fetchone()[0]; counts2=[tuple(r) for r in c.execute('select faction_id,controlled_capitals_count from polywar_faction_season_stats where season_id=? order by faction_id',(sid,))]
    assert first==second==7 and counts1==counts2 and sum(n for _,n in counts2)==7
    c.close()

def test_concurrent_initialization_and_phase4_captured_base_migration(polydb):
    connect,_=polydb; st=join(10,1); join(11,2); sid=st['season']['id']; bx,by=m.faction_base_positions()[2]
    c=connect(); c.execute('delete from polywar_capital_initializations where season_id=?',(sid,)); c.execute('delete from polywar_capitals where season_id=?',(sid,)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx,by)); c.commit(); c.close()
    errs=[]
    def run():
        try:
            c=connect()
            for i in range(20):
                try:
                    c.execute('BEGIN IMMEDIATE'); break
                except Exception:
                    import time; time.sleep(0.02)
            caps.ensure_capitals_initialized(c,sid); c.commit(); c.close()
        except Exception as e: errs.append(e)
    threads=[threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    c=connect(); cap=c.execute('select * from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone(); cell=c.execute('select owner_faction_id from polywar_cells where season_id=? and x=? and y=?',(sid,bx,by)).fetchone()[0]
    assert not errs and cap['controller_faction_id']==1 and cell==1 and c.execute('select count(*) from polywar_capital_initializations where season_id=?',(sid,)).fetchone()[0]==1
    c.close()

def test_attack_and_reinforce_capital_blocked_but_ordinary_combat_works(polydb):
    connect,_=polydb; st=join(20,1); join(21,2); sid=st['season']['id']; full_energy(connect,sid,20,21)
    bx,by=m.faction_base_positions()[2]; c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close()
    with pytest.raises(ValueError, match='capital_requires_siege'): combat.combat_action(20,'attack',bx,by,'cap-a')
    c=connect(); c.execute('update polywar_capitals set siege_progress=50, besieging_faction_id=1 where season_id=? and original_faction_id=2',(sid,)); c.commit(); c.close()
    with pytest.raises(ValueError, match='capital_requires_repair'): combat.combat_action(21,'reinforce',bx,by,'cap-r')
    ox,oy=bx+5,by; c=connect(); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id,contest_progress,contesting_faction_id) values (?,?,?,?,0,NULL)',(sid,ox,oy,2)); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,ox-1,oy)); c.commit(); c.close()
    assert combat.combat_action(20,'attack',ox,oy,'ord-a')['outcome']=='attack_progress'
    assert combat.combat_action(21,'reinforce',ox,oy,'ord-r')['outcome'] in {'reinforced','contest_cleared'}

def test_siege_rival_repair_capture_idempotency_and_live_energy(polydb):
    connect,settings=polydb; settings['polywar_capital_siege_required']='200'; settings['polywar_capital_siege_progress_per_action']='100'; settings['polywar_capital_repair_progress_per_action']='50'
    st=join(30,1); join(31,2); join(32,3); sid=st['season']['id']; full_energy(connect,sid,30,31,32); bx,by=m.faction_base_positions()[2]
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,3)',(sid,bx,by+1)); c.commit(); c.close()
    r1=caps.capital_action(30,'siege',bx,by,'s1'); assert r1['outcome']=='siege_started' and r1['capital']['besieging_faction_id']==1 and r1['capital']['siege_progress_after']==100
    dup=caps.capital_action(30,'siege',bx,by,'s1'); assert dup['duplicate'] and dup['capital']['siege_progress_after']==100
    rr=caps.capital_action(32,'siege',bx,by,'s3'); assert rr['outcome']=='rival_siege_cleared' and rr['capital']['besieging_faction_id'] is None and rr['capital']['siege_progress_after']==0
    caps.capital_action(30,'siege',bx,by,'s4'); rep=caps.capital_action(31,'repair_capital',bx,by,'rp'); assert rep['outcome']=='capital_repaired' and rep['capital']['siege_progress_after']==50
    caps.capital_action(30,'siege',bx,by,'s5'); cap=caps.capital_action(30,'siege',bx,by,'s6'); assert cap['outcome']=='capital_captured' and cap['capital']['controller_faction_id']==1 and cap['capital']['siege_progress_after']==0
    c=connect(); assert c.execute('select controller_faction_id from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()[0]==1; assert c.execute('select owner_faction_id from polywar_cells where season_id=? and x=? and y=?',(sid,bx,by)).fetchone()[0]==1; c.close()

def test_capital_begin_raises_on_locked_sqlite(polydb):
    connect,_=polydb; lock=connect(); lock.execute('BEGIN EXCLUSIVE')
    c=connect()
    with pytest.raises(Exception): caps._begin(c,c.cursor())
    lock.rollback(); lock.close(); c.close()

def test_legacy_contested_capital_migration_clears_contest(polydb):
    connect,_=polydb; st=join(40,1); join(41,2); sid=st['season']['id']; bx,by=m.faction_base_positions()[2]
    c=connect(); c.execute('delete from polywar_capital_initializations where season_id=?',(sid,)); c.execute('delete from polywar_capitals where season_id=?',(sid,)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id,contesting_faction_id,contest_progress,contested_at) values (?,?,?,?,?,?,?)',(sid,bx,by,1,2,50,datetime.utcnow())); c.commit(); c.close()
    caps.get_capitals(40)
    c=connect(); row=c.execute('select owner_faction_id,contesting_faction_id,contest_progress,contested_at from polywar_cells where season_id=? and x=? and y=?',(sid,bx,by)).fetchone(); assert row['owner_faction_id']==1 and row['contesting_faction_id'] is None and row['contest_progress']==0 and row['contested_at'] is None; c.close()

def test_capital_duplicate_does_not_consume_mutation_rate(polydb, monkeypatch):
    connect,_=polydb; st=join(50,1); join(51,2); sid=st['season']['id']; full_energy(connect,sid,50); bx,by=m.faction_base_positions()[2]
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close()
    calls=[]; monkeypatch.setattr(caps,'_rate_mut',lambda uid: calls.append(uid))
    caps.capital_action(50,'siege',bx,by,'same'); caps.capital_action(50,'siege',bx,by,'same')
    assert calls==[50]

def test_concurrent_same_key_siege_and_repair(polydb):
    connect,settings=polydb; settings['polywar_capital_siege_required']='400'; st=join(60,1); join(61,2); sid=st['season']['id']; full_energy(connect,sid,60,61); bx,by=m.faction_base_positions()[2]
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close()
    out=[]
    def siege():
        try: out.append(caps.capital_action(60,'siege',bx,by,'ck'))
        except Exception as e: out.append(e)
    ts=[threading.Thread(target=siege) for _ in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for r in out if isinstance(r,dict) and r.get('duplicate'))>=1
    c=connect(); assert c.execute('select siege_progress from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()[0]==100; c.close()
    out.clear()
    def repair():
        try: out.append(caps.capital_action(61,'repair_capital',bx,by,'rk'))
        except Exception as e: out.append(e)
    ts=[threading.Thread(target=repair) for _ in range(3)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute('select siege_progress from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()[0] in (25,100); c.close()

def test_public_build_chunks_concurrent_initialization(polydb):
    connect,_=polydb; st=join(70,1); sid=st['season']['id']; errs=[]
    def run():
        try: m.build_chunks(70, [(0,0)])
        except Exception as e: errs.append(e)
    ts=[threading.Thread(target=run) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert not errs and c.execute('select count(*) from polywar_capitals where season_id=?',(sid,)).fetchone()[0]==7; c.close()

def test_capital_event_cooldown_and_recapture(polydb):
    connect,settings=polydb; settings['polywar_capital_event_cooldown_seconds']='999'; settings['polywar_capital_siege_required']='100'; st=join(80,1); join(81,2); sid=st['season']['id']; full_energy(connect,sid,80,81); bx,by=m.faction_base_positions()[2]
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,bx+1,by)); c.commit(); c.close()
    caps.capital_action(80,'siege',bx,by,'cap1')
    c=connect(); assert c.execute("select count(*) from polywar_events where season_id=? and event_type='capital_captured'",(sid,)).fetchone()[0]==1; c.close()
    caps.capital_action(81,'siege',bx,by,'recap')
    c=connect(); assert c.execute('select controller_faction_id from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()[0]==2; assert c.execute("select count(*) from polywar_events where season_id=? and event_type='capital_recaptured'",(sid,)).fetchone()[0]==1; c.close()

def test_concurrent_get_capitals_retries_under_sqlite_contention(polydb):
    connect,_=polydb; st=join(90,1); sid=st['season']['id']; out=[]
    lock=connect(); lock.execute('BEGIN IMMEDIATE')
    def call():
        try: out.append(caps.get_capitals(90))
        except Exception as e: out.append(e)
    t=threading.Thread(target=call); t.start()
    import time; time.sleep(0.08); lock.commit(); lock.close(); t.join()
    assert len(out)==1 and isinstance(out[0],dict) and out[0]['ok']
    c=connect(); assert c.execute('select count(*) from polywar_capitals where season_id=?',(sid,)).fetchone()[0]==7; c.close()

def test_concurrent_same_key_siege_strict_once(polydb):
    connect,settings=polydb; settings['polywar_capital_siege_required']='400'; st=join(91,1); join(92,2); sid=st['season']['id']; full_energy(connect,sid,91); bx,by=m.faction_base_positions()[2]
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close()
    out=[]
    def run():
        try: out.append(caps.capital_action(91,'siege',bx,by,'strict-siege'))
        except Exception as e: out.append(e)
    ts=[threading.Thread(target=run) for _ in range(5)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert not [r for r in out if isinstance(r,Exception)]
    normal=[r for r in out if isinstance(r,dict) and not r.get('duplicate')]; dups=[r for r in out if isinstance(r,dict) and r.get('duplicate')]
    assert len(normal)==1 and len(dups)==4
    c=connect();
    assert c.execute('select siege_progress from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()[0]==100
    assert c.execute('select count(*) from polywar_action_outcomes where season_id=? and user_id=? and idempotency_key=?',(sid,91,'strict-siege')).fetchone()[0]==1
    assert c.execute('select current_energy from polywar_players where season_id=? and user_id=?',(sid,91)).fetchone()[0] < 50
    c.close()

def test_concurrent_same_key_repair_strict_once(polydb):
    connect,settings=polydb; settings['polywar_capital_repair_progress_per_action']='75'; st=join(93,1); join(94,2); sid=st['season']['id']; full_energy(connect,sid,94); bx,by=m.faction_base_positions()[2]
    c=connect(); caps.ensure_capitals_initialized(c,sid); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,bx+1,by)); c.execute('update polywar_capitals set siege_progress=100, besieging_faction_id=1 where season_id=? and original_faction_id=2',(sid,)); before_energy=c.execute('select current_energy from polywar_players where season_id=? and user_id=?',(sid,94)).fetchone()[0]; c.commit(); c.close()
    out=[]
    def run():
        try: out.append(caps.capital_action(94,'repair_capital',bx,by,'strict-repair'))
        except Exception as e: out.append(e)
    ts=[threading.Thread(target=run) for _ in range(5)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert not [r for r in out if isinstance(r,Exception)]
    normal=[r for r in out if isinstance(r,dict) and not r.get('duplicate')]; dups=[r for r in out if isinstance(r,dict) and r.get('duplicate')]
    assert len(normal)==1 and len(dups)==4
    c=connect();
    assert c.execute('select siege_progress from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()[0]==25
    assert c.execute('select count(*) from polywar_action_outcomes where season_id=? and user_id=? and idempotency_key=?',(sid,94,'strict-repair')).fetchone()[0]==1
    assert c.execute('select current_energy from polywar_players where season_id=? and user_id=?',(sid,94)).fetchone()[0] == before_energy - caps.repair_cost()
    c.close()
