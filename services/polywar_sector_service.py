import math
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from typing import Tuple

from services import polywar_service as polywar

_RATE_LOCK = threading.Lock()
_RATE = OrderedDict()
RATE_WINDOW = 10
RATE_MAX = 30
RATE_MAX_USERS = 5000


def _setting_int(key, default, lo, hi):
    return polywar._setting_int(key, default, lo, hi)


def sector_size(): return _setting_int('polywar_sector_size', 100, 10, 10000)
def min_claimed(): return _setting_int('polywar_sector_min_claimed_cells', 25, 1, 1000000)
def control_percent(): return _setting_int('polywar_sector_control_percent', 60, 1, 100)
def influence_value(): return _setting_int('polywar_sector_influence_value', 100, 0, 1000000)
def max_sectors_per_request(): return _setting_int('polywar_max_sectors_per_request', 100, 1, 500)


def public_rules():
    return {
        'sector_size': sector_size(),
        'min_claimed_cells': min_claimed(),
        'control_percent': control_percent(),
        'influence_value': influence_value(),
        'max_sectors_per_request': max_sectors_per_request(),
    }


def sector_coords(x: int, y: int) -> Tuple[int, int]:
    s = sector_size()
    return int(x) // s, int(y) // s


def _nonnegative_expr(conn, column: str, delta_placeholder: str = '%s') -> str:
    if polywar._is_sqlite(conn):
        return f'CASE WHEN {column} + {delta_placeholder} < 0 THEN 0 ELSE {column} + {delta_placeholder} END'
    return f'GREATEST(0, {column} + {delta_placeholder})'


def _decrement_expr(conn, column: str) -> str:
    if polywar._is_sqlite(conn):
        return f'CASE WHEN {column} - 1 < 0 THEN 0 ELSE {column} - 1 END'
    return f'GREATEST(0, {column} - 1)'


def _col_exists(conn, table, col):
    c = conn.cursor()
    if polywar._is_sqlite(conn):
        c.execute(f'PRAGMA table_info({table})')
        return any(r[1] == col for r in c.fetchall())
    c.execute('SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s', (table, col))
    return c.fetchone() is not None


def _add_col(conn, table, spec):
    col = spec.split()[0]
    if not _col_exists(conn, table, col):
        conn.cursor().execute(f'ALTER TABLE {table} ADD COLUMN {spec}')


def init_polywar_sector_schema(conn=None):
    own = conn is None
    conn = conn or polywar.get_connection()
    c = conn.cursor()
    try:
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_sector_faction_stats (season_id INTEGER NOT NULL, sector_x INTEGER NOT NULL, sector_y INTEGER NOT NULL, faction_id INTEGER NOT NULL, controlled_cells_count INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,sector_x,sector_y,faction_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_sectors (season_id INTEGER NOT NULL, sector_x INTEGER NOT NULL, sector_y INTEGER NOT NULL, controller_faction_id INTEGER NULL, total_claimed_cells INTEGER NOT NULL DEFAULT 0, leading_faction_id INTEGER NULL, leading_cells INTEGER NOT NULL DEFAULT 0, dominance_percent INTEGER NOT NULL DEFAULT 0, is_contested INTEGER NOT NULL DEFAULT 0, controlled_since TIMESTAMP NULL, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,sector_x,sector_y))''')
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_sector_initializations (season_id INTEGER NOT NULL, sector_x INTEGER NOT NULL, sector_y INTEGER NOT NULL, initialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,sector_x,sector_y))''')
        for sql in [
            'CREATE INDEX IF NOT EXISTS idx_polywar_sector_stats_xy ON polywar_sector_faction_stats(season_id,sector_x,sector_y)',
            'CREATE INDEX IF NOT EXISTS idx_polywar_sectors_xy ON polywar_sectors(season_id,sector_x,sector_y)',
            'CREATE INDEX IF NOT EXISTS idx_polywar_sectors_controller ON polywar_sectors(season_id,controller_faction_id)',
            'CREATE INDEX IF NOT EXISTS idx_polywar_sector_init_xy ON polywar_sector_initializations(season_id,sector_x,sector_y)',
        ]:
            c.execute(sql)
        if own: conn.commit()
    finally:
        if own:
            conn.close()


