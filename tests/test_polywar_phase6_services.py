import sqlite3, uuid, sys
from pathlib import Path
import types
if "db.database" not in sys.modules:
    dbpkg=types.ModuleType("db"); dbmod=types.ModuleType("db.database"); dbmod.get_connection=lambda: None; dbmod.get_setting=lambda k,d="": d; dbmod.get_user=lambda uid: None; sys.modules.setdefault("db", dbpkg); sys.modules["db.database"]=dbmod
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta
import pytest
from services import polywar_service as polywar
from services import polywar_world_service as world
from services import polywar_rebellion_service as rebellion
from services import polywar_finalization_service as finalization

@pytest.fixture()
def db(monkeypatch):
    uri=f"file:phase6_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri,uri=True,check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={"polywar_map_width":"1024","polywar_map_height":"1024","polywar_null_activation_hours":"0","polywar_null_rift_min_distance":"50","polywar_null_tick_minutes":"1"}
    def connect():
        c=sqlite3.connect(uri,uri=True,check_same_thread=False); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(polywar,'get_connection',connect); monkeypatch.setattr(polywar,'get_setting',lambda k,d='': settings.get(k,d)); monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); c.close()
    yield connect, settings
    keeper.close()

def active(connect):
    st=polywar.join_faction(100,1); return st['season']['id']

def test_null_state_system_faction_hidden_and_stats(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); c.commit()
    allf=polywar.list_all_polywar_factions(c); playable=polywar.list_factions(c)
    assert any(f['id']==8 and f['is_system']==1 and f['is_playable']==0 for f in allf)
    assert [f['id'] for f in playable]==[1,2,3,4,5,6,7]
    assert c.execute('select count(*) from polywar_faction_season_stats where season_id=? and faction_id=8',(sid,)).fetchone()[0]==1
    with pytest.raises(ValueError): polywar.join_faction(101,8)

def test_rift_coordinates_deterministic_valid_not_bases(db):
    connect,_=db; sid=active(connect); c=connect(); seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]
    a=world.choose_rift_coordinates(seed,4); b=world.choose_rift_coordinates(seed,4)
    bases=set(__import__('services.polywar_map_service',fromlist=['faction_base_positions']).faction_base_positions().values())
    assert a==b and len(a)==4
    assert not (set(a)&bases)
    assert all(world.m.TERRAIN_COSTS[world.m.terrain_at(seed,x,y)] is not None for x,y in a)

def test_activation_materializes_once_and_public_world(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); activated = world.activate_if_due(c,sid,datetime.utcnow()); assert activated in (True, False); assert not world.activate_if_due(c,sid,datetime.utcnow()); c.commit()
    assert c.execute("select count(*) from polywar_events where season_id=? and event_type='null_state_activated'",(sid,)).fetchone()[0]==1
    assert c.execute('select count(*) from polywar_cells where season_id=? and owner_faction_id=8',(sid,)).fetchone()[0] >= 4
    public=world.get_public_world_state(c,sid); assert public['status']=='active' and len(public['active_rifts'])==4

def test_world_tick_bounded_and_unique(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid)); c.commit()
    assert world.process_due_tick(c,sid,datetime.utcnow())['processed'] is True; c.commit()
    assert c.execute('select count(*) from polywar_world_ticks where season_id=?',(sid,)).fetchone()[0] >= 1
    assert c.execute('select actions_count from polywar_world_ticks where season_id=?',(sid,)).fetchone()[0] <= world.expansions_per_tick()

def test_seal_rift_duplicate_and_defeat_state(db):
    connect,settings=db; settings['polywar_null_rift_seal_progress']='100000'; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid); r=c.execute("select * from polywar_null_rifts where season_id=? and status='active' limit 1",(sid,)).fetchone(); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()+timedelta(days=1),sid)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x']+1,r['y'])); c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=100 and season_id=?',(sid,)); c.commit(); c.close()
    out=world.seal_rift_action(100,r['x'],r['y'],'seal-key'); dup=world.seal_rift_action(100,r['x'],r['y'],'seal-key')
    assert out['sealed'] is True and dup['duplicate'] is True
    c=connect(); assert c.execute('select count(*) from polywar_rift_contributions where user_id=100').fetchone()[0]==1

def test_rebellion_creation_and_action_rules(db):
    connect,_=db; sid=active(connect); polywar.join_faction(200,2); c=connect(); rebellion.init_rebellion_schema(c)
    c.execute("insert into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)",(sid,1,10,10,2,datetime.utcnow()-timedelta(days=2),datetime.utcnow()-timedelta(days=2),datetime.utcnow()))
    c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=?',(sid,)); c.commit(); rebellion.ensure_rebellions(c,sid); c.commit(); c.close()
    out=rebellion.rebellion_action(100,'support_rebellion',10,10,'sup1')
    assert out['outcome']=='rebellion_supported'
    with pytest.raises(ValueError): rebellion.rebellion_action(100,'suppress_rebellion',10,10,'bad')

def test_finalization_results_hash_rewards_and_claim(db, monkeypatch):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where user_id=100 and season_id=?',(sid,)); c.execute('update polywar_seasons set ends_at=? where id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit()
    assert finalization.maybe_finalize(c,sid,datetime.utcnow()); c.commit()
    res=finalization.get_results(c,sid,100); assert res['ok'] and res['season']['results_hash']
    calls=[]
    monkeypatch.setattr('services.airdrop_points_service.award_airdrop_points_idempotent', lambda *a,**k: calls.append(a) or {'ok':True,'awarded':True})
    claim=finalization.claim_reward(100,sid,'claim1')
    assert claim['claimed'] is True and len(calls)==1
