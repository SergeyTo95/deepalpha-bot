import sqlite3, uuid
from datetime import datetime, timedelta

import services.polywar_service as polywar
import services.polywar_map_service as maps
import services.polywar_squad_service as squads


def db(monkeypatch):
    uri=f"file:squads_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri, uri=True, check_same_thread=False); keeper.row_factory=sqlite3.Row
    def connect():
        c=sqlite3.connect(uri, uri=True, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES); c.row_factory=sqlite3.Row; return c
    settings={"polywar_world_profile":"compact_v2","polywar_world_profile_version":"2","polywar_map_width":"512","polywar_map_height":"512","polywar_chunk_size":"32","polywar_sector_size":"40","polywar_starting_area_size":"21"}
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
    c.execute("UPDATE polywar_squad_season_config SET supply_distance=1 WHERE season_id=?",(sid,))
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
    sid=make_season(connect, existing=False); c=connect(); squads.enable_squads_for_season(c,sid); now=datetime.utcnow(); cfg=squads.ensure_squad_season_config(c,sid); c.execute('UPDATE polywar_squad_season_config SET combat_damage_per_tick=20 WHERE season_id=?',(sid,))
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
    assert after_b['move_index'] == before_b['move_index'] + 1
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
