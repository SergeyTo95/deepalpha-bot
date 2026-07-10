import hashlib
import hmac
import logging
import threading
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from services import polywar_service as polywar

logger = logging.getLogger(__name__)
GENERATION_VERSION = "polywar:mine:v1"
DENSITY_DEFAULTS = {"plain": 400, "forest": 700, "mountain": 1000, "swamp": 900, "desert": 500, "road": 200, "ruins": 1400, "water": 0, "river": 0}
_CAPTURABLE = {"plain", "forest", "mountain", "swamp", "desert", "road", "ruins"}
_MINE_CACHE: "OrderedDict[Tuple[int, str, int, int, str, str], bool]" = OrderedDict()
_CACHE_LOCK = threading.RLock()
_RATE_LOCK = threading.Lock()
_SCAN_RATE: Dict[int, deque] = defaultdict(deque)
_FLAG_RATE: Dict[int, deque] = defaultdict(deque)
MAX_MINE_CACHE = 50000
RATE_WINDOW = 60
SCAN_RATE_MAX = 20
FLAG_RATE_MAX = 80


def _setting_int(key, default, lo, hi):
    return polywar._setting_int(key, default, lo, hi)


def mine_lock_minutes():
    return _setting_int("polywar_mine_lock_minutes", 180, 1, 1440)


def scan_energy_cost(size: int) -> int:
    if int(size) == 3:
        return _setting_int("polywar_scan_3_energy_cost", 2, 0, 100)
    if int(size) == 5:
        return _setting_int("polywar_scan_5_energy_cost", 4, 0, 100)
    raise ValueError("bad_scan_size")


def max_flags_per_player():
    return _setting_int("polywar_max_flags_per_player", 100, 0, 1000)


def density_bp(terrain: str) -> int:
    if terrain in {"water", "river"}:
        return 0
    return _setting_int(f"polywar_mine_density_{terrain}_bp", DENSITY_DEFAULTS.get(terrain, 0), 0, 3000)


