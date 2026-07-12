import json
import logging
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from services import polywar_service as polywar
from services import polywar_map_service as m
from services import polywar_mine_service as mines
from services import polywar_sector_service as sectors

logger = logging.getLogger(__name__)
_MUT_LOCK = threading.Lock(); _MUT_RATE: "OrderedDict[int, deque]" = OrderedDict()
_GET_LOCK = threading.Lock(); _GET_RATE: "OrderedDict[int, deque]" = OrderedDict()
RATE_WINDOW = 10; MUT_RATE_MAX = 30; GET_RATE_MAX = 120; RATE_MAX_USERS = 5000
NOISY_EVENTS = {'siege_started', 'siege_progress', 'rival_siege_reduced', 'capital_repaired'}
FINAL_EVENTS = {'capital_captured', 'capital_recaptured', 'capital_siege_cleared', 'rival_siege_cleared'}


def _setting_int(k, d, lo, hi): return polywar._setting_int(k, d, lo, hi)
def siege_required(): return _setting_int('polywar_capital_siege_required', 1000, 100, 100000)
def siege_power(): return _setting_int('polywar_capital_siege_progress_per_action', 100, 1, 10000)
def siege_extra_energy(): return _setting_int('polywar_capital_siege_extra_energy', 2, 0, 100)
def repair_cost(): return _setting_int('polywar_capital_repair_energy_cost', 2, 0, 100)
def repair_power(): return _setting_int('polywar_capital_repair_progress_per_action', 75, 1, 10000)
def influence_value(): return _setting_int('polywar_capital_influence_value', 1000, 0, 1000000)
def event_cooldown(): return _setting_int('polywar_capital_event_cooldown_seconds', 30, 0, 3600)


def public_rules():
    return {'siege_required': siege_required(), 'siege_progress_per_action': siege_power(), 'siege_extra_energy': siege_extra_energy(), 'repair_energy_cost': repair_cost(), 'repair_progress_per_action': repair_power(), 'influence_value': influence_value()}


def _rate(bucket, lock, uid, max_count):
    now = time.monotonic(); uid = int(uid)
    with lock:
        for key in list(bucket.keys()):
            q = bucket[key]
            while q and now - q[0] > RATE_WINDOW: q.popleft()
            if not q: bucket.pop(key, None)
        q = bucket.get(uid)
        if q is None:
            if len(bucket) >= RATE_MAX_USERS: bucket.popitem(last=False)
            q = deque(); bucket[uid] = q
        if len(q) >= max_count: raise ValueError('rate_limited')
        q.append(now); bucket.move_to_end(uid)

def _rate_mut(uid): _rate(_MUT_RATE, _MUT_LOCK, uid, MUT_RATE_MAX)
def _rate_get(uid): _rate(_GET_RATE, _GET_LOCK, uid, GET_RATE_MAX)


def _add_col(conn, table, spec): sectors._add_col(conn, table, spec)


