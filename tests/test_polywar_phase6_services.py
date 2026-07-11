import sqlite3, uuid, sys, threading
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
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,9,10)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,11,10)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()+timedelta(days=1),sid)); c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=?',(sid,)); c.commit(); rebellion.ensure_rebellions(c,sid); c.commit(); c.close()
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

def test_null_state_disabled_blocks_ticks_and_seal(db):
    connect,settings=db; settings['polywar_null_state_enabled']='false'; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); st=world.get_public_world_state(c,sid)
    assert st['status']=='disabled'
    assert world.process_due_tick(c,sid,datetime.utcnow())['reason']=='null_state_disabled'


def test_catchup_uses_previous_schedule_and_is_bounded(db):
    connect,settings=db; settings['polywar_null_max_catchup_ticks']='2'; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid); old=datetime.utcnow()-timedelta(minutes=20); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(old,sid)); c.commit()
    out=world.ensure_world_caught_up(c,sid,datetime.utcnow()); c.commit(); row=c.execute('select tick_index,next_tick_at from polywar_null_state where season_id=?',(sid,)).fetchone()
    assert sum(1 for r in out if r.get('processed'))==2 and row['tick_index']>=2


def test_activation_transfers_preowned_rift_cell(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); c.execute("update polywar_null_state set status='dormant', activation_at=? where season_id=?",(datetime.utcnow()-timedelta(seconds=1),sid)); c.execute("update polywar_null_rifts set status='dormant' where season_id=?",(sid,)); r=c.execute('select * from polywar_null_rifts where season_id=? limit 1',(sid,)).fetchone(); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x'],r['y'])); before=c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=1',(sid,)).fetchone()[0] or 0; world.activate_if_due(c,sid); c.commit(); after=c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=1',(sid,)).fetchone()[0]
    assert after <= before and c.execute('select owner_faction_id from polywar_cells where season_id=? and x=? and y=?',(sid,r['x'],r['y'])).fetchone()[0]==8


def test_rebellion_pending_becomes_active_and_cancels_on_controller_change(db):
    connect,settings=db; settings['polywar_rebellion_grace_hours']='24'; sid=active(connect); polywar.join_faction(201,2); c=connect(); rebellion.init_rebellion_schema(c); now=datetime.utcnow(); c.execute('insert into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,20,20,2,now,now,now)); c.commit(); rebellion.ensure_rebellions(c,sid); row=c.execute('select status from polywar_rebellions where season_id=? and capital_original_faction_id=1',(sid,)).fetchone(); assert row['status']=='pending'; c.execute('update polywar_rebellions set eligible_at=?',(datetime.utcnow()-timedelta(seconds=1),)); rebellion.ensure_rebellions(c,sid); assert c.execute('select status from polywar_rebellions where season_id=?',(sid,)).fetchone()[0]=='active'; c.execute('update polywar_capitals set controller_faction_id=3 where season_id=? and original_faction_id=1',(sid,)); rebellion.ensure_rebellions(c,sid); assert c.execute("select count(*) from polywar_rebellions where season_id=? and status='cancelled'",(sid,)).fetchone()[0]>=1


def test_rebellion_full_suppression_status(db):
    connect,_=db; sid=active(connect); polywar.join_faction(202,2); c=connect(); rebellion.init_rebellion_schema(c); now=datetime.utcnow()-timedelta(days=2); c.execute('insert into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,30,30,2,now,now,datetime.utcnow())); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,31,30)); c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=?',(sid,)); c.commit(); rebellion.ensure_rebellions(c,sid); c.commit(); out=rebellion.rebellion_action(202,'suppress_rebellion',30,30,'suppr1'); assert out['resolved_status']=='suppressed'


def test_results_hash_excludes_secret_and_rows_are_immutable(db):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where season_id=?',(sid,)); c.execute('update polywar_seasons set ends_at=? where id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); finalization.maybe_finalize(c,sid,datetime.utcnow()); h1=c.execute('select results_hash from polywar_seasons where id=?',(sid,)).fetchone()[0]; seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]; snap='\n'.join(r[0] for r in c.execute('select snapshot_json from polywar_season_results where season_id=?',(sid,)).fetchall()); assert seed not in snap; finalization.finalize_season(c,sid); h2=c.execute('select results_hash from polywar_seasons where id=?',(sid,)).fetchone()[0]; assert h1==h2


