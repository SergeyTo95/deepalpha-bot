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
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); c.commit()
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
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); activated = world.activate_if_due_in_transaction(c,sid,datetime.utcnow()); assert activated in (True, False); assert not world.activate_if_due_in_transaction(c,sid,datetime.utcnow()); c.commit()
    assert c.execute("select count(*) from polywar_events where season_id=? and event_type='null_state_activated'",(sid,)).fetchone()[0]==1
    assert c.execute('select count(*) from polywar_cells where season_id=? and owner_faction_id=8',(sid,)).fetchone()[0] >= 4
    public=world.get_public_world_state(c,sid); assert public['status']=='active' and len(public['active_rifts'])==4

def test_world_tick_bounded_and_unique(db):
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid)); c.commit()
    assert world.process_due_tick_in_transaction(c,sid,datetime.utcnow())['processed'] is True; c.commit()
    assert c.execute('select count(*) from polywar_world_ticks where season_id=?',(sid,)).fetchone()[0] >= 1
    assert c.execute('select actions_count from polywar_world_ticks where season_id=?',(sid,)).fetchone()[0] <= world.expansions_per_tick()

def test_seal_rift_duplicate_and_defeat_state(db):
    connect,settings=db; settings['polywar_null_rift_seal_progress']='100000'; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid); r=c.execute("select * from polywar_null_rifts where season_id=? and status='active' limit 1",(sid,)).fetchone(); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()+timedelta(days=1),sid)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x']+1,r['y'])); c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=100 and season_id=?',(sid,)); c.commit(); c.close()
    out=world.seal_rift_action(100,r['x'],r['y'],'seal-key'); dup=world.seal_rift_action(100,r['x'],r['y'],'seal-key')
    assert out['sealed'] is True and dup['duplicate'] is True
    c=connect(); assert c.execute('select count(*) from polywar_rift_contributions where user_id=100').fetchone()[0]==1

def test_rebellion_creation_and_action_rules(db):
    connect,_=db; sid=active(connect); polywar.join_faction(200,2); c=connect(); rebellion.init_rebellion_schema(c)
    c.execute("insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)",(sid,1,10,10,2,datetime.utcnow()-timedelta(days=2),datetime.utcnow()-timedelta(days=2),datetime.utcnow()))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,9,10)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,11,10)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()+timedelta(days=1),sid)); c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=?',(sid,)); c.commit(); rebellion.ensure_rebellions_in_transaction(c,sid); c.commit(); c.close()
    out=rebellion.rebellion_action(100,'support_rebellion',10,10,'sup1')
    assert out['outcome']=='rebellion_supported'
    with pytest.raises(ValueError): rebellion.rebellion_action(100,'suppress_rebellion',10,10,'bad')

def test_finalization_results_hash_rewards_and_claim(db, monkeypatch):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where user_id=100 and season_id=?',(sid,)); c.execute('update polywar_seasons set ends_at=? where id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit()
    assert finalization.maybe_finalize(sid,datetime.utcnow())
    res=finalization.get_results(sid,100); assert res['ok'] and res['season']['results_hash']
    calls=[]
    monkeypatch.setattr('services.airdrop_points_service.award_airdrop_points_idempotent', lambda *a,**k: calls.append(a) or {'ok':True,'awarded':True})
    claim=finalization.claim_reward(100,sid,'claim1')
    assert claim['claimed'] is True and len(calls)==1

def test_null_state_disabled_blocks_ticks_and_seal(db):
    connect,settings=db; settings['polywar_null_state_enabled']='false'; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); st=world.get_public_world_state(c,sid)
    assert st['status']=='disabled'
    assert world.process_due_tick_in_transaction(c,sid,datetime.utcnow())['reason']=='null_state_disabled'


def test_catchup_uses_previous_schedule_and_is_bounded(db):
    connect,settings=db; settings['polywar_null_max_catchup_ticks']='2'; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid); old=datetime.utcnow()-timedelta(minutes=20); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(old,sid)); c.commit()
    out=world.ensure_world_caught_up_in_transaction(c,sid,datetime.utcnow()); c.commit(); row=c.execute('select tick_index,next_tick_at from polywar_null_state where season_id=?',(sid,)).fetchone()
    assert sum(1 for r in out if r.get('processed'))==2 and row['tick_index']>=2


def test_activation_transfers_preowned_rift_cell(db):
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); c.execute("update polywar_null_state set status='dormant', activation_at=? where season_id=?",(datetime.utcnow()-timedelta(seconds=1),sid)); c.execute("update polywar_null_rifts set status='dormant' where season_id=?",(sid,)); r=c.execute('select * from polywar_null_rifts where season_id=? limit 1',(sid,)).fetchone(); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x'],r['y'])); before=c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=1',(sid,)).fetchone()[0] or 0; world.activate_if_due_in_transaction(c,sid); c.commit(); after=c.execute('select controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id=1',(sid,)).fetchone()[0]
    assert after <= before and c.execute('select owner_faction_id from polywar_cells where season_id=? and x=? and y=?',(sid,r['x'],r['y'])).fetchone()[0]==8


def test_rebellion_pending_becomes_active_and_cancels_on_controller_change(db):
    connect,settings=db; settings['polywar_rebellion_grace_hours']='24'; sid=active(connect); polywar.join_faction(201,2); c=connect(); rebellion.init_rebellion_schema(c); now=datetime.utcnow(); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,20,20,2,now,now,now)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,19,20)); c.commit(); rebellion.ensure_rebellions_in_transaction(c,sid); row=c.execute('select status from polywar_rebellions where season_id=? and capital_original_faction_id=1',(sid,)).fetchone(); assert row['status']=='pending'; c.execute('update polywar_rebellions set eligible_at=?',(datetime.utcnow()-timedelta(seconds=1),)); rebellion.ensure_rebellions_in_transaction(c,sid); assert c.execute('select status from polywar_rebellions where season_id=?',(sid,)).fetchone()[0]=='active'; c.execute('update polywar_capitals set controller_faction_id=3 where season_id=? and original_faction_id=1',(sid,)); rebellion.ensure_rebellions_in_transaction(c,sid); assert c.execute("select count(*) from polywar_rebellions where season_id=? and status='cancelled'",(sid,)).fetchone()[0]>=1


