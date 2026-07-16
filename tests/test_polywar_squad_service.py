import sqlite3, uuid
from pathlib import Path
from datetime import datetime, timedelta

import services.polywar_service as polywar
import services.polywar_map_service as maps
import services.polywar_squad_service as squads
import services.polywar_capital_service as capitals


def db(monkeypatch):
    uri=f"file:squads_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri, uri=True, check_same_thread=False); keeper.row_factory=sqlite3.Row
    def connect():
        c=sqlite3.connect(uri, uri=True, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES); c.row_factory=sqlite3.Row; return c
    settings={"polywar_world_profile":"compact_v2","polywar_world_profile_version":"2","polywar_map_width":"512","polywar_map_height":"512","polywar_chunk_size":"32","polywar_sector_size":"40","polywar_starting_area_size":"21","polywar_squad_pause_without_active_players":"false"}
    monkeypatch.setattr(polywar,'get_connection',connect)
    monkeypatch.setattr(polywar,'get_setting',lambda k,d='': settings.get(k,d))
    monkeypatch.setattr(polywar,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); polywar.init_polywar_schema(c); polywar.ensure_factions(c); c.commit(); return connect, keeper, settings


def make_season(connect, existing=False):
    c=connect(); polywar.begin_serialized_transaction(c); s=polywar.ensure_active_season_in_transaction(c); sid=s['id']; squads.ensure_squad_season_config(c,sid,existing_active=existing); c.commit(); c.close(); return sid


def join(connect, sid, uid=1, fid=1):
    c=connect(); polywar._insert_player_if_missing(c,uid,sid); c.execute('UPDATE polywar_players SET faction_id=? WHERE user_id=? AND season_id=?',(fid,uid,sid)); c.commit(); c.close()


def test_squad_schema_idempotent_sqlite(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    c=connect(); squads.init_squad_schema(c); squads.init_squad_schema(c); c.commit()
    tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'polywar_squad_season_config','polywar_faction_squads','polywar_squad_pressure','polywar_squad_ticks'} <= tables
    keeper.close()


def test_existing_active_disabled_new_season_enabled_and_snapshot_stable(monkeypatch):
    connect, keeper, settings = db(monkeypatch)
    c=connect(); now=datetime.utcnow(); c.execute("INSERT INTO polywar_seasons (name,status,starts_at,ends_at,secret_seed,created_at) VALUES (?,?,?,?,?,?)", ("Existing", "active", now, now+timedelta(days=1), "seed", now)); sid=c.execute("SELECT id FROM polywar_seasons").fetchone()["id"]; squads.ensure_squad_season_config(c,sid,existing_active=True); c.commit(); c.close()
    c=connect(); row=c.execute('SELECT enabled,move_interval_minutes FROM polywar_squad_season_config WHERE season_id=?',(sid,)).fetchone(); assert row['enabled']==0
    settings['polywar_squad_move_interval_minutes']='99'; squads.ensure_squad_season_config(c,sid); row2=c.execute('SELECT move_interval_minutes FROM polywar_squad_season_config WHERE season_id=?',(sid,)).fetchone(); assert row2['move_interval_minutes']==row['move_interval_minutes']; c.close()
    keeper.close()


def test_spawn_move_pressure_and_no_permanent_capture(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1)
    c=connect(); cfg=squads.ensure_squad_season_config(c,sid); squads.enable_squads_for_season(c,sid); maps.ensure_season_map_snapshot(c,sid); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]
    c.execute('INSERT OR IGNORE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,bx,by,1,datetime.utcnow()))
    c.commit(); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,datetime.utcnow()+timedelta(hours=4)); c.commit()
    sq=c.execute('SELECT * FROM polywar_faction_squads WHERE season_id=?',(sid,)).fetchone(); assert sq and out['spawned_count']>=1
    old=(sq['x'],sq['y']); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,datetime.utcnow()+timedelta(hours=4,minutes=11)); c.commit()
    sq2=c.execute('SELECT * FROM polywar_faction_squads WHERE id=?',(sq['id'],)).fetchone(); assert abs(sq2['x']-old[0])+abs(sq2['y']-old[1]) <= 1
    assert c.execute('SELECT COUNT(*) FROM polywar_cells WHERE season_id=? AND owner_faction_id=1',(sid,)).fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM polywar_squad_pressure WHERE season_id=?',(sid,)).fetchone()[0] >= 0
    c.close(); keeper.close()


def test_supply_limit_and_cardinal_step(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1)
    c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]; now=datetime.utcnow()
    c.execute("UPDATE polywar_squad_season_config SET supply_distance=1, require_faction_members=1 WHERE season_id=?",(sid,))
    c.execute("INSERT INTO polywar_faction_squads (season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(sid,1,1,'marching',bx,by,bx,by,bx+5,by,bx,by,100,100,0,0,now,now,now+timedelta(hours=1),now,now))
    c.commit(); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now+timedelta(minutes=10)); c.commit()
    sq=c.execute('SELECT * FROM polywar_faction_squads WHERE season_id=?',(sid,)).fetchone(); assert sq['status'] in {'waiting_for_supply','marching'}; assert abs(sq['x']-bx)+abs(sq['y']-by) <= 1
    keeper.close()


def test_service_source_no_auto_win_imports():
    src=open('services/polywar_squad_service.py',encoding='utf-8').read()
    forbidden=['transfer_capital_control','finalize_season_in_transaction','maybe_finalize_in_transaction','capture_cell(','faction_contribution=faction_contribution']
    assert not any(tok in src for tok in forbidden)


def test_disabled_config_does_not_create_tick(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.disable_squads_for_season(c,sid); c.commit(); c.close()
    c=connect(); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,datetime.utcnow()); c.commit()
    assert out['reason']=='squads_disabled'
    assert c.execute('SELECT COUNT(*) FROM polywar_squad_ticks WHERE season_id=?',(sid,)).fetchone()[0] == 0
    keeper.close()


def test_duplicate_tick_keeps_transaction_usable(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1)
    c=connect(); squads.enable_squads_for_season(c,sid); c.commit(); now=datetime.utcnow()+timedelta(hours=2); polywar.begin_serialized_transaction(c); first=squads.process_squad_tick_in_transaction(c,sid,now); second=squads.process_squad_tick_in_transaction(c,sid,now); usable=c.execute('SELECT 1').fetchone()[0]; c.commit()
    assert first['processed'] is True and second['duplicate'] is True and usable == 1
    keeper.close()


def test_engaged_pair_damage_once_and_survivor_waits(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); cfg=squads.ensure_squad_season_config(c,sid); c.execute('UPDATE polywar_squad_season_config SET combat_damage_per_tick=20,max_active_per_faction=0 WHERE season_id=?',(sid,))
    for i,(hp,eng) in enumerate([(100,2),(100,1)], start=1):
        c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,engaged_squad_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(i,sid,i,i,'engaged',10+i,10,10+i,10,20,10,10+i,10,hp,100,0,0,now,now,now+timedelta(hours=1),eng,now,now))
    c.commit(); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,now+timedelta(minutes=10)); c.commit()
    rows=c.execute('SELECT id,hp,status,engaged_squad_id FROM polywar_faction_squads ORDER BY id').fetchall()
    assert out['combat_count']==1
    assert [r['hp'] for r in rows] == [80,80]
    keeper.close()