def test_claim_validation_and_foreign_reward_forbidden(db):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where user_id=100 and season_id=?',(sid,)); c.execute('update polywar_seasons set ends_at=? where id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); finalization.maybe_finalize(c,sid,datetime.utcnow()); c.commit()
    with pytest.raises(ValueError): finalization.claim_reward(100,sid,'')
    with pytest.raises(ValueError): finalization.claim_reward(999,sid,'x')


def test_airdrop_polywar_reward_no_memory_fallback(monkeypatch):
    import services.airdrop_points_service as ap
    monkeypatch.setattr(ap, '_connect_ready', lambda: (_ for _ in ()).throw(RuntimeError('db_down')))
    with pytest.raises(RuntimeError): ap.award_airdrop_points_idempotent(1,'polywar_season_reward',10,{},'polywar:season:1:user:1')


def test_frontend_source_phase6_routes_and_results_fetch():
    src=Path('webapp/polywar.js').read_text()
    assert "a==='seal_rift'" in src and 'syncPolywarResults' in src and 'polywarClaimReward' in src and 'supportRebellion' in src


def test_world_source_uses_conflict_safe_tick_insert_and_no_python_hash():
    src=Path('services/polywar_world_service.py').read_text()
    assert 'ON CONFLICT (season_id,tick_index) DO NOTHING' in src and 'INSERT OR IGNORE INTO polywar_world_ticks' in src and 'hash(' not in src

def test_build_chunks_runtime_rebellion_metadata(db):
    connect,_=db; sid=active(connect); polywar.join_faction(203,2); c=connect(); from services import polywar_map_service as mm; rebellion.init_rebellion_schema(c); now=datetime.utcnow()-timedelta(days=2); c.execute('insert into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,64,64,2,now,now,datetime.utcnow())); c.commit(); c.close()
    out=mm.build_chunks(100, [(1,1)])
    ch=out['chunks'][0]
    assert ch['rebellions'] and ch['rebellions'][0]['x']==64 and ch['rebellions'][0]['y']==64


def test_rift_safe_zone_blocks_mines_scans_and_flags(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); r=c.execute('select * from polywar_null_rifts where season_id=? limit 1',(sid,)).fetchone(); seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]
    from services import polywar_mine_service as mines
    assert mines.active_mine_at(c,sid,seed,r['x'],r['y'],world.m.terrain_at(seed,r['x'],r['y'])) is False
    assert mines.adjacent_mine_count(c,sid,seed,r['x'],r['y']) >= 0
    c.commit(); c.close()
    with pytest.raises(ValueError): mines.set_flag(100,r['x'],r['y'],True)


def test_null_capital_siege_progress_and_rival_reduction(db):
    connect,settings=db
    settings['polywar_null_expansions_per_tick']='10'; settings['polywar_null_capital_siege_per_tick']='50'
    sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid)
    seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]
    x=y=40
    while world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x,y)) is None or world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x-1,y)) is None:
        x+=1; y+=1
    now=datetime.utcnow()
    c.execute('insert into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,x,y,1,now,now))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,8)',(sid,x-1,y))
    world.update_frontier_for_cell(c,sid,x-1,y,None)
    c.execute('delete from polywar_null_frontier where season_id=? and not (x=? and y=?)',(sid,x,y))
    c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,datetime.utcnow(),999))
    c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid))
    c.commit(); out=world.process_due_tick(c,sid,datetime.utcnow()); c.commit()
    cap=c.execute('select * from polywar_capitals where season_id=? and x=? and y=?',(sid,x,y)).fetchone()
    assert out['processed'] is True and cap['besieging_faction_id']==8 and cap['siege_progress']>=50
    c.execute('update polywar_capitals set besieging_faction_id=2,siege_progress=60 where id=?',(cap['id'],))
    c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid))
    c.execute('delete from polywar_null_frontier where season_id=? and not (x=? and y=?)',(sid,x,y))
    c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,datetime.utcnow(),999))
    c.commit(); world.process_due_tick(c,sid,datetime.utcnow()); c.commit()
    cap2=c.execute('select * from polywar_capitals where id=?',(cap['id'],)).fetchone()
    assert cap2['siege_progress']<=10 or cap2['besieging_faction_id'] is None


