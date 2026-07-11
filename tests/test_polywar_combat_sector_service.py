import sqlite3, sys, uuid, threading, time
from collections import deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import services.polywar_service as polywar
import services.polywar_map_service as m
import services.polywar_combat_service as combat
import services.polywar_sector_service as sectors

@pytest.fixture()
def polydb(monkeypatch):
    uri=f"file:polywar_combat_{uuid.uuid4().hex}?mode=memory&cache=shared"; keeper=sqlite3.connect(uri,uri=True,check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={}
    def connect():
        c=sqlite3.connect(uri,uri=True,check_same_thread=False,timeout=20); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(polywar,'get_connection',connect); monkeypatch.setattr(polywar,'get_setting',lambda k,d='': settings.get(k,d)); monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); c.close(); yield connect,settings; keeper.close()

def join(uid,fid): return polywar.join_faction(uid,fid)
def prep_enemy(connect, sid, x, y, owner=2):
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,?)',(sid,x,y,owner)); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,?)',(sid,x-1,y,1)); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,?)',(sid,x+1,y,owner)); c.execute('update polywar_faction_season_stats set controlled_cells_count=1 where season_id=? and faction_id=?',(sid,owner)); c.execute('update polywar_faction_season_stats set controlled_cells_count=1 where season_id=? and faction_id=1',(sid,)); c.commit(); c.close()

def test_attack_reinforce_transfer_idempotency_and_chunk(polydb):
    connect,settings=polydb; settings['polywar_attack_progress_per_action']='50'; st=join(201,1); join(202,2); sid=st['season']['id']; bx,by=m.faction_base_positions()[1]; x,y=bx+9,by; prep_enemy(connect,sid,x,y)
    r=combat.combat_action(201,'attack',x,y,'a1'); assert r['outcome']=='attack_progress' and r['cell']['contest_progress_after']==50 and r['energy']['current_energy']==8
    dup=combat.combat_action(201,'attack',x,y,'a1'); assert dup['duplicate'] and dup['energy']['current_energy']==8
    ch=m.build_chunks(201,[(x//m.chunk_size(),y//m.chunk_size())])['chunks'][0]; assert any(c['x']==x and c['contest_progress']==50 for c in ch['contested_cells'])
    rr=combat.combat_action(202,'reinforce',x,y,'r1'); assert rr['outcome']=='contest_cleared'
    r2=combat.combat_action(201,'attack',x,y,'a2'); assert r2['outcome']=='attack_progress'
    r3=combat.combat_action(201,'attack',x,y,'a3'); assert r3['outcome']=='territory_captured' and r3['cell']['owner_faction_id']==1 and r3['cell']['contest_progress_after']==0
    c=connect(); row=c.execute('select owner_faction_id,contest_progress,contesting_faction_id from polywar_cells where season_id=? and x=? and y=?',(sid,x,y)).fetchone(); assert row['owner_faction_id']==1 and row['contest_progress']==0 and row['contesting_faction_id'] is None
    assert c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=2',(sid,)).fetchone()[0] >= 0; c.close()

def test_attack_invalid_rules_and_rival_progress(polydb):
    connect,settings=polydb; st=join(211,1); join(212,2); join(213,3); sid=st['season']['id']; bx,by=m.faction_base_positions()[1]; x,y=bx+9,by; prep_enemy(connect,sid,x,y)
    with pytest.raises(ValueError, match='neutral_cell_requires_capture'): combat.combat_action(211,'attack',x+20,y,'neutral')
    with pytest.raises(ValueError, match='own_cell_cannot_be_attacked'): combat.combat_action(211,'attack',x-1,y,'own')
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,x+50,y)); c.commit(); c.close()
    with pytest.raises(ValueError, match='not_frontline'): combat.combat_action(211,'attack',x+50,y,'far')
    r=combat.combat_action(211,'attack',x,y,'p1'); assert r['outcome']=='attack_progress'
    c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,3)',(sid,x,y+1)); c.commit(); c.close()
    r2=combat.combat_action(213,'attack',x,y,'p2'); assert r2['outcome']=='rival_contest_cleared' and r2['cell']['contest_progress_after']==0