def test_support_rejects_mine_locked_player_and_disabled_season(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1)
    c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,sid,1,1,'marching',10,10,10,10,11,10,10,10,50,100,0,0,now,now,now+timedelta(hours=1),now,now)); c.execute('UPDATE polywar_players SET locked_until=? WHERE user_id=1 AND season_id=?',(now+timedelta(minutes=5),sid)); c.commit(); c.close()
    try:
        squads.support_squad(1,1,'lock')
        assert False
    except ValueError as e:
        assert str(e)=='player_locked'
    c=connect(); c.execute('UPDATE polywar_players SET locked_until=NULL WHERE user_id=1 AND season_id=?',(sid,)); squads.disable_squads_for_season(c,sid); c.commit(); c.close()
    try:
        squads.support_squad(1,1,'disabled')
        assert False
    except ValueError as e:
        assert str(e)=='squads_disabled'
    keeper.close()


def test_get_state_source_does_not_run_squad_catchup():
    src=open('services/polywar_service.py',encoding='utf-8').read()
    block=src[src.index('def get_state'):]
    assert 'ensure_squads_caught_up_in_transaction' not in block
    assert 'polywar_squad_ticks' not in block


def test_squad_source_uses_postgres_safe_claims_and_locks():
    src=open('services/polywar_squad_service.py',encoding='utf-8').read()
    assert 'ON CONFLICT (season_id,tick_index) DO NOTHING RETURNING id' in src
    assert 'FOR UPDATE SKIP LOCKED' in src
    assert 'INSERT OR IGNORE INTO polywar_squad_ticks' in src

def test_new_engagement_does_not_resolve_combat_until_next_due_tick(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]; c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0 WHERE season_id=?',(sid,))
    for i,(fid,x,tx) in enumerate([(1,bx,bx+1),(2,bx+1,bx)], start=1):
        c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(i,sid,fid,i,'marching',x,by,x,by,tx,by,x,by,100,100,0,0,now,now,now+timedelta(hours=1),now,now))
    c.commit(); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,now+timedelta(minutes=10)); c.commit()
    rows=c.execute('SELECT id,hp,status,engaged_squad_id FROM polywar_faction_squads ORDER BY id').fetchall()
    assert out['combat_count']==0
    assert [r['hp'] for r in rows] == [100,100]
    assert [r['status'] for r in rows] == ['engaged','engaged']
    keeper.close()


def test_support_success_enemy_reject_and_idempotency_deducts_once(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1); join(connect,sid,2,2)
    c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); c.execute('UPDATE polywar_squad_season_config SET support_energy_cost=2,support_hp=25 WHERE season_id=?',(sid,))
    c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,sid,1,1,'marching',10,10,10,10,11,10,10,10,50,100,0,0,now,now,now+timedelta(hours=1),now,now))
    c.commit(); c.close()
    first=squads.support_squad(1,1,'support-once'); dup=squads.support_squad(1,1,'support-once')
    c=connect(); player=c.execute('SELECT current_energy FROM polywar_players WHERE user_id=1 AND season_id=?',(sid,)).fetchone(); sq=c.execute('SELECT hp FROM polywar_faction_squads WHERE id=1').fetchone()
    assert first['ok'] is True and dup.get('duplicate') is True
    assert player['current_energy'] == 8
    assert sq['hp'] == 75
    try:
        squads.support_squad(2,1,'enemy')
        assert False
    except ValueError as e:
        assert str(e)=='squad_not_allied'
    keeper.close()


def test_stale_processing_tick_recovered_once(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); cfg=squads.ensure_squad_season_config(c,sid); tick=squads._tick_index_for(cfg, now)
    stale=now-timedelta(minutes=squads.SQUAD_TICK_STALE_MINUTES+1); scheduled=squads._tick_time_for(cfg,tick)
    c.execute("INSERT INTO polywar_squad_ticks (season_id,tick_index,scheduled_at,started_at,status,created_at,outcome_json) VALUES (?,?,?,?,?,?,?)",(sid,tick,scheduled,stale,'processing',stale,'{}'))
    c.commit(); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=scheduled); c.commit()
    row=c.execute('SELECT status,outcome_json FROM polywar_squad_ticks WHERE season_id=? AND tick_index=?',(sid,tick)).fetchone()
    assert out['processed'] is True
    assert row['status']=='completed'
    assert 'recovered_from_status' in row['outcome_json']
    keeper.close()


def test_catchup_does_not_claim_before_next_move_at_and_later_processes(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]
    now=datetime(2026,1,1,12,10,0); due=datetime(2026,1,1,12,13,0)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,move_interval_minutes=10 WHERE season_id=?',(sid,))
    c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,sid,1,1,'marching',bx,by,bx,by,bx+1,by,bx,by,100,100,0,0,now,due,now+timedelta(hours=1),now,now))
    c.commit(); polywar.begin_serialized_transaction(c); early=squads.ensure_squads_caught_up_in_transaction(c,sid,now); c.commit()
    assert early['reason']=='nothing_due'
    assert c.execute('SELECT COUNT(*) FROM polywar_squad_ticks WHERE season_id=?',(sid,)).fetchone()[0] == 0
    before=c.execute('SELECT x,y FROM polywar_faction_squads WHERE id=1').fetchone()
    polywar.begin_serialized_transaction(c); late=squads.ensure_squads_caught_up_in_transaction(c,sid,due); c.commit()
    after=c.execute('SELECT x,y FROM polywar_faction_squads WHERE id=1').fetchone()
    assert late['processed_count']==1
    assert abs(after['x']-before['x'])+abs(after['y']-before['y']) <= 1
    assert c.execute('SELECT COUNT(*) FROM polywar_squad_ticks WHERE season_id=?',(sid,)).fetchone()[0] == 1
    keeper.close()


def test_broken_engagement_repairs_destroyed_missing_expired_and_nonreciprocal(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    cases=[('destroyed',2),('missing',None),('expired',2),('nonreciprocal',2)]
    for idx,(case,enemy_id) in enumerate(cases, start=1):
        sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow()+timedelta(hours=idx); c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0 WHERE season_id=?',(sid,))
        base=idx*10
        c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,engaged_squad_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(base+1,sid,1,idx,'engaged',10,10,10,10,20,10,10,10,100,100,0,0,now,now,now+timedelta(hours=1),base+2,now,now))
        if case!='missing':
            status='destroyed' if case=='destroyed' else 'expired' if case=='expired' else 'engaged'
            hp=0 if case=='destroyed' else 100
            reciprocal=None if case=='nonreciprocal' else base+1
            c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,engaged_squad_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(base+2,sid,2,idx,status,11,10,11,10,20,10,11,10,hp,100,0,0,now,now,now+timedelta(hours=1),reciprocal,now,now))
        c.commit(); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,now+timedelta(minutes=10)); c.commit()
        survivor=c.execute('SELECT status,engaged_squad_id,x,y FROM polywar_faction_squads WHERE id=?',(base+1,)).fetchone()
        assert out['combat_count']==0
        assert survivor['status']=='marching' and survivor['engaged_squad_id'] is None, (case, out, dict(survivor))
        assert (survivor['x'],survivor['y']) == (10,10)
        c.close()
    keeper.close()


def test_disabled_visible_response_hides_squads_and_pressure(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.disable_squads_for_season(c,sid); now=datetime.utcnow()
    c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,sid,1,1,'marching',10,10,10,10,11,10,10,10,50,100,0,0,now,now,now+timedelta(hours=1),now,now))
    c.execute('INSERT INTO polywar_squad_pressure (season_id,x,y,faction_id,pressure,source_squad_id,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',(sid,10,10,1,50,1,now+timedelta(hours=1),now,now)); c.commit(); c.close()
    out=squads.visible_squads(1,0,0,20,20)
    assert out['squads_enabled'] is False and out['squads']==[] and out['pressure']==[]
    assert out['support_energy_cost'] == 1
    c=connect(); assert c.execute('SELECT COUNT(*) FROM polywar_cells WHERE season_id=?',(sid,)).fetchone()[0] == 0; c.close(); keeper.close()


