import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


FACTIONS = [
    (1, "Blue Coalition", "blue-coalition", "blue", "A disciplined bloc built on coordination and protocol trust."),
    (2, "Red Alliance", "red-alliance", "red", "Aggressive consensus challengers who favor decisive action."),
    (3, "Green Union", "green-union", "green", "Sustainable strategists focused on steady network growth."),
    (4, "Black Dominion", "black-dominion", "black", "Shadow operators who win through patience and leverage."),
    (5, "White Republic", "white-republic", "white", "Transparent defenders of order, rules, and open governance."),
    (6, "Orange League", "orange-league", "orange", "High-energy builders optimizing for speed and adoption."),
    (7, "Purple Pact", "purple-pact", "purple", "Diplomatic tacticians specializing in alliances and influence."),
]


def get_connection():
    from db.database import get_connection as _get_connection
    return _get_connection()


def get_setting(key: str, default: str = ""):
    from db.database import get_setting as _get_setting
    return _get_setting(key, default)


def get_airdrop_points_balance(user_id: int) -> dict:
    try:
        from services.airdrop_points_service import get_airdrop_points_balance as _get_balance
        return _get_balance(user_id)
    except Exception:
        return {"total": 0, "balance": 0}


def _now() -> datetime:
    return datetime.utcnow()


def _is_sqlite(conn) -> bool:
    return conn.__class__.__module__.startswith("sqlite3")


def _iso(dt: Any) -> Optional[str]:
    if not dt:
        return None
    if isinstance(dt, datetime):
        return dt.replace(microsecond=0).isoformat()
    return str(dt)