def init_polywar_capital_schema(conn=None):
    own = conn is None; conn = conn or polywar.get_connection(); c = conn.cursor()
    id_sql = 'INTEGER PRIMARY KEY AUTOINCREMENT' if polywar._is_sqlite(conn) else 'SERIAL PRIMARY KEY'
    try:
        c.execute(f'''CREATE TABLE IF NOT EXISTS polywar_capitals (id {id_sql}, season_id INTEGER NOT NULL, original_faction_id INTEGER NOT NULL, controller_faction_id INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, besieging_faction_id INTEGER NULL, siege_progress INTEGER NOT NULL DEFAULT 0, siege_started_at TIMESTAMP NULL, last_siege_at TIMESTAMP NULL, last_siege_by_user_id BIGINT NULL, captured_at TIMESTAMP NULL, controlled_since TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, UNIQUE(season_id,original_faction_id), UNIQUE(season_id,x,y))''')
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_capital_initializations (season_id INTEGER NOT NULL UNIQUE, initialized_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        for spec in ['controlled_capitals_count INTEGER NOT NULL DEFAULT 0', 'commander_user_id BIGINT NULL', 'commander_since TIMESTAMP NULL', 'commander_term_ends_at TIMESTAMP NULL']:
            _add_col(conn, 'polywar_faction_season_stats', spec)
        for sql in ['CREATE INDEX IF NOT EXISTS idx_polywar_capitals_controller ON polywar_capitals(season_id,controller_faction_id)', 'CREATE INDEX IF NOT EXISTS idx_polywar_capitals_besieger ON polywar_capitals(season_id,besieging_faction_id)', 'CREATE INDEX IF NOT EXISTS idx_polywar_capitals_xy ON polywar_capitals(season_id,x,y)', 'CREATE INDEX IF NOT EXISTS idx_polywar_capital_init_sid ON polywar_capital_initializations(season_id)']:
            c.execute(sql)
        if own: conn.commit()
    finally:
        if own: conn.close()


def recalc_influence(conn, sid, fid, now):
    polywar._execute(conn.cursor(), 'UPDATE polywar_faction_season_stats SET influence_score=controlled_cells_count + controlled_sectors_count * %s + COALESCE(controlled_capitals_count,0) * %s, updated_at=%s WHERE season_id=%s AND faction_id=%s', (sectors.influence_value(), influence_value(), now, sid, fid))


def _upsert_capital(conn, sid, fid, owner, x, y, now):
    c = conn.cursor()
    if polywar._is_sqlite(conn):
        polywar._execute(c, 'INSERT OR IGNORE INTO polywar_capitals (season_id,original_faction_id,controller_faction_id,x,y,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)', (sid, fid, owner, x, y, now, now))
        polywar._execute(c, 'INSERT OR IGNORE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s)', (sid, x, y, owner, now))
    else:
        polywar._execute(c, 'INSERT INTO polywar_capitals (season_id,original_faction_id,controller_faction_id,x,y,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,original_faction_id) DO NOTHING', (sid, fid, owner, x, y, now, now))
        polywar._execute(c, 'INSERT INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s) ON CONFLICT (season_id,x,y) DO NOTHING', (sid, x, y, owner, now))
    polywar._execute(c, 'UPDATE polywar_cells SET owner_faction_id=%s, contesting_faction_id=NULL, contest_progress=0, contested_at=NULL, updated_at=%s WHERE season_id=%s AND x=%s AND y=%s', (owner, now, sid, x, y))


def _recount_capitals(conn, sid, now):
    c = conn.cursor()
    polywar._execute(c, 'UPDATE polywar_faction_season_stats SET controlled_capitals_count=0, updated_at=%s WHERE season_id=%s', (now, sid))
    rows = polywar._fetchall(c, 'SELECT controller_faction_id,COUNT(*) n FROM polywar_capitals WHERE season_id=%s GROUP BY controller_faction_id', (sid,))
    for r in rows:
        polywar._execute(c, 'UPDATE polywar_faction_season_stats SET controlled_capitals_count=%s, updated_at=%s WHERE season_id=%s AND faction_id=%s', (int(r['n']), now, sid, int(r['controller_faction_id'])))
    for fid, *_ in polywar.FACTIONS:
        recalc_influence(conn, sid, fid, now)


def ensure_capitals_initialized(conn, season_id: int):
    # Safe in any entry point: own SQLite transaction only when caller is not already in one.
    own_tx = False
    if polywar._is_sqlite(conn) and not getattr(conn, 'in_transaction', False):
        _begin(conn, conn.cursor()); own_tx = True
    try:
        sectors.ensure_starting_territories_bootstrap(conn, season_id)
        c = conn.cursor(); now = datetime.utcnow()
        if polywar._fetchone(c, 'SELECT 1 FROM polywar_capital_initializations WHERE season_id=%s', (season_id,)):
            if own_tx: conn.commit()
            return False
        if not polywar._is_sqlite(conn):
            # Serializes concurrent initializers against season row in Postgres.
            polywar._fetchone(c, 'SELECT id FROM polywar_seasons WHERE id=%s FOR UPDATE', (season_id,))
            if polywar._fetchone(c, 'SELECT 1 FROM polywar_capital_initializations WHERE season_id=%s', (season_id,)):
                return False
        for fid, (x, y) in m.faction_base_positions().items():
            row = polywar._fetchone(c, 'SELECT owner_faction_id FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s', (season_id, x, y))
            owner = int(row['owner_faction_id']) if row else int(m._owner_at(conn, season_id, x, y) or fid)
            _upsert_capital(conn, season_id, fid, owner, x, y, now)
        _recount_capitals(conn, season_id, now)
        if polywar._is_sqlite(conn):
            polywar._execute(c, 'INSERT OR IGNORE INTO polywar_capital_initializations (season_id,initialized_at) VALUES (%s,%s)', (season_id, now))
        else:
            polywar._execute(c, 'INSERT INTO polywar_capital_initializations (season_id,initialized_at) VALUES (%s,%s) ON CONFLICT (season_id) DO NOTHING', (season_id, now))
        if own_tx: conn.commit()
        return True
    except Exception:
        if own_tx: polywar._safe_rollback(conn)
        raise


def get_capital_at(conn, sid, x, y):
    return polywar._fetchone(conn.cursor(), 'SELECT * FROM polywar_capitals WHERE season_id=%s AND x=%s AND y=%s', (sid, x, y))


def _has_adjacent(conn, sid, x, y, fid):
    return any(m.in_bounds(nx, ny) and m._owner_at(conn, sid, nx, ny) == fid for nx, ny in ((x+1,y), (x-1,y), (x,y+1), (x,y-1)))


def _begin(conn, c):
    if polywar._is_sqlite(conn):
        last = None
        for i in range(20):
            try:
                c.execute('BEGIN IMMEDIATE'); return
            except Exception as e:
                if 'locked' not in str(e).lower(): raise
                last = e; time.sleep(.025 * (i + 1))
        raise last or RuntimeError('sqlite_begin_failed')
    polywar._execute(c, 'BEGIN')


def _duplicate_response(conn, sid, seed, uid, key):
    if not key: return None
    dup = mines.duplicate_outcome_response(conn, sid, uid, key)
    if dup: return dup
    action = polywar._fetchone(conn.cursor(), 'SELECT * FROM polywar_actions WHERE season_id=%s AND user_id=%s AND idempotency_key=%s', (sid, uid, key))
    if not action: return None
    player = polywar.get_or_create_player(uid, sid, conn)
    energy = mines.public_energy_for_player(player)
    payload = {'action_type': action['action_type'], 'capital': {'x': action['x'], 'y': action['y'], 'terrain': m.terrain_at(seed, int(action['x']), int(action['y'])), 'energy_cost': action['energy_cost']}, 'energy': energy}
    return {'ok': True, 'duplicate': True, 'outcome': action.get('action_type'), **payload}


def _lock_capital(conn, cap_id):
    return polywar._fetchone(conn.cursor(), 'SELECT * FROM polywar_capitals WHERE id=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE'), (cap_id,))