def test_support_duplicate_survives_later_destroyed_disabled_and_zero_cost(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); c.execute('UPDATE polywar_squad_season_config SET support_energy_cost=0,support_hp=25 WHERE season_id=?',(sid,))
    c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,sid,1,1,'marching',10,10,10,10,11,10,10,10,50,100,0,0,now,now,now+timedelta(hours=1),now,now)); c.commit(); c.close()
    first=squads.support_squad(1,1,'zero-cost')
    c=connect(); c.execute("UPDATE polywar_faction_squads SET status='destroyed' WHERE id=1"); squads.disable_squads_for_season(c,sid); c.commit(); c.close()
    dup=squads.support_squad(1,1,'zero-cost')
    c=connect(); player=c.execute('SELECT current_energy FROM polywar_players WHERE user_id=1 AND season_id=?',(sid,)).fetchone(); c.close()
    assert first['ok'] is True and dup.get('duplicate') is True
    assert dup['squad']['hp'] == 75
    assert player['current_energy'] == 10
    keeper.close()


def test_due_times_same_bucket_get_distinct_tick_keys_and_no_starvation(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,move_interval_minutes=10 WHERE season_id=?',(sid,))
    t0=datetime(2026,1,1,12,10,0); a_due=datetime(2026,1,1,12,13,0); b_due=datetime(2026,1,1,12,19,0)
    c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,sid,1,1,'marching',bx,by,bx,by,bx+1,by,bx,by,100,100,0,0,t0,a_due,t0+timedelta(hours=2),t0,t0))
    c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(2,sid,2,1,'marching',bx+5,by,bx+5,by,bx+6,by,bx+5,by,100,100,0,0,t0,b_due,t0+timedelta(hours=2),t0,t0))
    c.commit(); polywar.begin_serialized_transaction(c); early=squads.ensure_squads_caught_up_in_transaction(c,sid,t0); c.commit()
    assert early['reason']=='nothing_due'
    assert c.execute('SELECT COUNT(*) FROM polywar_squad_ticks WHERE season_id=?',(sid,)).fetchone()[0] == 0
    before_b=c.execute('SELECT x,y,move_index FROM polywar_faction_squads WHERE id=2').fetchone()
    polywar.begin_serialized_transaction(c); at_a=squads.ensure_squads_caught_up_in_transaction(c,sid,a_due); c.commit()
    assert at_a['processed_count']==1 and at_a['results'][0]['tick_index']==int(a_due.timestamp())
    mid_b=c.execute('SELECT x,y,move_index FROM polywar_faction_squads WHERE id=2').fetchone()
    assert tuple(mid_b)==tuple(before_b)
    polywar.begin_serialized_transaction(c); at_b=squads.ensure_squads_caught_up_in_transaction(c,sid,b_due); c.commit()
    assert at_b['processed_count']==1 and at_b['results'][0]['tick_index']==int(b_due.timestamp())
    assert c.execute("SELECT COUNT(*) FROM polywar_squad_ticks WHERE season_id=? AND status='completed'",(sid,)).fetchone()[0] == 2
    after_b=c.execute('SELECT x,y,move_index FROM polywar_faction_squads WHERE id=2').fetchone()
    assert after_b['move_index'] == before_b['move_index'] + 1 or c.execute('SELECT status FROM polywar_faction_squads WHERE id=2').fetchone()[0] == 'attacking_cell'
    polywar.begin_serialized_transaction(c); again=squads.ensure_squads_caught_up_in_transaction(c,sid,b_due); c.commit()
    assert again['processed'] is False and again['reason'] in {'nothing_due','tick_completed','tick_processing'}
    assert c.execute('SELECT move_index FROM polywar_faction_squads WHERE id=2').fetchone()[0] == after_b['move_index']
    keeper.close()


def test_exact_scheduled_tick_key_is_epoch_seconds(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); cfg=squads.ensure_squad_season_config(c,sid)
    a=datetime(2026,1,1,12,13,0); b=datetime(2026,1,1,12,19,0)
    assert squads._tick_index_for(cfg,a)==int(a.timestamp())
    assert squads._tick_index_for(cfg,b)==int(b.timestamp())
    assert squads._tick_index_for(cfg,a) != squads._tick_index_for(cfg,b)
    keeper.close()


def test_squad_config_bootstrap_savepoint_keeps_transaction_usable(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    import services.polywar_squad_service as squad_mod
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError('simulated squad config failure')
    monkeypatch.setattr(squad_mod, 'ensure_squad_season_config', boom)
    c=connect(); polywar.begin_serialized_transaction(c); season=polywar.ensure_active_season_in_transaction(c); usable=c.execute('SELECT COUNT(*) FROM polywar_seasons').fetchone()[0]; c.commit()
    assert season['id'] and usable >= 1
    keeper.close()


def _insert_squad(c, sid, *, id=1, fid=1, status='marching', x=10, y=10, hp=100, next_at=None, expires_at=None, engaged=None, supply=None, target=None, spawn_index=None):
    now = next_at or datetime.utcnow()
    sx, sy = supply or (x, y)
    tx, ty = target or (x + 1, y)
    c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,engaged_squad_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (id, sid, fid, spawn_index or id, status, x, y, x, y, tx, ty, sx, sy, hp, 100, 0, 0, now, next_at or now, expires_at or now + timedelta(hours=2), engaged, now, now))


def test_reinforcement_additive_schema_and_zero_defaults(monkeypatch):
    connect, keeper, settings = db(monkeypatch)
    settings['polywar_squad_support_hp'] = '0'
    settings['polywar_squad_reinforcement_boost_minutes'] = '0'
    settings['polywar_squad_reinforcement_min_remaining_minutes'] = '0'
    settings['polywar_squad_reinforcement_energy_cost'] = '0'
    sid = make_season(connect, existing=False)
    c = connect(); row = c.execute('SELECT config_version,support_hp,reinforcement_boost_minutes,reinforcement_min_remaining_minutes,reinforcement_energy_cost,reinforcement_delay_notified_at FROM polywar_squad_season_config sc LEFT JOIN polywar_faction_squads fs ON 1=0 WHERE sc.season_id=?', (sid,)).fetchone()
    assert row['config_version'] == 3
    assert row['support_hp'] == 0 and row['reinforcement_boost_minutes'] == 0 and row['reinforcement_min_remaining_minutes'] == 0 and row['reinforcement_energy_cost'] == 0
    cols = {r['name'] for r in c.execute('PRAGMA table_info(polywar_faction_squads)')}
    assert {'defeated_at','reinforcement_at','reinforcement_delay_notified_at','last_reinforced_at','reinforcement_count','reinforcement_boost_count','defeated_by_squad_id'} <= cols
    keeper.close()