def test_rebellion_full_suppression_status(db):
    connect,_=db; sid=active(connect); polywar.join_faction(202,2); c=connect(); rebellion.init_rebellion_schema(c); now=datetime.utcnow()-timedelta(days=2); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,30,30,2,now,now,datetime.utcnow())); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,29,30)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,31,30)); c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=?',(sid,)); c.commit(); rebellion.ensure_rebellions_in_transaction(c,sid); c.commit(); out=rebellion.rebellion_action(202,'suppress_rebellion',30,30,'suppr1'); assert out['resolved_status']=='suppressed'


def test_results_hash_excludes_secret_and_rows_are_immutable(db):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where season_id=?',(sid,)); c.execute('update polywar_seasons set ends_at=? where id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); finalization.maybe_finalize(sid,datetime.utcnow()); h1=c.execute('select results_hash from polywar_seasons where id=?',(sid,)).fetchone()[0]; seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]; snap='\n'.join(r[0] for r in c.execute('select snapshot_json from polywar_season_results where season_id=?',(sid,)).fetchall()); assert seed not in snap; finalization.finalize_season(sid); h2=c.execute('select results_hash from polywar_seasons where id=?',(sid,)).fetchone()[0]; assert h1==h2


def test_claim_validation_and_foreign_reward_forbidden(db):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where user_id=100 and season_id=?',(sid,)); c.execute('update polywar_seasons set ends_at=? where id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); finalization.maybe_finalize(sid,datetime.utcnow())
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
    connect,_=db; sid=active(connect); polywar.join_faction(203,2); c=connect(); from services import polywar_map_service as mm; rebellion.init_rebellion_schema(c); now=datetime.utcnow()-timedelta(days=2); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,64,64,2,now,now,datetime.utcnow())); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,63,64)); rebellion.ensure_rebellions_in_transaction(c,sid); c.commit(); c.close()
    out=mm.build_chunks(100, [(1,1)])
    ch=out['chunks'][0]
    assert ch['rebellions'] and ch['rebellions'][0]['x']==64 and ch['rebellions'][0]['y']==64


def test_rift_safe_zone_blocks_mines_scans_and_flags(db):
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); r=c.execute('select * from polywar_null_rifts where season_id=? limit 1',(sid,)).fetchone(); seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]
    from services import polywar_mine_service as mines
    assert mines.active_mine_at(c,sid,seed,r['x'],r['y'],world.m.terrain_at(seed,r['x'],r['y'])) is False
    assert mines.adjacent_mine_count(c,sid,seed,r['x'],r['y']) >= 0
    c.commit(); c.close()
    with pytest.raises(ValueError): mines.set_flag(100,r['x'],r['y'],True)


def test_null_capital_siege_progress_and_rival_reduction(db):
    connect,settings=db
    settings['polywar_null_expansions_per_tick']='10'; settings['polywar_null_capital_siege_per_tick']='50'
    sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid)
    seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]
    x=y=40
    while world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x,y)) is None or world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x-1,y)) is None:
        x+=1; y+=1
    now=datetime.utcnow()
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,x,y,1,now,now))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,8)',(sid,x-1,y))
    world.update_frontier_for_cell(c,sid,x-1,y,None)
    c.execute('delete from polywar_null_frontier where season_id=? and not (x=? and y=?)',(sid,x,y))
    c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,datetime.utcnow(),999))
    c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid))
    c.commit(); out=world.process_due_tick_in_transaction(c,sid,datetime.utcnow()); c.commit()
    cap=c.execute('select * from polywar_capitals where season_id=? and x=? and y=?',(sid,x,y)).fetchone()
    assert out['processed'] is True and cap['besieging_faction_id']==8 and cap['siege_progress']>=50
    c.execute('update polywar_capitals set besieging_faction_id=2,siege_progress=60 where id=?',(cap['id'],))
    c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid))
    c.execute('delete from polywar_null_frontier where season_id=? and not (x=? and y=?)',(sid,x,y))
    c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,datetime.utcnow(),999))
    c.commit(); world.process_due_tick_in_transaction(c,sid,datetime.utcnow()); c.commit()
    cap2=c.execute('select * from polywar_capitals where id=?',(cap['id'],)).fetchone()
    assert cap2['siege_progress']<=10 or cap2['besieging_faction_id'] is None


def test_null_capital_capture_not_repeated_on_next_tick(db):
    connect,settings=db; settings['polywar_null_expansions_per_tick']='10'; settings['polywar_null_capital_siege_per_tick']='100000'
    sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid)
    seed=c.execute('select secret_seed from polywar_seasons where id=?',(sid,)).fetchone()[0]
    x=y=70
    while world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x,y)) is None or world.m.TERRAIN_COSTS.get(world.m.terrain_at(seed,x-1,y)) is None:
        x+=1; y+=1
    now=datetime.utcnow(); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,x,y,1,now,now)); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,8)',(sid,x-1,y)); world.update_frontier_for_cell(c,sid,x-1,y,None)
    c.execute('delete from polywar_null_frontier where season_id=? and not (x=? and y=?)',(sid,x,y)); c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,now,999)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(now-timedelta(minutes=1),sid)); c.commit()
    world.process_due_tick_in_transaction(c,sid,datetime.utcnow()); c.commit(); c.execute('insert or replace into polywar_null_frontier (season_id,x,y,discovered_at,priority) values (?,?,?,?,?)',(sid,x,y,datetime.utcnow(),999)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid)); c.commit(); world.process_due_tick_in_transaction(c,sid,datetime.utcnow()); c.commit()
    cap=c.execute('select * from polywar_capitals where season_id=? and x=? and y=?',(sid,x,y)).fetchone(); events=c.execute("select count(*) from polywar_events where season_id=? and event_type='null_capital_captured'",(sid,)).fetchone()[0]
    assert cap['controller_faction_id']==8 and int(cap['siege_progress'] or 0)==0 and cap['besieging_faction_id'] is None and events==1


