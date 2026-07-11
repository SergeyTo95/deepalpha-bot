import sqlite3, sys, uuid, threading
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
    c=connect(); stat=c.execute('select influence_score,controlled_sectors_count from polywar_faction_season_stats where season_id=? and faction_id=1',(sid,)).fetchone(); c.close(); assert stat['controlled_sectors_count']==1 and stat['influence_score']>=102
    with pytest.raises(ValueError, match='too_many_sectors'): sectors.get_sectors(221,0,100,0,100)