def test_combat_defeat_examples_event_once_and_idempotent_helper(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    # 100 vs 100 -> 80/80 engaged
    sid = make_season(connect, existing=False); c = connect(); squads.enable_squads_for_season(c, sid); now = datetime(2026,1,1,12,0); c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,combat_damage_per_tick=20 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=1,fid=1,status='engaged',x=10,y=10,hp=100,next_at=now,engaged=2)
    _insert_squad(c,sid,id=2,fid=2,status='engaged',x=11,y=10,hp=100,next_at=now,engaged=1)
    c.commit(); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    assert [(r['hp'],r['status']) for r in c.execute('SELECT hp,status FROM polywar_faction_squads ORDER BY id')] == [(80,'engaged'),(80,'engaged')]
    # 20 vs 100 -> awaiting / 80 marching
    sid = make_season(connect, existing=False); c = connect(); squads.enable_squads_for_season(c, sid); now += timedelta(hours=1); c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,combat_damage_per_tick=20 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=11,fid=1,status='engaged',x=20,y=10,hp=20,next_at=now,engaged=12)
    _insert_squad(c,sid,id=12,fid=2,status='engaged',x=21,y=10,hp=100,next_at=now,engaged=11)
    c.commit(); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    rows=c.execute('SELECT id,hp,status,engaged_squad_id,defeated_by_squad_id,reinforcement_at FROM polywar_faction_squads WHERE id IN (11,12) ORDER BY id').fetchall()
    assert (rows[0]['hp'], rows[0]['status'], rows[0]['engaged_squad_id'], rows[0]['defeated_by_squad_id']) == (0,'awaiting_reinforcement',None,12)
    assert rows[0]['reinforcement_at'] is not None and (rows[1]['hp'], rows[1]['status'], rows[1]['engaged_squad_id']) == (80,'marching',None)
    assert c.execute("SELECT COUNT(*) FROM polywar_events WHERE season_id=? AND event_type='squad_defeated'",(sid,)).fetchone()[0] == 1
    cfg=squads.ensure_squad_season_config(c,sid); polywar.begin_serialized_transaction(c); assert squads.mark_squad_awaiting_reinforcement_in_transaction(c, dict(rows[0]), 12, cfg, now) is False; assert c.execute('SELECT 1').fetchone()[0] == 1; c.commit()
    assert c.execute("SELECT COUNT(*) FROM polywar_events WHERE season_id=? AND event_type='squad_defeated'",(sid,)).fetchone()[0] == 1
    # 20 vs 20 -> both awaiting
    sid = make_season(connect, existing=False); c = connect(); squads.enable_squads_for_season(c, sid); now += timedelta(hours=1); c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,combat_damage_per_tick=20 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=21,fid=1,status='engaged',x=30,y=10,hp=20,next_at=now,engaged=22)
    _insert_squad(c,sid,id=22,fid=2,status='engaged',x=31,y=10,hp=20,next_at=now,engaged=21)
    c.commit(); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    assert [r['status'] for r in c.execute('SELECT status FROM polywar_faction_squads WHERE id IN (21,22) ORDER BY id')] == ['awaiting_reinforcement','awaiting_reinforcement']
    keeper.close()


def test_awaiting_inert_slot_and_occupancy(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]; now=datetime(2026,1,1,12,0)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=1,require_faction_members=1 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=1,fid=1,status='awaiting_reinforcement',x=bx,y=by,hp=0,next_at=now,expires_at=now+timedelta(hours=2),supply=(bx,by))
    c.execute('UPDATE polywar_faction_squads SET reinforcement_at=? WHERE id=1',(now+timedelta(hours=1),))
    c.execute('INSERT OR IGNORE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,bx,by,1,now))
    c.commit(); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    sq=c.execute('SELECT status,hp,x,y FROM polywar_faction_squads WHERE id=1').fetchone()
    assert out['moved_count']==0 and out['combat_count']==0 and sq['status']=='awaiting_reinforcement' and (sq['x'],sq['y'])==(bx,by)
    polywar.begin_serialized_transaction(c); spawned=squads.spawn_due_squads_in_transaction(c,sid,now+timedelta(hours=4)); c.commit()
    assert spawned == 0
    # awaiting does not block active movement into same cell
    _insert_squad(c,sid,id=2,fid=2,status='marching',x=bx+1,y=by,hp=100,next_at=now,target=(bx,by),supply=(bx+1,by))
    c.commit(); kind,val=squads._choose_step(c, dict(c.execute('SELECT * FROM polywar_faction_squads WHERE id=2').fetchone()), squads.ensure_squad_season_config(c,sid), 'seed', config)
    assert kind in {'move','wait','attack_cell'}
    assert c.execute('SELECT COUNT(*) FROM polywar_squad_pressure WHERE season_id=?',(sid,)).fetchone()[0] == 0
    keeper.close()


def test_expired_awaiting_does_not_starve_later_due_and_hides(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]; t0=datetime(2026,1,1,12,0); later=t0+timedelta(minutes=5)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=1,fid=1,status='awaiting_reinforcement',x=bx,y=by,hp=0,next_at=t0,expires_at=t0-timedelta(seconds=1),supply=(bx,by))
    c.execute('UPDATE polywar_faction_squads SET reinforcement_at=? WHERE id=1',(t0,))
    _insert_squad(c,sid,id=2,fid=2,status='marching',x=bx+5,y=by,hp=100,next_at=later,expires_at=t0+timedelta(hours=2),supply=(bx+5,by),target=(bx+6,by))
    c.commit(); polywar.begin_serialized_transaction(c); first=squads.ensure_squads_caught_up_in_transaction(c,sid,t0); c.commit()
    assert c.execute('SELECT status,reinforcement_at FROM polywar_faction_squads WHERE id=1').fetchone()['status']=='expired'
    assert c.execute("SELECT COUNT(*) FROM polywar_events WHERE season_id=? AND event_type='squad_reinforced'",(sid,)).fetchone()[0] == 0
    polywar.begin_serialized_transaction(c); second=squads.ensure_squads_caught_up_in_transaction(c,sid,later); usable=c.execute('SELECT 1').fetchone()[0]; c.commit()
    assert second['processed_count'] >= 1 and usable == 1
    row2=c.execute('SELECT move_index,status FROM polywar_faction_squads WHERE id=2').fetchone(); assert row2['move_index'] >= 1 or row2['status']=='attacking_cell'
    out=squads.visible_squads(1,bx-1,by-1,bx+1,by+1)
    assert all(s['id'] != 1 for s in out['squads'])
    keeper.close()