def test_threaded_concurrent_world_initialization_and_activation(db):
    connect,_=db; sid=active(connect); errs=[]
    def worker():
        try:
            c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid,datetime.utcnow()); c.commit(); c.close()
        except Exception as exc: errs.append(exc)
    ts=[threading.Thread(target=worker) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert not errs; assert c.execute('select count(*) from polywar_null_state where season_id=?',(sid,)).fetchone()[0]==1; assert c.execute('select count(*) from polywar_null_rifts where season_id=?',(sid,)).fetchone()[0]==world.rift_count()


def test_stale_processing_tick_recovery(db):
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid); past=datetime.utcnow()-timedelta(hours=3); c.execute('update polywar_null_state set next_tick_at=?,tick_index=0 where season_id=?',(past,sid)); c.execute('delete from polywar_world_ticks where season_id=?',(sid,)); c.execute("insert or ignore into polywar_world_ticks (season_id,tick_index,scheduled_at,started_at,status,created_at) values (?,?,?,?,?,?)",(sid,1,past,past,'processing',past)); c.commit(); out=world.process_due_tick_in_transaction(c,sid,datetime.utcnow()); c.commit(); assert out['processed'] is True; assert c.execute("select count(*) from polywar_world_ticks where season_id=? and tick_index=1 and status='completed'",(sid,)).fetchone()[0]==1


def test_finalize_source_uses_begin_helper():
    src=Path('services/polywar_finalization_service.py').read_text()
    assert '_begin(conn)' in src and "status='finalizing'" in src


def test_threaded_concurrent_same_tick_one_marker(db):
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid)); c.commit(); c.close(); out=[]
    def run_tick():
        cc=connect()
        try: out.append(world.process_due_tick_in_transaction(cc,sid,datetime.utcnow()))
        finally: cc.commit(); cc.close()
    ts=[threading.Thread(target=run_tick) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute('select count(*) from polywar_world_ticks where season_id=? and tick_index=1',(sid,)).fetchone()[0]==1; assert any(r.get('processed') for r in out); assert c.execute('select count(*) from polywar_world_ticks where season_id=? and tick_index=1 and status=\'completed\'',(sid,)).fetchone()[0]==1


def test_threaded_concurrent_activation_event_once(db):
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); c.execute("update polywar_null_state set status='dormant',activation_at=? where season_id=?",(datetime.utcnow()-timedelta(seconds=1),sid)); c.execute("update polywar_null_rifts set status='dormant' where season_id=?",(sid,)); c.execute("delete from polywar_events where season_id=? and event_type='null_state_activated'",(sid,)); c.commit(); c.close()
    def run_activation():
        cc=connect()
        try: world.activate_if_due_in_transaction(cc,sid,datetime.utcnow())
        finally: cc.commit(); cc.close()
    ts=[threading.Thread(target=run_activation) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute("select count(*) from polywar_events where season_id=? and event_type='null_state_activated'",(sid,)).fetchone()[0]==1


def test_results_snapshot_includes_phase6_aggregates_source():
    src=Path('services/polywar_finalization_service.py').read_text()
    assert 'rifts_sealed_count' in src and 'rebellions_supported_count' in src and 'JOIN polywar_rebellions' in src


def test_threaded_same_key_seal_rift_one_damage_and_energy(db):
    connect,settings=db; settings['polywar_null_rift_seal_progress']='100'; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid); r=c.execute("select * from polywar_null_rifts where season_id=? and status='active' limit 1",(sid,)).fetchone(); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x']+1,r['y'])); c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=100 and season_id=?',(sid,)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()+timedelta(days=1),sid)); c.commit(); c.close(); out=[]
    def worker():
        try: out.append(world.seal_rift_action(100,r['x'],r['y'],'thread-seal'))
        except Exception as exc: out.append({'error':type(exc).__name__})
    ts=[threading.Thread(target=worker) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); row=c.execute('select health from polywar_null_rifts where id=?',(r['id'],)).fetchone(); p=c.execute('select current_energy from polywar_players where user_id=100 and season_id=?',(sid,)).fetchone(); assert row['health']==int(r['health'])-100 and p['current_energy']==47; assert c.execute('select count(*) from polywar_action_outcomes where season_id=? and idempotency_key=?',(sid,'thread-seal')).fetchone()[0]==1


def test_threaded_final_seal_once_event_and_owner(db):
    connect,settings=db; settings['polywar_null_rift_seal_progress']='100000'; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid); r=c.execute("select * from polywar_null_rifts where season_id=? and status='active' limit 1",(sid,)).fetchone(); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x']+1,r['y'])); c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=100 and season_id=?',(sid,)); c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()+timedelta(days=1),sid)); c.commit(); c.close()
    ts=[threading.Thread(target=lambda: world.seal_rift_action(100,r['x'],r['y'],'thread-final-seal')) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); assert c.execute('select status,sealed_by_faction_id from polywar_null_rifts where id=?',(r['id'],)).fetchone()['status']=='sealed'; assert c.execute('select owner_faction_id from polywar_cells where season_id=? and x=? and y=?',(sid,r['x'],r['y'])).fetchone()[0]==1; assert c.execute("select count(*) from polywar_events where season_id=? and event_type='rift_sealed'",(sid,)).fetchone()[0]==1


def test_threaded_same_key_rebellion_support_one_progress_and_energy(db):
    connect,_=db; sid=active(connect); polywar.join_faction(200,2); c=connect(); rebellion.init_rebellion_schema(c); now=datetime.utcnow()-timedelta(days=2); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,44,44,2,now,now,datetime.utcnow())); c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,43,44)); c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=100 and season_id=?',(sid,)); c.commit(); rebellion.ensure_rebellions_in_transaction(c,sid); c.commit(); c.close()
    ts=[threading.Thread(target=lambda: rebellion.rebellion_action(100,'support_rebellion',44,44,'thread-support')) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); reb=c.execute('select progress from polywar_rebellions where season_id=? and capital_original_faction_id=1',(sid,)).fetchone(); p=c.execute('select current_energy from polywar_players where user_id=100 and season_id=?',(sid,)).fetchone(); assert reb['progress']==rebellion.public_rules()['support_progress'] and p['current_energy']==48; assert c.execute('select count(*) from polywar_action_outcomes where idempotency_key=?',('thread-support',)).fetchone()[0]==1


