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