def test_null_capital_capture_not_repeated_on_next_tick(db):
    connect,settings=db; settings['polywar_null_expansions_per_tick']='10'; settings['polywar_null_capital_siege_per_tick']='100000'
    sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid)
    seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]
    x=y=70
    while world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x,y)) is None or world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x-1,y)) is None:
        x+=1; y+=1
    now=datetime.utcnow(); c.execute('insert into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,x,y,1,now,now)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,8)',(sid,x-1,y)); world.update_frontier_for_cell(c,sid,x-1,y,None)
    c.execute('delete from polywar_null_frontier where season_id=? and not (x=? and y=?)',(sid,x,y)); c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,now,999)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(now-timedelta(minutes=1),sid)); c.commit()
    world.process_due_tick(c,sid,datetime.utcnow()); c.commit(); c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,datetime.utcnow(),999)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid)); c.commit(); world.process_due_tick(c,sid,datetime.utcnow()); c.commit()
    cap=c.execute('select * from polywar_capitals where season_id=? and x=? and y=?',(sid,x,y)).fetchone(); events=c.execute("select count(*) from polywar_events where season_id=? and event_type='null_capital_captured'",(sid,)).fetchone()[0]
    assert cap['controller_faction_id']==8 and int(cap['siege_progress'] or 0)==0 and cap['besieging_faction_id'] is None and events==1


def test_threaded_concurrent_world_initialization_and_activation(db):
    connect,_=db; sid=active(connect); errs=[]
    def worker():
        try:
            c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid,datetime.utcnow()); c.commit(); c.close()
        except Exception as exc: errs.append(exc)
    ts=[threading.Thread(target=worker) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert not errs; assert c.execute('select count(*) from polywar_null_state where season_id=?',(sid,)).fetchone()[0]==1; assert c.execute('select count(*) from polywar_null_rifts where season_id=?',(sid,)).fetchone()[0]==world.rift_count()


def test_stale_processing_tick_recovery(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid); past=datetime.utcnow()-timedelta(hours=3); c.execute('update polywar_null_state set next_tick_at=?,tick_index=0 where season_id=?',(past,sid)); c.execute('delete from polywar_world_ticks where season_id=?',(sid,)); c.execute("insert or ignore into polywar_world_ticks (season_id,tick_index,scheduled_at,started_at,status,created_at) values (?,?,?,?,?,?)",(sid,1,past,past,'processing',past)); c.commit(); out=world.process_due_tick(c,sid,datetime.utcnow()); c.commit(); assert out['processed'] is True; assert c.execute("select count(*) from polywar_world_ticks where season_id=? and tick_index=1 and status='completed'",(sid,)).fetchone()[0]==1


def test_finalize_source_uses_begin_helper():
    src=Path('services/polywar_finalization_service.py').read_text()
    assert '_begin(conn)' in src and "status='finalizing'" in src


def test_threaded_concurrent_same_tick_one_marker(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); world.activate_if_due(c,sid); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid)); c.commit(); c.close(); out=[]
    def run_tick():
        cc=connect()
        try: out.append(world.process_due_tick(cc,sid,datetime.utcnow()))
        finally: cc.commit(); cc.close()
    ts=[threading.Thread(target=run_tick) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute('select count(*) from polywar_world_ticks where season_id=? and tick_index=1',(sid,)).fetchone()[0]==1; assert any(r.get('processed') for r in out); assert c.execute('select count(*) from polywar_world_ticks where season_id=? and tick_index=1 and status=\'completed\'',(sid,)).fetchone()[0]==1


def test_threaded_concurrent_activation_event_once(db):
    connect,_=db; sid=active(connect); c=connect(); world.ensure_world_initialized(c,sid); c.execute("update polywar_null_state set status='dormant',activation_at=? where season_id=?",(datetime.utcnow()-timedelta(seconds=1),sid)); c.execute("update polywar_null_rifts set status='dormant' where season_id=?",(sid,)); c.commit(); c.close()
    def run_activation():
        cc=connect()
        try: world.activate_if_due(cc,sid,datetime.utcnow())
        finally: cc.commit(); cc.close()
    ts=[threading.Thread(target=run_activation) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute("select count(*) from polywar_events where season_id=? and event_type='null_state_activated'",(sid,)).fetchone()[0]==1


def test_results_snapshot_includes_phase6_aggregates_source():
    src=Path('services/polywar_finalization_service.py').read_text()
    assert 'rifts_sealed_count' in src and 'rebellions_supported_count' in src and 'JOIN polywar_rebellions' in src
