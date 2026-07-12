import copy
import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from services import polywar_service as polywar

logger = logging.getLogger(__name__)
TERRAIN_COSTS = {"plain": 1, "forest": 1, "mountain": 2, "swamp": 2, "desert": 1, "road": 1, "ruins": 1, "water": None, "river": None}
_TERRAIN_CACHE: "OrderedDict[Tuple[Any, ...], List[List[str]]]" = OrderedDict()
_NOISE_CACHE: "OrderedDict[Tuple[str, int, int, int], float]" = OrderedDict()
_CACHE_LOCK = threading.RLock()
_RATE_LOCK = threading.Lock()
_CHUNK_RATE: Dict[int, deque] = defaultdict(deque)
MAX_TERRAIN_CHUNKS = 256
MAX_NOISE_VALUES = 20000
CHUNK_RATE_WINDOW = 10
CHUNK_RATE_MAX = 60


@dataclass(frozen=True)
class PolyWarMapConfig:
    width: int
    height: int
    chunk_size: int
    starting_area_size: int
    bases: Dict[int, Tuple[int, int]]
    max_chunks_per_request: int
    capture_progress_required: int
    sector_size: int
    max_sectors_per_request: int
    capital_siege_required: int
    governance_rules: Dict[str, int]


def _clamp_int(value, default, lo, hi):
    try:
        value = int(str(value).strip())
    except Exception:
        value = default
    return max(lo, min(hi, value))


def load_map_config(conn) -> PolyWarMapConfig:
    keys = ("polywar_map_width", "polywar_map_height", "polywar_chunk_size", "polywar_starting_area_size", "polywar_max_chunks_per_request", "polywar_capture_progress_required", "polywar_sector_size", "polywar_max_sectors_per_request", "polywar_capital_siege_required", "polywar_commander_election_hours", "polywar_commander_term_hours", "polywar_commander_min_contribution", "polywar_commander_min_members_for_election", "polywar_commander_max_statement_length", "polywar_commander_order_limit", "polywar_capital_order_duration_hours")
    placeholders = ",".join(["%s"] * len(keys))
    try:
        rows = polywar._fetchall(conn.cursor(), f"SELECT key, value FROM settings WHERE key IN ({placeholders})", keys)
    except Exception as exc:
        if "settings" not in str(exc).lower():
            raise
        polywar._safe_rollback(conn)
        if polywar._is_sqlite(conn):
            rows = [{"key": key, "value": polywar.get_setting(key, "")} for key in keys]
        else:
            raise RuntimeError("polywar_not_initialized") from exc
    values = {str(r.get("key")): r.get("value") for r in rows}
    width = _clamp_int(values.get("polywar_map_width"), 32000, 512, 100000)
    height = _clamp_int(values.get("polywar_map_height"), 32000, 512, 100000)
    cs = _clamp_int(values.get("polywar_chunk_size"), 64, 16, 128)
    area = _clamp_int(values.get("polywar_starting_area_size"), 15, 3, 65)
    max_chunks = _clamp_int(values.get("polywar_max_chunks_per_request"), 9, 1, 25)
    capture_required = _clamp_int(values.get("polywar_capture_progress_required"), 100, 1, 1000)
    sec_size = _clamp_int(values.get("polywar_sector_size"), 100, 10, 10000)
    max_sectors = _clamp_int(values.get("polywar_max_sectors_per_request"), 100, 1, 500)
    siege_required_value = _clamp_int(values.get("polywar_capital_siege_required"), 1000, 100, 100000)
    governance_rules = {
        "election_hours": _clamp_int(values.get("polywar_commander_election_hours"), 24, 1, 168),
        "term_hours": _clamp_int(values.get("polywar_commander_term_hours"), 168, 1, 8760),
        "min_contribution": _clamp_int(values.get("polywar_commander_min_contribution"), 5, 0, 10**9),
        "min_members": _clamp_int(values.get("polywar_commander_min_members_for_election"), 2, 1, 1000000),
        "max_statement_length": _clamp_int(values.get("polywar_commander_max_statement_length"), 280, 0, 1000),
        "max_orders": _clamp_int(values.get("polywar_commander_order_limit"), 5, 0, 100),
        "order_duration_hours": _clamp_int(values.get("polywar_capital_order_duration_hours"), 24, 1, 168),
    }
    bases = faction_base_positions(width, height)
    return PolyWarMapConfig(width, height, cs, area, bases, max_chunks, capture_required, sec_size, max_sectors, siege_required_value, governance_rules)


