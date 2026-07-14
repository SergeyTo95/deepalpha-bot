import sqlite3
from datetime import datetime, timedelta
from services import polywar_service as p
from services import polywar_map_service as m
from services import polywar_sector_service as sectors
from services import polywar_world_overview_service as ov

def make(monkeypatch):
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    monkeypatch.setattr(p,'get_connection',lambda: c)
    monkeypatch.setattr(p,'get_setting',lambda k,d='': d)
    p.init_polywar_schema(c); sectors.init_polywar_sector_schema(c); p.ensure_factions(c)
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT)')
    for k,v in {'polywar_map_width':10000,'polywar_map_height':10000,'polywar_chunk_size':64,'polywar_sector_size':50,'polywar_starting_area_size':15}.items(): c.execute('insert or replace into settings values(?,?)',(k,str(v)))
    now=datetime.utcnow(); c.execute('insert into polywar_seasons(name,status,starts_at,ends_at,secret_seed,created_at) values(?,?,?,?,?,?)',('S','active',now,now+timedelta(days=1),'seed',now)); m.ensure_season_map_snapshot(c,1); c.commit(); return c


def test_overview_grid_bounded_and_implicit_starting_territories(monkeypatch):
    c=make(monkeypatch); out=ov.build_world_overview(1)
    assert out['ok'] and out['overview_grid']['columns'] <= 128 and out['overview_grid']['rows'] <= 128
    assert len(out['hq']) == 7 and out['overview_grid']['cells']


def test_persisted_sector_overrides_implicit(monkeypatch):
    c=make(monkeypatch); c.execute('insert into polywar_sectors(season_id,sector_x,sector_y,controller_faction_id,total_claimed_cells,leading_faction_id,leading_cells,dominance_percent,is_contested,updated_at) values(1,0,0,2,30,2,30,100,0,CURRENT_TIMESTAMP)'); c.commit(); ov._CACHE.clear()
    out=ov.build_world_overview(1); cell=[x for x in out['overview_grid']['cells'] if x['grid_x']==0 and x['grid_y']==0][0]
    assert cell['controller_faction_id']==2


def test_overview_uses_single_connection(monkeypatch):
    calls=[]; c=make(monkeypatch); monkeypatch.setattr(p,'get_connection',lambda: calls.append(1) or c); ov._CACHE.clear(); ov.build_world_overview(1); assert len(calls)==1


def test_postgresql_tuple_dict_row_mapping():
    class Cur: description=(('a',),('b',))
    assert p._row_to_dict(Cur(), (1,2)) == {'a':1,'b':2}