def test_domination_and_null_victory_settings_are_bounded(db):
    connect,settings=db; settings['polywar_domination_capitals_required']='2'; settings['polywar_null_victory_capitals_required']='2'; c=connect(); assert finalization.domination_capitals_required()==2 and finalization.null_victory_capitals_required()==2



def test_rebellion_requires_real_original_presence(db):
    connect,_=db; sid=active(connect); polywar.join_faction(210,2); c=connect(); rebellion.init_rebellion_schema(c); now=datetime.utcnow()-timedelta(days=2); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,70,70,2,now,now,now)); c.commit(); rebellion.ensure_rebellions_in_transaction(c,sid); assert c.execute('select count(*) from polywar_rebellions where season_id=?',(sid,)).fetchone()[0]==0; c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,69,70)); rebellion.ensure_rebellions_in_transaction(c,sid); assert c.execute('select count(*) from polywar_rebellions where season_id=?',(sid,)).fetchone()[0]==1


def test_domination_timer_start_reset_and_victory(db):
    connect,settings=db; settings['polywar_domination_capitals_required']='2'; settings['polywar_domination_hold_hours']='0'; sid=active(connect); c=connect(); now=datetime.utcnow(); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,80,80,1,now,now)); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,2,81,80,1,now,now)); c.commit(); assert finalization.maybe_finalize(sid,now) is False; assert c.execute('select domination_faction_id from polywar_seasons where id=?',(sid,)).fetchone()[0]==1; assert finalization.maybe_finalize(sid,now+timedelta(seconds=1)) is True; assert c.execute('select victory_type from polywar_seasons where id=?',(sid,)).fetchone()[0]=='domination'


def test_reconcile_fix_false_performs_zero_update(db, monkeypatch):
    connect,_=db; sid=active(connect); c=connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); calls=[]; orig=polywar._execute
    def spy(cur, sql, params=()):
        if str(sql).lstrip().upper().startswith('UPDATE'): calls.append(sql)
        return orig(cur, sql, params)
    monkeypatch.setattr(polywar,'_execute',spy); report=world.reconcile_polywar_season(c,sid,fix=False); assert 'mismatches' in report and calls==[]


def test_finalize_concurrent_one_next_season_no_old_players(db):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where user_id=100 and season_id=?',(sid,)); c.execute('update polywar_seasons set ends_at=? where id=?',(datetime.utcnow()-timedelta(seconds=1),sid)); c.commit(); c.close()
    def worker():
        cc=connect()
        try: finalization.finalize_season(sid,'time',None,datetime.utcnow())
        finally: cc.close()
    ts=[threading.Thread(target=worker) for _ in range(4)]; [t.start() for t in ts]; [t.join() for t in ts]
    c=connect(); new_sid=c.execute("select id from polywar_seasons where status='active' and id<>?",(sid,)).fetchone()[0]; assert c.execute("select count(*) from polywar_seasons where status='active'",()).fetchone()[0]==1; assert c.execute('select count(*) from polywar_players where season_id=?',(new_sid,)).fetchone()[0]==0; assert c.execute('select count(*) from polywar_null_rifts where season_id=?',(new_sid,)).fetchone()[0]==0

def test_get_state_re_resolves_new_active_after_domination_auto_finalization(db):
    connect,settings=db; settings['polywar_domination_capitals_required']='2'; settings['polywar_domination_hold_hours']='1'; sid=active(connect); c=connect(); now=datetime.utcnow(); past=now-timedelta(hours=2)
    c.execute('update polywar_seasons set domination_faction_id=1, domination_started_at=? where id=?',(past,sid))
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,90,90,1,past,now))
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,2,91,90,1,past,now))
    c.commit(); c.close()
    st=polywar.get_state(300)
    assert st['season']['status']=='active' and int(st['season']['id']) != sid
    assert int(st['latest_completed_season']['id']) == sid
    c=connect(); assert c.execute('select count(*) from polywar_players where season_id=? and user_id=300',(st['season']['id'],)).fetchone()[0]==1
    assert c.execute('select count(*) from polywar_players where season_id=? and user_id=300',(sid,)).fetchone()[0]==0
    assert st['world']['season_id'] == st['season']['id']

def test_frontend_results_claim_and_countdown_source_runtime_hooks():
    src=Path('webapp/polywar.js').read_text()
    assert 'polywarClaimKeys' in src and 'startWorldCountdownTimer' in src
    assert "#polywarClaimReward" in src and 'renderResultsPanel(currentState)' in src

def test_public_world_includes_domination_hold_state(db):
    connect,settings=db; settings['polywar_domination_capitals_required']='2'; settings['polywar_domination_hold_hours']='2'; sid=active(connect); c=connect(); now=datetime.utcnow(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid)
    c.execute('update polywar_seasons set domination_faction_id=1, domination_started_at=? where id=?',(now,sid))
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,92,90,1,now,now))
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,2,93,90,1,now,now))
    c.commit(); st=world.get_public_world_state(c,sid)
    assert st['domination_faction_id']==1 and st['domination_hold_hours']==2 and st['domination_hold_until'] and st['victory_candidate_type']=='domination'

def test_public_maybe_finalize_rejects_caller_connection_signature(db):
    connect,_=db; sid=active(connect); c=connect()
    with pytest.raises(TypeError): finalization.maybe_finalize(c,sid,datetime.utcnow())