def init_polywar_mine_schema(conn=None):
    own = conn is None
    conn = conn or polywar.get_connection()
    c = conn.cursor()
    id_sql = "INTEGER PRIMARY KEY AUTOINCREMENT" if polywar._is_sqlite(conn) else "SERIAL PRIMARY KEY"
    try:
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_mine_events (id {id_sql}, season_id INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, event_type TEXT NOT NULL, triggered_by_user_id BIGINT NULL, triggered_by_faction_id INTEGER NULL, triggered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, action_id INTEGER NULL, idempotency_key TEXT NULL, UNIQUE(season_id,x,y))""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_action_outcomes (id {id_sql}, season_id INTEGER NOT NULL, user_id BIGINT NOT NULL, idempotency_key TEXT NOT NULL, action_type TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, outcome TEXT NOT NULL, energy_cost INTEGER NOT NULL DEFAULT 0, payload_json TEXT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,user_id,idempotency_key))""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_cell_intel (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, intel_type TEXT NOT NULL, adjacent_mines INTEGER NULL, discovered_by_user_id BIGINT NULL, discovered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,faction_id,x,y,intel_type))""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_scans (id {id_sql}, season_id INTEGER NOT NULL, user_id BIGINT NOT NULL, faction_id INTEGER NOT NULL, center_x INTEGER NOT NULL, center_y INTEGER NOT NULL, scan_size INTEGER NOT NULL, active_mine_count INTEGER NOT NULL, energy_cost INTEGER NOT NULL, idempotency_key TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,user_id,idempotency_key))""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_flags (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, user_id BIGINT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, flag_type TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(season_id,user_id,x,y))""")
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_polywar_intel_chunk ON polywar_cell_intel(season_id,faction_id,x,y)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_scans_chunk ON polywar_scans(season_id,faction_id,center_x,center_y)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_flags_chunk ON polywar_flags(season_id,faction_id,x,y)",
        ]:
            c.execute(sql)
        conn.commit()
    finally:
        if own:
            conn.close()


def _rate(bucket, user_id, maximum):
    now = time.monotonic()
    with _RATE_LOCK:
        q = bucket[int(user_id)]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= maximum:
            raise ValueError("rate_limited")
        q.append(now)
        if len(bucket) > 5000:
            for key in list(bucket.keys())[:1000]:
                if not bucket[key] or now - bucket[key][-1] > RATE_WINDOW:
                    bucket.pop(key, None)


def _bucket(secret_seed: str, season_id: int, x: int, y: int) -> int:
    msg = f"{GENERATION_VERSION}:{season_id}:{x}:{y}".encode()
    digest = hmac.new(str(secret_seed).encode(), msg, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % 10000


def is_safe_zone(x: int, y: int) -> bool:
    from services import polywar_map_service as m
    half = m.starting_area_size() // 2
    for bx, by in m.faction_base_positions().values():
        dx = max(0, abs(int(x) - bx) - half)
        dy = max(0, abs(int(y) - by) - half)
        if max(dx, dy) <= 5:
            return True
    return False


def deterministic_mine_exists(season_id: int, secret_seed: str, x: int, y: int, terrain: str) -> bool:
    if terrain not in _CAPTURABLE or is_safe_zone(x, y):
        return False
    bp = density_bp(terrain)
    if bp <= 0:
        return False
    key = (int(season_id), str(secret_seed), int(x), int(y), str(terrain), GENERATION_VERSION)
    with _CACHE_LOCK:
        if key in _MINE_CACHE:
            _MINE_CACHE.move_to_end(key)
            return _MINE_CACHE[key]
    value = _bucket(secret_seed, season_id, x, y) < bp
    with _CACHE_LOCK:
        _MINE_CACHE[key] = value
        if len(_MINE_CACHE) > MAX_MINE_CACHE:
            _MINE_CACHE.popitem(last=False)
    return value


def is_mine_triggered(conn, season_id, x, y):
    return bool(polywar._fetchone(conn.cursor(), "SELECT id FROM polywar_mine_events WHERE season_id=%s AND x=%s AND y=%s AND event_type=%s", (season_id, x, y, "triggered")))


def active_mine_at(conn, season_id: int, secret_seed: str, x: int, y: int, terrain: str) -> bool:
    from services import polywar_map_service as m
    if m._owner_at(conn, season_id, x, y) is not None:
        return False
    return deterministic_mine_exists(season_id, secret_seed, x, y, terrain) and not is_mine_triggered(conn, season_id, x, y)


def adjacent_mine_count(conn, season_id: int, secret_seed: str, x: int, y: int) -> int:
    from services import polywar_map_service as m
    count = 0
    for yy in range(y - 1, y + 2):
        for xx in range(x - 1, x + 2):
            if xx == x and yy == y or not m.in_bounds(xx, yy):
                continue
            if active_mine_at(conn, season_id, secret_seed, xx, yy, m.terrain_at(secret_seed, xx, yy)):
                count += 1
    return count


def upsert_safe_hint(conn, season_id, faction_id, x, y, user_id, secret_seed, now=None):
    now = now or datetime.utcnow()
    n = adjacent_mine_count(conn, season_id, secret_seed, x, y)
    polywar._execute(conn.cursor(), """INSERT INTO polywar_cell_intel (season_id,faction_id,x,y,intel_type,adjacent_mines,discovered_by_user_id,discovered_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,faction_id,x,y,intel_type) DO UPDATE SET adjacent_mines=excluded.adjacent_mines, updated_at=excluded.updated_at""", (season_id, faction_id, x, y, "safe_hint", n, user_id, now, now))
    return n


def record_triggered_mine(conn, season_id, faction_id, user_id, x, y, idempotency_key, secret_seed, now=None):
    now = now or datetime.utcnow()
    polywar._execute(conn.cursor(), "INSERT INTO polywar_mine_events (season_id,x,y,event_type,triggered_by_user_id,triggered_by_faction_id,triggered_at,idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,x,y) DO NOTHING", (season_id, x, y, "triggered", user_id, faction_id, now, idempotency_key))
    polywar._execute(conn.cursor(), "INSERT INTO polywar_cell_intel (season_id,faction_id,x,y,intel_type,adjacent_mines,discovered_by_user_id,discovered_at,updated_at) VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s) ON CONFLICT (season_id,faction_id,x,y,intel_type) DO UPDATE SET updated_at=excluded.updated_at", (season_id, faction_id, x, y, "triggered_mine", user_id, now, now))
    for yy in range(y - 1, y + 2):
        for xx in range(x - 1, x + 2):
            row = polywar._fetchone(conn.cursor(), "SELECT id FROM polywar_cell_intel WHERE season_id=%s AND faction_id=%s AND x=%s AND y=%s AND intel_type=%s", (season_id, faction_id, xx, yy, "safe_hint"))
            if row:
                upsert_safe_hint(conn, season_id, faction_id, xx, yy, user_id, secret_seed, now)


def duplicate_outcome_response(conn, season_id, user_id, idempotency_key):
    import json
    row = polywar._fetchone(conn.cursor(), "SELECT * FROM polywar_action_outcomes WHERE season_id=%s AND user_id=%s AND idempotency_key=%s", (season_id, user_id, idempotency_key))
    if not row:
        return None
    payload = json.loads(row.get("payload_json") or "{}")
    payload.update({"ok": True, "duplicate": True, "outcome": row["outcome"], "energy_cost": row["energy_cost"]})
    return payload


def insert_outcome(conn, season_id, user_id, idempotency_key, action_type, x, y, outcome, energy_cost, payload, now=None):
    import json
    now = now or datetime.utcnow()
    polywar._execute(conn.cursor(), "INSERT INTO polywar_action_outcomes (season_id,user_id,idempotency_key,action_type,x,y,outcome,energy_cost,payload_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,user_id,idempotency_key) DO NOTHING", (season_id, user_id, idempotency_key, action_type, x, y, outcome, energy_cost, json.dumps(payload, default=str), now))


def enrich_chunks(conn, season_id, faction_id, chunks):
    if not faction_id:
        for ch in chunks:
            ch.update({"intel": [], "flags": [], "scans": []})
        return chunks
    c = conn.cursor()
    for ch in chunks:
        x0, y0, w, h = ch["chunk_x"] * ch["chunk_size"], ch["chunk_y"] * ch["chunk_size"], ch["width"], ch["height"]
        ch["intel"] = polywar._fetchall(c, "SELECT x,y,intel_type,adjacent_mines,discovered_at,updated_at FROM polywar_cell_intel WHERE season_id=%s AND faction_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s", (season_id, faction_id, x0, x0+w, y0, y0+h))
        flags = polywar._fetchall(c, "SELECT x,y,COUNT(*) AS flag_count,MAX(CASE WHEN user_id=%s THEN 1 ELSE 0 END) AS current_user_flagged FROM polywar_flags WHERE season_id=%s AND faction_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s GROUP BY x,y", (ch.get("user_id") or 0, season_id, faction_id, x0, x0+w, y0, y0+h))
        ch["flags"] = [{"x": r["x"], "y": r["y"], "flag_count": r["flag_count"], "current_user_flagged": bool(r["current_user_flagged"])} for r in flags]
        pad = 2
        ch["scans"] = polywar._fetchall(c, "SELECT center_x,center_y,scan_size AS size,active_mine_count,created_at FROM polywar_scans WHERE season_id=%s AND faction_id=%s AND center_x >= %s AND center_x < %s AND center_y >= %s AND center_y < %s", (season_id, faction_id, x0-pad, x0+w+pad, y0-pad, y0+h+pad))
    return chunks


def _private_season(conn):
    s = polywar.ensure_active_season(conn)
    row = polywar._fetchone(conn.cursor(), "SELECT secret_seed FROM polywar_seasons WHERE id=%s", (int(s["id"]),))
    s["secret_seed"] = row["secret_seed"]
    return s


def _near_own_territory(conn, season_id, faction_id, x, y, radius=5):
    from services import polywar_map_service as m
    for yy in range(y-radius, y+radius+1):
        for xx in range(x-radius, x+radius+1):
            if m.in_bounds(xx, yy) and max(abs(xx-x), abs(yy-y)) <= radius and m._owner_at(conn, season_id, xx, yy) == faction_id:
                return True
    return False


def _area_has_own(conn, season_id, faction_id, cx, cy, size):
    from services import polywar_map_service as m
    half = size // 2
    return any(m.in_bounds(x,y) and m._owner_at(conn, season_id, x, y) == faction_id for y in range(cy-half, cy+half+1) for x in range(cx-half, cx+half+1))


def scan_area(user_id: int, center_x: int, center_y: int, size: int, idempotency_key: str):
    if not idempotency_key or len(str(idempotency_key)) > 120: raise ValueError("bad_idempotency_key")
    if int(size) not in {3,5}: raise ValueError("bad_scan_size")
    _rate(_SCAN_RATE, user_id, SCAN_RATE_MAX)
    from services import polywar_map_service as m
    conn = polywar.get_connection(); c = conn.cursor()
    try:
        polywar.init_polywar_schema(conn); m.init_polywar_map_schema(conn); init_polywar_mine_schema(conn)
        season = _private_season(conn); sid, seed = int(season["id"]), season["secret_seed"]
        dup = duplicate_outcome_response(conn, sid, user_id, idempotency_key)
        if dup: return dup
        c.execute("BEGIN IMMEDIATE") if polywar._is_sqlite(conn) else polywar._execute(c, "BEGIN")
        dup = duplicate_outcome_response(conn, sid, user_id, idempotency_key)
        if dup: conn.commit(); return dup
        polywar._insert_player_if_missing(conn, user_id, sid)
        player = polywar._fetchone(c, "SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s" + ("" if polywar._is_sqlite(conn) else " FOR UPDATE"), (user_id, sid))
        fid = player.get("faction_id")
        if not fid: raise ValueError("faction_required")
        e = polywar._energy(player)
        if e.get("is_locked"): raise ValueError("player_locked")
        if not m.in_bounds(center_x, center_y): raise ValueError("out_of_bounds")
        cost = scan_energy_cost(size)
        if int(e["current_energy"]) < cost: raise ValueError("insufficient_energy")
        if not (_area_has_own(conn, sid, fid, center_x, center_y, size) or _near_own_territory(conn, sid, fid, center_x, center_y, 5)):
            raise ValueError("scan_too_far")
        half = size // 2; count = 0
        for yy in range(center_y-half, center_y+half+1):
            for xx in range(center_x-half, center_x+half+1):
                if m.in_bounds(xx, yy) and active_mine_at(conn, sid, seed, xx, yy, m.terrain_at(seed, xx, yy)):
                    count += 1
        now = datetime.utcnow(); new_energy = int(e["current_energy"]) - cost
        polywar._execute(c, "UPDATE polywar_players SET current_energy=%s, energy_updated_at=%s, last_active_at=%s WHERE user_id=%s AND season_id=%s", (new_energy, e["energy_updated_at"], now, user_id, sid))
        polywar._execute(c, "INSERT INTO polywar_scans (season_id,user_id,faction_id,center_x,center_y,scan_size,active_mine_count,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (sid,user_id,fid,center_x,center_y,size,count,cost,idempotency_key,now))
        payload = {"center_x": center_x, "center_y": center_y, "size": size, "active_mine_count": count, "energy_cost": cost, "created_at": polywar._iso(now), "energy": {"current_energy": new_energy, "max_energy": e["max_energy"], "recharge_minutes": e["recharge_minutes"], "seconds_until_next_energy": e["seconds_until_next_energy"], "is_locked": e["is_locked"], "locked_until": e["locked_until"]}}
        insert_outcome(conn, sid, user_id, idempotency_key, "scan", center_x, center_y, "scanned", cost, payload, now)
        conn.commit(); payload.update({"ok": True, "outcome": "scanned"}); return payload
    except ValueError:
        polywar._safe_rollback(conn); raise
    except Exception:
        polywar._safe_rollback(conn); logger.exception("Unexpected PolyWar scan failure"); raise
    finally:
        conn.close()


def set_flag(user_id: int, x: int, y: int, active: bool):
    _rate(_FLAG_RATE, user_id, FLAG_RATE_MAX)
    from services import polywar_map_service as m
    conn = polywar.get_connection(); c = conn.cursor()
    try:
        polywar.init_polywar_schema(conn); m.init_polywar_map_schema(conn); init_polywar_mine_schema(conn)
        season = _private_season(conn); sid, seed = int(season["id"]), season["secret_seed"]
        player = polywar.get_or_create_player(user_id, sid, conn); fid = player.get("faction_id")
        if not fid: raise ValueError("faction_required")
        if not m.in_bounds(x, y): raise ValueError("out_of_bounds")
        terr = m.terrain_at(seed, x, y)
        if terr not in _CAPTURABLE: raise ValueError("not_capturable")
        owner = m._owner_at(conn, sid, x, y)
        if owner is not None: raise ValueError("not_neutral")
        if active:
            n = polywar._fetchone(c, "SELECT COUNT(*) AS count FROM polywar_flags WHERE season_id=%s AND user_id=%s", (sid, user_id))["count"]
            if int(n) >= max_flags_per_player(): raise ValueError("flag_limit")
            in_scan = bool(polywar._fetchone(c, "SELECT id FROM polywar_scans WHERE season_id=%s AND faction_id=%s AND ABS(center_x-%s) <= scan_size/2 AND ABS(center_y-%s) <= scan_size/2 LIMIT 1", (sid, fid, x, y)))
            if not (in_scan or _near_own_territory(conn, sid, fid, x, y, 5)): raise ValueError("flag_too_far")
            now = datetime.utcnow()
            polywar._execute(c, "INSERT INTO polywar_flags (season_id,faction_id,user_id,x,y,flag_type,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,user_id,x,y) DO UPDATE SET updated_at=excluded.updated_at, flag_type=excluded.flag_type", (sid,fid,user_id,x,y,"suspected_mine",now,now))
        else:
            polywar._execute(c, "DELETE FROM polywar_flags WHERE season_id=%s AND user_id=%s AND x=%s AND y=%s", (sid,user_id,x,y))
        conn.commit(); return {"ok": True, "x": x, "y": y, "active": bool(active)}
    except ValueError:
        polywar._safe_rollback(conn); raise
    finally:
        conn.close()
