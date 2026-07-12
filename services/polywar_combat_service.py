import logging
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from services import polywar_service as polywar
from services import polywar_map_service as m
from services import polywar_mine_service as mines
from services import polywar_sector_service as sectors

logger = logging.getLogger(__name__)
_RATE_LOCK = threading.Lock()
_RATE = OrderedDict()
RATE_WINDOW = 10
RATE_MAX = 20
RATE_MAX_USERS = 5000


def _setting_int(k, d, lo, hi): return polywar._setting_int(k, d, lo, hi)
def attack_extra(): return _setting_int('polywar_enemy_attack_extra_energy', 1, 0, 100)
def attack_power(): return _setting_int('polywar_attack_progress_per_action', 50, 1, 100)
def required(): return _setting_int('polywar_capture_progress_required', 100, 1, 1000)
def reinforce_cost(): return _setting_int('polywar_reinforce_energy_cost', 1, 0, 100)
def reinforce_power(): return _setting_int('polywar_reinforce_progress_per_action', 50, 1, 100)


def public_rules():
    return {
        'enemy_attack_extra_energy': attack_extra(),
        'attack_progress_per_action': attack_power(),
        'capture_progress_required': required(),
        'reinforce_energy_cost': reinforce_cost(),
        'reinforce_progress_per_action': reinforce_power(),
    }


def _rate(uid):
    now = time.monotonic(); uid = int(uid)
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


def _begin(conn, c):
    if polywar._is_sqlite(conn):
        last = None
        for i in range(20):
            try:
                c.execute('BEGIN IMMEDIATE'); return
            except Exception as e:
                if 'locked' not in str(e).lower():
                    raise
                last = e; time.sleep(.025 * (i + 1))
        raise last
    polywar._execute(c, 'BEGIN')


def _dup(conn, sid, uid, key): return mines.duplicate_outcome_response(conn, sid, uid, key)
def _owner(conn, sid, x, y): return m._owner_at(conn, sid, x, y)

def _legacy_action_duplicate_response(conn, season_id, seed, user_id, action):
    player = polywar.get_or_create_player(user_id, season_id, conn)
    e = {k: v for k, v in polywar._energy(player).items() if k != 'energy_updated_at'}
    return {'ok': True, 'duplicate': True, 'outcome': action.get('outcome') or action.get('action_type'), 'cell': {'x': action['x'], 'y': action['y'], 'terrain': m.terrain_at(seed, action['x'], action['y']), 'energy_cost': action['energy_cost']}, 'energy': e}


def _find_duplicate(conn, sid, seed, user_id, key):
    dup = _dup(conn, sid, user_id, key)
    if dup:
        return dup
    existing = polywar._fetchone(conn.cursor(), 'SELECT * FROM polywar_actions WHERE season_id=%s AND user_id=%s AND idempotency_key=%s', (sid, user_id, key))
    if existing:
        return _legacy_action_duplicate_response(conn, sid, seed, user_id, existing)
    return None


def _lock_cell(conn, sid, x, y):
    sql = 'SELECT * FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE')
    return polywar._fetchone(conn.cursor(), sql, (sid, x, y))


def _materialize(conn, sid, x, y, owner, now):
    if owner is None:
        return None
    c = conn.cursor()
    if polywar._is_sqlite(conn):
        polywar._execute(c, 'INSERT OR IGNORE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s)', (sid, x, y, owner, now))
    else:
        polywar._execute(c, 'INSERT INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s) ON CONFLICT (season_id,x,y) DO NOTHING', (sid, x, y, owner, now))
    sectors.apply_materialized_starting_cell(conn, sid, x, y, owner, now)
    return _lock_cell(conn, sid, x, y)


def _faction_name(fid):
    return next((f[1] for f in polywar.FACTIONS if f[0] == fid), f'Faction {fid}')


def _is_unique(exc):
    text = str(exc).lower()
    return 'unique' in text or 'duplicate' in text or 'constraint' in text or 'integrity' in text