def get_active_season_readonly(conn, include_secret_seed=False):
    cols = "id,name,status,starts_at,ends_at,completed_at,victory_type,winner_faction_id,domination_faction_id,domination_started_at,finalization_started_at,created_at"
    if include_secret_seed:
        cols += ",secret_seed"
    row = polywar._fetchone(conn.cursor(), f"SELECT {cols} FROM polywar_seasons WHERE status=%s ORDER BY starts_at DESC LIMIT 1", ("active",))
    if not row:
        raise RuntimeError("polywar_not_initialized")
    return row


def begin_polywar_readonly(conn):
    if not polywar._is_sqlite(conn):
        polywar._execute(conn.cursor(), "SET TRANSACTION READ ONLY")
        polywar._execute(conn.cursor(), "SET LOCAL statement_timeout = '15s'")
        polywar._execute(conn.cursor(), "SET LOCAL lock_timeout = '2s'")


def _setting_int(key, default, lo, hi):
    return polywar._setting_int(key, default, lo, hi)


def map_width():
    return _setting_int("polywar_map_width", 32000, 512, 100000)


def map_height():
    return _setting_int("polywar_map_height", 32000, 512, 100000)


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
    margin = max(starting_area_size() + 64, min(w, h) // 7)
    midx, midy = w // 2, h // 2
    return {
        1: (margin, margin),
        2: (w - margin - 1, margin),
        3: (margin, h - margin - 1),
        4: (w - margin - 1, h - margin - 1),
        5: (midx, margin),
        6: (margin, midy),
        7: (w - margin - 1, midy),
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


def terrain_at_with_config(seed, x: int, y: int, config: PolyWarMapConfig) -> str:
    if not (0 <= int(x) < config.width and 0 <= int(y) < config.height):
        raise ValueError("out_of_bounds")
    for bx, by in config.bases.values():
        if (abs(x - bx) <= 1 and abs(y - by) < 90) or (abs(y - by) <= 1 and abs(x - bx) < 90):
            return "road"
    river_a = abs((x * 11 + y * 7 + int(_hash(seed, "river-a") * 20011)) % 2201 - 1100)
    river_b = abs((x * 5 - y * 13 + int(_hash(seed, "river-b") * 30011)) % 3001 - 1500)
    if river_a < 5 or river_b < 4:
        return "river"
    lake = _smooth(seed + "lake", x, y, 1400) * 0.72 + _smooth(seed + "lake", x, y, 360) * 0.28
    if lake < 0.18:
        return "water"
    ridge = abs(_smooth(seed + "ridge", x, y, 1800) - 0.5) + _smooth(seed + "ridge-detail", x, y, 240) * 0.35
    forest_mass = _smooth(seed + "forest-mass", x, y, 1150) * 0.75 + _smooth(seed + "forest-detail", x, y, 260) * 0.25
    dry = _smooth(seed + "dry-plains", x, y, 1700)
    rare = _hash(seed, "old-war", x // 4, y // 4)
    if ridge > 0.52:
        return "mountain"
    if lake < 0.25 and forest_mass > 0.50:
        return "swamp"
    if dry > 0.83 and forest_mass < 0.45:
        return "desert"
    if forest_mass > 0.58:
        return "forest"
    if rare > 0.992:
        return "ruins"
    return "plain"





def world_feature_at_with_config(seed, x: int, y: int, terrain: str, owner, config: PolyWarMapConfig):
    if terrain in {"water", "river", "mountain"}:
        return None
    for fid, (bx, by) in config.bases.items():
        if abs(x - bx) <= 2 and abs(y - by) <= 2:
            return {"type": "hq", "faction_id": fid, "label": "HQ"}
    if owner:
        age = _hash(seed, "territory-age", x // 2, y // 2)
        if age > 0.985:
            return {"type": "factory_smoke", "label": "Workshop"}
        if age > 0.955:
            return {"type": "house", "label": "Homestead"}
        if age > 0.925:
            return {"type": "flag", "label": "Flag"}
        if (x + y + int(owner)) % 37 == 0:
            return {"type": "roadlet", "label": "Patrol road"}
    cell = _hash(seed, "feature", x // 3, y // 3)
    spacing = (x % 3 == 1 and y % 3 == 1)
    if not spacing or cell < 0.965:
        return None
    if terrain == "forest":
        typ = "forest_camp" if cell > 0.988 else "grove"
    elif terrain == "ruins":
        typ = "abandoned_outpost" if cell > 0.984 else "battlefield"
    elif terrain == "road":
        typ = "radio_tower" if cell > 0.990 else "village"
    else:
        typ = "city" if cell > 0.996 else "factory" if cell > 0.990 else "village"
    return {"type": typ, "label": typ.replace("_", " ").title()}

def _legacy_map_config(chunk_size_override=None):
    return PolyWarMapConfig(
        width=map_width(),
        height=map_height(),
        chunk_size=int(chunk_size_override or chunk_size()),
        starting_area_size=starting_area_size(),
        bases=faction_base_positions(),
        max_chunks_per_request=max_chunks_per_request(),
        capture_progress_required=_setting_int("polywar_capture_progress_required", 100, 1, 1000),
        sector_size=_setting_int("polywar_sector_size", 100, 10, 10000),
        max_sectors_per_request=_setting_int("polywar_max_sectors_per_request", 100, 1, 500),
        capital_siege_required=_setting_int("polywar_capital_siege_required", 1000, 100, 100000),
        governance_rules={},
    )

def terrain_at(seed, x: int, y: int) -> str:
    cfg = _legacy_map_config()
    return terrain_at_with_config(seed, x, y, cfg)

def get_starting_bases():
    colors = {fid: color for fid, _, _, color, _ in polywar.FACTIONS}
    return [{"faction_id": fid, "x": x, "y": y, "size": starting_area_size(), "color": colors.get(fid)} for fid, (x, y) in faction_base_positions().items()]


def start_owner_with_config(x, y, config: PolyWarMapConfig):
    half = config.starting_area_size // 2
    for fid, (bx, by) in config.bases.items():
        if abs(x - bx) <= half and abs(y - by) <= half:
            return fid
    return None

def _start_owner(x, y):
    cfg = _legacy_map_config()
    return start_owner_with_config(x, y, cfg)


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


def terrain_chunk_with_config(season_id, seed, cx, cy, config: PolyWarMapConfig):
    cs = config.chunk_size
    bases_key = tuple(sorted((int(fid), int(x), int(y)) for fid, (x, y) in config.bases.items()))
    key = (int(season_id), str(seed), int(cx), int(cy), int(cs), int(config.width), int(config.height), int(config.starting_area_size), bases_key)
    with _CACHE_LOCK:
        cached = _TERRAIN_CACHE.get(key)
        if cached is not None:
            _TERRAIN_CACHE.move_to_end(key)
            return copy.deepcopy(cached)
    x0, y0 = cx * cs, cy * cs
    w, h = min(cs, config.width - x0), min(cs, config.height - y0)
    terrain = [[terrain_at_with_config(seed, xx, yy, config) for xx in range(x0, x0 + w)] for yy in range(y0, y0 + h)]
    with _CACHE_LOCK:
        _TERRAIN_CACHE[key] = terrain
        if len(_TERRAIN_CACHE) > MAX_TERRAIN_CHUNKS:
            _TERRAIN_CACHE.popitem(last=False)
    return copy.deepcopy(terrain)

def _terrain_chunk(season_id, seed, cx, cy, cs):
    cfg = _legacy_map_config(cs)
    return terrain_chunk_with_config(season_id, seed, cx, cy, cfg)


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


def _set_read_timeouts(conn):
    begin_polywar_readonly(conn)


def build_chunks(user_id: int, chunks: List[Tuple[int, int]]):
    started = time.monotonic()
    logger.info("polywar_chunks_request_started user_id=%s chunk_count=%s", int(user_id), len(chunks))
    conn = polywar.get_connection()
    try:
        _set_read_timeouts(conn)
        t = time.monotonic(); config = load_map_config(conn); logger.info("polywar_chunks_stage user_id=%s chunk_count=%s stage=config duration_ms=%.2f", int(user_id), len(chunks), (time.monotonic()-t)*1000)
        if len(chunks) > config.max_chunks_per_request:
            raise ValueError("too_many_chunks")
        _check_chunk_rate(user_id, max(1, len(chunks)))
        t = time.monotonic(); season = get_active_season_readonly(conn, include_secret_seed=True); sid, seed = int(season["id"]), season["secret_seed"]; logger.info("polywar_chunks_stage user_id=%s chunk_count=%s stage=season duration_ms=%.2f", int(user_id), len(chunks), (time.monotonic()-t)*1000)
        for cx, cy in chunks:
            if cx < 0 or cy < 0 or cx * config.chunk_size >= config.width or cy * config.chunk_size >= config.height:
                raise ValueError("out_of_bounds")
        ranges = []
        for cx, cy in chunks:
            x0, y0 = cx * config.chunk_size, cy * config.chunk_size
            ranges.append((cx, cy, x0, y0, min(config.chunk_size, config.width - x0), min(config.chunk_size, config.height - y0)))
        t = time.monotonic()
        materialized = {}
        requested_chunk_keys = {(int(cx), int(cy)) for cx, cy, *_ in ranges}
        if ranges:
            predicates = []
            params = [sid]
            for _, _, x0, y0, w, h in ranges:
                predicates.append("(x >= %s AND x < %s AND y >= %s AND y < %s)")
                params.extend([x0, x0 + w, y0, y0 + h])
            rows = polywar._fetchall(conn.cursor(), "SELECT x,y,owner_faction_id,contesting_faction_id,contest_progress,contested_at FROM polywar_cells WHERE season_id=%s AND (" + " OR ".join(predicates) + ")", tuple(params))
            for r in rows:
                x, y = int(r["x"]), int(r["y"])
                if (x // config.chunk_size, y // config.chunk_size) in requested_chunk_keys:
                    materialized[(x, y)] = r
        logger.info("polywar_chunks_stage user_id=%s chunk_count=%s stage=materialized_cells duration_ms=%.2f", int(user_id), len(chunks), (time.monotonic()-t)*1000)
        out = []
        colors = {fid: color for fid, _, _, color, _ in polywar.FACTIONS}
        contest_required = config.capture_progress_required
        for cx, cy, x0, y0, w, h in ranges:
            t = time.monotonic(); terrain = terrain_chunk_with_config(sid, seed, cx, cy, config); logger.info("polywar_chunks_stage user_id=%s chunk_count=%s stage=terrain duration_ms=%.2f", int(user_id), len(chunks), (time.monotonic()-t)*1000)
            owners = [[int(materialized[(xx, yy)]["owner_faction_id"]) if (xx, yy) in materialized else start_owner_with_config(xx, yy, config) for xx in range(x0, x0 + w)] for yy in range(y0, y0 + h)]
            bases = [{"faction_id": fid, "x": bx, "y": by, "size": config.starting_area_size, "color": colors.get(fid)} for fid, (bx, by) in config.bases.items() if x0 <= bx < x0 + w and y0 <= by < y0 + h]
            contested = [{"x": int(r["x"]), "y": int(r["y"]), "owner_faction_id": int(r["owner_faction_id"]), "contesting_faction_id": r.get("contesting_faction_id"), "contest_progress": int(r.get("contest_progress") or 0), "contest_required": contest_required, "contested_at": polywar._iso(r.get("contested_at"))} for r in materialized.values() if x0 <= int(r["x"]) < x0 + w and y0 <= int(r["y"]) < y0 + h and int(r.get("contest_progress") or 0) > 0]
            features = []
            for yy in range(h):
                for xx in range(w):
                    feat = world_feature_at_with_config(seed, x0 + xx, y0 + yy, terrain[yy][xx], owners[yy][xx], config)
                    if feat:
                        features.append({"x": x0 + xx, "y": y0 + yy, **feat})
            out.append({"chunk_x": cx, "chunk_y": cy, "chunk_size": config.chunk_size, "width": w, "height": h, "terrain": terrain, "owners": owners, "bases": bases, "features": features, "contested_cells": contested, "user_id": user_id})
        t = time.monotonic()
        for ch in out:
            x0, y0 = ch["chunk_x"] * config.chunk_size, ch["chunk_y"] * config.chunk_size
            rows = polywar._fetchall(conn.cursor(), "SELECT x,y,status,health,max_health FROM polywar_null_rifts WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s", (sid, x0, x0 + ch["width"], y0, y0 + ch["height"]))
            ch["rifts"] = [{"x": int(r["x"]), "y": int(r["y"]), "status": r["status"], "health": int(r["health"]), "max_health": int(r["max_health"]), "health_percent": round(100*int(r["health"])/max(1,int(r["max_health"])),2)} for r in rows]
        from services import polywar_capital_service as capitals, polywar_governance_service as governance, polywar_mine_service as mines, polywar_rebellion_service as reb
        capitals.enrich_chunks(conn, sid, out, siege_required_value=config.capital_siege_required)
        player = polywar._fetchone(conn.cursor(), "SELECT faction_id FROM polywar_players WHERE season_id=%s AND user_id=%s", (sid, int(user_id))) or {}
        fid = player.get("faction_id")
        for ch in out:
            x0, y0 = ch["chunk_x"] * config.chunk_size, ch["chunk_y"] * config.chunk_size
            ch["rebellions"] = reb.get_public_rebellions_readonly(conn, sid, (x0, y0, x0 + ch["width"], y0 + ch["height"]))
        mines.enrich_chunks(conn, sid, fid, out)
        governance.enrich_chunks(conn, sid, fid, out)
        logger.info("polywar_chunks_stage user_id=%s chunk_count=%s stage=enrichment duration_ms=%.2f", int(user_id), len(chunks), (time.monotonic()-t)*1000)
        for ch in out: ch.pop("user_id", None)
        duration = (time.monotonic() - started) * 1000
        logger.info("polywar_chunks_stage user_id=%s chunk_count=%s stage=total duration_ms=%.2f", int(user_id), len(chunks), duration)
        logger.info("polywar_chunks_request_finished user_id=%s chunk_count=%s duration_ms=%.2f", int(user_id), len(chunks), duration)
        return {"ok": True, "season_id": sid, "chunks": out, "chunk_size": config.chunk_size, "map_width": config.width, "map_height": config.height, "server_timestamp": int(time.time())}
    except Exception as exc:
        polywar._safe_rollback(conn)
        logger.exception("polywar_chunks_request_failed error_type=%s duration_ms=%.2f", type(exc).__name__, (time.monotonic()-started)*1000)
        raise
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
        prepared=polywar.prepare_gameplay_mutation_in_transaction(conn,sid)
        if not prepared.get('ok'):
            if prepared.get('season_finalized'):
                conn.commit(); return {'ok': False, 'error': prepared.get('error') or 'season_ended', 'season_finalized': True}
            raise ValueError(prepared.get('error') or 'season_ended')
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
            logger.exception("polywar_capture_rift_check_failed season_id=%s x=%s y=%s", sid, x, y)
            raise
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