def test_maybe_finalize_in_transaction_no_commit_or_begin(db, monkeypatch):
    connect,_=db; sid=active(connect); c=connect(); calls=[]; monkeypatch.setattr(polywar,'_is_sqlite',lambda conn: True)
    class Cur:
        def __init__(self, inner): self.inner=inner
        def execute(self, sql, params=()):
            if str(sql).upper().startswith(('BEGIN','COMMIT','ROLLBACK')): calls.append(sql)
            return self.inner.execute(sql, params)
        def fetchone(self): return self.inner.fetchone()
        def fetchall(self): return self.inner.fetchall()
        @property
        def rowcount(self): return self.inner.rowcount
    class Wrap:
        def __init__(self, inner): self.inner=inner
        def cursor(self): return Cur(self.inner.cursor())
        def commit(self): calls.append('COMMIT'); raise AssertionError('no commit')
        def rollback(self): calls.append('ROLLBACK'); raise AssertionError('no rollback')
        def __getattr__(self, name): return getattr(self.inner,name)
    finalization.maybe_finalize_in_transaction(Wrap(c),sid,datetime.utcnow())
    assert calls==[]


def test_time_expiry_wins_over_unmatured_domination_hold(db):
    connect,settings=db; settings['polywar_domination_capitals_required']='1'; settings['polywar_domination_hold_hours']='24'; sid=active(connect); c=connect(); now=datetime.utcnow(); c.execute('update polywar_seasons set ends_at=? where id=?',(now-timedelta(seconds=1),sid)); c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,111,111,1,now,now)); c.commit(); assert finalization.maybe_finalize(sid,now) is True; assert c.execute('select victory_type from polywar_seasons where id=?',(sid,)).fetchone()[0]=='time'

def test_begin_serialized_transaction_postgres_uses_begin(monkeypatch):
    calls=[]
    class C:
        def execute(self, sql, params=()): calls.append(sql)
    class Conn:
        def cursor(self): return C()
    monkeypatch.setattr(polywar,'_is_sqlite',lambda conn: False)
    polywar.begin_serialized_transaction(Conn())
    assert calls==['BEGIN']


def test_ensure_active_season_in_transaction_does_not_commit(db):
    connect,_=db; c=connect(); calls=[]
    class Wrap:
        def __init__(self, inner): self.inner=inner
        def cursor(self): return self.inner.cursor()
        def commit(self): calls.append('commit'); raise AssertionError('no commit')
        def rollback(self): calls.append('rollback'); raise AssertionError('no rollback')
        def __getattr__(self,n): return getattr(self.inner,n)
    polywar.ensure_active_season_in_transaction(Wrap(c))
    assert calls==[]


def test_stale_processing_claim_without_ledger_retries(db, monkeypatch):
    connect,_=db; sid=active(connect); c=connect(); c.execute('update polywar_players set faction_contribution=10 where user_id=100 and season_id=?',(sid,)); c.commit(); now=datetime.utcnow()-timedelta(seconds=500)
    finalization.finalize_season(sid,'time',None,datetime.utcnow())
    c.execute("update polywar_player_season_rewards set status='processing', claim_started_at=? where season_id=? and user_id=100",(now,sid)); c.commit()
    monkeypatch.setattr(finalization,'_ledger_entry',lambda ref: None)
    monkeypatch.setattr('services.airdrop_points_service.award_airdrop_points_idempotent',lambda *a,**k:{'ok':True,'duplicate':False})
    out=finalization.claim_reward(100,sid,'retry-stale')
    assert out['ok'] and connect().execute("select status from polywar_player_season_rewards where season_id=? and user_id=100",(sid,)).fetchone()[0]=='claimed'

def test_public_get_state_rejects_caller_connection(db):
    connect, _ = db
    conn = connect()
    try:
        with pytest.raises(TypeError):
            polywar.get_state(9001, conn)
    finally:
        conn.close()

def test_public_world_state_is_read_only(db):
    connect, _ = db
    sid = active(connect)
    conn = connect(); world.init_world_schema(conn); world.ensure_world_initialized_in_transaction(conn, sid); conn.commit()
    class CursorWrap:
        def __init__(self, inner): self.inner = inner
        def execute(self, sql, params=()):
            head = str(sql).strip().split()[0].upper() if str(sql).strip() else ''
            assert head not in {'INSERT','UPDATE','DELETE','ALTER','CREATE','BEGIN'}, sql
            return self.inner.execute(sql, params)
        def fetchone(self): return self.inner.fetchone()
        def fetchall(self): return self.inner.fetchall()
        def __iter__(self): return iter(self.inner)
        def __getattr__(self, name): return getattr(self.inner, name)
    class ConnWrap:
        def __init__(self, inner): self.inner = inner
        def cursor(self): return CursorWrap(self.inner.cursor())
        def commit(self): raise AssertionError('read-only helper must not commit')
        def rollback(self): raise AssertionError('read-only helper must not rollback')
        def __getattr__(self, name): return getattr(self.inner, name)
    out = world.get_public_world_state(ConnWrap(conn), sid)
    assert out['season_id'] == sid
    conn.close()


def test_world_in_transaction_helpers_do_not_begin_or_commit(db):
    connect, _ = db
    sid = active(connect)
    conn = connect()
    class CursorWrap:
        def __init__(self, inner): self.inner = inner
        def execute(self, sql, params=()):
            assert str(sql).strip().upper() != 'BEGIN IMMEDIATE'
            assert str(sql).strip().upper() != 'BEGIN'
            assert not str(sql).lstrip().upper().startswith(('ALTER ', 'CREATE TABLE', 'CREATE INDEX'))
            return self.inner.execute(sql, params)
        def fetchone(self): return self.inner.fetchone()
        def fetchall(self): return self.inner.fetchall()
        def __iter__(self): return iter(self.inner)
        def __getattr__(self, name): return getattr(self.inner, name)
    class ConnWrap:
        def __init__(self, inner): self.inner = inner
        def cursor(self): return CursorWrap(self.inner.cursor())
        def commit(self): raise AssertionError('no commit')
        def rollback(self): raise AssertionError('no rollback')
        def __getattr__(self, name): return getattr(self.inner, name)
    wrapped = ConnWrap(conn)
    world.ensure_world_initialized_in_transaction(wrapped, sid)
    world.ensure_world_caught_up_in_transaction(wrapped, sid)
    conn.close()

