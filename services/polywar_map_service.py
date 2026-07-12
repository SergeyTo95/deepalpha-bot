import copy
import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from services import polywar_service as polywar

logger = logging.getLogger(__name__)
TERRAIN_COSTS = {"plain": 1, "forest": 1, "mountain": 2, "swamp": 2, "desert": 1, "road": 1, "ruins": 1, "water": None, "river": None}
_TERRAIN_CACHE: "OrderedDict[Tuple[int, str, int, int, int], List[List[str]]]" = OrderedDict()
_NOISE_CACHE: "OrderedDict[Tuple[str, int, int, int], float]" = OrderedDict()
_CACHE_LOCK = threading.RLock()
_RATE_LOCK = threading.Lock()
_CHUNK_RATE: Dict[int, deque] = defaultdict(deque)
MAX_TERRAIN_CHUNKS = 256
MAX_NOISE_VALUES = 20000
CHUNK_RATE_WINDOW = 10
CHUNK_RATE_MAX = 60


def _setting_int(key, default, lo, hi):
    return polywar._setting_int(key, default, lo, hi)


def map_width():
    return _setting_int("polywar_map_width", 10000, 512, 100000)


def map_height():
    return _setting_int("polywar_map_height", 10000, 512, 100000)


def chunk_size():
    return _setting_int("polywar_chunk_size", 64, 16, 128)


def max_chunks_per_request():
    return _setting_int("polywar_max_chunks_per_request", 9, 1, 25)


def starting_area_size():
    return _setting_int("polywar_starting_area_size", 15, 3, 65)


def init_polywar_map_schema(conn=None):
    own = conn is None
    conn = conn or polywar.get_connection()
    c = conn.cursor()
    id_sql = "INTEGER PRIMARY KEY AUTOINCREMENT" if polywar._is_sqlite(conn) else "SERIAL PRIMARY KEY"
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_cells (season_id INTEGER NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,owner_faction_id INTEGER NOT NULL,capture_progress INTEGER NOT NULL DEFAULT 100,updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_by_user_id BIGINT NULL,contesting_faction_id INTEGER NULL,contest_progress INTEGER NOT NULL DEFAULT 0,contested_at TIMESTAMP NULL,last_attacked_at TIMESTAMP NULL,last_attacked_by_user_id BIGINT NULL,UNIQUE(season_id,x,y))""")
        from services import polywar_sector_service as sectors
        for spec in ["contesting_faction_id INTEGER NULL", "contest_progress INTEGER NOT NULL DEFAULT 0", "contested_at TIMESTAMP NULL", "last_attacked_at TIMESTAMP NULL", "last_attacked_by_user_id BIGINT NULL"]:
            sectors._add_col(conn, "polywar_cells", spec)
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_actions (id {id_sql},season_id INTEGER NOT NULL,user_id BIGINT NOT NULL,faction_id INTEGER NOT NULL,action_type TEXT NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,energy_cost INTEGER NOT NULL,idempotency_key TEXT NOT NULL,created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(season_id,user_id,idempotency_key))""")
        if not polywar._is_sqlite(conn):
            c.execute("ALTER TABLE polywar_actions DROP CONSTRAINT IF EXISTS polywar_actions_user_id_idempotency_key_key")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_polywar_actions_idempotency ON polywar_actions(season_id,user_id,idempotency_key)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_polywar_cells_range ON polywar_cells(season_id,x,y)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_polywar_cells_owner ON polywar_cells(season_id,owner_faction_id,x,y)")
        from services import polywar_sector_service as sectors
        sectors.init_polywar_sector_schema(conn)
        if own: conn.commit()
    finally:
        if own:
            conn.close()