def test_reinforcement_return_once_no_safe_cell_rate_limit_and_safe_capital_controller(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); bx,by=config.bases[1]; now=datetime(2026,1,1,12,0)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,reinforcement_return_radius=0,reinforcement_hp=50 WHERE season_id=?',(sid,))
    c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,bx,by,1,now))
    _insert_squad(c,sid,id=1,fid=1,status='awaiting_reinforcement',x=bx+2,y=by,hp=0,next_at=now,expires_at=now+timedelta(hours=2),supply=(bx,by))
    c.execute('UPDATE polywar_faction_squads SET reinforcement_at=? WHERE id=1',(now,))
    c.commit(); polywar.begin_serialized_transaction(c); assert squads.process_due_reinforcements_in_transaction(c,sid,squads.ensure_squad_season_config(c,sid),now,now,config=config)==1; assert squads.process_due_reinforcements_in_transaction(c,sid,squads.ensure_squad_season_config(c,sid),now,now,config=config)==0; c.commit()
    sq=c.execute('SELECT status,hp,reinforcement_count,next_move_at FROM polywar_faction_squads WHERE id=1').fetchone(); assert (sq['status'],sq['hp'],sq['reinforcement_count']) == ('marching',50,1)
    assert c.execute("SELECT COUNT(*) FROM polywar_events WHERE season_id=? AND event_type='squad_reinforced'",(sid,)).fetchone()[0] == 1
    # no safe cell: retry updates but event is rate-limited
    _insert_squad(c,sid,id=2,fid=1,status='awaiting_reinforcement',x=bx+3,y=by,hp=0,next_at=now,expires_at=now+timedelta(hours=2),supply=(bx+9,by+9))
    _insert_squad(c,sid,id=3,fid=1,status='marching',x=bx+9,y=by+9,hp=100,next_at=now+timedelta(hours=1),expires_at=now+timedelta(hours=2),supply=(bx+9,by+9))
    c.execute('UPDATE polywar_faction_squads SET reinforcement_at=? WHERE id=2',(now,)); c.commit()
    polywar.begin_serialized_transaction(c); squads.process_due_reinforcements_in_transaction(c,sid,squads.ensure_squad_season_config(c,sid),now,now,config=config); c.commit()
    polywar.begin_serialized_transaction(c); squads.process_due_reinforcements_in_transaction(c,sid,squads.ensure_squad_season_config(c,sid),now+timedelta(minutes=10),now+timedelta(minutes=10),config=config); c.commit()
    assert c.execute("SELECT COUNT(*) FROM polywar_events WHERE season_id=? AND event_type='squad_reinforcement_delayed'",(sid,)).fetchone()[0] == 1
    assert c.execute('SELECT status,hp FROM polywar_faction_squads WHERE id=2').fetchone()['status']=='awaiting_reinforcement'
    # current capital controller decides eligibility, not original owner; no mutation occurs
    capitals.ensure_capitals_initialized(c,sid)
    cap=c.execute('SELECT * FROM polywar_capitals WHERE season_id=? AND original_faction_id=?',(sid,1)).fetchone(); c.execute('UPDATE polywar_capitals SET controller_faction_id=2 WHERE season_id=? AND original_faction_id=?',(sid,1)); c.commit()
    fake={'id':99,'season_id':sid,'faction_id':1,'x':cap['x'],'y':cap['y'],'supply_x':cap['x'],'supply_y':cap['y']}
    assert squads._safe_return_cell(c,fake,{'reinforcement_return_radius':0},'seed',config) is None
    assert c.execute('SELECT controller_faction_id,siege_progress FROM polywar_capitals WHERE season_id=? AND original_faction_id=?',(sid,1)).fetchone()['controller_faction_id']==2
    keeper.close()


def test_reinforcement_support_zero_values_and_duplicates(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); join(connect,sid,1,1); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); c.execute('UPDATE polywar_squad_season_config SET reinforcement_energy_cost=0,reinforcement_boost_minutes=15,reinforcement_min_remaining_minutes=0,support_hp=0 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=1,fid=1,status='awaiting_reinforcement',x=10,y=10,hp=0,next_at=now,expires_at=now+timedelta(hours=2))
    c.execute('UPDATE polywar_faction_squads SET reinforcement_at=? WHERE id=1',(now+timedelta(hours=1),)); c.commit(); c.close()
    out=squads.support_squad(1,1,'boost-zero-energy','reinforcement'); dup=squads.support_squad(1,1,'boost-zero-energy','reinforcement')
    c=connect(); assert out['support_type']=='reinforcement' and out['energy_cost']==0 and dup.get('duplicate') is True
    assert c.execute('SELECT current_energy FROM polywar_players WHERE user_id=1 AND season_id=?',(sid,)).fetchone()[0] == 10
    c.execute('UPDATE polywar_squad_season_config SET reinforcement_boost_minutes=0 WHERE season_id=?',(sid,)); c.commit(); c.close()
    try:
        squads.support_squad(1,1,'boost-disabled','reinforcement')
        assert False
    except ValueError as e:
        assert str(e) == 'reinforcement_boost_disabled'
    keeper.close()


def test_no_safe_cell_retry_sql_is_postgresql_safe_and_transaction_usable(monkeypatch):
    assert 'CASE WHEN %s THEN' not in Path('services/polywar_squad_service.py').read_text()
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime(2026,1,1,12,0); bx,by=config.bases[1]
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,reinforcement_return_radius=0 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=77,fid=1,status='awaiting_reinforcement',x=bx+8,y=by+8,hp=0,next_at=now,expires_at=now+timedelta(hours=2),supply=(bx+9,by+9))
    c.execute('UPDATE polywar_faction_squads SET reinforcement_at=? WHERE id=77',(now,)); c.commit()
    monkeypatch.setattr(squads, '_safe_return_cell', lambda *a, **k: None)
    polywar.begin_serialized_transaction(c); squads.process_due_reinforcements_in_transaction(c,sid,squads.ensure_squad_season_config(c,sid),now,now,config=config); usable=c.execute('SELECT 1').fetchone()[0]; c.commit()
    row=c.execute('SELECT status,hp,expires_at,reinforcement_at FROM polywar_faction_squads WHERE id=77').fetchone()
    assert usable == 1 and row['status']=='awaiting_reinforcement' and row['hp']==0 and row['reinforcement_at'] is not None
    keeper.close()


def test_supply_zero_coordinates_are_preserved_for_safe_return(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime(2026,1,1,12,0)
    c.execute('UPDATE polywar_squad_season_config SET reinforcement_return_radius=0 WHERE season_id=?',(sid,))
    c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,0,0,1,now))
    monkeypatch.setattr(squads, '_passable', lambda seed,x,y,config: 0 <= x < config.width and 0 <= y < config.height)
    monkeypatch.setattr(squads.m, 'owner_at_with_config', lambda conn,sid,x,y,config: 1 if (x,y)==(0,0) else None)
    monkeypatch.setattr(squads, '_capital_at', lambda conn,sid,x,y: None)
    fake={'id':501,'season_id':sid,'faction_id':1,'x':25,'y':25,'supply_x':0,'supply_y':0}
    assert squads._safe_return_cell(c,fake,{'reinforcement_return_radius':0},'seed',config) == (0,0)
    keeper.close()