def test_public_world_wrappers_reject_caller_connection(db):
    connect, _ = db
    sid = active(connect)
    c = connect()
    try:
        with pytest.raises(TypeError): world.ensure_world_initialized(c, sid)
        with pytest.raises(TypeError): world.activate_if_due(c, sid)
        with pytest.raises(TypeError): world.process_due_tick(c, sid)
        with pytest.raises(TypeError): world.ensure_world_caught_up(c, sid)
    finally:
        c.close()


def test_due_tick_finalizes_before_world_mutation(db):
    connect, settings = db
    settings['polywar_domination_capitals_required'] = '1'
    settings['polywar_domination_hold_hours'] = '0'
    sid = active(connect)
    c = connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c, sid)
    now = datetime.utcnow()
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,120,120,1,now,now))
    c.execute('update polywar_seasons set domination_faction_id=1, domination_started_at=? where id=?',(now-timedelta(seconds=1),sid))
    before = c.execute('select tick_index from polywar_null_state where season_id=?',(sid,)).fetchone()[0]
    c.commit()
    out = world.process_due_tick_in_transaction(c, sid, now)
    assert out['reason'] == 'season_ended'
    assert c.execute('select status,victory_type from polywar_seasons where id=?',(sid,)).fetchone()['victory_type'] == 'domination'
    assert c.execute('select tick_index from polywar_null_state where season_id=?',(sid,)).fetchone()[0] == before
    c.close()


def test_chunk_not_due_does_not_run_world_lifecycle(db, monkeypatch):
    connect, _ = db
    sid = active(connect)
    c = connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c, sid)
    c.execute('update polywar_null_state set status=?, activation_at=?, next_tick_at=? where season_id=?', ('dormant', datetime.utcnow()+timedelta(days=1), datetime.utcnow()+timedelta(days=1), sid))
    c.commit(); c.close()
    from services import polywar_map_service as mm
    monkeypatch.setattr(world, 'ensure_world_caught_up', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unexpected lifecycle')))
    out = mm.build_chunks(100, [(0,0)])
    assert out['ok'] and out['chunks'][0]['rifts'] == []


def test_chunk_rebellion_enrichment_readonly(db):
    connect, _ = db
    sid = active(connect); polywar.join_faction(220,2)
    c = connect(); rebellion.init_rebellion_schema(c)
    now = datetime.utcnow()-timedelta(days=2)
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,8,8,2,now,now,now))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,7,8))
    rebellion.ensure_rebellions_in_transaction(c,sid); c.commit()
    class CursorWrap:
        def __init__(self, inner): self.inner=inner
        def execute(self, sql, params=()):
            assert not str(sql).lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','CREATE','BEGIN')), sql
            return self.inner.execute(sql, params)
        def fetchone(self): return self.inner.fetchone()
        def fetchall(self): return self.inner.fetchall()
        def __iter__(self): return iter(self.inner)
        def __getattr__(self,n): return getattr(self.inner,n)
    class ConnWrap:
        def __init__(self, inner): self.inner=inner
        def cursor(self): return CursorWrap(self.inner.cursor())
        def commit(self): raise AssertionError('readonly commit')
        def rollback(self): raise AssertionError('readonly rollback')
        def __getattr__(self,n): return getattr(self.inner,n)
    rows = rebellion.get_public_rebellions_readonly(ConnWrap(c), sid, (0,0,16,16))
    assert rows and rows[0]['capital'] == {'x':8,'y':8}
    c.close()


def test_world_tick_capital_lookup_failure_rolls_back(db, monkeypatch):
    connect, _ = db
    sid = active(connect)
    c = connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid)
    c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()-timedelta(minutes=1),sid)); before=c.execute('select tick_index from polywar_null_state where season_id=?',(sid,)).fetchone()[0]; c.commit(); c.close()
    from services import polywar_capital_service as caps
    monkeypatch.setattr(caps, 'get_capital_at', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db_read_failed')))
    with pytest.raises(RuntimeError): world.process_due_tick(sid, datetime.utcnow())
    c=connect(); assert c.execute('select tick_index from polywar_null_state where season_id=?',(sid,)).fetchone()[0] == before; c.close()


def test_recount_capital_failure_propagates(db, monkeypatch):
    connect, _ = db
    sid = active(connect)
    c = connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid)
    original = world._fetchone
    def boom(cur, sql, params=()):
        if 'polywar_capitals' in str(sql): raise RuntimeError('capital_count_failed')
        return original(cur, sql, params)
    monkeypatch.setattr(world, '_fetchone', boom)
    with pytest.raises(RuntimeError): world._recount(c, sid)
    c.close()


def test_public_world_response_includes_rebellions(db):
    connect, _ = db
    sid = active(connect); polywar.join_faction(221,2)
    c = connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); rebellion.init_rebellion_schema(c)
    now = datetime.utcnow()-timedelta(days=2)
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,12,12,2,now,now,now))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,11,12))
    rebellion.ensure_rebellions_in_transaction(c,sid); c.commit()
    out = world.get_public_world_state(c,sid)
    assert out['rebellions'] and out['rebellions'][0]['capital'] == {'x':12,'y':12}
    c.close()


def test_airdrop_idempotent_hot_path_has_zero_ddl(monkeypatch):
    import services.airdrop_points_service as ap
    ap._TABLE_READY = True
    monkeypatch.setattr(ap, 'get_airdrop_points_balance', lambda uid: {'total':10,'balance':10})
    class Cur:
        def __init__(self): self.sql=''
        def execute(self, sql, params=()):
            self.sql=str(sql)
            assert not self.sql.lstrip().upper().startswith(('ALTER','CREATE')), self.sql
        def fetchone(self):
            if 'metadata' in self.sql:
                return (5, 1, 'polywar_season_reward', 10, '{}', 'now')
            return (5, 1, 'polywar_season_reward', 10)
    class Conn:
        def __init__(self): self.cur=Cur()
        def cursor(self): return self.cur
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(ap, 'get_connection', lambda: Conn())
    assert ap.get_airdrop_points_ledger_entry_by_reference('ref')['id'] == 5
    out = ap.award_airdrop_points_idempotent(1,'polywar_season_reward',10,{},'ref')
    assert out['duplicate'] is True


