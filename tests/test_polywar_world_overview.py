import sqlite3
from datetime import datetime, timedelta
from services import polywar_service as p
from services import polywar_map_service as m
from services import polywar_sector_service as sectors
from services import polywar_world_overview_service as ov


def make(monkeypatch):
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    monkeypatch.setattr(p,'get_connection',lambda: c)
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