def test_existing_config_version_migrates_to_three_without_rollout_changes(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    c=connect(); squads.init_squad_schema(c)
    now=datetime(2026,1,1,12,0)
    c.execute("INSERT INTO polywar_squad_season_config (season_id,enabled,config_version,spawn_interval_minutes,move_interval_minutes,max_active_per_faction,ttl_minutes,max_hp,supply_distance,pressure_ttl_minutes,neutral_pressure_per_step,enemy_pressure_per_step,enemy_pressure_cap,capital_pressure_cap,combat_damage_per_tick,support_energy_cost,support_hp,max_catchup_ticks,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(99,0,1,111,12,3,700,90,20,300,80,10,50,20,15,2,30,5,now,now))
    c.commit(); squads.init_squad_schema(c); row=c.execute('SELECT enabled,config_version,spawn_interval_minutes,move_interval_minutes,max_hp,reinforcement_cooldown_minutes,reinforcement_hp FROM polywar_squad_season_config WHERE season_id=99').fetchone()
    assert dict(row) == {'enabled':0,'config_version':3,'spawn_interval_minutes':111,'move_interval_minutes':12,'max_hp':90,'reinforcement_cooldown_minutes':60,'reinforcement_hp':50}
    keeper.close()

def test_new_season_squad_snapshot_uses_new_defaults(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); row=squads.ensure_squad_season_config(c,sid)
    assert row['move_interval_minutes']==squads.DEFAULTS['move_interval_minutes']
    assert row['max_active_per_faction']==squads.DEFAULTS['max_active_per_faction']
    assert row['ttl_minutes']==squads.DEFAULTS['ttl_minutes']
    assert row['supply_distance']==squads.DEFAULTS['supply_distance']
    assert row['require_faction_members']==0 and row['enemy_cell_capture_enabled']==1
    c.close(); keeper.close()


def test_existing_config_legacy_safe_then_explicit_update(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    c=connect(); squads.init_squad_schema(c); now=datetime.utcnow()
    c.execute("INSERT INTO polywar_squad_season_config (season_id,enabled,config_version,spawn_interval_minutes,move_interval_minutes,max_active_per_faction,ttl_minutes,max_hp,supply_distance,pressure_ttl_minutes,neutral_pressure_per_step,enemy_pressure_per_step,enemy_pressure_cap,capital_pressure_cap,combat_damage_per_tick,support_energy_cost,support_hp,max_catchup_ticks,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(50,1,1,180,10,1,720,100,24,360,100,15,60,20,20,1,25,6,now,now))
    c.commit(); squads.init_squad_schema(c)
    row=c.execute('SELECT require_faction_members,enemy_cell_capture_enabled FROM polywar_squad_season_config WHERE season_id=50').fetchone()
    assert (row['require_faction_members'],row['enemy_cell_capture_enabled'])==(1,0)
    squads.update_squad_season_config(c,50,require_faction_members=False,enemy_cell_capture_enabled=True,enemy_cell_attack_progress_per_tick=7)
    row=c.execute('SELECT require_faction_members,enemy_cell_capture_enabled,enemy_cell_attack_progress_per_tick FROM polywar_squad_season_config WHERE season_id=50').fetchone()
    assert (row['require_faction_members'],row['enemy_cell_capture_enabled'],row['enemy_cell_attack_progress_per_tick'])==(0,1,7)
    keeper.close()


def test_all_playable_spawn_without_members_and_null_skipped(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); maps.ensure_season_map_snapshot(c,sid)
    c.execute('UPDATE polywar_squad_season_config SET require_faction_members=0,max_active_per_faction=1 WHERE season_id=?',(sid,)); c.commit()
    polywar.begin_serialized_transaction(c); spawned=squads.spawn_due_squads_in_transaction(c,sid,datetime.utcnow()+timedelta(hours=4)); c.commit()
    fids=[r['faction_id'] for r in c.execute('SELECT faction_id FROM polywar_faction_squads WHERE season_id=? ORDER BY faction_id',(sid,))]
    assert spawned>=7 and set(range(1,8)) <= set(fids) and 8 not in fids
    keeper.close()


def test_enemy_squad_on_enemy_cell_engages_before_cell_attack(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime.utcnow()
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0 WHERE season_id=?',(sid,))
    c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,11,10,2,now))
    _insert_squad(c,sid,id=1,fid=1,x=10,y=10,target=(12,10),next_at=now); _insert_squad(c,sid,id=2,fid=2,x=11,y=10,target=(10,10),next_at=now)
    c.commit(); monkeypatch.setattr(squads, '_passable', lambda seed,x,y,config: True); kind,val=squads._choose_step(c,dict(c.execute('SELECT * FROM polywar_faction_squads WHERE id=1').fetchone()),squads.ensure_squad_season_config(c,sid),'seed',config)
    assert kind=='engage' and val['id']==2
    keeper.close()


def test_squad_attack_rival_progress_reduced_not_stolen(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime.utcnow()
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,enemy_cell_attack_progress_per_tick=10 WHERE season_id=?',(sid,))
    c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,contesting_faction_id,contest_progress,contested_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',(sid,11,10,3,100,2,25,now,now))
    _insert_squad(c,sid,id=1,fid=1,status='attacking_cell',x=10,y=10,target=(11,10),supply=(10,10),next_at=now); c.execute('UPDATE polywar_faction_squads SET attack_target_x=11,attack_target_y=10 WHERE id=1')
    c.commit(); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    row=c.execute('SELECT contesting_faction_id,contest_progress FROM polywar_cells WHERE season_id=? AND x=11 AND y=10',(sid,)).fetchone()
    assert (row['contesting_faction_id'],row['contest_progress'])==(2,15)
    keeper.close()


def test_squad_capture_threshold_and_event_source(monkeypatch):
    connect, keeper, settings = db(monkeypatch); settings['polywar_capture_progress_required']='30'
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime(2026,1,1,12,0)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,enemy_cell_attack_progress_per_tick=10,enemy_cell_capture_enabled=1 WHERE season_id=?',(sid,))
    c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,11,10,2,now))
    _insert_squad(c,sid,id=1,fid=1,status='attacking_cell',x=10,y=10,next_at=now); c.execute('UPDATE polywar_faction_squads SET attack_target_x=11,attack_target_y=10 WHERE id=1'); c.commit()
    for i in range(3):
        t=now+timedelta(minutes=5*i); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,t,scheduled_at=t); c.commit()
    cell=c.execute('SELECT owner_faction_id FROM polywar_cells WHERE season_id=? AND x=11 AND y=10',(sid,)).fetchone()
    sq=c.execute('SELECT status,attack_target_x,attack_progress FROM polywar_faction_squads WHERE id=1').fetchone()
    ev=c.execute("SELECT user_id,source_squad_id FROM polywar_events WHERE season_id=? AND event_type='squad_cell_captured'",(sid,)).fetchone()
    assert cell['owner_faction_id']==1 and sq['status']=='marching' and sq['attack_target_x'] is None and sq['attack_progress']==0
    assert ev['user_id'] is None and ev['source_squad_id']==1
    keeper.close()


def test_capital_pressure_capped_and_cancelled(monkeypatch):
    connect, keeper, _ = db(monkeypatch)
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime(2026,1,1,12,0)
    capitals.ensure_capitals_initialized(c,sid); cap=c.execute('SELECT * FROM polywar_capitals WHERE season_id=? AND original_faction_id=2',(sid,)).fetchone(); x,y=cap['x'],cap['y']
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,enemy_cell_attack_progress_per_tick=10,capital_pressure_cap=20 WHERE season_id=?',(sid,))
    _insert_squad(c,sid,id=1,fid=1,status='pressuring_capital',x=x-1,y=y,next_at=now); c.execute('UPDATE polywar_faction_squads SET attack_target_x=?,attack_target_y=? WHERE id=1',(x,y)); c.commit()
    for i in range(3):
        t=now+timedelta(minutes=5*i); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,t,scheduled_at=t); c.commit()
    row=c.execute('SELECT status,attack_progress FROM polywar_faction_squads WHERE id=1').fetchone(); cap2=c.execute('SELECT controller_faction_id FROM polywar_capitals WHERE id=?',(cap['id'],)).fetchone()
    assert row['status']=='pressuring_capital' and row['attack_progress']==20 and cap2['controller_faction_id']==2
    c.execute('UPDATE polywar_capitals SET controller_faction_id=? WHERE id=?',(1,cap['id'])); c.commit(); t=now+timedelta(minutes=20); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,t,scheduled_at=t); c.commit()
    assert c.execute('SELECT status,attack_target_x,attack_progress FROM polywar_faction_squads WHERE id=1').fetchone()['status']=='marching'
    keeper.close()

def test_special_cell_errors_fail_closed(monkeypatch):
    connect, keeper, _ = db(monkeypatch); sid=make_season(connect, existing=False); c=connect(); config=maps.load_map_config(c,season_id=sid); now=datetime.utcnow()
    c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,11,10,2,now)); c.commit()
    import services.polywar_world_service as world
    monkeypatch.setattr(world,'is_rift',lambda *a, **k: True); assert squads._attackable_normal_cell(c,sid,1,11,10,config) is False
    monkeypatch.setattr(world,'is_rift',lambda *a, **k: False); monkeypatch.setattr(world,'is_safe_zone',lambda *a, **k: True); assert squads._attackable_normal_cell(c,sid,1,11,10,config) is False
    monkeypatch.setattr(world,'is_safe_zone',lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))); assert squads._attackable_normal_cell(c,sid,1,11,10,config) is False
    monkeypatch.setattr(world,'is_rift',lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('no such table: polywar_null_rifts'))); monkeypatch.setattr(world,'is_safe_zone',lambda *a, **k: False); assert squads._attackable_normal_cell(c,sid,1,11,10,config) is True
    keeper.close()