def test_sector_api_and_influence(polydb):
    connect,settings=polydb; settings['polywar_sector_min_claimed_cells']='2'; settings['polywar_sector_control_percent']='60'; st=join(221,1); sid=st['season']['id']; now=polywar._now()
    sectors.transfer_cell_ownership(connect(),sid,10,10,None,1,221,now) if False else None
    c=connect(); sectors.init_polywar_sector_schema(c); sectors.transfer_cell_ownership(c,sid,10,10,None,1,221,now); sectors.transfer_cell_ownership(c,sid,11,10,None,1,221,now); c.commit(); c.close()
    api=sectors.get_sectors(221,0,0,0,0); assert api['ok'] and api['sectors'][0]['controller_faction_id']==1
    c=connect(); stat=c.execute('select influence_score,controlled_sectors_count from polywar_faction_season_stats where season_id=? and faction_id=1',(sid,)).fetchone(); c.close(); assert stat['controlled_sectors_count']>=1 and stat['influence_score']>=102
    with pytest.raises(ValueError, match='too_many_sectors'): sectors.get_sectors(221,0,9,0,10)

def test_concurrent_same_attack_key_one_spend_one_progress(polydb):
    connect,settings=polydb; st=join(301,1); join(302,2); sid=st['season']['id']; bx,by=m.faction_base_positions()[1]; x,y=bx+9,by; prep_enemy(connect,sid,x,y)
    out=[]; err=[]
    def worker():
        try: out.append(combat.combat_action(301,'attack',x,y,'same-attack'))
        except ValueError as e: err.append(str(e))
    ts=[threading.Thread(target=worker) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert err==[] and sorted(bool(r.get('duplicate')) for r in out)==[False, True]
    c=connect(); row=c.execute('select current_energy from polywar_players where user_id=301 and season_id=?',(sid,)).fetchone(); cell=c.execute('select contest_progress from polywar_cells where season_id=? and x=? and y=?',(sid,x,y)).fetchone(); assert row['current_energy']==8 and cell['contest_progress']==50
    assert c.execute('select count(*) from polywar_actions where user_id=301 and season_id=?',(sid,)).fetchone()[0]==1
    assert c.execute('select count(*) from polywar_action_outcomes where user_id=301 and season_id=?',(sid,)).fetchone()[0]==1; c.close()

def test_concurrent_same_reinforce_key_one_spend_one_progress(polydb):
    connect,settings=polydb; st=join(311,1); sid=st['season']['id']; bx,by=m.faction_base_positions()[1]; x,y=bx+8,by; c=connect(); c.execute('insert into polywar_cells (season_id,x,y,owner_faction_id,contesting_faction_id,contest_progress,contested_at) values (?,?,?,?,?,?,CURRENT_TIMESTAMP)',(sid,x,y,1,2,100)); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,x-1,y)); c.commit(); c.close()
    out=[]
    def worker(): out.append(combat.combat_action(311,'reinforce',x,y,'same-reinforce'))
    ts=[threading.Thread(target=worker) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(bool(r.get('duplicate')) for r in out)==[False, True]
    c=connect(); assert c.execute('select current_energy from polywar_players where user_id=311 and season_id=?',(sid,)).fetchone()[0]==9
    assert c.execute('select contest_progress from polywar_cells where season_id=? and x=? and y=?',(sid,x,y)).fetchone()[0]==50; c.close()

def test_concurrent_same_faction_attacks_accumulate_and_final_once(polydb):
    connect,settings=polydb; st=join(321,1); join(322,1); join(323,2); sid=st['season']['id']; bx,by=m.faction_base_positions()[1]; x,y=bx+9,by; prep_enemy(connect,sid,x,y)
    out=[]
    def worker(uid,key): out.append(combat.combat_action(uid,'attack',x,y,key)['outcome'])
    ts=[threading.Thread(target=worker,args=(321,'a')), threading.Thread(target=worker,args=(322,'b'))]; [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(out)==['attack_progress','territory_captured']
    c=connect(); assert c.execute('select owner_faction_id,contest_progress from polywar_cells where season_id=? and x=? and y=?',(sid,x,y)).fetchone()['owner_faction_id']==1; c.close()

def test_concurrent_different_factions_and_attack_reinforce_serialize(polydb):
    connect,settings=polydb; st=join(331,1); join(332,2); join(333,3); sid=st['season']['id']; bx,by=m.faction_base_positions()[1]; x,y=bx+9,by; prep_enemy(connect,sid,x,y); c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,3)',(sid,x,y+1)); c.commit(); c.close()
    results=[]
    ts=[threading.Thread(target=lambda: results.append(combat.combat_action(331,'attack',x,y,'af')['outcome'])), threading.Thread(target=lambda: results.append(combat.combat_action(333,'attack',x,y,'cf')['outcome']))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert 'territory_captured' not in results or results.count('territory_captured') <= 1
    c=connect(); c.execute('update polywar_cells set owner_faction_id=2, contesting_faction_id=1, contest_progress=100, contested_at=CURRENT_TIMESTAMP where season_id=? and x=? and y=?',(sid,x,y)); c.commit(); c.close(); results=[]; errs=[]
    def run_action(uid,typ,key):
        try: results.append(combat.combat_action(uid,typ,x,y,key)['outcome'])
        except ValueError as e: errs.append(str(e))
    ts=[threading.Thread(target=run_action,args=(331,'attack','atk')), threading.Thread(target=run_action,args=(332,'reinforce','def'))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(results)+len(errs)==2

def test_sector_initialization_backfills_once_and_preserves_existing_cells(polydb):
    connect,settings=polydb; st=join(341,1); sid=st['season']['id']; c=connect(); c.execute('insert into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,5,5)); c.commit(); sectors.initialize_sector(c,sid,0,0); first=c.execute('select sum(controlled_cells_count) from polywar_sector_faction_stats where season_id=? and sector_x=0 and sector_y=0',(sid,)).fetchone()[0]; sectors.initialize_sector(c,sid,0,0); second=c.execute('select sum(controlled_cells_count) from polywar_sector_faction_stats where season_id=? and sector_x=0 and sector_y=0',(sid,)).fetchone()[0]; assert first==second and c.execute('select count(*) from polywar_sector_initializations where season_id=? and sector_x=0 and sector_y=0',(sid,)).fetchone()[0]==1; c.close()

def test_starting_global_counts_and_starting_cell_transfer(polydb):
    connect,settings=polydb; st=join(351,1); join(352,2); sid=st['season']['id']; bx,by=m.faction_base_positions()[2]; prep_enemy(connect,sid,bx,by,owner=2); c=connect(); before=c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=2',(sid,)).fetchone()[0]; c.close(); c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,bx-1,by)); c.commit(); c.close(); r=combat.combat_action(351,'attack',bx,by,'s1'); r2=combat.combat_action(351,'attack',bx,by,'s2'); assert r2['outcome']=='territory_captured'; c=connect(); after=c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=2',(sid,)).fetchone()[0]; assert after==max(0,before-1); c.close()

def test_controlled_since_preserved_and_rival_clear_resets_contested_at(polydb):
    connect,settings=polydb; st=join(361,1); join(362,2); join(363,3); sid=st['season']['id']; now=polywar._now(); c=connect(); sectors.init_polywar_sector_schema(c); sectors.transfer_cell_ownership(c,sid,20,20,None,1,361,now); sectors.transfer_cell_ownership(c,sid,21,20,None,1,361,now); old=c.execute('select controlled_since from polywar_sectors where season_id=? and sector_x=0 and sector_y=0',(sid,)).fetchone()['controlled_since']; sectors.recalc_sector(c,sid,0,0,polywar._now()); assert c.execute('select controlled_since from polywar_sectors where season_id=? and sector_x=0 and sector_y=0',(sid,)).fetchone()['controlled_since']==old; c.close()
    bx,by=m.faction_base_positions()[1]; x,y=bx+9,by; prep_enemy(connect,sid,x,y); combat.combat_action(361,'attack',x,y,'ra'); c=connect(); c.execute('insert or ignore into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,3)',(sid,x,y+1)); c.commit(); c.close(); combat.combat_action(363,'attack',x,y,'rb'); c=connect(); row=c.execute('select contest_progress,contesting_faction_id,contested_at from polywar_cells where season_id=? and x=? and y=?',(sid,x,y)).fetchone(); assert row['contest_progress']==0 and row['contesting_faction_id'] is None and row['contested_at'] is None; c.close()

def test_postgres_sql_branches_and_bounds_and_rate_cleanup(polydb):
    text=Path('services/polywar_sector_service.py').read_text(); assert 'GREATEST(0,' in text and '1 if contested else 0' in text
    connect,settings=polydb; st=join(371,1)
    with pytest.raises(ValueError, match='out_of_bounds'):
        sectors.get_sectors(371,0,999999,0,0)
    combat._RATE.clear(); sectors._RATE.clear(); combat._RATE[999999]=deque([time.monotonic()-999]); sectors._RATE[999999]=deque([time.monotonic()-999]); combat._rate(371); sectors._check_rate(371); assert 999999 not in combat._RATE and 999999 not in sectors._RATE

def test_frontend_sector_api_and_server_rules_usage():
    js=Path('webapp/polywar.js').read_text()
    assert '/api/polywar/map/sectors' in js and 'sectorRules()' in js and 'combatRules()' in js
    assert 'enemy_attack_extra_energy' in js and 'reinforce_energy_cost' in js and 'syncState(false, { soft: true })' in js
    assert 'new Set(["capture", "attack", "reinforce"])' in js