def _ensure_sector_row(conn, sid, sx, sy, now):
    c = conn.cursor()
    if polywar._is_sqlite(conn):
        polywar._execute(c, 'INSERT OR IGNORE INTO polywar_sectors (season_id,sector_x,sector_y,updated_at) VALUES (%s,%s,%s,%s)', (sid, sx, sy, now))
        return
    polywar._execute(c, 'INSERT INTO polywar_sectors (season_id,sector_x,sector_y,updated_at) VALUES (%s,%s,%s,%s) ON CONFLICT (season_id,sector_x,sector_y) DO NOTHING', (sid, sx, sy, now))
    polywar._fetchone(c, 'SELECT season_id FROM polywar_sectors WHERE season_id=%s AND sector_x=%s AND sector_y=%s FOR UPDATE', (sid, sx, sy))


def _sector_lock(conn, sid, sx, sy, now=None):
    if polywar._is_sqlite(conn):
        return
    _ensure_sector_row(conn, sid, sx, sy, now or datetime.utcnow())


def _upsert_stat(conn, sid, sx, sy, fid, delta, now):
    if not fid or not delta:
        return
    c = conn.cursor()
    if polywar._is_sqlite(conn):
        polywar._execute(c, 'INSERT OR IGNORE INTO polywar_sector_faction_stats (season_id,sector_x,sector_y,faction_id,controlled_cells_count,updated_at) VALUES (%s,%s,%s,%s,0,%s)', (sid, sx, sy, fid, now))
    else:
        polywar._execute(c, 'INSERT INTO polywar_sector_faction_stats (season_id,sector_x,sector_y,faction_id,controlled_cells_count,updated_at) VALUES (%s,%s,%s,%s,0,%s) ON CONFLICT (season_id,sector_x,sector_y,faction_id) DO NOTHING', (sid, sx, sy, fid, now))
    expr = _nonnegative_expr(conn, 'controlled_cells_count')
    params = ((delta, delta, now, sid, sx, sy, fid) if polywar._is_sqlite(conn) else (delta, now, sid, sx, sy, fid))
    polywar._execute(c, f'UPDATE polywar_sector_faction_stats SET controlled_cells_count={expr}, updated_at=%s WHERE season_id=%s AND sector_x=%s AND sector_y=%s AND faction_id=%s', params)


def recalc_influence(conn, sid, fid, now):
    
    try:
        from services.polywar_capital_service import influence_value as cap_value
        cap = cap_value()
    except Exception:
        cap = 0
    polywar._execute(conn.cursor(), 'UPDATE polywar_faction_season_stats SET influence_score=controlled_cells_count + controlled_sectors_count * %s + COALESCE(controlled_capitals_count,0) * %s, updated_at=%s WHERE season_id=%s AND faction_id=%s', (influence_value(), cap, now, sid, fid))