def test_capture_disabled_at_cap_does_not_spam_events(monkeypatch):
    connect, keeper, _ = db(monkeypatch); sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime(2026,1,1,12,0)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,enemy_cell_attack_progress_per_tick=50,enemy_cell_capture_enabled=0 WHERE season_id=?',(sid,))
    c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,11,10,2,now))
    _insert_squad(c,sid,id=1,fid=1,status='attacking_cell',x=10,y=10,next_at=now); c.execute('UPDATE polywar_faction_squads SET attack_target_x=11,attack_target_y=10 WHERE id=1'); c.commit()
    for i in range(12):
        t=now+timedelta(minutes=5*i); polywar.begin_serialized_transaction(c); out=squads.process_squad_tick_in_transaction(c,sid,t,scheduled_at=t); c.commit()
    cell=c.execute('SELECT owner_faction_id,contest_progress,contesting_faction_id FROM polywar_cells WHERE season_id=? AND x=11 AND y=10',(sid,)).fetchone()
    events=c.execute("SELECT COUNT(*) FROM polywar_events WHERE season_id=? AND event_type IN ('squad_cell_attack_progress','squad_cell_capture_blocked','squad_cell_captured')",(sid,)).fetchone()[0]
    assert (cell['owner_faction_id'],cell['contest_progress'],cell['contesting_faction_id'])==(2,100,1)
    assert events <= 2 and out['cell_attack_count']==0
    keeper.close()


def test_attack_started_and_delayed_events_have_source_squad_id(monkeypatch):
    connect, keeper, _ = db(monkeypatch); sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); config=maps.load_map_config(c,season_id=sid)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0 WHERE season_id=?',(sid,)); c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,11,10,2,now))
    _insert_squad(c,sid,id=1,fid=1,x=10,y=10,target=(11,10),next_at=now); c.commit(); monkeypatch.setattr(squads,'_passable',lambda *a, **k: True)
    polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    ev=c.execute("SELECT user_id,source_squad_id FROM polywar_events WHERE event_type='squad_cell_attack_started'").fetchone(); assert ev['user_id'] is None and ev['source_squad_id']==1
    _insert_squad(c,sid,id=2,fid=1,status='awaiting_reinforcement',x=20,y=20,hp=0,next_at=now); c.execute('UPDATE polywar_faction_squads SET reinforcement_at=? WHERE id=2',(now,)); c.commit(); monkeypatch.setattr(squads,'_safe_return_cell',lambda *a, **k: None)
    polywar.begin_serialized_transaction(c); squads.process_due_reinforcements_in_transaction(c,sid,squads.ensure_squad_season_config(c,sid),now,now,config=config); c.commit()
    ev=c.execute("SELECT user_id,source_squad_id FROM polywar_events WHERE event_type='squad_reinforcement_delayed'").fetchone(); assert ev['user_id'] is None and ev['source_squad_id']==2
    keeper.close()


def test_materialize_cell_uses_conflict_safe_insert():
    src=Path('services/polywar_squad_service.py').read_text()
    assert 'ON CONFLICT (season_id, x, y) DO NOTHING' in src
    assert 'INSERT OR IGNORE INTO polywar_cells' in src
    assert 'SELECT * FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s FOR UPDATE' in src
    assert 'polywar_squad_cell_materialization_failed' in src


def test_second_squad_cancels_attack_after_first_captures(monkeypatch):
    connect, keeper, _ = db(monkeypatch); sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime(2026,1,1,12,0)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,enemy_cell_attack_progress_per_tick=100 WHERE season_id=?',(sid,)); c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,11,10,2,now))
    calls=[]; import services.polywar_sector_service as sectors; orig=sectors.transfer_cell_ownership; monkeypatch.setattr(sectors,'transfer_cell_ownership',lambda *a, **k: (calls.append(a), orig(*a, **k))[1])
    for i in (1,2): _insert_squad(c,sid,id=i,fid=1,status='attacking_cell',x=10,y=9+i,next_at=now); c.execute('UPDATE polywar_faction_squads SET attack_target_x=11,attack_target_y=10 WHERE id IN (1,2)'); c.commit()
    polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    t=now+timedelta(minutes=5); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,t,scheduled_at=t); c.commit()
    rows=c.execute('SELECT id,status,attack_target_x FROM polywar_faction_squads WHERE id IN (1,2) ORDER BY id').fetchall(); assert len(calls)==1 and all(r['status']=='marching' for r in rows)
    assert c.execute("SELECT COUNT(*) FROM polywar_events WHERE event_type='squad_cell_captured'").fetchone()[0]==1
    keeper.close()


def test_cross_faction_contest_clears_before_new_progress(monkeypatch):
    connect, keeper, _ = db(monkeypatch); sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); config=maps.load_map_config(c,season_id=sid)
    c.execute('UPDATE polywar_squad_season_config SET max_active_per_faction=0,enemy_cell_attack_progress_per_tick=10 WHERE season_id=?',(sid,)); c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,contesting_faction_id,contest_progress,contested_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',(sid,11,10,3,100,1,10,now,now))
    _insert_squad(c,sid,id=2,fid=2,status='attacking_cell',x=12,y=10,next_at=now); c.execute('UPDATE polywar_faction_squads SET attack_target_x=11,attack_target_y=10 WHERE id=2'); c.commit()
    polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    row=c.execute('SELECT contesting_faction_id,contest_progress FROM polywar_cells WHERE season_id=? AND x=11 AND y=10',(sid,)).fetchone(); assert row['contesting_faction_id'] is None and row['contest_progress']==0
    keeper.close()

