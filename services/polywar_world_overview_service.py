import hashlib
import math
import threading
import time
from collections import OrderedDict
from typing import Dict, Tuple

from services import polywar_service as polywar
from services import polywar_map_service as m

MAX_GRID = 128
TTL_SECONDS = 10
_CACHE = OrderedDict()
_LOCK = threading.RLock()


def _rev(*parts):
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:24]


def _cache_get(key):
    now = time.time()
    with _LOCK:
        item = _CACHE.get(key)
        if item and now - item[0] <= TTL_SECONDS:
            _CACHE.move_to_end(key)
            return item[1]
        if item:
            _CACHE.pop(key, None)
    return None


def _cache_set(key, value):
    with _LOCK:
        _CACHE[key] = (time.time(), value)
        while len(_CACHE) > 32:
            _CACHE.popitem(last=False)


def _table_columns(conn, table):
    try:
        if polywar._is_sqlite(conn):
            rows = polywar._fetchall(conn.cursor(), f"PRAGMA table_info({table})")
            return {r.get("name") for r in rows}
        rows = polywar._fetchall(conn.cursor(), "SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (table,))
        return {r.get("name") for r in rows}
    except Exception:
        return set()

def _latest(conn, table, season_id):
    cols = _table_columns(conn, table)
    if "updated_at" not in cols:
        return ""
    row = polywar._fetchone(conn.cursor(), f"SELECT MAX(updated_at) AS v FROM {table} WHERE season_id=%s", (int(season_id),)) or {}
    return row.get("v") or ""


def _grid(config):
    sector_cols = max(1, math.ceil(config.width / config.sector_size))
    sector_rows = max(1, math.ceil(config.height / config.sector_size))
    cols = min(MAX_GRID, sector_cols)
    rows = min(MAX_GRID, sector_rows)
    return sector_cols, sector_rows, cols, rows, max(1, math.ceil(sector_cols / cols)), max(1, math.ceil(sector_rows / rows))


def _factions(conn):
    return polywar.list_all_polywar_factions(conn)


def _implicit_bins(config, cols, rows):
    out = {}
    half = config.starting_area_size // 2
    wpc = config.width / cols
    wpr = config.height / rows
    for fid, (bx, by) in config.bases.items():
        x0, x1 = max(0, bx - half), min(config.width - 1, bx + half)
        y0, y1 = max(0, by - half), min(config.height - 1, by + half)
        gx0, gx1 = int(x0 / wpc), min(cols - 1, int(x1 / wpc))
        gy0, gy1 = int(y0 / wpr), min(rows - 1, int(y1 / wpr))
        for gy in range(gy0, gy1 + 1):
            for gx in range(gx0, gx1 + 1):
                out.setdefault((gx, gy), {'grid_x': gx, 'grid_y': gy, 'controller_faction_id': int(fid), 'leading_faction_id': int(fid), 'dominance_percent': 35, 'is_contested': False, 'implicit': True})
    return out


def build_world_overview(user_id=None):
    conn = polywar.get_connection()
    try:
        m.begin_polywar_readonly(conn)
        season = m.get_active_season_readonly(conn)
        sid = int(season['id'])
        config = m.load_map_config(conn, season=season)
        sector_cols, sector_rows, cols, rows, sx_bin, sy_bin = _grid(config)
        latest_sector = _latest(conn, 'polywar_sectors', sid)
        latest_cap = _latest(conn, 'polywar_capitals', sid)
        latest_rift = _latest(conn, 'polywar_null_rifts', sid)
        key = (sid, config.width, config.height, config.sector_size, cols, rows, latest_sector, latest_cap, latest_rift)
        cached = _cache_get(key)
        if cached:
            return cached
        cells_by_key = _implicit_bins(config, cols, rows)
        expr_x = f"CAST(sector_x / {int(sx_bin)} AS INTEGER)"
        expr_y = f"CAST(sector_y / {int(sy_bin)} AS INTEGER)"
        faction_expr = "COALESCE(controller_faction_id, leading_faction_id, 0)"
        rows_db = polywar._fetchall(conn.cursor(), f'''SELECT {expr_x} AS grid_x,{expr_y} AS grid_y,{faction_expr} AS faction_id,COUNT(*) AS controlled_sector_count,SUM(total_claimed_cells) AS total_claimed_cells,SUM(CASE WHEN is_contested THEN 1 ELSE 0 END) AS contested_count FROM polywar_sectors WHERE season_id=%s AND (controller_faction_id IS NOT NULL OR leading_faction_id IS NOT NULL OR total_claimed_cells>0) GROUP BY grid_x,grid_y,faction_id LIMIT %s''', (sid, int(cols * rows * 8)))
        accum: Dict[Tuple[int,int], Dict[int,int]] = {}
        meta = {}
        for r in rows_db:
            gx = min(cols - 1, int(r['grid_x'])); gy = min(rows - 1, int(r['grid_y']))
            k=(gx,gy); fid = int(r.get('faction_id') or 0); n = int(r.get('controlled_sector_count') or 0)
            accum.setdefault(k, {})[fid] = accum.setdefault(k, {}).get(fid, 0) + n
            mm = meta.setdefault(k, {'claimed':0,'contested':False})
            mm['claimed'] += int(r.get('total_claimed_cells') or 0); mm['contested'] = mm['contested'] or int(r.get('contested_count') or 0) > 0
        for k, counts in accum.items():
            best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            total = max(1, sum(counts.values()))
            cells_by_key[k] = {'grid_x': k[0], 'grid_y': k[1], 'controller_faction_id': best[0] or None, 'leading_faction_id': best[0] or None, 'dominance_percent': int(best[1]*100/total), 'is_contested': bool(meta[k]['contested'] or len(counts) > 1), 'controlled_sector_count': best[1], 'total_claimed_cells': meta[k]['claimed']}
        factions = _factions(conn)
        fmap = {int(f['id']): f for f in factions}
        hq = [{'faction_id': fid, 'x': x, 'y': y, 'name': f"{fmap.get(fid,{}).get('name','Faction')} HQ", 'color': fmap.get(fid,{}).get('color'), 'is_player_faction': False} for fid,(x,y) in sorted(config.bases.items())]
        caps = polywar._fetchall(conn.cursor(), 'SELECT original_faction_id,controller_faction_id,x,y,besieging_faction_id,siege_progress,captured_at FROM polywar_capitals WHERE season_id=%s ORDER BY original_faction_id', (sid,))
        rifts = []
        if {'x','y','status'}.issubset(_table_columns(conn, 'polywar_null_rifts')):
            rifts = polywar._fetchall(conn.cursor(), "SELECT x,y,status FROM polywar_null_rifts WHERE season_id=%s AND status IN ('active','sealed') LIMIT 200", (sid,))
        revision = _rev(*key)
        out = {'ok': True, 'season_id': sid, 'revision': revision, 'world': {'width': config.width, 'height': config.height, 'chunk_size': config.chunk_size, 'sector_size': config.sector_size, 'world_version': int(season.get('map_world_version') or 1), 'sector_columns': sector_cols, 'sector_rows': sector_rows}, 'overview_grid': {'columns': cols, 'rows': rows, 'world_per_column': config.width / cols, 'world_per_row': config.height / rows, 'cells': list(cells_by_key.values())}, 'factions': factions, 'hq': hq, 'capitals': [{**c, 'is_under_siege': int(c.get('siege_progress') or 0)>0, 'is_captured': int(c.get('original_faction_id')) != int(c.get('controller_faction_id'))} for c in caps], 'major_objects': [{'type':'rift','x':int(r['x']),'y':int(r['y']),'status':r.get('status'),'importance':2} for r in rifts], 'stats': {'controlled_sectors': len(rows_db), 'contested_sectors': sum(1 for c in cells_by_key.values() if c.get('is_contested'))}, 'server_timestamp': int(time.time())}
        _cache_set(key, out)
        return out
    finally:
        conn.close()