def _lock_capital_cell(conn, sid, x, y):
    return polywar._fetchone(conn.cursor(), 'SELECT * FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE'), (sid, x, y))


def _event_allowed(conn, sid, x, y, event_type, now):
    if event_type not in NOISY_EVENTS:
        return True
    cd = event_cooldown()
    if cd <= 0: return True
    cutoff = now - timedelta(seconds=cd)
    row = polywar._fetchone(conn.cursor(), 'SELECT id FROM polywar_events WHERE season_id=%s AND event_type=%s AND message LIKE %s AND created_at >= %s ORDER BY created_at DESC LIMIT 1', (sid, event_type, f'%{x},{y}%', cutoff))
    return row is None


def _emit_event(conn, sid, uid, fid, event_type, x, y, now):
    if not _event_allowed(conn, sid, x, y, event_type, now):
        return False
    polywar._execute(conn.cursor(), 'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)', (sid, uid, fid, event_type, f'Capital {event_type} at {x},{y}', now))
    return True


def transfer_capital_control(conn, sid, cap, new_controller, user_id, now):
    old = int(cap['controller_faction_id']); x = int(cap['x']); y = int(cap['y']); c = conn.cursor()
    if old == int(new_controller): return None
    polywar._execute(c, 'UPDATE polywar_capitals SET controller_faction_id=%s, besieging_faction_id=NULL, siege_progress=0, siege_started_at=NULL, captured_at=%s, controlled_since=%s, updated_at=%s WHERE id=%s', (new_controller, now, now, now, cap['id']))
    polywar._execute(c, 'UPDATE polywar_cells SET owner_faction_id=%s, contesting_faction_id=NULL, contest_progress=0, updated_at=%s, updated_by_user_id=%s WHERE season_id=%s AND x=%s AND y=%s', (new_controller, now, user_id, sid, x, y))
    change = sectors.transfer_cell_ownership(conn, sid, x, y, old, int(new_controller), user_id, now)
    decr = sectors._decrement_expr(conn, 'controlled_capitals_count')
    polywar._execute(c, f'UPDATE polywar_faction_season_stats SET controlled_capitals_count={decr}, updated_at=%s WHERE season_id=%s AND faction_id=%s', (now, sid, old))
    polywar._execute(c, 'UPDATE polywar_faction_season_stats SET controlled_capitals_count=controlled_capitals_count+1, updated_at=%s WHERE season_id=%s AND faction_id=%s', (now, sid, new_controller))
    recalc_influence(conn, sid, old, now); recalc_influence(conn, sid, int(new_controller), now)
    et = 'capital_recaptured' if int(cap['original_faction_id']) == int(new_controller) else 'capital_captured'
    _emit_event(conn, sid, user_id, int(new_controller), et, x, y, now)
    return change