def recalc_sector(conn, sid, sx, sy, now):
    c = conn.cursor()
    before = polywar._fetchone(c, 'SELECT controller_faction_id, controlled_since FROM polywar_sectors WHERE season_id=%s AND sector_x=%s AND sector_y=%s', (sid, sx, sy)) or {}
    rows = polywar._fetchall(c, 'SELECT faction_id, controlled_cells_count FROM polywar_sector_faction_stats WHERE season_id=%s AND sector_x=%s AND sector_y=%s AND controlled_cells_count>0', (sid, sx, sy))
    total = sum(int(r['controlled_cells_count'] or 0) for r in rows)
    leader = None; leading = 0; ties = 0
    for r in rows:
        n = int(r['controlled_cells_count'] or 0)
        if n > leading:
            leader = int(r['faction_id']); leading = n; ties = 1
        elif n == leading and n > 0:
            ties += 1
    dominance = (leading * 100 // total) if total else 0
    controller = leader if total >= min_claimed() and leader and ties == 1 and leading * 100 >= control_percent() * total else None
    contested = bool(total >= min_claimed() and len(rows) >= 2 and controller is None)
    old = before.get('controller_faction_id')
    controlled_since = before.get('controlled_since') if controller and controller == old else (now if controller else None)
    contested_int = 1 if contested else 0
    if polywar._is_sqlite(conn):
        polywar._execute(c, 'INSERT OR REPLACE INTO polywar_sectors (season_id,sector_x,sector_y,controller_faction_id,total_claimed_cells,leading_faction_id,leading_cells,dominance_percent,is_contested,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (sid, sx, sy, controller, total, leader, leading, dominance, contested_int, controlled_since, now))
    else:
        polywar._execute(c, 'INSERT INTO polywar_sectors (season_id,sector_x,sector_y,controller_faction_id,total_claimed_cells,leading_faction_id,leading_cells,dominance_percent,is_contested,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,sector_x,sector_y) DO UPDATE SET controller_faction_id=excluded.controller_faction_id,total_claimed_cells=excluded.total_claimed_cells,leading_faction_id=excluded.leading_faction_id,leading_cells=excluded.leading_cells,dominance_percent=excluded.dominance_percent,is_contested=excluded.is_contested,controlled_since=excluded.controlled_since,updated_at=excluded.updated_at', (sid, sx, sy, controller, total, leader, leading, dominance, contested_int, controlled_since, now))
    if old != controller:
        if old:
            expr = _decrement_expr(conn, 'controlled_sectors_count')
            polywar._execute(c, f'UPDATE polywar_faction_season_stats SET controlled_sectors_count={expr}, updated_at=%s WHERE season_id=%s AND faction_id=%s', (now, sid, old))
            recalc_influence(conn, sid, old, now)
            polywar._execute(c, 'INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s)', (sid, old, 'sector_lost', f'Faction lost sector {sx},{sy}', now))
        if controller:
            polywar._execute(c, 'UPDATE polywar_faction_season_stats SET controlled_sectors_count=controlled_sectors_count+1, updated_at=%s WHERE season_id=%s AND faction_id=%s', (now, sid, controller))
            recalc_influence(conn, sid, controller, now)
            polywar._execute(c, 'INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s)', (sid, controller, 'sector_captured', f'Faction captured sector {sx},{sy}', now))
    return {'sector_x': sx, 'sector_y': sy, 'old_controller_faction_id': old, 'controller_faction_id': controller}


def initialize_sector(conn, sid, sx, sy, now=None):
    from services import polywar_map_service as m
    now = now or datetime.utcnow()
    _sector_lock(conn, sid, sx, sy, now)
    c = conn.cursor()
    if polywar._fetchone(c, 'SELECT 1 FROM polywar_sector_initializations WHERE season_id=%s AND sector_x=%s AND sector_y=%s', (sid, sx, sy)):
        return False
    size = sector_size(); x0, y0 = sx * size, sy * size; x1, y1 = min(m.map_width(), x0 + size), min(m.map_height(), y0 + size)
    rows = polywar._fetchall(c, 'SELECT x,y,owner_faction_id FROM polywar_cells WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s', (sid, x0, x1, y0, y1))
    sparse = {(int(r['x']), int(r['y'])): int(r['owner_faction_id']) for r in rows}
    counts = {}
    for y in range(y0, y1):
        for x in range(x0, x1):
            fid = sparse.get((x, y)) or m._start_owner(x, y)
            if fid:
                counts[fid] = counts.get(fid, 0) + 1
    for fid, n in counts.items():
        _upsert_stat(conn, sid, sx, sy, fid, n, now)
    if polywar._is_sqlite(conn):
        polywar._execute(c, 'INSERT OR IGNORE INTO polywar_sector_initializations (season_id,sector_x,sector_y,initialized_at) VALUES (%s,%s,%s,%s)', (sid, sx, sy, now))
    else:
        polywar._execute(c, 'INSERT INTO polywar_sector_initializations (season_id,sector_x,sector_y,initialized_at) VALUES (%s,%s,%s,%s) ON CONFLICT (season_id,sector_x,sector_y) DO NOTHING', (sid, sx, sy, now))
    recalc_sector(conn, sid, sx, sy, now)
    return True


def _starting_rects(width, height, area_size, bases):
    half = int(area_size) // 2
    rects = []
    for fid, (bx, by) in bases.items():
        x0 = max(0, int(bx) - half); y0 = max(0, int(by) - half)
        x1 = min(int(width), int(bx) + half + 1); y1 = min(int(height), int(by) + half + 1)
        if x0 < x1 and y0 < y1:
            rects.append({'fid': int(fid), 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1})
    return rects


def _rect_intersection(a, b):
    x0 = max(a['x0'], b['x0']); y0 = max(a['y0'], b['y0'])
    x1 = min(a['x1'], b['x1']); y1 = min(a['y1'], b['y1'])
    if x0 >= x1 or y0 >= y1:
        return None
    return {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1}


def _rect_area(r):
    return max(0, int(r['x1']) - int(r['x0'])) * max(0, int(r['y1']) - int(r['y0']))


def _implicit_owner_from_rects(x, y, rects):
    for r in rects:
        if r['x0'] <= x < r['x1'] and r['y0'] <= y < r['y1']:
            return r['fid']
    return None


def _starting_sector_rects(rects, size):
    out = {}
    for r in rects:
        for sx in range(r['x0'] // size, (r['x1'] - 1) // size + 1):
            for sy in range(r['y0'] // size, (r['y1'] - 1) // size + 1):
                sr = {'x0': sx * size, 'y0': sy * size, 'x1': (sx + 1) * size, 'y1': (sy + 1) * size}
                out[(sx, sy)] = sr
    return out


def initialize_starting_sectors_in_transaction(conn, sid, rects, sectors_seen, now=None):
    now = now or datetime.utcnow(); c = conn.cursor()
    sector_deltas = {}; global_deltas = {}
    for sx, sy in sorted(sectors_seen):
        _sector_lock(conn, sid, sx, sy, now)
        if polywar._fetchone(c, 'SELECT 1 FROM polywar_sector_initializations WHERE season_id=%s AND sector_x=%s AND sector_y=%s', (sid, sx, sy)):
            continue
        sr = sectors_seen[(sx, sy)]
        counts = {}
        for r in rects:
            inter = _rect_intersection(r, sr)
            if inter:
                n = _rect_area(inter)
                counts[r['fid']] = counts.get(r['fid'], 0) + n
                global_deltas[r['fid']] = global_deltas.get(r['fid'], 0) + n
        rows = polywar._fetchall(c, 'SELECT x,y,owner_faction_id FROM polywar_cells WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s', (sid, sr['x0'], sr['x1'], sr['y0'], sr['y1']))
        for row in rows:
            x = int(row['x']); y = int(row['y']); actual = int(row['owner_faction_id'])
            implicit = _implicit_owner_from_rects(x, y, rects)
            if implicit:
                counts[implicit] = counts.get(implicit, 0) - 1
                global_deltas[implicit] = global_deltas.get(implicit, 0) - 1
            counts[actual] = counts.get(actual, 0) + 1
        for fid, n in counts.items():
            if n:
                _upsert_stat(conn, sid, sx, sy, fid, n, now)
        if polywar._is_sqlite(conn):
            polywar._execute(c, 'INSERT OR IGNORE INTO polywar_sector_initializations (season_id,sector_x,sector_y,initialized_at) VALUES (%s,%s,%s,%s)', (sid, sx, sy, now))
        else:
            polywar._execute(c, 'INSERT INTO polywar_sector_initializations (season_id,sector_x,sector_y,initialized_at) VALUES (%s,%s,%s,%s) ON CONFLICT (season_id,sector_x,sector_y) DO NOTHING', (sid, sx, sy, now))
        recalc_sector(conn, sid, sx, sy, now)
    return global_deltas


def ensure_starting_territories_bootstrap(conn, sid):
    from services import polywar_map_service as m
    own_tx = False
    if not polywar._is_sqlite(conn):
        polywar._execute(conn.cursor(), "SET LOCAL lock_timeout = '5s'")
        polywar._execute(conn.cursor(), "SET LOCAL statement_timeout = '20s'")
    if polywar._is_sqlite(conn) and not getattr(conn, "in_transaction", False):
        last = None
        for i in range(20):
            try:
                conn.cursor().execute("BEGIN IMMEDIATE"); own_tx = True; break
            except Exception as exc:
                if "locked" not in str(exc).lower():
                    raise
                last = exc; time.sleep(0.025 * (i + 1))
        if not own_tx and last:
            raise last
    now = datetime.utcnow(); c = conn.cursor(); marker = (-1, -1)
    try:
        _sector_lock(conn, sid, marker[0], marker[1], now)
        if polywar._fetchone(c, 'SELECT 1 FROM polywar_sector_initializations WHERE season_id=%s AND sector_x=%s AND sector_y=%s', (sid, marker[0], marker[1])):
            if own_tx: conn.commit()
            return False
        width = m.map_width(); height = m.map_height(); area = m.starting_area_size(); size = sector_size(); bases = m.faction_base_positions(width, height)
        rects = _starting_rects(width, height, area, bases)
        sectors_seen = _starting_sector_rects(rects, size)
        global_deltas = initialize_starting_sectors_in_transaction(conn, sid, rects, sectors_seen, now)
        for fid, n in global_deltas.items():
            if n:
                polywar._execute(c, 'UPDATE polywar_faction_season_stats SET controlled_cells_count=controlled_cells_count+%s, updated_at=%s WHERE season_id=%s AND faction_id=%s', (n, now, sid, fid))
                recalc_influence(conn, sid, fid, now)
        if polywar._is_sqlite(conn):
            polywar._execute(c, 'INSERT OR IGNORE INTO polywar_sector_initializations (season_id,sector_x,sector_y,initialized_at) VALUES (%s,%s,%s,%s)', (sid, marker[0], marker[1], now))
        else:
            polywar._execute(c, 'INSERT INTO polywar_sector_initializations (season_id,sector_x,sector_y,initialized_at) VALUES (%s,%s,%s,%s) ON CONFLICT (season_id,sector_x,sector_y) DO NOTHING', (sid, marker[0], marker[1], now))
        if own_tx: conn.commit()
        return True
    except Exception:
        if own_tx: polywar._safe_rollback(conn)
        raise

def apply_materialized_starting_cell(conn, sid, x, y, owner, now):
    initialize_sector(conn, sid, *sector_coords(x, y), now)


def transfer_cell_ownership(conn, sid, x, y, old_owner, new_owner, user_id, now):
    c = conn.cursor(); sx, sy = sector_coords(x, y)
    initialize_sector(conn, sid, sx, sy, now)
    _sector_lock(conn, sid, sx, sy, now)
    if old_owner:
        expr = _decrement_expr(conn, 'controlled_cells_count')
        polywar._execute(c, f'UPDATE polywar_faction_season_stats SET controlled_cells_count={expr}, updated_at=%s WHERE season_id=%s AND faction_id=%s', (now, sid, old_owner))
        _upsert_stat(conn, sid, sx, sy, old_owner, -1, now)
        recalc_influence(conn, sid, old_owner, now)
    if new_owner:
        polywar._execute(c, 'UPDATE polywar_faction_season_stats SET controlled_cells_count=controlled_cells_count+1, updated_at=%s WHERE season_id=%s AND faction_id=%s', (now, sid, new_owner))
        _upsert_stat(conn, sid, sx, sy, new_owner, 1, now)
        recalc_influence(conn, sid, new_owner, now)
    return recalc_sector(conn, sid, sx, sy, now)


def _check_rate(user_id):
    now = time.monotonic(); uid = int(user_id)
    with _RATE_LOCK:
        for key in list(_RATE.keys()):
            q = _RATE[key]
            while q and now - q[0] > RATE_WINDOW:
                q.popleft()
            if not q:
                _RATE.pop(key, None)
        q = _RATE.get(uid)
        if q is None:
            if len(_RATE) >= RATE_MAX_USERS:
                _RATE.popitem(last=False)
            q = deque(); _RATE[uid] = q
        if len(q) >= RATE_MAX:
            raise ValueError('rate_limited')
        q.append(now); _RATE.move_to_end(uid)


def get_sectors(user_id, min_sx, max_sx, min_sy, max_sy):
    if min_sx < 0 or min_sy < 0 or max_sx < min_sx or max_sy < min_sy:
        raise ValueError('out_of_bounds')
    from services import polywar_map_service as m
    max_x = math.ceil(m.map_width() / sector_size()) - 1
    max_y = math.ceil(m.map_height() / sector_size()) - 1
    if max_sx > max_x or max_sy > max_y:
        raise ValueError('out_of_bounds')
    count = (max_sx - min_sx + 1) * (max_sy - min_sy + 1)
    if count > max_sectors_per_request():
        raise ValueError('too_many_sectors')
    conn = polywar.get_connection()
    try:
        polywar.init_polywar_schema(conn); init_polywar_sector_schema(conn); season = polywar.ensure_active_season_in_transaction(conn); sid = int(season['id'])
        ensure_starting_territories_bootstrap(conn, sid)
        _check_rate(user_id)
        now = datetime.utcnow()
        for sx in range(min_sx, max_sx + 1):
            for sy in range(min_sy, max_sy + 1):
                initialize_sector(conn, sid, sx, sy, now)
        conn.commit()
        rows = polywar._fetchall(conn.cursor(), 'SELECT * FROM polywar_sectors WHERE season_id=%s AND sector_x>=%s AND sector_x<=%s AND sector_y>=%s AND sector_y<=%s', (sid, min_sx, max_sx, min_sy, max_sy))
        return {'ok': True, 'season_id': sid, 'sector_size': sector_size(), 'sectors': [{k: (polywar._iso(v) if k.endswith('_at') or k == 'controlled_since' else v) for k, v in r.items() if k != 'season_id'} for r in rows], 'server_timestamp': int(time.time())}
    finally:
        conn.close()