def faction_base_positions(width=None, height=None) -> Dict[int, Tuple[int, int]]:
    w, h = int(width or map_width()), int(height or map_height())
    margin = max(starting_area_size() + 8, min(w, h) // 10)
    return {
        1: (margin, margin),
        2: (w - margin - 1, margin),
        3: (margin, h - margin - 1),
        4: (w - margin - 1, h - margin - 1),
        5: (w // 2, margin),
        6: (margin, h // 2),
        7: (w - margin - 1, h // 2),
    }


def _hash(seed, *parts):
    h = hashlib.sha256((str(seed) + ":" + ":".join(map(str, parts))).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _lattice(seed, ix, iy, scale):
    key = (str(seed), int(ix), int(iy), int(scale))
    with _CACHE_LOCK:
        if key in _NOISE_CACHE:
            _NOISE_CACHE.move_to_end(key)
            return _NOISE_CACHE[key]
    value = _hash(seed, ix, iy, scale)
    with _CACHE_LOCK:
        _NOISE_CACHE[key] = value
        if len(_NOISE_CACHE) > MAX_NOISE_VALUES:
            _NOISE_CACHE.popitem(last=False)
    return value


def _smooth(seed, x, y, scale):
    gx, gy = x / scale, y / scale
    x0, y0 = math.floor(gx), math.floor(gy)
    tx, ty = gx - x0, gy - y0
    fade = lambda t: t * t * (3 - 2 * t)
    fx, fy = fade(tx), fade(ty)
    a = _lattice(seed, x0, y0, scale) * (1 - fx) + _lattice(seed, x0 + 1, y0, scale) * fx
    b = _lattice(seed, x0, y0 + 1, scale) * (1 - fx) + _lattice(seed, x0 + 1, y0 + 1, scale) * fx
    return a * (1 - fy) + b * fy


def in_bounds(x, y):
    return 0 <= int(x) < map_width() and 0 <= int(y) < map_height()


def terrain_at(seed, x: int, y: int) -> str:
    if not in_bounds(x, y):
        raise ValueError("out_of_bounds")
    for bx, by in faction_base_positions().values():
        if (abs(x - bx) <= 1 and abs(y - by) < 90) or (abs(y - by) <= 1 and abs(x - bx) < 90):
            return "road"
    r1 = abs((x * 37 + y * 19 + int(_hash(seed, "river") * 9973)) % 911 - 455)
    r2 = abs((x * 13 - y * 29 + int(_hash(seed, "river2") * 9973)) % 1327 - 663)
    if r1 < 3 or r2 < 2:
        return "river"
    water = _smooth(seed, x, y, 900) * 0.65 + _smooth(seed, x, y, 260) * 0.35
    if water < 0.23:
        return "water"
    hills = _smooth(seed + "m", x, y, 520) * 0.7 + _smooth(seed + "m", x, y, 130) * 0.3
    woods = _smooth(seed + "f", x, y, 380)
    dry = _smooth(seed + "d", x, y, 700)
    rare = _hash(seed, "rare", x // 5, y // 5)
    if hills > 0.78:
        return "mountain"
    if water < 0.30 and woods > 0.55:
        return "swamp"
    if dry > 0.78:
        return "desert"
    if woods > 0.62:
        return "forest"
    if rare > 0.995:
        return "ruins"
    return "plain"


def get_starting_bases():
    colors = {fid: color for fid, _, _, color, _ in polywar.FACTIONS}
    return [{"faction_id": fid, "x": x, "y": y, "size": starting_area_size(), "color": colors.get(fid)} for fid, (x, y) in faction_base_positions().items()]


def _start_owner(x, y):
    half = starting_area_size() // 2
    for fid, (bx, by) in faction_base_positions().items():
        if abs(x - bx) <= half and abs(y - by) <= half:
            return fid
    return None


def _private_active_season(conn):
    s = polywar.ensure_active_season_in_transaction(conn)
    row = polywar._fetchone(conn.cursor(), "SELECT secret_seed FROM polywar_seasons WHERE id = %s", (int(s["id"]),))
    s["secret_seed"] = row["secret_seed"]
    return s


def _owner_at(conn, season_id, x, y):
    row = polywar._fetchone(conn.cursor(), "SELECT owner_faction_id FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s", (season_id, x, y))
    if row:
        return int(row["owner_faction_id"])
    return _start_owner(x, y)


def _terrain_chunk(season_id, seed, cx, cy, cs):
    key = (int(season_id), str(seed), int(cx), int(cy), int(cs))
    with _CACHE_LOCK:
        cached = _TERRAIN_CACHE.get(key)
        if cached is not None:
            _TERRAIN_CACHE.move_to_end(key)
            return copy.deepcopy(cached)
    x0, y0 = cx * cs, cy * cs
    w, h = min(cs, map_width() - x0), min(cs, map_height() - y0)
    terrain = [[terrain_at(seed, xx, yy) for xx in range(x0, x0 + w)] for yy in range(y0, y0 + h)]
    with _CACHE_LOCK:
        _TERRAIN_CACHE[key] = terrain
        if len(_TERRAIN_CACHE) > MAX_TERRAIN_CHUNKS:
            _TERRAIN_CACHE.popitem(last=False)
    return copy.deepcopy(terrain)


def _check_chunk_rate(user_id: int, amount: int):
    now = time.monotonic()
    with _RATE_LOCK:
        q = _CHUNK_RATE[int(user_id)]
        while q and now - q[0] > CHUNK_RATE_WINDOW:
            q.popleft()
        if len(q) + amount > CHUNK_RATE_MAX:
            raise ValueError("rate_limited")
        for _ in range(amount):
            q.append(now)


def build_chunks(user_id: int, chunks: List[Tuple[int, int]]):
    if len(chunks) > max_chunks_per_request():
        raise ValueError("too_many_chunks")
    _check_chunk_rate(user_id, max(1, len(chunks)))
    cs = chunk_size()
    conn = polywar.get_connection()
    try:
        polywar.init_polywar_schema(conn)
        init_polywar_map_schema(conn)
        from services import polywar_mine_service as mines
        mines.init_polywar_mine_schema(conn)
        season = _private_active_season(conn)
        sid, seed = int(season["id"]), season["secret_seed"]
        conn.commit()
        from services import polywar_world_service as world
        now = datetime.utcnow()
        due = False
        wrow = polywar._fetchone(conn.cursor(), "SELECT status,activation_at,next_tick_at FROM polywar_null_state WHERE season_id=%s", (sid,))
        if not wrow:
            due = True
        else:
            activation_at = wrow.get("activation_at")
            next_tick_at = wrow.get("next_tick_at")
            if isinstance(activation_at, str):
                activation_at = datetime.fromisoformat(activation_at)
            if isinstance(next_tick_at, str):
                next_tick_at = datetime.fromisoformat(next_tick_at)
            due = (wrow.get("status") == "dormant" and activation_at and activation_at <= now) or (wrow.get("status") == "active" and next_tick_at and next_tick_at <= now)
        ends = season.get("ends_at")
        if isinstance(ends, str):
            ends = datetime.fromisoformat(ends)
        if ends and ends <= now:
            due = True
        if due:
            conn.close()
            world.ensure_world_caught_up(sid, now)
            conn = polywar.get_connection()
            polywar.init_polywar_schema(conn); init_polywar_map_schema(conn); mines.init_polywar_mine_schema(conn); conn.commit()
            season = _private_active_season(conn)
            sid, seed = int(season["id"]), season["secret_seed"]
        out = []
        for cx, cy in chunks:
            if cx < 0 or cy < 0 or cx * cs >= map_width() or cy * cs >= map_height():
                raise ValueError("out_of_bounds")
            x0, y0 = cx * cs, cy * cs
            terrain = _terrain_chunk(sid, seed, cx, cy, cs)
            h, w = len(terrain), len(terrain[0]) if terrain else 0
            rows = polywar._fetchall(conn.cursor(), "SELECT x,y,owner_faction_id,contesting_faction_id,contest_progress,contested_at FROM polywar_cells WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s", (sid, x0, x0 + w, y0, y0 + h))
            sparse = {(int(r["x"]), int(r["y"])): int(r["owner_faction_id"]) for r in rows}
            owners = [[sparse.get((xx, yy)) or _start_owner(xx, yy) for xx in range(x0, x0 + w)] for yy in range(y0, y0 + h)]
            bases = [b for b in get_starting_bases() if x0 <= b["x"] < x0 + w and y0 <= b["y"] < y0 + h]
            contested = [{"x": int(r["x"]), "y": int(r["y"]), "owner_faction_id": int(r["owner_faction_id"]), "contesting_faction_id": r.get("contesting_faction_id"), "contest_progress": int(r.get("contest_progress") or 0), "contest_required": _setting_int("polywar_capture_progress_required",100,1,1000), "contested_at": polywar._iso(r.get("contested_at"))} for r in rows if int(r.get("contest_progress") or 0) > 0]
            out.append({"chunk_x": cx, "chunk_y": cy, "chunk_size": cs, "width": w, "height": h, "terrain": terrain, "owners": owners, "bases": bases, "contested_cells": contested, "user_id": user_id})
        player = polywar.get_or_create_player(user_id, sid, conn)
        from services import polywar_capital_service as capitals
        from services import polywar_governance_service as governance
        capitals.ensure_capitals_initialized(conn, sid)
        for ch in out:
            x0, y0 = ch["chunk_x"] * cs, ch["chunk_y"] * cs
            rows = polywar._fetchall(conn.cursor(), "SELECT x,y,status,health,max_health FROM polywar_null_rifts WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s", (sid, x0, x0 + ch["width"], y0, y0 + ch["height"]))
            ch["rifts"] = [{"x": int(r["x"]), "y": int(r["y"]), "status": r["status"], "health": int(r["health"]), "max_health": int(r["max_health"]), "health_percent": round(100*int(r["health"])/max(1,int(r["max_health"])),2)} for r in rows]
        capitals.enrich_chunks(conn, sid, out)
        from services import polywar_rebellion_service as reb
        for ch in out:
            x0, y0 = ch["chunk_x"] * cs, ch["chunk_y"] * cs
            ch["rebellions"] = reb.get_public_rebellions_readonly(conn, sid, (x0, y0, x0 + ch["width"], y0 + ch["height"]))
        mines.enrich_chunks(conn, sid, player.get("faction_id"), out)
        governance.enrich_chunks(conn, sid, player.get("faction_id"), out)
        conn.commit()
        for ch in out:
            ch.pop("user_id", None)
        return {"ok": True, "season_id": sid, "chunks": out, "chunk_size": cs, "map_width": map_width(), "map_height": map_height(), "server_timestamp": int(time.time())}
    finally:
        conn.close()




def legacy_action_duplicate_response(conn, season_id: int, seed: str, user_id: int, action: Dict[str, Any]):
    player = polywar.get_or_create_player(user_id, season_id, conn)
    e = {k: v for k, v in polywar._energy(player).items() if k != "energy_updated_at"}
    return {"ok": True, "duplicate": True, "outcome": "captured", "cell": {"x": action["x"], "y": action["y"], "terrain": terrain_at(seed, action["x"], action["y"]), "owner_faction_id": action.get("faction_id"), "energy_cost": action["energy_cost"]}, "energy": e}

def capture_cell(user_id: int, x: int, y: int, idempotency_key: str):
    if not idempotency_key or len(str(idempotency_key)) > 120:
        raise ValueError("bad_idempotency_key")
    from services import polywar_mine_service as mines
    conn = polywar.get_connection(); c = conn.cursor()
    try:
        polywar.init_polywar_schema(conn); init_polywar_map_schema(conn); mines.init_polywar_mine_schema(conn)
        season = _private_active_season(conn); sid, seed = int(season["id"]), season["secret_seed"]
        from services import polywar_world_service as world
        world.ensure_world_initialized_in_transaction(conn, sid)
        conn.commit()
        dup = mines.duplicate_outcome_response(conn, sid, user_id, idempotency_key)
        if dup: return dup
        existing = polywar._fetchone(c, "SELECT * FROM polywar_actions WHERE season_id=%s AND user_id=%s AND idempotency_key=%s", (sid, user_id, idempotency_key))
        if existing: return legacy_action_duplicate_response(conn, sid, seed, user_id, existing)
        if polywar._is_sqlite(conn):
            began = False; last_lock_error = None
            for attempt in range(20):
                try: c.execute("BEGIN IMMEDIATE"); began = True; break
                except Exception as exc:
                    if "locked" not in str(exc).lower(): raise
                    last_lock_error = exc; time.sleep(0.025 * (attempt + 1))
            if not began: raise last_lock_error
        else: polywar._execute(c, "BEGIN")
        dup = mines.duplicate_outcome_response(conn, sid, user_id, idempotency_key)
        if dup: conn.commit(); return dup
        existing = polywar._fetchone(c, "SELECT * FROM polywar_actions WHERE season_id=%s AND user_id=%s AND idempotency_key=%s", (sid, user_id, idempotency_key))
        if existing: conn.commit(); return legacy_action_duplicate_response(conn, sid, seed, user_id, existing)
        polywar.assert_gameplay_mutation_allowed(conn, sid)
        if not in_bounds(x, y): raise ValueError("out_of_bounds")
        from services import polywar_capital_service as capitals
        capitals.ensure_capitals_initialized(conn, sid)
        if capitals.get_capital_at(conn, sid, x, y): raise ValueError("capital_requires_siege")
        try:
            from services import polywar_world_service as world
            if world.is_rift(conn, sid, x, y): raise ValueError("rift_requires_seal")
        except ValueError:
            raise
        except Exception:
            pass
        polywar._insert_player_if_missing(conn, int(user_id), sid)
        player = polywar._fetchone(c, "SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s" + ("" if polywar._is_sqlite(conn) else " FOR UPDATE"), (user_id, sid))
        dup = mines.duplicate_outcome_response(conn, sid, user_id, idempotency_key)
        if dup:
            conn.commit(); return dup
        existing = polywar._fetchone(c, "SELECT * FROM polywar_actions WHERE season_id=%s AND user_id=%s AND idempotency_key=%s", (sid, user_id, idempotency_key))
        if existing:
            conn.commit(); return legacy_action_duplicate_response(conn, sid, seed, user_id, existing)
        fid = player.get("faction_id")
        if not fid: raise ValueError("faction_required")
        e = polywar._energy(player)
        if e.get("is_locked"): raise ValueError("player_locked")
        from services import polywar_sector_service as sectors
        sectors.ensure_starting_territories_bootstrap(conn, sid)
        terr = terrain_at(seed, x, y); cost = TERRAIN_COSTS[terr]
        if cost is None: raise ValueError(f"{terr}_not_capturable")
        owner = _owner_at(conn, sid, x, y)
        if owner == fid: raise ValueError("already_owned")
        if owner is not None: raise ValueError("enemy_capture_unavailable")
        if not any(_owner_at(conn, sid, nx, ny) == fid for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if in_bounds(nx, ny)):
            raise ValueError("not_adjacent")
        if int(e["current_energy"]) < cost: raise ValueError("insufficient_energy")
        now = datetime.utcnow()
        mine = mines.active_mine_at(conn, sid, seed, x, y, terr)
        if mine and mines.try_trigger_mine(conn, sid, x, y, user_id, fid, idempotency_key, now):
            locked_until = now + timedelta(minutes=mines.mine_lock_minutes())
            polywar._execute(c, "INSERT INTO polywar_actions (season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (sid,user_id,fid,"capture",x,y,cost,idempotency_key,now))
            mines.record_triggered_mine(conn, sid, fid, user_id, x, y, idempotency_key, seed, now)
            new_energy, _, energy = mines.spend_player_energy(conn, player, cost, now, locked_until)
            payload = {"cell": {"x": x, "y": y, "terrain": terr, "owner_faction_id": None, "energy_cost": cost}, "mine_hit": True, "locked_until": polywar._iso(locked_until), "energy": energy}
            mines.insert_outcome(conn, sid, user_id, idempotency_key, "capture", x, y, "mine_hit", cost, payload, now)
            conn.commit(); payload.update({"ok": True, "outcome": "mine_hit"}); return payload
        owner = _owner_at(conn, sid, x, y)
        if owner == fid: raise ValueError("already_owned")
        if owner is not None: raise ValueError("enemy_capture_unavailable")
        if not any(_owner_at(conn, sid, nx, ny) == fid for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if in_bounds(nx, ny)):
            raise ValueError("not_adjacent")
        sectors.initialize_sector(conn, sid, *sectors.sector_coords(x, y), now)
        polywar._execute(c, "INSERT INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at,updated_by_user_id) VALUES (%s,%s,%s,%s,100,%s,%s)", (sid,x,y,fid,now,user_id))
        polywar._execute(c, "INSERT INTO polywar_actions (season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (sid,user_id,fid,"capture",x,y,cost,idempotency_key,now))
        new_energy, _, energy = mines.spend_player_energy(conn, player, cost, now)
        sectors.transfer_cell_ownership(conn, sid, x, y, None, fid, user_id, now)
        hint = mines.upsert_safe_hint(conn, sid, fid, x, y, user_id, seed, now)
        payload = {"cell": {"x": x, "y": y, "terrain": terr, "owner_faction_id": fid, "energy_cost": cost, "adjacent_mines": hint}, "adjacent_mines": hint, "energy": energy}
        mines.insert_outcome(conn, sid, user_id, idempotency_key, "capture", x, y, "captured", cost, payload, now)
        conn.commit(); payload.update({"ok": True, "outcome": "captured"}); return payload
    except ValueError:
        polywar._safe_rollback(conn); raise
    except Exception as exc:
        polywar._safe_rollback(conn)
        if _is_expected_unique_error(exc):
            try:
                dup = mines.duplicate_outcome_response(conn, sid, user_id, idempotency_key)
                if dup: return dup
                existing = polywar._fetchone(c, "SELECT * FROM polywar_actions WHERE season_id=%s AND user_id=%s AND idempotency_key=%s", (sid,user_id,idempotency_key))
                if existing: return legacy_action_duplicate_response(conn, sid, seed, user_id, existing)
            except Exception:
                logger.exception("Failed to load duplicate PolyWar action after unique conflict")
            raise ValueError("cell_conflict") from exc
        logger.exception("Unexpected PolyWar capture failure"); raise
    finally:
        conn.close()

def _is_expected_unique_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "unique" in text or "duplicate" in text or "constraint" in text or "integrity" in text