def test_overview_sector_query_is_sql_aggregated_and_bounded(monkeypatch):
    c=make(monkeypatch)
    rows=[]
    for i in range(50000):
        rows.append((1, i % 200, i // 200, (i % 7)+1, 10, (i % 7)+1, 10, 80, 0, '2026-01-01'))
    c.executemany('insert into polywar_sectors(season_id,sector_x,sector_y,controller_faction_id,total_claimed_cells,leading_faction_id,leading_cells,dominance_percent,is_contested,updated_at) values(?,?,?,?,?,?,?,?,?,?)', rows)
    c.commit(); ov._CACHE.clear()
    seen=[]; orig=p._fetchall
    def wrapped(cur, sql, params=()):
        if 'FROM polywar_sectors' in sql:
            seen.append(sql)
        return orig(cur, sql, params)
    monkeypatch.setattr(p,'_fetchall',wrapped)
    out=ov.build_world_overview(1)
    assert len(out['overview_grid']['cells']) <= 128*128
    assert any('GROUP BY' in q and 'LIMIT' in q for q in seen)


def test_postgresql_contested_sql_uses_integer_safe_comparison(monkeypatch):
    c=make(monkeypatch); ov._CACHE.clear(); seen=[]; orig=p._fetchall
    def wrapped(cur, sql, params=()):
        if 'contested_count' in sql:
            seen.append(sql)
        return orig(cur, sql, params)
    monkeypatch.setattr(p,'_fetchall',wrapped)
    out=ov.build_world_overview(1)
    assert out['ok']
    assert seen
    sql='\n'.join(seen)
    assert 'CASE WHEN is_contested THEN' not in sql
    assert 'COALESCE(is_contested, 0) <> 0' in sql


def test_overview_stats_sum_aggregated_counts(monkeypatch):
    c=make(monkeypatch); c.execute('delete from polywar_sectors')
    c.executemany('insert into polywar_sectors(season_id,sector_x,sector_y,controller_faction_id,total_claimed_cells,leading_faction_id,leading_cells,dominance_percent,is_contested,updated_at) values(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)', [(1,0,0,1,5,1,5,100,1),(1,1,0,1,7,1,7,100,0)])
    c.commit(); ov._CACHE.clear(); out=ov.build_world_overview(1)
    assert out['stats']['controlled_sectors'] >= 2
    assert out['stats']['contested_sectors'] >= 1


def test_overview_includes_bounded_starting_zones(monkeypatch):
    c=make(monkeypatch); ov._CACHE.clear(); out=ov.build_world_overview(1)
    assert len(out['starting_zones']) == 7
    z=out['starting_zones'][0]
    assert {'faction_id','min_x','min_y','max_x','max_y'} <= set(z)
    assert z['min_x'] >= 0 and z['max_x'] < out['world']['width']

def test_squad_pressure_two_factions_same_bin_contested(monkeypatch):
    c=make(monkeypatch)
    from services import polywar_squad_service as squads
    squads.init_squad_schema(c); squads.ensure_squad_season_config(c,1,existing_active=False); squads.enable_squads_for_season(c,1)
    future=datetime.utcnow()+timedelta(hours=1)
    c.execute('insert or replace into polywar_squad_pressure(season_id,x,y,faction_id,pressure,source_squad_id,expires_at,created_at,updated_at) values(?,?,?,?,?,?,?,?,?)',(1,10,10,1,40,None,future,future,future))
    c.execute('insert or replace into polywar_squad_pressure(season_id,x,y,faction_id,pressure,source_squad_id,expires_at,created_at,updated_at) values(?,?,?,?,?,?,?,?,?)',(1,11,10,2,80,None,future,future,future))
    c.commit(); ov._CACHE.clear(); out=ov.build_world_overview(1)
    bins=[b for b in out.get('squad_pressure_bins',[]) if b['grid_x']==0 and b['grid_y']==0]
    assert bins and bins[0]['is_contested'] is True and bins[0]['faction_id']==2


def test_disabled_squad_overview_hides_and_invalidates_cache(monkeypatch):
    from services import polywar_squad_service as squads
    uri='file:polywar_overview_squads_disabled?mode=memory&cache=shared'
    keeper=sqlite3.connect(uri, uri=True); keeper.row_factory=sqlite3.Row
    def connect():
        conn=sqlite3.connect(uri, uri=True); conn.row_factory=sqlite3.Row; return conn
    monkeypatch.setattr(p,'get_connection',connect)
    monkeypatch.setattr(p,'get_setting',lambda k,d='': d)
    c=connect()
    p.init_polywar_schema(c); sectors.init_polywar_sector_schema(c); p.ensure_factions(c)
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT)')
    for k,v in {'polywar_map_width':10000,'polywar_map_height':10000,'polywar_chunk_size':64,'polywar_sector_size':50,'polywar_starting_area_size':15}.items():
        c.execute('insert or replace into settings values(?,?)',(k,str(v)))
    now=datetime.utcnow()
    c.execute('insert into polywar_seasons(id,name,status,starts_at,ends_at,secret_seed,created_at) values(?,?,?,?,?,?,?)',(1,'S','active',now,now+timedelta(days=1),'seed',now))
    m.ensure_season_map_snapshot(c,1)
    squads.init_squad_schema(c); squads.ensure_squad_season_config(c,1,existing_active=False); squads.enable_squads_for_season(c,1)
    future=datetime.utcnow()+timedelta(hours=1)
    c.execute("insert into polywar_faction_squads(id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(1,1,1,1,'marching',10,10,10,10,11,10,10,10,100,100,0,0,future,future,future,future,future))
    c.execute('insert or replace into polywar_squad_pressure(season_id,x,y,faction_id,pressure,source_squad_id,expires_at,created_at,updated_at) values(?,?,?,?,?,?,?,?,?)',(1,10,10,1,80,1,future,future,future))
    c.commit(); c.close(); ov._CACHE.clear(); enabled=ov.build_world_overview(1)
    assert enabled['squads_enabled'] is True and enabled['squads'] and enabled['squad_pressure_bins']
    c=connect()
    squads.disable_squads_for_season(c,1); c.commit()
    c.close()
    disabled=ov.build_world_overview(1)
    assert disabled['squads_enabled'] is False
    assert disabled['squads']==[] and disabled['squad_pressure_bins']==[]
    assert disabled['hq'] and disabled['capitals'] is not None and disabled['overview_grid']['cells']
    keeper.close()

def test_awaiting_squad_visible_and_expired_hidden_in_overview(monkeypatch):
    from services import polywar_squad_service as squads
    c=make(monkeypatch)
    squads.init_squad_schema(c); squads.ensure_squad_season_config(c,1,existing_active=False); squads.enable_squads_for_season(c,1)
    now=datetime.utcnow(); future=now+timedelta(hours=1); past=now-timedelta(seconds=1)
    c.execute("insert into polywar_faction_squads(id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,reinforcement_at,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(11,1,1,1,'awaiting_reinforcement',10,10,10,10,11,10,10,10,0,100,0,0,now,now,future,future,now,now))
    c.execute("insert into polywar_faction_squads(id,season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,reinforcement_at,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(12,1,1,2,'awaiting_reinforcement',12,10,12,10,13,10,12,10,0,100,0,0,now,now,past,past,now,now))
    c.commit(); ov._CACHE.clear(); out=ov.build_world_overview(1)
    ids={s['id'] for s in out['squads']}
    assert 11 in ids and 12 not in ids
