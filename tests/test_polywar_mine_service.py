import sqlite3, sys, uuid, threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import services.polywar_service as polywar
import services.polywar_map_service as m
import services.polywar_mine_service as mines

@pytest.fixture()
def polydb(monkeypatch):
    uri=f"file:polywar_mines_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri,uri=True,check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={}
    def connect():
        c=sqlite3.connect(uri,uri=True,check_same_thread=False,timeout=10); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(polywar,'get_connection',connect)
    monkeypatch.setattr(polywar,'get_setting',lambda k,d='': settings.get(k,d))
    monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); c.close(); yield connect, settings; keeper.close()

def season_secret(connect):
    c=connect(); polywar.ensure_factions(c); s=polywar.ensure_active_season(c); sec=c.execute('select secret_seed from polywar_seasons where id=?',(s['id'],)).fetchone()['secret_seed']; c.close(); return s, sec

def join(uid,fid): return polywar.join_faction(uid,fid)

def near_cell(fid=1):
    bx,by=m.faction_base_positions()[fid]
    return bx+8, by

def find_mine(connect, fid=1):
    s,sec=season_secret(connect); bx,by=m.faction_base_positions()[fid]
    c=connect()
    for r in range(8,400):
        for x,y in ((bx+r,by),(bx,by+r),(bx+r,by+1),(bx+1,by+r)):
            if m.in_bounds(x,y):
                t=m.terrain_at(sec,x,y)
                if m.TERRAIN_COSTS[t] is not None and mines.deterministic_mine_exists(s['id'],sec,x,y,t):
                    c.close(); return s,sec,x,y,t
    c.close(); raise AssertionError('mine not found')

def test_deterministic_mines_season_terrain_and_safe_zones(polydb):
    connect,_=polydb; s,sec=season_secret(connect)
    vals=[mines.deterministic_mine_exists(s['id'],sec,1000+i,1000,'plain') for i in range(500)]
    assert vals == [mines.deterministic_mine_exists(s['id'],sec,1000+i,1000,'plain') for i in range(500)]
    assert vals != [mines.deterministic_mine_exists(s['id']+1,sec+'x',1000+i,1000,'plain') for i in range(500)]
    assert mines.density_bp('ruins') > mines.density_bp('plain') > mines.density_bp('road')
    assert not mines.deterministic_mine_exists(s['id'],sec,123,123,'water')
    assert not mines.deterministic_mine_exists(s['id'],sec,123,123,'river')
    bx,by=m.faction_base_positions()[1]
    assert not mines.deterministic_mine_exists(s['id'],sec,bx,by,'plain')
    assert not mines.deterministic_mine_exists(s['id'],sec,bx+12,by,'plain')

def test_safe_capture_hint_and_legacy_duplicate_current_energy(polydb):
    connect,_=polydb; st=join(101,1); sid=st['season']['id']; x,y=near_cell()
    r=m.capture_cell(101,x,y,'safe'); assert r['ok'] and 0 <= r['adjacent_mines'] <= 8
    c=connect(); c.execute('update polywar_players set current_energy=5 where user_id=101 and season_id=?',(sid,)); c.commit(); c.close()
    dup=m.capture_cell(101,x,y,'safe')
    assert dup['duplicate'] is True and dup['energy']['current_energy']==5
    c=connect(); assert c.execute('select count(*) from polywar_cell_intel where season_id=? and faction_id=1 and intel_type="safe_hint"',(sid,)).fetchone()[0]==1; c.close()

