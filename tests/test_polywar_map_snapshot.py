import sqlite3
from datetime import datetime, timedelta

from services import polywar_service as p
from services import polywar_map_service as m
from services import polywar_capital_service as caps


def conn():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row; return c


def setup(settings_width=1000, settings_height=1000):
    c=conn(); p.init_polywar_schema(c); m.ensure_map_snapshot_schema(c); p.ensure_factions(c)
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT)')
    for k,v in {'polywar_map_width':settings_width,'polywar_map_height':settings_height,'polywar_chunk_size':64,'polywar_sector_size':100,'polywar_starting_area_size':15}.items():
        c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(k,str(v)))
    now=datetime.utcnow(); c.execute('INSERT INTO polywar_seasons(name,status,starts_at,ends_at,secret_seed,created_at) VALUES(?,?,?,?,?,?)',('S','active',now,now+timedelta(days=1),'seed',now)); c.commit(); return c


def test_existing_active_season_without_snapshot_gets_snapshot():
    c=setup(); sid=1; assert m.ensure_season_map_snapshot(c,sid) is True
    r=c.execute('select map_width,map_height,map_base_layout_json from polywar_seasons where id=?',(sid,)).fetchone()
    assert r['map_width']==1000 and r['map_height']==1000 and m.parse_base_layout_json(r['map_base_layout_json'],1000,1000)


def test_existing_capital_coordinates_preserved_as_hq_source():
    c=setup(); caps.init_polywar_capital_schema(c); c.execute('insert into polywar_capitals(season_id,original_faction_id,controller_faction_id,x,y,controlled_since,updated_at) values(1,1,1,900,901,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)'); c.commit()
    m.ensure_season_map_snapshot(c,1); cfg=m.load_map_config(c, season_id=1)
    assert cfg.bases[1] == (900,901)


def test_global_settings_change_after_snapshot_does_not_change_active_config():
    c=setup(); m.ensure_season_map_snapshot(c,1); c.execute("update settings set value='2000' where key='polywar_map_width'"); c.commit()
    assert m.load_map_config(c, season_id=1).width == 1000


def test_new_season_gets_new_settings():
    c=setup(1000,1000); p.begin_serialized_transaction(c); s=p.ensure_active_season_in_transaction(c); c.commit(); assert s['map_width']==1000
    c.execute("update polywar_seasons set status='completed' where id=1"); c.execute("update settings set value='1600' where key='polywar_map_width'"); c.commit()
    p.begin_serialized_transaction(c); s2=p.ensure_active_season_in_transaction(c); c.commit(); assert s2['map_width']==1600


def test_existing_coordinate_expands_snapshot_and_never_shrinks_or_updates_coordinates():
    c=setup(1000,1000); m.init_polywar_map_schema(c); c.execute('insert into polywar_cells(season_id,x,y,owner_faction_id) values(1,1500,1200,1)'); c.commit()
    before=c.execute('select x,y from polywar_cells').fetchone(); m.ensure_season_map_snapshot(c,1); after=c.execute('select x,y from polywar_cells').fetchone()
    cfg=m.load_map_config(c, season_id=1); assert cfg.width==1501 and cfg.height==1201 and tuple(before)==tuple(after)


def test_repeated_backfill_idempotent_and_malformed_layout_safe():
    c=setup(); assert m.ensure_season_map_snapshot(c,1) is True; first=c.execute('select map_base_layout_json from polywar_seasons').fetchone()[0]; assert m.ensure_season_map_snapshot(c,1) is False
    c.execute("update polywar_seasons set map_base_layout_json='bad'"); c.commit(); assert m.parse_base_layout_json('bad',1000,1000) is None; assert m.ensure_season_map_snapshot(c,1) is True; assert c.execute('select map_base_layout_json from polywar_seasons').fetchone()[0] == first


def test_postgresql_coordinate_sources_use_metadata_and_skip_rebellions(monkeypatch):
    class Conn:
        pass
    conn = Conn()
    monkeypatch.setattr(p, '_is_sqlite', lambda c: False)
    def fake_fetchall(cur, sql, params=()):
        assert 'polywar_rebellions' not in sql
        if 'information_schema.columns' in sql:
            return [
                {'table_name':'polywar_cells','column_name':'x'}, {'table_name':'polywar_cells','column_name':'y'},
                {'table_name':'polywar_mine_events','column_name':'x'}, {'table_name':'polywar_mine_events','column_name':'y'},
                {'table_name':'polywar_rebellions','column_name':'capital_id'},
            ]
        return []
    conn.cursor = lambda: object()
    monkeypatch.setattr(p, '_fetchall', fake_fetchall)
    sources = m.get_existing_coordinate_sources(conn)
    assert sources['polywar_mine_events'] == ('x','y')
    assert 'polywar_rebellions' not in sources