def _dict(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return None


def _setting_int(key: str, default: int, min_value: int = 1, max_value: int = 3650) -> int:
    try:
        value = int(str(get_setting(key, str(default))).strip())
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def is_enabled() -> bool:
    return str(get_setting("polywar_enabled", "true") or "true").strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _execute(cursor, sql: str, params=()):
    rendered = sql
    last_error = None
    for attempt in range(30):
        try:
            return cursor.execute(rendered, params)
        except Exception as exc:
            text = str(exc).lower()
            last_error = exc
            if "%s" in rendered:
                rendered = rendered.replace("%s", "?")
                continue
            if "locked" in text:
                time.sleep(0.025 * (attempt + 1))
                continue
            raise
    raise last_error


def _fetchone(cursor, sql: str, params=()):
    _execute(cursor, sql, params)
    return _dict(cursor.fetchone())


def _fetchall(cursor, sql: str, params=()) -> List[Dict[str, Any]]:
    _execute(cursor, sql, params)
    return [dict(r) for r in cursor.fetchall()]


def _rowcount(cursor) -> int:
    try:
        return int(cursor.rowcount or 0)
    except Exception:
        return 0


def _safe_commit(conn):
    try:
        conn.commit()
    except Exception:
        pass


def _safe_rollback(conn):
    try:
        conn.rollback()
    except Exception:
        pass


def init_polywar_schema(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    c = conn.cursor()
    try:
        id_sql = "INTEGER PRIMARY KEY AUTOINCREMENT" if _is_sqlite(conn) else "SERIAL PRIMARY KEY"
        c.execute(f"""
        CREATE TABLE IF NOT EXISTS polywar_seasons (
            id {id_sql},
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            starts_at TIMESTAMP NOT NULL,
            ends_at TIMESTAMP NOT NULL,
            secret_seed TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS polywar_factions (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS polywar_faction_season_stats (
            season_id INTEGER NOT NULL,
            faction_id INTEGER NOT NULL,
            influence_score BIGINT NOT NULL DEFAULT 0,
            active_members_count INTEGER NOT NULL DEFAULT 0,
            controlled_cells_count INTEGER NOT NULL DEFAULT 0,
            controlled_sectors_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (season_id, faction_id)
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS polywar_players (
            user_id BIGINT NOT NULL,
            season_id INTEGER NOT NULL,
            faction_id INTEGER NULL,
            current_energy INTEGER NOT NULL DEFAULT 10,
            max_energy INTEGER NOT NULL DEFAULT 10,
            energy_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            locked_until TIMESTAMP NULL,
            season_spendable_points BIGINT NOT NULL DEFAULT 0,
            faction_contribution BIGINT NOT NULL DEFAULT 0,
            joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_active_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, season_id)
        )
        """)
        c.execute(f"""
        CREATE TABLE IF NOT EXISTS polywar_events (
            id {id_sql},
            season_id INTEGER NOT NULL,
            user_id BIGINT NULL,
            faction_id INTEGER NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)
        # Idempotent additive migrations for databases created by the first Phase 1 draft.
        for sql in [
            "ALTER TABLE polywar_factions DROP COLUMN IF EXISTS seasonal_influence_score",
            "ALTER TABLE polywar_factions DROP COLUMN IF EXISTS active_members_count",
            "ALTER TABLE polywar_players DROP COLUMN IF EXISTS lifetime_earned_points",
        ]:
            if not _is_sqlite(conn):
                try:
                    c.execute(sql)
                except Exception:
                    pass
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_polywar_seasons_status ON polywar_seasons(status)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_players_user ON polywar_players(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_players_faction ON polywar_players(season_id, faction_id)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_events_season_created ON polywar_events(season_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_faction_stats_season ON polywar_faction_season_stats(season_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_polywar_one_active_season ON polywar_seasons(status) WHERE status = 'active'",
        ]:
            c.execute(sql)
        
        try:
            from services.polywar_map_service import init_polywar_map_schema
            init_polywar_map_schema(conn)
            from services.polywar_mine_service import init_polywar_mine_schema
            init_polywar_mine_schema(conn)
        except Exception:
            pass
        conn.commit()
    finally:
        if own:
            conn.close()


def ensure_factions(conn=None) -> List[Dict[str, Any]]:
    own = conn is None
    conn = conn or get_connection(); c = conn.cursor()
    try:
        for fid, name, slug, color, desc in FACTIONS:
            _execute(c, """
            INSERT INTO polywar_factions (id, name, slug, color, description, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """, (fid, name, slug, color, desc, _now()))
        conn.commit()
        return list_factions(conn)
    finally:
        if own: conn.close()


def _public_season(row):
    return {k: _iso(v) if k.endswith("_at") else v for k, v in row.items() if k != "secret_seed"}


def _complete_expired_active_seasons(conn, now: datetime) -> None:
    c = conn.cursor()
    _execute(c, "UPDATE polywar_seasons SET status = %s, completed_at = %s WHERE status = %s AND ends_at <= %s", ("completed", now, "active", now))


def _next_season_name(conn) -> str:
    c = conn.cursor()
    row = _fetchone(c, "SELECT COUNT(*) AS count FROM polywar_seasons") or {}
    return f"Season {int(row.get('count') or 0) + 1}"


def _ensure_faction_stats_for_season(conn, season_id: int) -> None:
    c = conn.cursor()
    for fid, *_ in FACTIONS:
        _execute(c, """
        INSERT INTO polywar_faction_season_stats (season_id, faction_id, influence_score, active_members_count, controlled_cells_count, controlled_sectors_count, created_at, updated_at)
        VALUES (%s, %s, 0, 0, 0, 0, %s, %s)
        ON CONFLICT (season_id, faction_id) DO NOTHING
        """, (season_id, fid, _now(), _now()))


def ensure_active_season(conn=None) -> Dict[str, Any]:
    own = conn is None
    conn = conn or get_connection(); c = conn.cursor()
    try:
        now = _now()
        _complete_expired_active_seasons(conn, now)
        row = _fetchone(c, "SELECT * FROM polywar_seasons WHERE status = %s ORDER BY starts_at DESC LIMIT 1", ("active",))
        if row:
            _ensure_faction_stats_for_season(conn, int(row["id"]))
            conn.commit()
            return _public_season(row)
        start = now; end = start + timedelta(days=_setting_int("polywar_season_days", 30, 1, 365))
        try:
            _execute(c, """
            INSERT INTO polywar_seasons (name, status, starts_at, ends_at, secret_seed, created_at)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
            """, (_next_season_name(conn), "active", start, end, secrets.token_hex(32), start))
            row = _dict(c.fetchone())
        except Exception:
            _safe_rollback(conn)
            row = None
        if not row:
            row = _fetchone(c, "SELECT * FROM polywar_seasons WHERE status = %s ORDER BY starts_at DESC LIMIT 1", ("active",))
        if not row:
            raise RuntimeError("polywar_active_season_unavailable")
        _ensure_faction_stats_for_season(conn, int(row["id"]))
        conn.commit(); return _public_season(row)
    finally:
        if own: conn.close()


def list_factions(conn=None) -> List[Dict[str, Any]]:
    own = conn is None; conn = conn or get_connection(); c = conn.cursor()
    try:
        return _fetchall(c, "SELECT * FROM polywar_factions ORDER BY id")
    finally:
        if own: conn.close()


def list_factions_with_stats(season_id: int, conn=None) -> List[Dict[str, Any]]:
    own = conn is None; conn = conn or get_connection(); c = conn.cursor()
    try:
        _ensure_faction_stats_for_season(conn, int(season_id))
        rows = _fetchall(c, """
        SELECT f.id, f.name, f.slug, f.color, f.description, f.created_at,
               COALESCE(s.influence_score, 0) AS influence_score,
               COALESCE(s.active_members_count, 0) AS active_members_count,
               COALESCE(s.controlled_cells_count, 0) AS controlled_cells_count,
               COALESCE(s.controlled_sectors_count, 0) AS controlled_sectors_count
        FROM polywar_factions f
        LEFT JOIN polywar_faction_season_stats s ON s.faction_id = f.id AND s.season_id = %s
        ORDER BY f.id
        """, (int(season_id),))
        return rows
    finally:
        if own: conn.close()


def _energy(player: Dict[str, Any]) -> Dict[str, Any]:
    max_energy = int(player.get("max_energy") or _setting_int("polywar_energy_max", 10, 1, 1000))
    recharge = _setting_int("polywar_energy_recharge_minutes", 60, 1, 10080)
    cur = min(max_energy, int(player.get("current_energy") or 0))
    updated = player.get("energy_updated_at") or _now()
    if isinstance(updated, str):
        updated = datetime.fromisoformat(updated)
    elapsed = max(0, int((_now() - updated).total_seconds()))
    gained = elapsed // (recharge * 60)
    if gained:
        cur = min(max_energy, cur + gained)
        updated = updated + timedelta(minutes=recharge * gained)
    seconds_next = 0 if cur >= max_energy else max(0, recharge * 60 - int((_now() - updated).total_seconds()))
    locked_until = player.get("locked_until")
    is_locked = bool(locked_until and _iso(locked_until) > _iso(_now()))
    return {"current_energy": cur, "max_energy": max_energy, "recharge_minutes": recharge, "seconds_until_next_energy": seconds_next, "locked_until": _iso(locked_until), "is_locked": is_locked, "lock_seconds_remaining": max(0, int((locked_until - _now()).total_seconds())) if is_locked and not isinstance(locked_until, str) else (max(0, int((datetime.fromisoformat(locked_until) - _now()).total_seconds())) if is_locked else 0), "lock_reason": "mine_hit" if is_locked else None, "energy_updated_at": updated}


def _insert_player_if_missing(conn, user_id: int, season_id: int) -> None:
    c = conn.cursor(); now = _now(); maxe = _setting_int("polywar_energy_max", 10, 1, 1000)
    _execute(c, """
    INSERT INTO polywar_players (user_id, season_id, current_energy, max_energy, energy_updated_at, joined_at, last_active_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (user_id, season_id) DO NOTHING
    """, (user_id, season_id, maxe, maxe, now, now, now))


def get_or_create_player(user_id: int, season_id: int, conn=None) -> Dict[str, Any]:
    own = conn is None; conn = conn or get_connection(); c = conn.cursor()
    try:
        _insert_player_if_missing(conn, int(user_id), int(season_id))
        row = _fetchone(c, "SELECT * FROM polywar_players WHERE user_id = %s AND season_id = %s", (user_id, season_id))
        e = _energy(row)
        _execute(c, "UPDATE polywar_players SET current_energy = %s, energy_updated_at = %s, last_active_at = %s WHERE user_id = %s AND season_id = %s", (e["current_energy"], e["energy_updated_at"], _now(), user_id, season_id))
        conn.commit(); row.update({"current_energy": e["current_energy"], "max_energy": e["max_energy"], "energy_updated_at": e["energy_updated_at"]})
        return row
    finally:
        if own: conn.close()


def join_faction(user_id: int, faction_id: int) -> Dict[str, Any]:
    if not is_enabled():
        raise ValueError("polywar_disabled")
    conn = get_connection(); c = conn.cursor()
    try:
        init_polywar_schema(conn); ensure_factions(conn); season = ensure_active_season(conn)
        faction_id = int(faction_id)
        faction = _fetchone(c, "SELECT * FROM polywar_factions WHERE id = %s", (faction_id,))
        if not faction:
            raise ValueError("unknown_faction")
        try:
            _insert_player_if_missing(conn, int(user_id), int(season["id"]))
            _execute(c, """
            UPDATE polywar_players
            SET faction_id = %s, last_active_at = %s
            WHERE user_id = %s AND season_id = %s AND faction_id IS NULL
            """, (faction_id, _now(), int(user_id), int(season["id"])))
            if _rowcount(c) != 1:
                _safe_rollback(conn)
                raise ValueError("faction_already_selected")
            _execute(c, """
            UPDATE polywar_faction_season_stats
            SET active_members_count = active_members_count + 1, updated_at = %s
            WHERE season_id = %s AND faction_id = %s
            """, (_now(), int(season["id"]), faction_id))
            _execute(c, """
            INSERT INTO polywar_events (season_id, user_id, faction_id, event_type, message, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (int(season["id"]), int(user_id), faction_id, "join", f"Player joined {faction['name']}", _now()))
            conn.commit()
        except ValueError:
            raise
        except Exception:
            _safe_rollback(conn)
            raise
        return get_state(user_id, conn)
    finally:
        conn.close()


def get_events(season_id: Optional[int] = None, limit: int = 20, conn=None):
    own = conn is None; conn = conn or get_connection(); c = conn.cursor()
    try:
        if season_id:
            return _fetchall(c, "SELECT * FROM polywar_events WHERE season_id = %s ORDER BY created_at DESC LIMIT %s", (season_id, limit))
        return _fetchall(c, "SELECT * FROM polywar_events ORDER BY created_at DESC LIMIT %s", (limit,))
    finally:
        if own: conn.close()


def _lifetime_airdrop_points(user_id: int) -> int:
    balance = get_airdrop_points_balance(int(user_id)) or {}
    for key in ("total", "lifetime", "earned", "balance"):
        try:
            return int(float(balance.get(key) or 0))
        except Exception:
            continue
    return 0


def get_state(user_id: int, conn=None) -> Dict[str, Any]:
    if not is_enabled():
        return {"ok": True, "enabled": False, "message": "PolyWar is temporarily unavailable", "feature_flags": {"polywar_enabled": False}}
    own = conn is None; conn = conn or get_connection()
    try:
        init_polywar_schema(conn); ensure_factions(conn); season = ensure_active_season(conn)
        player = get_or_create_player(int(user_id), int(season["id"]), conn)
        factions = list_factions_with_stats(int(season["id"]), conn)
        faction = next((f for f in factions if f["id"] == player.get("faction_id")), None)
        e = _energy(player)
        public_player = {k: _iso(v) if k.endswith("_at") or k == "locked_until" else v for k, v in player.items() if k != "lifetime_earned_points"}
        public_player["lifetime_airdrop_points"] = _lifetime_airdrop_points(int(user_id))
        ranking = sorted(factions, key=lambda f: (-int(f.get("influence_score") or 0), -int(f.get("active_members_count") or 0), f["id"]))
        from services.polywar_map_service import map_width, map_height, chunk_size, max_chunks_per_request, get_starting_bases
        from services import polywar_combat_service as combat_rules
        from services import polywar_sector_service as sector_rules
        try:
            sector_rules.ensure_starting_territories_bootstrap(conn, int(season["id"]))
            conn.commit()
            factions = list_factions_with_stats(int(season["id"]), conn)
            ranking = sorted(factions, key=lambda f: (-int(f.get("influence_score") or 0), -int(f.get("active_members_count") or 0), f["id"]))
        except Exception:
            pass
        rules = {"combat": combat_rules.public_rules(), "sectors": sector_rules.public_rules()}
        return {"ok": True, "enabled": True, "map": {"width": map_width(), "height": map_height(), "chunk_size": chunk_size(), "max_chunks_per_request": max_chunks_per_request(), "bases": get_starting_bases()}, "rules": rules, "season": season, "player": public_player, "energy": {k:v for k,v in e.items() if k != "energy_updated_at"}, "selected_faction": faction, "factions": factions, "faction_ranking": ranking, "events": get_events(season["id"], 20, conn), "feature_flags": {"polywar_enabled": True, "map_enabled": True, "boosts_enabled": False, "purchases_enabled": False}}
    finally:
        if own: conn.close()