def combat_action(user_id: int, action_type: str, x: int, y: int, idempotency_key: str):
    if action_type not in {'attack', 'reinforce'}: raise ValueError('bad_action_type')
    if not idempotency_key or len(str(idempotency_key)) > 120: raise ValueError('bad_idempotency_key')
    conn = polywar.get_connection(); c = conn.cursor(); sid = None; seed = None
    try:
        polywar.init_polywar_schema(conn); m.init_polywar_map_schema(conn); mines.init_polywar_mine_schema(conn); sectors.init_polywar_sector_schema(conn)
        season = m._private_active_season(conn); sid = int(season['id']); seed = season['secret_seed']
        from services import polywar_world_service as world
        world.ensure_world_initialized_in_transaction(conn, sid)
        conn.commit()
        dup = _find_duplicate(conn, sid, seed, user_id, idempotency_key)
        if dup:
            return dup
        _rate(user_id)
        _begin(conn, c)
        dup = _find_duplicate(conn, sid, seed, user_id, idempotency_key)
        if dup:
            conn.commit(); return dup
        polywar._insert_player_if_missing(conn, user_id, sid)
        player = polywar._fetchone(c, 'SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE'), (user_id, sid))
        dup = _find_duplicate(conn, sid, seed, user_id, idempotency_key)
        if dup:
            conn.commit(); return dup
        fid = player.get('faction_id')
        if not fid: raise ValueError('faction_required')
        e = polywar._energy(player)
        if e.get('is_locked'): raise ValueError('player_locked')
        sectors.ensure_starting_territories_bootstrap(conn, sid)
        from services import polywar_capital_service as capitals
        capitals.ensure_capitals_initialized(conn, sid)
        if not m.in_bounds(x, y): raise ValueError('out_of_bounds')
        cap = capitals.get_capital_at(conn, sid, x, y)
        if cap:
            raise ValueError('capital_requires_repair' if action_type == 'reinforce' else 'capital_requires_siege')
        try:
            from services import polywar_world_service as world
            if world.is_rift(conn, sid, x, y): raise ValueError('rift_requires_seal')
        except ValueError:
            raise
        except Exception:
            pass
        terr = m.terrain_at(seed, x, y); base = m.TERRAIN_COSTS[terr]
        if base is None: raise ValueError('not_capturable')
        now = datetime.utcnow(); owner = _owner(conn, sid, x, y)
        row = _materialize(conn, sid, x, y, owner, now) if owner is not None else None
        if row: owner = int(row['owner_faction_id'])
        before = int((row or {}).get('contest_progress') or 0); contesting = (row or {}).get('contesting_faction_id')
        polywar.assert_gameplay_mutation_allowed(conn, sid)
        if action_type == 'attack':
            if owner is None: raise ValueError('neutral_cell_requires_capture')
            if owner == fid: raise ValueError('own_cell_cannot_be_attacked')
            if not any(_owner(conn, sid, nx, ny) == fid for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if m.in_bounds(nx, ny)): raise ValueError('not_frontline')
            cost = int(base) + attack_extra()
            if int(e['current_energy']) < cost: raise ValueError('insufficient_energy')
            power = attack_power(); outcome = 'attack_progress'; new_owner = owner; after = min(required(), before + power); new_contesting = fid; sector_change = None
            if contesting and int(contesting) != int(fid):
                after = max(0, before - power); new_contesting = contesting if after > 0 else None; outcome = 'rival_progress_reduced' if after > 0 else 'rival_contest_cleared'
            elif after >= required():
                outcome = 'territory_captured'; new_owner = fid; new_contesting = None; after = 0
            _, _, energy = mines.spend_player_energy(conn, player, cost, now)
            if outcome == 'territory_captured':
                polywar._execute(c, 'UPDATE polywar_cells SET owner_faction_id=%s, contesting_faction_id=NULL, contest_progress=0, contested_at=NULL, last_attacked_at=%s,last_attacked_by_user_id=%s, updated_at=%s, updated_by_user_id=%s WHERE season_id=%s AND x=%s AND y=%s', (fid, now, user_id, now, user_id, sid, x, y))
                sector_change = sectors.transfer_cell_ownership(conn, sid, x, y, owner, fid, user_id, now)
            else:
                contested_at = None if after == 0 else (now if new_contesting and before == 0 else (row or {}).get('contested_at'))
                polywar._execute(c, 'UPDATE polywar_cells SET contesting_faction_id=%s, contest_progress=%s, contested_at=%s,last_attacked_at=%s,last_attacked_by_user_id=%s, updated_at=%s WHERE season_id=%s AND x=%s AND y=%s', (new_contesting, after, contested_at, now, user_id, now, sid, x, y))
            et = 'territory_captured' if outcome == 'territory_captured' else ('attack_started' if before == 0 and outcome == 'attack_progress' else 'attack_progress')
            polywar._execute(c, 'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)', (sid, user_id, fid, et, f'{_faction_name(fid)} attacked cell {x},{y}', now))
        else:
            if owner != fid: raise ValueError('not_cell_owner')
            if before <= 0: raise ValueError('cell_not_contested')
            if not any(_owner(conn, sid, nx, ny) == fid for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if m.in_bounds(nx, ny)): raise ValueError('not_frontline')
            cost = reinforce_cost()
            if int(e['current_energy']) < cost: raise ValueError('insufficient_energy')
            after = max(0, before - reinforce_power()); outcome = 'reinforced' if after > 0 else 'contest_cleared'; new_owner = owner; new_contesting = contesting if after > 0 else None; sector_change = None
            _, _, energy = mines.spend_player_energy(conn, player, cost, now)
            polywar._execute(c, 'UPDATE polywar_cells SET contest_progress=%s, contesting_faction_id=%s, contested_at=%s, updated_at=%s WHERE season_id=%s AND x=%s AND y=%s', (after, new_contesting, (row or {}).get('contested_at') if after > 0 else None, now, sid, x, y))
            polywar._execute(c, 'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)', (sid, user_id, fid, outcome, f'{_faction_name(fid)} reinforced cell {x},{y}', now))
        polywar._execute(c, 'INSERT INTO polywar_actions (season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', (sid, user_id, fid, action_type, x, y, cost, idempotency_key, now))
        payload = {'cell': {'x': x, 'y': y, 'terrain': terr, 'previous_owner_faction_id': owner, 'owner_faction_id': new_owner, 'contesting_faction_id': new_contesting, 'contest_progress_before': before, 'contest_progress_after': after, 'contest_required': required(), 'energy_cost': cost}, 'sector_change': sector_change, 'energy': energy}
        mines.insert_outcome(conn, sid, user_id, idempotency_key, action_type, x, y, outcome, cost, payload, now)
        conn.commit(); payload.update({'ok': True, 'outcome': outcome}); return payload
    except ValueError:
        polywar._safe_rollback(conn); raise
    except Exception as exc:
        polywar._safe_rollback(conn)
        if sid is not None and _is_unique(exc):
            try:
                dup = _find_duplicate(conn, sid, seed, user_id, idempotency_key)
                if dup:
                    return dup
            except Exception:
                logger.exception('Failed to load duplicate PolyWar combat outcome after unique conflict')
        logger.exception('Unexpected PolyWar combat failure')
        raise
    finally:
        conn.close()
