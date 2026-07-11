import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from services import polywar_service as polywar

_RATE = defaultdict(deque)

def _setting_int(key, default, lo, hi):
    return polywar._setting_int(key, default, lo, hi)

def sector_size(): return _setting_int('polywar_sector_size',100,10,10000)
def min_claimed(): return _setting_int('polywar_sector_min_claimed_cells',25,1,1000000)
def control_percent(): return _setting_int('polywar_sector_control_percent',60,1,100)
def influence_value(): return _setting_int('polywar_sector_influence_value',100,0,1000000)
def max_sectors_per_request(): return _setting_int('polywar_max_sectors_per_request',100,1,500)

def sector_coords(x:int,y:int)->Tuple[int,int]:
    s=sector_size(); return int(x)//s, int(y)//s

def _col_exists(conn, table, col):
    c=conn.cursor()
    if polywar._is_sqlite(conn):
        c.execute(f'PRAGMA table_info({table})')
        return any(r[1]==col for r in c.fetchall())
    c.execute('SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s',(table,col))
    return c.fetchone() is not None

def _add_col(conn, table, spec):
    col=spec.split()[0]
    if not _col_exists(conn, table, col):
        conn.cursor().execute(f'ALTER TABLE {table} ADD COLUMN {spec}')

def init_polywar_sector_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor()
    try:
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_sector_faction_stats (season_id INTEGER NOT NULL, sector_x INTEGER NOT NULL, sector_y INTEGER NOT NULL, faction_id INTEGER NOT NULL, controlled_cells_count INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,sector_x,sector_y,faction_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_sectors (season_id INTEGER NOT NULL, sector_x INTEGER NOT NULL, sector_y INTEGER NOT NULL, controller_faction_id INTEGER NULL, total_claimed_cells INTEGER NOT NULL DEFAULT 0, leading_faction_id INTEGER NULL, leading_cells INTEGER NOT NULL DEFAULT 0, dominance_percent INTEGER NOT NULL DEFAULT 0, is_contested INTEGER NOT NULL DEFAULT 0, controlled_since TIMESTAMP NULL, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,sector_x,sector_y))''')
        for sql in ['CREATE INDEX IF NOT EXISTS idx_polywar_sector_stats_xy ON polywar_sector_faction_stats(season_id,sector_x,sector_y)','CREATE INDEX IF NOT EXISTS idx_polywar_sectors_xy ON polywar_sectors(season_id,sector_x,sector_y)','CREATE INDEX IF NOT EXISTS idx_polywar_sectors_controller ON polywar_sectors(season_id,controller_faction_id)']:
            c.execute(sql)
        conn.commit()
    finally:
        if own: conn.close()

def _upsert_stat(conn,sid,sx,sy,fid,delta,now):
    if not fid: return
    c=conn.cursor()
    if polywar._is_sqlite(conn):
        polywar._execute(c,'INSERT OR IGNORE INTO polywar_sector_faction_stats (season_id,sector_x,sector_y,faction_id,controlled_cells_count,updated_at) VALUES (%s,%s,%s,%s,0,%s)',(sid,sx,sy,fid,now))
    else:
        polywar._execute(c,'INSERT INTO polywar_sector_faction_stats (season_id,sector_x,sector_y,faction_id,controlled_cells_count,updated_at) VALUES (%s,%s,%s,%s,0,%s) ON CONFLICT (season_id,sector_x,sector_y,faction_id) DO NOTHING',(sid,sx,sy,fid,now))
    polywar._execute(c,'UPDATE polywar_sector_faction_stats SET controlled_cells_count=MAX(0,controlled_cells_count + %s), updated_at=%s WHERE season_id=%s AND sector_x=%s AND sector_y=%s AND faction_id=%s',(delta,now,sid,sx,sy,fid))

def recalc_influence(conn,sid,fid,now):
    polywar._execute(conn.cursor(),'UPDATE polywar_faction_season_stats SET influence_score=controlled_cells_count + controlled_sectors_count * %s, updated_at=%s WHERE season_id=%s AND faction_id=%s',(influence_value(),now,sid,fid))

def recalc_sector(conn,sid,sx,sy,now):
    c=conn.cursor(); before=polywar._fetchone(c,'SELECT controller_faction_id FROM polywar_sectors WHERE season_id=%s AND sector_x=%s AND sector_y=%s',(sid,sx,sy)) or {}
    rows=polywar._fetchall(c,'SELECT faction_id, controlled_cells_count FROM polywar_sector_faction_stats WHERE season_id=%s AND sector_x=%s AND sector_y=%s AND controlled_cells_count>0',(sid,sx,sy))
    total=sum(int(r['controlled_cells_count'] or 0) for r in rows); leader=None; leading=0; ties=0
    for r in rows:
        n=int(r['controlled_cells_count'] or 0)
        if n>leading: leader=int(r['faction_id']); leading=n; ties=1
        elif n==leading and n>0: ties+=1
    dominance=(leading*100//total) if total else 0
    controller=leader if total>=min_claimed() and leader and ties==1 and leading*100>=control_percent()*total else None
    contested=bool(total>=min_claimed() and len(rows)>=2 and controller is None)
    old=before.get('controller_faction_id')
    controlled_since=now if controller and controller!=old else (before.get('controlled_since') if controller else None)
    if polywar._is_sqlite(conn):
        polywar._execute(c,'INSERT OR REPLACE INTO polywar_sectors (season_id,sector_x,sector_y,controller_faction_id,total_claimed_cells,leading_faction_id,leading_cells,dominance_percent,is_contested,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(sid,sx,sy,controller,total,leader,leading,dominance,1 if contested else 0,controlled_since,now))
    else:
        polywar._execute(c,'INSERT INTO polywar_sectors (season_id,sector_x,sector_y,controller_faction_id,total_claimed_cells,leading_faction_id,leading_cells,dominance_percent,is_contested,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,sector_x,sector_y) DO UPDATE SET controller_faction_id=excluded.controller_faction_id,total_claimed_cells=excluded.total_claimed_cells,leading_faction_id=excluded.leading_faction_id,leading_cells=excluded.leading_cells,dominance_percent=excluded.dominance_percent,is_contested=excluded.is_contested,controlled_since=excluded.controlled_since,updated_at=excluded.updated_at',(sid,sx,sy,controller,total,leader,leading,dominance,contested,controlled_since,now))
    if old!=controller:
        if old:
            polywar._execute(c,'UPDATE polywar_faction_season_stats SET controlled_sectors_count=MAX(0,controlled_sectors_count-1), updated_at=%s WHERE season_id=%s AND faction_id=%s',(now,sid,old)); recalc_influence(conn,sid,old,now)
            polywar._execute(c,'INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s)',(sid,old,'sector_lost',f'Faction lost sector {sx},{sy}',now))
        if controller:
            polywar._execute(c,'UPDATE polywar_faction_season_stats SET controlled_sectors_count=controlled_sectors_count+1, updated_at=%s WHERE season_id=%s AND faction_id=%s',(now,sid,controller)); recalc_influence(conn,sid,controller,now)
            polywar._execute(c,'INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s)',(sid,controller,'sector_captured',f'Faction captured sector {sx},{sy}',now))
    return {'sector_x':sx,'sector_y':sy,'old_controller_faction_id':old,'controller_faction_id':controller}

def transfer_cell_ownership(conn,sid,x,y,old_owner,new_owner,user_id,now):
    c=conn.cursor(); sx,sy=sector_coords(x,y)
    if old_owner:
        polywar._execute(c,'UPDATE polywar_faction_season_stats SET controlled_cells_count=MAX(0,controlled_cells_count-1), updated_at=%s WHERE season_id=%s AND faction_id=%s',(now,sid,old_owner)); _upsert_stat(conn,sid,sx,sy,old_owner,-1,now); recalc_influence(conn,sid,old_owner,now)
    if new_owner:
        polywar._execute(c,'UPDATE polywar_faction_season_stats SET controlled_cells_count=controlled_cells_count+1, updated_at=%s WHERE season_id=%s AND faction_id=%s',(now,sid,new_owner)); _upsert_stat(conn,sid,sx,sy,new_owner,1,now); recalc_influence(conn,sid,new_owner,now)
    sec=recalc_sector(conn,sid,sx,sy,now)
    return sec

def initialize_starting_sector(conn,sid,sx,sy):
    from services import polywar_map_service as m
    now=datetime.utcnow(); size=sector_size(); x0,y0=sx*size,sy*size; x1=min(m.map_width(),x0+size); y1=min(m.map_height(),y0+size)
    counts={}
    for y in range(y0,y1):
        for x in range(x0,x1):
            fid=m._start_owner(x,y)
            if fid: counts[fid]=counts.get(fid,0)+1
    for fid,n in counts.items(): _upsert_stat(conn,sid,sx,sy,fid,n,now)
    if counts: recalc_sector(conn,sid,sx,sy,now)

def apply_materialized_starting_cell(conn,sid,x,y,owner,now):
    sx,sy=sector_coords(x,y)
    row=polywar._fetchone(conn.cursor(),'SELECT 1 FROM polywar_sector_faction_stats WHERE season_id=%s AND sector_x=%s AND sector_y=%s AND faction_id=%s',(sid,sx,sy,owner))
    if not row: initialize_starting_sector(conn,sid,sx,sy)

def _check_rate(user_id):
    now=time.monotonic(); q=_RATE[int(user_id)];
    while q and now-q[0]>10: q.popleft()
    if len(q)>=30: raise ValueError('rate_limited')
    q.append(now)
    if len(_RATE)>10000:
        for k in list(_RATE)[:1000]:
            if not _RATE[k] or now-_RATE[k][-1]>60: _RATE.pop(k,None)

def get_sectors(user_id,min_sx,max_sx,min_sy,max_sy):
    _check_rate(user_id)
    if min_sx<0 or min_sy<0 or max_sx<min_sx or max_sy<min_sy: raise ValueError('out_of_bounds')
    count=(max_sx-min_sx+1)*(max_sy-min_sy+1)
    if count>max_sectors_per_request(): raise ValueError('too_many_sectors')
    conn=polywar.get_connection()
    try:
        polywar.init_polywar_schema(conn); init_polywar_sector_schema(conn); season=polywar.ensure_active_season(conn); sid=int(season['id'])
        rows=polywar._fetchall(conn.cursor(),'SELECT * FROM polywar_sectors WHERE season_id=%s AND sector_x>=%s AND sector_x<=%s AND sector_y>=%s AND sector_y<=%s',(sid,min_sx,max_sx,min_sy,max_sy))
        return {'ok':True,'season_id':sid,'sector_size':sector_size(),'sectors':[{k:(polywar._iso(v) if k.endswith('_at') or k=='controlled_since' else v) for k,v in r.items() if k!='season_id'} for r in rows],'server_timestamp':int(time.time())}
    finally: conn.close()