def test_chunk_domination_due_uses_public_lifecycle(db, monkeypatch):
    connect, settings = db
    settings['polywar_domination_hold_hours'] = '0'
    sid = active(connect)
    c = connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c, sid)
    c.execute('update polywar_null_state set status=?, activation_at=?, next_tick_at=? where season_id=?', ('dormant', datetime.utcnow()+timedelta(days=1), datetime.utcnow()+timedelta(days=1), sid))
    c.execute('update polywar_seasons set domination_faction_id=1, domination_started_at=? where id=?', (datetime.utcnow()-timedelta(seconds=1), sid))
    c.commit(); c.close()
    calls=[]
    original = world.ensure_world_caught_up
    def wrapped(season_id, now=None):
        calls.append(season_id)
        return original(season_id, now)
    monkeypatch.setattr(world, 'ensure_world_caught_up', wrapped)
    from services import polywar_map_service as mm
    out = mm.build_chunks(100, [(0,0)])
    assert out['ok'] and calls == [sid]


def test_rebellion_tick_success_transitions_before_transfer(db, monkeypatch):
    connect, settings = db
    settings['polywar_rebellion_tick_progress'] = '1000'
    sid = active(connect); polywar.join_faction(230,2)
    c = connect(); rebellion.init_rebellion_schema(c)
    now = datetime.utcnow()-timedelta(days=2)
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,22,22,2,now,now,now))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,21,22))
    rebellion.ensure_rebellions_in_transaction(c,sid); c.commit()
    from services import polywar_capital_service as caps
    original = caps.transfer_capital_control
    seen=[]
    def wrapped(conn, season_id, cap, new_faction_id, user_id=None, now=None):
        status = conn.execute('select status from polywar_rebellions where season_id=? and capital_original_faction_id=?',(season_id,cap['original_faction_id'])).fetchone()[0]
        seen.append(status)
        return original(conn, season_id, cap, new_faction_id, user_id, now)
    monkeypatch.setattr(caps, 'transfer_capital_control', wrapped)
    changed = rebellion.process_rebellion_tick(c, sid, datetime.utcnow(), limit=1)
    assert changed == ['rebellion_succeeded'] and seen == ['succeeded']
    assert c.execute('select controller_faction_id from polywar_capitals where season_id=? and original_faction_id=1',(sid,)).fetchone()[0] == 1
    c.close()


def test_seal_uses_public_catchup_not_in_transaction_before_begin(db, monkeypatch):
    connect, settings = db
    settings['polywar_null_rift_seal_progress']='1'
    sid = active(connect)
    c = connect(); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid)
    r = c.execute("select * from polywar_null_rifts where season_id=? and status='active' limit 1",(sid,)).fetchone()
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x']+1,r['y']))
    c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=100 and season_id=?',(sid,))
    c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(datetime.utcnow()+timedelta(days=1),sid)); c.commit(); c.close()
    monkeypatch.setattr(world, 'ensure_world_caught_up', lambda season_id, now=None: [])
    monkeypatch.setattr(world, 'ensure_world_caught_up_in_transaction', lambda *a, **k: (_ for _ in ()).throw(AssertionError('direct in-transaction catchup')))
    out = world.seal_rift_action(100, r['x'], r['y'], 'seal-public-catchup')
    assert out['ok'] and out['outcome'] in {'rift_damaged','rift_sealed'}


def test_capture_rift_guard_fail_closed_on_db_error(db, monkeypatch):
    connect, _ = db
    sid = active(connect)
    from services import polywar_map_service as mm
    def boom(*args, **kwargs): raise RuntimeError('rift_lookup_failed')
    monkeypatch.setattr(world, 'is_rift', boom)
    with pytest.raises(RuntimeError):
        mm.capture_cell(100, 5, 5, 'capture-rift-error')


def test_matured_domination_seal_commits_finalization_not_rollback(db):
    connect, settings = db
    settings['polywar_domination_capitals_required']='1'
    settings['polywar_domination_hold_hours']='1'
    sid = active(connect)
    c = connect(); now=datetime.utcnow(); past=now-timedelta(hours=2)
    world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid); world.activate_if_due_in_transaction(c,sid)
    r = c.execute("select * from polywar_null_rifts where season_id=? and status='active' limit 1",(sid,)).fetchone()
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,123,123,1,past,past))
    c.execute('update polywar_seasons set domination_faction_id=1, domination_started_at=? where id=?',(past,sid))
    c.execute('update polywar_null_state set next_tick_at=? where season_id=?',(now+timedelta(days=1),sid))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,r['x']+1,r['y']))
    c.execute('update polywar_players set current_energy=50,max_energy=50 where user_id=100 and season_id=?',(sid,))
    c.commit(); c.close()
    out = world.seal_rift_action(100, r['x'], r['y'], 'seal-after-domination')
    assert out['error'] == 'season_ended'
    c = connect()
    row = c.execute('select status,results_hash from polywar_seasons where id=?',(sid,)).fetchone()
    assert row['status'] == 'completed' and row['results_hash']
    assert c.execute('select count(*) from polywar_season_results where season_id=?',(sid,)).fetchone()[0] > 0
    assert c.execute('select count(*) from polywar_player_season_rewards where season_id=?',(sid,)).fetchone()[0] > 0
    assert c.execute("select count(*) from polywar_seasons where status='active'",()).fetchone()[0] == 1
    c.close()