def test_state_response_uses_snapshot_after_global_settings_change(monkeypatch):
    import uuid
    uri=f"file:state_snapshot_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri, uri=True, check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={'polywar_map_width':'1000','polywar_map_height':'1000','polywar_chunk_size':'32','polywar_sector_size':'25','polywar_starting_area_size':'15'}
    def connect():
        c=sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=10); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(p,'get_connection',connect)
    monkeypatch.setattr(p,'get_setting',lambda k,d='': settings.get(k,d))
    monkeypatch.setattr(p,'get_airdrop_points_balance',lambda uid:{'total':0,'balance':0})
    c=connect(); p.init_polywar_schema(c); p.ensure_factions(c); c.commit(); p.begin_serialized_transaction(c); season=p.ensure_active_season_in_transaction(c); c.commit(); c.close()
    settings.update({'polywar_map_width':'32000','polywar_map_height':'32000','polywar_chunk_size':'64','polywar_sector_size':'100'})
    state=p.get_state(10001)
    assert state['map']['width']==1000 and state['map']['height']==1000
    assert state['map']['chunk_size']==32 and state['rules']['sectors']['sector_size']==25
    assert state['map']['bases'][0]['x'] == m.load_map_config(connect(), season_id=season['id']).bases[1][0]
    keeper.close()


def test_sector_mutations_use_snapshot_sector_size_after_global_change():
    from services import polywar_sector_service as sectors
    c=setup(1000,1000); m.init_polywar_map_schema(c); sectors.init_polywar_sector_schema(c); m.ensure_season_map_snapshot(c,1)
    c.execute("update polywar_seasons set map_sector_size=25 where id=1")
    c.execute("update settings set value='100' where key='polywar_sector_size'"); c.commit()
    cfg=m.load_map_config(c, season_id=1); now=datetime.utcnow()
    sectors.transfer_cell_ownership(c,1,74,10,None,1,42,now,config=cfg)
    row=c.execute('select sector_x,sector_y from polywar_sectors where season_id=1 and sector_x=2').fetchone()
    assert row is not None and row['sector_x']==2
    assert c.execute('select 1 from polywar_sectors where season_id=1 and sector_x=0 and sector_y=0').fetchone() is None


def test_apply_materialized_starting_cell_uses_snapshot_sector_size_after_global_change():
    from services import polywar_sector_service as sectors
    c=setup(1000,1000); m.init_polywar_map_schema(c); sectors.init_polywar_sector_schema(c); m.ensure_season_map_snapshot(c,1)
    c.execute("update polywar_seasons set map_sector_size=25 where id=1")
    c.execute("update settings set value='100' where key='polywar_sector_size'"); c.commit()
    cfg=m.load_map_config(c, season_id=1); sectors.apply_materialized_starting_cell(c,1,74,10,1,datetime.utcnow(),config=cfg)
    assert c.execute('select 1 from polywar_sector_initializations where season_id=1 and sector_x=2 and sector_y=0').fetchone() is not None


def test_postgresql_snapshot_skips_missing_capitals_metadata(monkeypatch):
    class Cur:
        def __init__(self): self.queries=[]; self.description=(('map_width',),); self.rowcount=1
        def execute(self, sql, params=()): self.queries.append(sql)
        def fetchone(self): return None
        def fetchall(self): return []
    class Conn:
        def __init__(self): self.cur=Cur()
        def cursor(self): return self.cur
    fake=Conn(); monkeypatch.setattr(p, '_is_sqlite', lambda c: False)
    def fake_fetchone(cur, sql, params=()):
        assert 'FROM polywar_capitals' not in sql
        if 'FROM polywar_seasons' in sql:
            return {'id':1,'map_width':None,'map_height':None,'map_chunk_size':None,'map_sector_size':None,'map_starting_area_size':None,'map_base_layout_json':None,'map_world_version':1}
        return None
    def fake_fetchall(cur, sql, params=()):
        assert 'FROM polywar_capitals' not in sql
        if 'information_schema.columns' in sql:
            return [{'table_name':'polywar_cells','column_name':'x'},{'table_name':'polywar_cells','column_name':'y'}]
        return []
    updates=[]
    monkeypatch.setattr(p, '_fetchone', fake_fetchone); monkeypatch.setattr(p, '_fetchall', fake_fetchall)
    monkeypatch.setattr(p, '_execute', lambda cur, sql, params=(): updates.append(sql))
    monkeypatch.setattr(p, 'get_setting', lambda k,d='': {'polywar_map_width':'1000','polywar_map_height':'1000','polywar_chunk_size':'64','polywar_sector_size':'100','polywar_starting_area_size':'15'}.get(k,d))
    assert m.ensure_season_map_snapshot(fake,1) is True
    assert any('UPDATE polywar_seasons' in q for q in updates)