def test_mine_hit_event_neutral_lock_and_no_repeat(polydb):
    connect,_=polydb; s,sec,x,y,t=find_mine(connect); join(102,1)
    bx,by=m.faction_base_positions()[1]
    c=connect(); c.execute('insert into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(s['id'],x-1,y)); c.commit(); c.close()
    r=m.capture_cell(102,x,y,'mine')
    assert r['outcome']=='mine_hit' and r['mine_hit'] and r['energy']['is_locked']
    c=connect(); assert c.execute('select count(*) from polywar_mine_events where season_id=? and x=? and y=?',(s['id'],x,y)).fetchone()[0]==1
    assert c.execute('select count(*) from polywar_cells where season_id=? and x=? and y=?',(s['id'],x,y)).fetchone()[0]==0
    c.execute('update polywar_players set locked_until=NULL where user_id=102 and season_id=?',(s['id'],)); c.commit(); c.close()
    r2=m.capture_cell(102,x,y,'after')
    assert r2['outcome']=='captured'

def test_concurrent_same_capture_key_one_spend_one_outcome(polydb):
    connect,_=polydb; st=join(103,1); sid=st['season']['id']; x,y=near_cell(); out=[]; err=[]
    def worker():
        try: out.append(m.capture_cell(103,x,y,'same'))
        except ValueError as e: err.append(str(e))
    ts=[threading.Thread(target=worker) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert err==[] and len(out)==2 and sorted(bool(r.get('duplicate')) for r in out)==[False,True]
    c=connect(); assert c.execute('select count(*) from polywar_actions where user_id=103 and season_id=?',(sid,)).fetchone()[0]==1
    assert c.execute('select count(*) from polywar_action_outcomes where user_id=103 and season_id=?',(sid,)).fetchone()[0]==1
    assert c.execute('select current_energy from polywar_players where user_id=103 and season_id=?',(sid,)).fetchone()[0]==9; c.close()

def test_concurrent_mine_claim_only_one_hit(polydb):
    connect,_=polydb; s,sec,x,y,t=find_mine(connect); join(104,1); join(105,1)
    c=connect(); c.execute('insert into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(s['id'],x-1,y)); c.commit(); c.close()
    out=[]; err=[]
    def worker(uid,key):
        try: out.append(m.capture_cell(uid,x,y,key))
        except ValueError as e: err.append(str(e))
    ts=[threading.Thread(target=worker,args=(104,'a')), threading.Thread(target=worker,args=(105,'b'))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for r in out if r.get('mine_hit')) == 1
    assert sum(1 for r in out if r.get('energy',{}).get('is_locked')) == 1
    c=connect(); assert c.execute('select count(*) from polywar_mine_events where season_id=? and x=? and y=?',(s['id'],x,y)).fetchone()[0]==1; c.close()

def test_hint_refresh_all_factions_and_intel_isolation(polydb):
    connect,_=polydb; s,sec,x,y,t=find_mine(connect); join(106,1); join(107,2)
    c=connect(); mines.upsert_safe_hint(c,s['id'],1,x-1,y,106,sec); mines.upsert_safe_hint(c,s['id'],2,x-1,y,107,sec); before=[r['adjacent_mines'] for r in c.execute('select adjacent_mines from polywar_cell_intel where x=? and y=?',(x-1,y)).fetchall()]; c.commit(); c.close()
    c=connect(); assert mines.try_trigger_mine(c,s['id'],x,y,106,1,'h'); mines.record_triggered_mine(c,s['id'],1,106,x,y,'h',sec); c.commit(); after=[r['adjacent_mines'] for r in c.execute('select adjacent_mines from polywar_cell_intel where x=? and y=?',(x-1,y)).fetchall()]; c.close()
    assert len(before)==2 and len(after)==2 and all(a <= b for a,b in zip(sorted(after), sorted(before)))
    ch=m.build_chunks(106,[(x//m.chunk_size(),y//m.chunk_size())])['chunks'][0]; assert ch['intel']

def test_scans_idempotency_concurrency_distance_and_secrecy(polydb):
    connect,_=polydb; st=join(108,1); sid=st['season']['id']; x,y=near_cell()
    r=mines.scan_area(108,x,y,3,'s3'); assert r['ok'] and r['size']==3 and 'mine_positions' not in r and 'mines' not in r
    r5=mines.scan_area(108,x,y,5,'s5'); assert r5['ok'] and r5['energy_cost']==4
    dup=mines.scan_area(108,x,y,3,'s3'); assert dup['duplicate'] and dup['energy']['current_energy']==4
    with pytest.raises(ValueError, match='scan_too_far'): mines.scan_area(108,5000,5000,3,'far')
    out=[]
    def worker(): out.append(mines.scan_area(108,x,y,3,'same-scan'))
    ts=[threading.Thread(target=worker) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(bool(r.get('duplicate')) for r in out)==[False,True]
    c=connect(); assert c.execute('select count(*) from polywar_scans where user_id=108 and idempotency_key="same-scan"').fetchone()[0]==1; c.close()

def test_scan_limited_energy_and_offline_recovery(polydb):
    connect,_=polydb; st=join(109,1); sid=st['season']['id']; x,y=near_cell()
    c=connect(); c.execute('update polywar_players set current_energy=2,max_energy=2 where user_id=109 and season_id=?',(sid,)); c.commit(); c.close()
    out=[]; err=[]
    def worker(k):
        try: out.append(mines.scan_area(109,x,y,3,k))
        except ValueError as e: err.append(str(e))
    ts=[threading.Thread(target=worker,args=('a',)),threading.Thread(target=worker,args=('b',))]; [t.start() for t in ts]; [t.join() for t in ts]
    assert len(out)==1 and 'insufficient_energy' in err
    old=datetime.utcnow()-timedelta(minutes=121); c=connect(); c.execute('update polywar_players set current_energy=0,max_energy=2,energy_updated_at=? where user_id=109 and season_id=?',(old,sid)); c.commit(); c.close()
    assert mines.scan_area(109,x,y,3,'recovered')['energy']['current_energy']==0

def test_flags_add_remove_aggregation_isolation_limit_and_delete_after_capture(polydb):
    connect,settings=polydb; settings['polywar_max_flags_per_player']='1'; st=join(110,1); join(111,1); join(112,2); sid=st['season']['id']; x,y=near_cell(); fx,fy=x+1,y
    assert mines.set_flag(110,x,y,True)['ok']; assert mines.set_flag(110,x,y,True)['duplicate']
    with pytest.raises(ValueError, match='flag_limit'): mines.set_flag(110,fx,fy,True)
    assert mines.set_flag(111,x,y,True)['ok']
    ch=m.build_chunks(110,[(x//m.chunk_size(),y//m.chunk_size())])['chunks'][0]; flag=next(f for f in ch['flags'] if f['x']==x and f['y']==y); assert flag['flag_count']==2 and flag['current_user_flagged']
    ch2=m.build_chunks(112,[(x//m.chunk_size(),y//m.chunk_size())])['chunks'][0]; assert ch2['flags']==[]
    m.capture_cell(110,x,y,'cap-flag')
    assert mines.set_flag(110,x,y,False)['ok']
    assert mines.set_flag(112,x,y,False)['ok']
    c=connect(); assert c.execute('select count(*) from polywar_flags where season_id=? and user_id=111',(sid,)).fetchone()[0]==1; c.close()

def test_web_source_strict_boolean_and_client_ignored():
    src=Path('web.py').read_text(); block=src[src.index('async def handle_polywar_flag_api'):src.index('async def handle_webapp_summary')]
    assert 'isinstance(data.get("active"), bool)' in block and 'invalid_active' in block and 'bool(data.get("active"))' not in block
    assert 'data.get("user_id")' not in block and 'data.get("faction_id")' not in block and 'data.get("season_id")' not in block and 'data.get("mine_count")' not in block