def test_faction_squad_compact_map_end_to_end(monkeypatch):
    connect, keeper, settings = db(monkeypatch); settings['polywar_capture_progress_required']='30'
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); config=maps.load_map_config(c,season_id=sid); now=datetime(2026,1,1,12,0)
    c.execute('UPDATE polywar_squad_season_config SET require_faction_members=0,max_active_per_faction=1,move_interval_minutes=1,supply_distance=6,ttl_minutes=1000,enemy_cell_attack_progress_per_tick=10,enemy_cell_capture_enabled=1,capital_pressure_cap=20 WHERE season_id=?',(sid,))
    # Compact deterministic frontier around 40,40 with F1 adjacent to F2.
    for x,y,fid in [(39,40,1),(40,40,2),(41,40,2),(42,40,2)]:
        c.execute('INSERT OR REPLACE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (?,?,?,?,100,?)',(sid,x,y,fid,now))
    c.commit(); polywar.begin_serialized_transaction(c); spawned=squads.spawn_due_squads_in_transaction(c,sid,now+timedelta(hours=4)); c.commit()
    assert spawned >= 2 and {1,2} <= {r['faction_id'] for r in c.execute('SELECT faction_id FROM polywar_faction_squads WHERE season_id=?',(sid,))}
    f1=c.execute('SELECT * FROM polywar_faction_squads WHERE season_id=? AND faction_id=1 ORDER BY id LIMIT 1',(sid,)).fetchone(); assert (f1['target_x'],f1['target_y']) != config.bases[2]
    # Public tick path: force squad-vs-squad engagement and combat resolution.
    c.execute("UPDATE polywar_faction_squads SET max_hp=40,hp=40,status='marching',x=39,y=40,target_x=40,target_y=40,supply_x=39,supply_y=40,next_move_at=? WHERE id=?",(now,f1['id']))
    f2=c.execute('SELECT * FROM polywar_faction_squads WHERE season_id=? AND faction_id=2 ORDER BY id LIMIT 1',(sid,)).fetchone(); c.execute("UPDATE polywar_faction_squads SET max_hp=20,hp=20,status='marching',x=40,y=40,target_x=39,target_y=40,supply_x=40,supply_y=40,next_move_at=? WHERE id=?",(now,f2['id'])); c.execute('UPDATE polywar_squad_season_config SET combat_damage_per_tick=20 WHERE season_id=?',(sid,)); c.commit()
    polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,now,scheduled_at=now); c.commit()
    assert c.execute("SELECT COUNT(*) FROM polywar_faction_squads WHERE season_id=? AND status='engaged'",(sid,)).fetchone()[0] == 2
    t=now+timedelta(minutes=1); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,t,scheduled_at=t); c.commit()
    assert c.execute("SELECT COUNT(*) FROM polywar_faction_squads WHERE season_id=? AND status='awaiting_reinforcement'",(sid,)).fetchone()[0] >= 1
    survivor=c.execute("SELECT * FROM polywar_faction_squads WHERE season_id=? AND status='marching' AND faction_id IN (1,2) ORDER BY faction_id LIMIT 1",(sid,)).fetchone()
    # Drive surviving squad through normal cell attack/capture via tick path.
    sfid=int(survivor['faction_id']); enemy=2 if sfid==1 else 1
    c.execute("UPDATE polywar_faction_squads SET status='attacking_cell',x=39,y=40,attack_target_x=40,attack_target_y=40,attack_progress=0,hp=20,next_move_at=? WHERE id=?",(t,survivor['id'])); c.execute('UPDATE polywar_cells SET owner_faction_id=?,contesting_faction_id=NULL,contest_progress=0 WHERE season_id=? AND x=40 AND y=40',(enemy,sid)); c.commit()
    for i in range(3):
        tt=t+timedelta(minutes=2+i); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,tt,scheduled_at=tt); c.commit()
    cell=c.execute('SELECT owner_faction_id FROM polywar_cells WHERE season_id=? AND x=40 AND y=40',(sid,)).fetchone(); sq=c.execute('SELECT status,attack_target_x,attack_progress FROM polywar_faction_squads WHERE id=?',(survivor['id'],)).fetchone(); ev=c.execute("SELECT user_id,source_squad_id FROM polywar_events WHERE season_id=? AND event_type='squad_cell_captured'",(sid,)).fetchone()
    assert cell['owner_faction_id']==1 and sq['status']=='marching' and sq['attack_target_x'] is None and sq['attack_progress']==0 and ev['user_id'] is None and ev['source_squad_id']==survivor['id']
    stats={r['faction_id']:r['controlled_cells_count'] for r in c.execute('SELECT faction_id,controlled_cells_count FROM polywar_faction_season_stats WHERE season_id=? AND faction_id IN (1,2)',(sid,))}; assert stats[sfid] >= 1
    # Capital pressure cannot transfer control or finalize season; ownership change cancels pressure.
    capitals.ensure_capitals_initialized(c,sid); cap=c.execute('SELECT * FROM polywar_capitals WHERE season_id=? AND original_faction_id=2',(sid,)).fetchone(); cx,cy=cap['x'],cap['y']
    c.execute("UPDATE polywar_faction_squads SET status='pressuring_capital',x=?,y=?,attack_target_x=?,attack_target_y=?,attack_progress=0,next_move_at=? WHERE id=?",(cx-1,cy,cx,cy,t,survivor['id'])); c.commit()
    for i in range(3):
        tt=t+timedelta(minutes=10+i); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,tt,scheduled_at=tt); c.commit()
    cap2=c.execute('SELECT controller_faction_id FROM polywar_capitals WHERE id=?',(cap['id'],)).fetchone(); season=c.execute('SELECT status,finalized_at FROM polywar_seasons WHERE id=?',(sid,)).fetchone(); assert cap2['controller_faction_id']==2 and season['status']=='active' and season['finalized_at'] is None
    c.execute('UPDATE polywar_capitals SET controller_faction_id=? WHERE id=?',(1,cap['id'])); c.commit(); tt=t+timedelta(minutes=20); polywar.begin_serialized_transaction(c); squads.process_squad_tick_in_transaction(c,sid,tt,scheduled_at=tt); c.commit()
    sq=c.execute('SELECT status,attack_target_x FROM polywar_faction_squads WHERE id=?',(survivor['id'],)).fetchone(); assert sq['status']=='marching' and sq['attack_target_x'] is None
    keeper.close()


def test_dormant_world_freezes_engaged_and_reschedules(monkeypatch):
    connect, keeper, settings = db(monkeypatch); settings['polywar_squad_pause_without_active_players']='true'
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow()
    c.execute('UPDATE polywar_squad_season_config SET pause_without_active_players=1,max_active_per_faction=0,move_interval_minutes=5 WHERE season_id=?',(sid,))
    for i,(fid,enemy) in enumerate(((1,2),(2,1)),1):
        c.execute("INSERT INTO polywar_faction_squads (id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,supply_x,supply_y,hp,max_hp,spawned_at,next_move_at,expires_at,engaged_squad_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(i,sid,fid,i,'engaged',10+i,10,10+i,10,10+i,10,100,100,now,now-timedelta(hours=8),now+timedelta(days=1),enemy,now,now))
    c.commit(); polywar.begin_serialized_transaction(c); out=squads.ensure_squads_caught_up_in_transaction(c,sid,now); c.commit()
    rows=c.execute('SELECT hp,status,engaged_squad_id,next_move_at FROM polywar_faction_squads ORDER BY id').fetchall()
    assert out['reason']=='waiting_for_active_players' and out['rescheduled_count']==2 and out['combat_count']==0
    assert [(r['hp'],r['status'],r['engaged_squad_id']) for r in rows]==[(100,'engaged',2),(100,'engaged',1)]
    assert all(r['next_move_at']>now for r in rows)
    assert c.execute('SELECT COUNT(*) FROM polywar_squad_ticks').fetchone()[0]==0
    keeper.close()


def test_recent_presence_activates_and_stale_presence_does_not(monkeypatch):
    connect, keeper, settings = db(monkeypatch); settings['polywar_squad_pause_without_active_players']='true'
    sid=make_season(connect, existing=False); join(connect,sid,1,1); c=connect(); now=datetime.utcnow()
    c.execute('UPDATE polywar_squad_season_config SET pause_without_active_players=1,active_player_window_minutes=5,max_active_per_faction=0 WHERE season_id=?',(sid,))
    c.execute('UPDATE polywar_players SET last_active_at=? WHERE season_id=?',(now-timedelta(minutes=6),sid)); c.commit()
    polywar.begin_serialized_transaction(c); stale=squads.process_squad_tick_in_transaction(c,sid,now); c.commit(); assert stale['simulation_mode']=='dormant'
    c.execute('UPDATE polywar_players SET last_active_at=? WHERE season_id=?',(now-timedelta(minutes=1),sid)); c.commit()
    polywar.begin_serialized_transaction(c); active=squads.process_squad_tick_in_transaction(c,sid,now); c.commit(); assert active['processed'] is True
    keeper.close()