def test_rebellion_and_null_state_source_are_config_aware_static():
    from pathlib import Path
    reb=Path('services/polywar_rebellion_service.py').read_text()
    world=Path('services/polywar_world_service.py').read_text()
    assert 'def _original_presence(conn,sid,orig,x,y,config=None)' in reb
    assert 'sectors.sector_coords(x,y,config=config)' in reb
    assert 'def _adjacent_owner(conn,sid,x,y,fid,config=None)' in reb
    assert 'm.owner_at_with_config(conn,sid,nx,ny,config)' in reb
    assert 'config = config or m.load_map_config(conn, season_id=season_id)' in reb
    assert 'config.starting_area_size' in world and 'config.bases.values()' in world
    assert 'def is_safe_zone(conn,season_id,x,y,config=None)' in world
    assert 'process_rebellion_tick(conn,season_id,now,limit=10,config=config)' in world


def test_mine_and_duplicate_responses_thread_config_static():
    from pathlib import Path
    mine=Path('services/polywar_mine_service.py').read_text()
    combat=Path('services/polywar_combat_service.py').read_text()
    capital=Path('services/polywar_capital_service.py').read_text()
    map_s=Path('services/polywar_map_service.py').read_text()
    assert 'def record_triggered_mine(conn, season_id, faction_id, user_id, x, y, idempotency_key, secret_seed, now=None, config=None)' in mine
    assert 'upsert_safe_hint(conn, season_id, int(row["faction_id"]), int(row["x"]), int(row["y"]), user_id, secret_seed, now, config=config)' in mine
    assert 'record_triggered_mine(conn, sid, fid, user_id, x, y, idempotency_key, seed, now, config=config)' in map_s
    assert 'def _find_duplicate(conn, sid, seed, user_id, key, config=None)' in combat
    assert '_legacy_action_duplicate_response(conn, sid, seed, user_id, existing, config=config)' in combat
    assert 'def _duplicate_response(conn, sid, seed, uid, key, config=None)' in capital
    assert "m.terrain_at_with_config(seed, int(action['x']), int(action['y']), config)" in capital


def test_snapshot_diagnostics_use_polywar_faction_orders_and_preserve_order_coordinates(monkeypatch):
    from services import polywar_governance_service as gov
    c=setup(1000,1000); gov.init_polywar_governance_schema(c)
    now=datetime.utcnow()
    c.execute('insert into polywar_faction_orders (season_id,faction_id,commander_user_id,order_type,x,y,sector_x,sector_y,message,active,created_at,expires_at,updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?)', (1,1,11,'attack',1700,1300,0,0,'',1,now,now+timedelta(hours=1),now))
    c.commit()
    seen=[]; orig=p._fetchall
    def wrapped(cur, sql, params=()):
        seen.append(sql)
        assert 'polywar_commander_orders' not in sql
        return orig(cur, sql, params)
    monkeypatch.setattr(p, '_fetchall', wrapped)
    assert m.ensure_season_map_snapshot(c,1) is True
    cfg=m.load_map_config(c, season_id=1)
    assert cfg.width >= 1701 and cfg.height >= 1301
    row=c.execute('select x,y from polywar_faction_orders where season_id=1').fetchone()
    assert (row['x'], row['y']) == (1700, 1300)
    assert any('polywar_faction_orders' in q or 'sqlite_master' in q for q in seen)


def test_capture_legacy_duplicate_response_uses_snapshot_config_and_not_global_terrain(monkeypatch):
    import uuid
    from services import polywar_world_service as world
    uri=f"file:capture_duplicate_snapshot_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri, uri=True, check_same_thread=False); keeper.row_factory=sqlite3.Row
    settings={'polywar_map_width':'1000','polywar_map_height':'1000','polywar_chunk_size':'64','polywar_sector_size':'25','polywar_starting_area_size':'15'}
    def connect():
        c=sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=10); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(p,'get_connection',connect)
    monkeypatch.setattr(p,'get_setting',lambda k,d='': settings.get(k,d))
    c=connect(); p.init_polywar_schema(c); m.init_polywar_map_schema(c); world.init_world_schema(c); p.ensure_factions(c)
    now=datetime.utcnow(); c.execute('insert into polywar_seasons(name,status,starts_at,ends_at,secret_seed,created_at) values(?,?,?,?,?,?)',('S','active',now,now+timedelta(days=1),'snapshot-seed',now)); m.ensure_season_map_snapshot(c,1)
    cfg=m.load_map_config(c, season_id=1); x,y=cfg.bases[1][0]+8,cfg.bases[1][1]
    c.execute('insert into polywar_players(user_id,season_id,faction_id,joined_at,last_active_at,current_energy,max_energy,energy_updated_at) values(?,?,?,?,?,?,?,?)',(777,1,1,now,now,10,10,now))
    c.execute('insert into polywar_actions(season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) values(?,?,?,?,?,?,?,?,?)',(1,777,1,'capture',x,y,1,'legacy-dupe',now))
    c.commit(); c.close()
    settings.update({'polywar_map_width':'32000','polywar_map_height':'32000','polywar_sector_size':'100'})
    expected=m.terrain_at_with_config('snapshot-seed',x,y,cfg)
    monkeypatch.setattr(m,'terrain_at',lambda *a, **k: (_ for _ in ()).throw(AssertionError('global terrain_at called')))
    out=m.capture_cell(777,x,y,'legacy-dupe')
    assert out['duplicate'] is True
    assert out['cell']['terrain'] == expected
    keeper.close()