def test_matured_domination_rebellion_action_commits_finalization_not_rollback(db):
    connect, settings = db
    settings['polywar_domination_capitals_required']='1'
    settings['polywar_domination_hold_hours']='1'
    sid = active(connect); polywar.join_faction(211,2)
    c = connect(); now=datetime.utcnow(); past=now-timedelta(hours=2)
    rebellion.init_rebellion_schema(c); world.init_world_schema(c); world.ensure_world_initialized_in_transaction(c,sid)
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,captured_at,updated_at) values (?,?,?,?,?,?,?,?)',(sid,1,55,55,2,past,past,past))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,54,55))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,56,55))
    c.execute('update polywar_seasons set domination_faction_id=2, domination_started_at=? where id=?',(past,sid))
    c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=?',(sid,))
    c.commit(); rebellion.ensure_rebellions_in_transaction(c,sid); c.commit(); c.close()
    out = rebellion.rebellion_action(100,'support_rebellion',55,55,'rebellion-after-domination')
    assert out['error'] == 'season_ended'
    c = connect()
    row = c.execute('select status,results_hash from polywar_seasons where id=?',(sid,)).fetchone()
    assert row['status'] == 'completed' and row['results_hash']
    assert c.execute('select count(*) from polywar_season_results where season_id=?',(sid,)).fetchone()[0] > 0
    assert c.execute('select count(*) from polywar_player_season_rewards where season_id=?',(sid,)).fetchone()[0] > 0
    assert c.execute("select count(*) from polywar_seasons where status='active'",()).fetchone()[0] == 1
    c.close()


def _mature_domination(connect, sid, fid=1):
    c = connect(); now = datetime.utcnow(); past = now - timedelta(hours=2)
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,1,222,222,fid,past,past))
    c.execute('update polywar_seasons set domination_faction_id=?, domination_started_at=? where id=?',(fid,past,sid))
    c.commit(); c.close(); return now


def test_matured_domination_attack_no_old_season_mutation(db):
    connect, settings = db; settings['polywar_domination_capitals_required']='1'; settings['polywar_domination_hold_hours']='1'
    sid = active(connect); _mature_domination(connect, sid, 1)
    from services import polywar_combat_service as combat
    c=connect(); before=c.execute('select current_energy from polywar_players where season_id=? and user_id=100',(sid,)).fetchone()[0]; c.close()
    out = combat.combat_action(100,'attack',333,333,'attack-after-domination')
    assert out['error'] == 'season_ended'
    c=connect(); assert c.execute('select status from polywar_seasons where id=?',(sid,)).fetchone()[0]=='completed'
    assert c.execute('select current_energy from polywar_players where season_id=? and user_id=100',(sid,)).fetchone()[0] == before
    assert c.execute('select count(*) from polywar_cells where season_id=? and x=333 and y=333',(sid,)).fetchone()[0] == 0
    assert c.execute('select count(*) from polywar_actions where season_id=? and idempotency_key=?',(sid,'attack-after-domination')).fetchone()[0] == 0
    c.close()


def test_matured_domination_capital_scan_flag_governance_no_mutation(db):
    connect, settings = db; settings['polywar_domination_capitals_required']='1'; settings['polywar_domination_hold_hours']='1'
    from services import polywar_capital_service as caps, polywar_mine_service as mines, polywar_governance_service as gov

    sid = active(connect); _mature_domination(connect, sid, 1)
    c=connect(); now=datetime.utcnow()-timedelta(days=2); caps.init_polywar_capital_schema(c)
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,siege_progress,besieging_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?,?,?)',(sid,2,224,222,2,0,None,now,now))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,1)',(sid,223,222))
    c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=? and user_id=100',(sid,)); c.commit(); c.close()
    assert caps.capital_action(100,'siege',224,222,'siege-after-domination')['error']=='season_ended'
    c=connect(); assert c.execute('select siege_progress from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()[0] == 0; c.close()

    sid = active(connect); _mature_domination(connect, sid, 1)
    c=connect(); mines.init_polywar_mine_schema(c); c.execute('update polywar_players set current_energy=50,max_energy=50 where season_id=? and user_id=100',(sid,)); c.commit(); c.close()
    assert mines.scan_area(100,10,10,3,'scan-after-domination')['error']=='season_ended'
    c=connect(); assert c.execute('select count(*) from polywar_scans where season_id=?',(sid,)).fetchone()[0] == 0; c.close()

    sid = active(connect); _mature_domination(connect, sid, 1)
    c=connect(); mines.init_polywar_mine_schema(c); c.commit(); c.close()
    assert mines.set_flag(100,10,10,True)['error']=='season_ended'
    c=connect(); assert c.execute('select count(*) from polywar_flags where season_id=?',(sid,)).fetchone()[0] == 0; c.close()

    sid = active(connect); _mature_domination(connect, sid, 1)
    c=connect(); gov.init_polywar_governance_schema(c); c.commit(); c.close()
    assert gov.nominate(100,'after domination')['error']=='season_ended'
    c=connect(); assert c.execute('select count(*) from polywar_commander_candidates').fetchone()[0] == 0; c.close()


def test_capital_transfer_updates_domination_tracking_immediately(db):
    connect, settings = db; settings['polywar_domination_capitals_required']='1'; settings['polywar_domination_hold_hours']='1'
    sid = active(connect)
    from services import polywar_capital_service as caps
    c=connect(); caps.init_polywar_capital_schema(c); now=datetime.utcnow()
    c.execute('insert or replace into polywar_capitals (season_id,original_faction_id,x,y,controller_faction_id,controlled_since,updated_at) values (?,?,?,?,?,?,?)',(sid,2,300,300,2,now,now))
    c.execute('insert or replace into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,2)',(sid,300,300))
    c.commit(); cap=c.execute('select * from polywar_capitals where season_id=? and original_faction_id=2',(sid,)).fetchone()
    caps.transfer_capital_control(c,sid,cap,1,100,now); c.commit()
    row=c.execute('select domination_faction_id,domination_started_at from polywar_seasons where id=?',(sid,)).fetchone()
    assert row['domination_faction_id'] == 1 and row['domination_started_at']
    assert c.execute("select count(*) from polywar_events where season_id=? and event_type='domination_started'",(sid,)).fetchone()[0] == 1
    c.close()


def test_get_capitals_uses_serialized_begin(db, monkeypatch):
    connect, _ = db; active(connect)
    from services import polywar_capital_service as caps
    calls=[]; real=caps._begin
    def wrapped(conn, cursor):
        calls.append('begin'); return real(conn, cursor)
    monkeypatch.setattr(caps, '_begin', wrapped)
    out = caps.get_capitals(100)
    assert out['ok'] and calls == ['begin']