def capital_action(user_id: int, action_type: str, x: int, y: int, idempotency_key: str):
    if action_type not in {'siege', 'repair_capital'}: raise ValueError('bad_action_type')
    if not idempotency_key or len(str(idempotency_key)) > 120: raise ValueError('bad_idempotency_key')
    conn = polywar.get_connection(); c = conn.cursor(); sid = None; seed = None
    try:
        polywar.init_polywar_schema(conn); init_polywar_capital_schema(conn); mines.init_polywar_mine_schema(conn); sectors.init_polywar_sector_schema(conn)
        season = m._private_active_season(conn); sid = int(season['id']); seed = season['secret_seed']
        from services import polywar_world_service as world
        world.ensure_world_initialized_in_transaction(conn, sid)
        conn.commit()
        dup = _duplicate_response(conn, sid, seed, user_id, idempotency_key)
        if dup: return dup
        _begin(conn, c)
        dup = _duplicate_response(conn, sid, seed, user_id, idempotency_key)
        if dup: conn.commit(); return dup
        polywar._insert_player_if_missing(conn, user_id, sid)
        player = polywar._fetchone(c, 'SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE'), (user_id, sid))
        dup = _duplicate_response(conn, sid, seed, user_id, idempotency_key)
        if dup: conn.commit(); return dup
        fid = player.get('faction_id')
        if not fid: raise ValueError('faction_required')
        if polywar._energy(player).get('is_locked'): raise ValueError('player_locked')
        ensure_capitals_initialized(conn, sid)
        cap0 = get_capital_at(conn, sid, x, y)
        if not cap0: raise ValueError('capital_required')
        cap = _lock_capital(conn, cap0['id'])
        cell = _lock_capital_cell(conn, sid, x, y)
        if not cell: raise ValueError('capital_required')
        dup = _duplicate_response(conn, sid, seed, user_id, idempotency_key)
        if dup: conn.commit(); return dup
        _rate_mut(user_id)
        terr = m.terrain_at(seed, x, y); base = m.TERRAIN_COSTS[terr]
        if base is None: raise ValueError('not_capturable')
        if not _has_adjacent(conn, sid, x, y, fid): raise ValueError('capital_not_frontline')
        now = datetime.utcnow(); before = int(cap.get('siege_progress') or 0); previous = int(cap['controller_faction_id']); bes_before = cap.get('besieging_faction_id'); transfer = None
        after = before; bes_after = bes_before; current = previous
        prepared=polywar.prepare_gameplay_mutation_in_transaction(conn,sid)
        if not prepared.get('ok'):
            if prepared.get('season_finalized'):
                conn.commit(); return {'ok': False, 'error': prepared.get('error') or 'season_ended', 'season_finalized': True}
            raise ValueError(prepared.get('error') or 'season_ended')
        if action_type == 'siege':
            if previous == int(fid): raise ValueError('own_capital_cannot_be_sieged')
            cost = int(base) + siege_extra_energy(); _, _, energy = mines.spend_player_energy(conn, player, cost, now); power = siege_power(); req = siege_required()
            if bes_before and int(bes_before) != int(fid):
                after = max(0, before - power); outcome = 'rival_siege_reduced' if after > 0 else 'rival_siege_cleared'; bes_after = bes_before if after > 0 else None
                polywar._execute(c, 'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=%s, last_siege_at=%s,last_siege_by_user_id=%s, updated_at=%s WHERE id=%s', (after, bes_after, cap.get('siege_started_at') if after > 0 else None, now, user_id, now, cap['id']))
            else:
                after = min(req, before + power); bes_after = int(fid); outcome = 'siege_started' if before == 0 else 'siege_progress'
                if after >= req:
                    outcome = 'capital_captured'; transfer = transfer_capital_control(conn, sid, cap, int(fid), user_id, now); after = 0; bes_after = None; current = int(fid)
                else:
                    polywar._execute(c, 'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=COALESCE(siege_started_at,%s), last_siege_at=%s,last_siege_by_user_id=%s, updated_at=%s WHERE id=%s', (after, int(fid), now, now, user_id, now, cap['id']))
        else:
            if previous != int(fid): raise ValueError('not_capital_controller')
            if before <= 0: raise ValueError('capital_not_under_siege')
            cost = repair_cost(); _, _, energy = mines.spend_player_energy(conn, player, cost, now)
            after = max(0, before - repair_power()); outcome = 'capital_repaired' if after > 0 else 'capital_siege_cleared'; bes_after = bes_before if after > 0 else None
            polywar._execute(c, 'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=%s, updated_at=%s WHERE id=%s', (after, bes_after, cap.get('siege_started_at') if after > 0 else None, now, cap['id']))
        polywar._execute(c, 'INSERT INTO polywar_actions (season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', (sid, user_id, fid, action_type, x, y, cost, idempotency_key, now))
        payload = {'action_type': action_type, 'capital': {'x': x, 'y': y, 'original_faction_id': cap['original_faction_id'], 'previous_controller_faction_id': previous, 'controller_faction_id': current, 'besieging_faction_id': bes_after, 'siege_progress_before': before, 'siege_progress_after': after, 'siege_required': siege_required(), 'energy_cost': cost}, 'capital_transfer': transfer, 'energy': energy}
        mines.insert_outcome(conn, sid, user_id, idempotency_key, action_type, x, y, outcome, cost, payload, now)
        if outcome != 'capital_captured': _emit_event(conn, sid, user_id, int(fid), outcome, x, y, now)
        conn.commit(); payload.update({'ok': True, 'outcome': outcome}); return payload
    except ValueError:
        polywar._safe_rollback(conn); raise
    except Exception as exc:
        polywar._safe_rollback(conn)
        text = str(exc).lower()
        if sid is not None and any(s in text for s in ('unique', 'duplicate', 'constraint', 'integrity')):
            try:
                dup = _duplicate_response(conn, sid, seed, user_id, idempotency_key)
                if dup: return dup
            except Exception:
                logger.exception('failed duplicate recovery for capital action')
        logger.exception('capital action failed'); raise
    finally:
        conn.close()