def test_compact_profile_preserves_active_coordinates_and_new_season_uses_compact_snapshot():
    from services import polywar_capital_service as caps
    from services import polywar_governance_service as gov
    from services import polywar_world_service as world
    from services import polywar_mine_service as mines
    c=setup(10000,10000); m.init_polywar_map_schema(c); caps.init_polywar_capital_schema(c); gov.init_polywar_governance_schema(c); world.init_world_schema(c); mines.init_polywar_mine_schema(c)
    legacy_layout='{"1":{"x":1000,"y":1000},"2":{"x":8999,"y":5000}}'
    c.execute('update polywar_seasons set map_width=10000,map_height=10000,map_chunk_size=64,map_sector_size=100,map_starting_area_size=15,map_base_layout_json=?,map_world_version=1 where id=1',(legacy_layout,))
    now=datetime.utcnow()
    c.execute('insert into polywar_capitals(season_id,original_faction_id,controller_faction_id,x,y,controlled_since,updated_at) values(1,1,1,1000,1000,?,?)',(now,now))
    c.execute('insert into polywar_cells(season_id,x,y,owner_faction_id) values(1,9999,9998,1)')
    c.execute('insert into polywar_faction_orders(season_id,faction_id,commander_user_id,order_type,x,y,sector_x,sector_y,message,active,created_at,expires_at,updated_at) values(1,1,1,"attack",9000,5000,0,0,"",1,?,?,?)',(now,now+timedelta(hours=1),now))
    c.execute('insert into polywar_null_rifts(season_id,x,y,status,health,max_health,spawned_at,created_at,updated_at) values(1,8000,8000,"active",10,10,?,?,?)',(now,now,now))
    c.execute('insert into polywar_mine_events(season_id,x,y,event_type,triggered_at) values(1,7000,7000,"test",?)',(now,))
    c.commit()
    before={t:c.execute(f'select x,y from {t} where season_id=1').fetchone() for t in ['polywar_capitals','polywar_cells','polywar_faction_orders','polywar_null_rifts','polywar_mine_events']}
    out=m.apply_compact_next_season_profile(c)
    assert out['applied'] is True
    active=c.execute('select map_width,map_height,map_base_layout_json from polywar_seasons where id=1').fetchone()
    assert active['map_width']==10000 and active['map_height']==10000 and active['map_base_layout_json']==legacy_layout
    after={t:c.execute(f'select x,y from {t} where season_id=1').fetchone() for t in before}
    assert {k:tuple(v) for k,v in before.items()} == {k:tuple(v) for k,v in after.items()}
    settings={r['key']:r['value'] for r in c.execute('select key,value from settings')}
    assert settings['polywar_map_width']=='1600' and settings['polywar_map_height']=='1600'
    assert settings['polywar_chunk_size']=='32' and settings['polywar_sector_size']=='40' and settings['polywar_starting_area_size']=='41'
    assert settings['polywar_world_profile']=='compact_v2' and settings['polywar_world_profile_version']=='2'
    c.execute("update polywar_seasons set status='completed' where id=1"); c.commit()
    p.begin_serialized_transaction(c); s2=p.ensure_active_season_in_transaction(c); c.commit()
    assert (s2['map_width'],s2['map_height'],s2['map_chunk_size'],s2['map_sector_size'],s2['map_starting_area_size'],s2['map_world_version']) == (1600,1600,32,40,41,2)
    assert m.load_map_config(c, season_id=s2['id']).bases == {1:(200,200),2:(1400,200),3:(200,1400),4:(1400,1400),5:(800,200),6:(200,800),7:(1400,800)}


def test_compact_profile_idempotent_and_custom_requires_force():
    c=setup(2400,1800); m.ensure_season_map_snapshot(c,1)
    skipped=m.apply_compact_next_season_profile(c)
    assert skipped['applied'] is False and skipped['skip_reason']=='custom_global_settings'
    forced=m.apply_compact_next_season_profile(c, force=True)
    assert forced['applied'] is True
    again=m.apply_compact_next_season_profile(c)
    assert again['applied'] is False and again['skip_reason']=='version_already_current'
    assert tuple(c.execute('select map_width,map_height from polywar_seasons where id=1').fetchone()) == (2400,1800)