def get_capitals(user_id: int = None):
    if user_id is not None: _rate_get(user_id)
    conn = polywar.get_connection()
    try:
        polywar.init_polywar_schema(conn); init_polywar_capital_schema(conn); season = polywar.ensure_active_season_in_transaction(conn); sid = int(season['id'])
        ensure_capitals_initialized(conn, sid)
        conn.commit(); rows = polywar._fetchall(conn.cursor(), 'SELECT * FROM polywar_capitals WHERE season_id=%s ORDER BY original_faction_id', (sid,)); req = siege_required()
        return {'ok': True, 'season_id': sid, 'siege_required': req, 'capitals': [{'original_faction_id': r['original_faction_id'], 'controller_faction_id': r['controller_faction_id'], 'x': r['x'], 'y': r['y'], 'besieging_faction_id': r.get('besieging_faction_id'), 'siege_progress': int(r.get('siege_progress') or 0), 'siege_required': req, 'siege_percent': min(100, int((int(r.get('siege_progress') or 0) * 100) / req)), 'siege_started_at': polywar._iso(r.get('siege_started_at')), 'controlled_since': polywar._iso(r.get('controlled_since')), 'captured_at': polywar._iso(r.get('captured_at')), 'is_under_siege': int(r.get('siege_progress') or 0) > 0} for r in rows], 'server_timestamp': int(time.time())}
    finally:
        conn.close()


def enrich_chunks(conn, sid, chunks):
    req = siege_required(); c = conn.cursor()
    for ch in chunks:
        x0, y0, w, h = ch['chunk_x'] * ch['chunk_size'], ch['chunk_y'] * ch['chunk_size'], ch['width'], ch['height']
        rows = polywar._fetchall(c, 'SELECT x,y,original_faction_id,controller_faction_id,besieging_faction_id,siege_progress FROM polywar_capitals WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s', (sid, x0, x0+w, y0, y0+h))
        ch['capitals'] = [{'x': r['x'], 'y': r['y'], 'original_faction_id': r['original_faction_id'], 'controller_faction_id': r['controller_faction_id'], 'besieging_faction_id': r.get('besieging_faction_id'), 'siege_progress': int(r.get('siege_progress') or 0), 'siege_required': req, 'is_under_siege': int(r.get('siege_progress') or 0) > 0} for r in rows]
    return chunks
