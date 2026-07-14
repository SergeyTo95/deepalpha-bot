import hashlib, json, logging, time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from services import polywar_service as polywar
from services import polywar_map_service as m

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = ("spawning","marching","engaged","waiting_for_supply","waiting_for_players","retreating")
NON_COMBAT_VISIBLE_STATUSES = ("awaiting_reinforcement",)
VISIBLE_STATUSES = ACTIVE_STATUSES + NON_COMBAT_VISIBLE_STATUSES
TERMINAL_STATUSES = ("destroyed", "expired")
DEFAULTS = {
    "enabled": True, "spawn_interval_minutes": 180, "move_interval_minutes": 10,
    "max_active_per_faction": 1, "ttl_minutes": 720, "max_hp": 100,
    "supply_distance": 24, "pressure_ttl_minutes": 360,
    "neutral_pressure_per_step": 100, "enemy_pressure_per_step": 15,
    "enemy_pressure_cap": 60, "capital_pressure_cap": 20,
    "combat_damage_per_tick": 20, "support_energy_cost": 1,
    "support_hp": 25, "max_catchup_ticks": 6,
    "reinforcement_cooldown_minutes": 60, "reinforcement_hp": 50,
    "reinforcement_boost_minutes": 15, "reinforcement_min_remaining_minutes": 5,
    "reinforcement_energy_cost": 1, "reinforcement_return_radius": 6,
    "reinforcement_retry_minutes": 10, "reinforcement_batch_limit": 14,
}
HARD_ACTIVE_CAP = 14
PRESSURE_CLEANUP_BATCH = 500
SQUAD_TICK_STALE_MINUTES = 15
SQUAD_MAINTENANCE_INTERVAL_SECONDS = 60

def _now(): return datetime.utcnow()
def _is_sqlite(conn): return polywar._is_sqlite(conn)
def _execute(c, sql, params=()): return polywar._execute(c, sql, params)
def _fetchone(c, sql, params=()): return polywar._fetchone(c, sql, params)
def _fetchall(c, sql, params=()): return polywar._fetchall(c, sql, params)
def _iso(v): return polywar._iso(v)
def _as_dt(v):
    if isinstance(v, datetime): return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00").replace("+00:00", ""))
def _setting_int(k,d,lo,hi): return polywar._setting_int(k,d,lo,hi)
def _setting_bool(k,d=True): return str(polywar.get_setting(k, "true" if d else "false") or "").strip().lower() not in {"0","false","off","no","disabled"}

def _add_col(conn, table, spec):
    name = spec.split()[0]; c = conn.cursor()
    if _is_sqlite(conn):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if name not in cols: c.execute(f"ALTER TABLE {table} ADD COLUMN {spec}")
    else:
        c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {spec}")

def init_squad_schema(conn=None):
    own = conn is None; conn = conn or polywar.get_connection(); c = conn.cursor()
    id_sql = "INTEGER PRIMARY KEY AUTOINCREMENT" if _is_sqlite(conn) else "SERIAL PRIMARY KEY"
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_squad_season_config (season_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL, config_version INTEGER NOT NULL DEFAULT 1, spawn_interval_minutes INTEGER NOT NULL, move_interval_minutes INTEGER NOT NULL, max_active_per_faction INTEGER NOT NULL, ttl_minutes INTEGER NOT NULL, max_hp INTEGER NOT NULL, supply_distance INTEGER NOT NULL, pressure_ttl_minutes INTEGER NOT NULL, neutral_pressure_per_step INTEGER NOT NULL, enemy_pressure_per_step INTEGER NOT NULL, enemy_pressure_cap INTEGER NOT NULL, capital_pressure_cap INTEGER NOT NULL, combat_damage_per_tick INTEGER NOT NULL, support_energy_cost INTEGER NOT NULL, support_hp INTEGER NOT NULL, max_catchup_ticks INTEGER NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_faction_squads (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, spawn_index INTEGER NOT NULL, status TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, previous_x INTEGER NOT NULL, previous_y INTEGER NOT NULL, target_x INTEGER NULL, target_y INTEGER NULL, supply_x INTEGER NOT NULL, supply_y INTEGER NOT NULL, hp INTEGER NOT NULL, max_hp INTEGER NOT NULL, move_index INTEGER NOT NULL DEFAULT 0, blocked_ticks INTEGER NOT NULL DEFAULT 0, spawned_at TIMESTAMP NOT NULL, next_move_at TIMESTAMP NOT NULL, expires_at TIMESTAMP NOT NULL, last_moved_at TIMESTAMP NULL, engaged_squad_id INTEGER NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, UNIQUE(season_id,faction_id,spawn_index))""")
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_squad_pressure (season_id INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, faction_id INTEGER NOT NULL, pressure INTEGER NOT NULL, source_squad_id INTEGER NULL, expires_at TIMESTAMP NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, PRIMARY KEY(season_id,x,y,faction_id))""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_squad_ticks (id {id_sql}, season_id INTEGER NOT NULL, tick_index BIGINT NOT NULL, scheduled_at TIMESTAMP NOT NULL, started_at TIMESTAMP NOT NULL, processed_at TIMESTAMP NULL, status TEXT NOT NULL, spawned_count INTEGER NOT NULL DEFAULT 0, moved_count INTEGER NOT NULL DEFAULT 0, combat_count INTEGER NOT NULL DEFAULT 0, pressure_count INTEGER NOT NULL DEFAULT 0, outcome_json TEXT NULL, created_at TIMESTAMP NOT NULL, UNIQUE(season_id,tick_index))""")
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_polywar_squads_due ON polywar_faction_squads(season_id,status,next_move_at)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_squads_xy ON polywar_faction_squads(season_id,x,y)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_squads_faction ON polywar_faction_squads(season_id,faction_id,status)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_squads_expires ON polywar_faction_squads(expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_pressure_expires ON polywar_squad_pressure(season_id,expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_pressure_xy ON polywar_squad_pressure(season_id,x,y)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_pressure_faction ON polywar_squad_pressure(season_id,faction_id)",
            "CREATE INDEX IF NOT EXISTS idx_polywar_squad_ticks ON polywar_squad_ticks(season_id,tick_index,status)",
        ]: c.execute(sql)
        for spec in [
            "reinforcement_cooldown_minutes INTEGER NOT NULL DEFAULT 60",
            "reinforcement_hp INTEGER NOT NULL DEFAULT 50",
            "reinforcement_boost_minutes INTEGER NOT NULL DEFAULT 15",
            "reinforcement_min_remaining_minutes INTEGER NOT NULL DEFAULT 5",
            "reinforcement_energy_cost INTEGER NOT NULL DEFAULT 1",
            "reinforcement_return_radius INTEGER NOT NULL DEFAULT 6",
            "reinforcement_retry_minutes INTEGER NOT NULL DEFAULT 10",
            "reinforcement_batch_limit INTEGER NOT NULL DEFAULT 14",
        ]: _add_col(conn, "polywar_squad_season_config", spec)
        for spec in [
            "defeated_at TIMESTAMP NULL", "reinforcement_at TIMESTAMP NULL",
            "last_reinforced_at TIMESTAMP NULL", "reinforcement_count INTEGER NOT NULL DEFAULT 0",
            "reinforcement_boost_count INTEGER NOT NULL DEFAULT 0", "defeated_by_squad_id INTEGER NULL",
        ]: _add_col(conn, "polywar_faction_squads", spec)
        c.execute("CREATE INDEX IF NOT EXISTS idx_polywar_squad_reinforcement_due ON polywar_faction_squads(season_id,status,reinforcement_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_polywar_squad_faction_status ON polywar_faction_squads(season_id,faction_id,status)")
        logger.info("polywar_squad_schema_initialized")
        if own: conn.commit()
    finally:
        if own: conn.close()

def _snapshot_values(enabled: bool):
    max_hp=_setting_int('polywar_squad_max_hp',100,1,100000)
    cooldown=_setting_int('polywar_squad_reinforcement_cooldown_minutes',60,1,10080)
    boost=min(_setting_int('polywar_squad_reinforcement_boost_minutes',15,0,10080), cooldown)
    min_remaining=min(_setting_int('polywar_squad_reinforcement_min_remaining_minutes',5,0,10080), cooldown)
    return dict(enabled=1 if enabled else 0,
        spawn_interval_minutes=_setting_int('polywar_squad_spawn_interval_minutes',180,1,10080), move_interval_minutes=_setting_int('polywar_squad_move_interval_minutes',10,1,1440), max_active_per_faction=min(_setting_int('polywar_squad_max_active_per_faction',1,0,14),14), ttl_minutes=_setting_int('polywar_squad_ttl_minutes',720,1,43200), max_hp=max_hp, supply_distance=_setting_int('polywar_squad_supply_distance',24,1,10000), pressure_ttl_minutes=_setting_int('polywar_squad_pressure_ttl_minutes',360,1,43200), neutral_pressure_per_step=_setting_int('polywar_squad_neutral_pressure_per_step',100,0,1000), enemy_pressure_per_step=_setting_int('polywar_squad_enemy_pressure_per_step',15,0,1000), enemy_pressure_cap=_setting_int('polywar_squad_enemy_pressure_cap',60,0,100), capital_pressure_cap=_setting_int('polywar_squad_capital_pressure_cap',20,0,100), combat_damage_per_tick=_setting_int('polywar_squad_combat_damage_per_tick',20,0,100000), support_energy_cost=_setting_int('polywar_squad_support_energy_cost',1,0,1000), support_hp=_setting_int('polywar_squad_support_hp',25,0,100000), max_catchup_ticks=_setting_int('polywar_squad_max_catchup_ticks',6,1,100), reinforcement_cooldown_minutes=cooldown, reinforcement_hp=min(_setting_int('polywar_squad_reinforcement_hp',50,1,max_hp), max_hp), reinforcement_boost_minutes=boost, reinforcement_min_remaining_minutes=min_remaining, reinforcement_energy_cost=_setting_int('polywar_squad_reinforcement_energy_cost',1,0,1000000), reinforcement_return_radius=_setting_int('polywar_squad_reinforcement_return_radius',6,0,32), reinforcement_retry_minutes=_setting_int('polywar_squad_reinforcement_retry_minutes',10,1,1440), reinforcement_batch_limit=_setting_int('polywar_squad_reinforcement_batch_limit',14,1,HARD_ACTIVE_CAP))

def ensure_squad_season_config(conn, season_id:int, *, existing_active:Optional[bool]=None):
    c=conn.cursor(); row=_fetchone(c,'SELECT * FROM polywar_squad_season_config WHERE season_id=%s',(season_id,))
    if row: return row
    season=_fetchone(c,'SELECT * FROM polywar_seasons WHERE id=%s',(season_id,)) or {}
    enabled = _setting_bool('polywar_squads_enabled', True)
    if existing_active is None:
        existing_active = season.get('status') == 'active' and season.get('created_at')
    if existing_active: enabled = False
    vals=_snapshot_values(enabled); now=_now()
    params=(season_id, vals['enabled'], vals['spawn_interval_minutes'], vals['move_interval_minutes'], vals['max_active_per_faction'], vals['ttl_minutes'], vals['max_hp'], vals['supply_distance'], vals['pressure_ttl_minutes'], vals['neutral_pressure_per_step'], vals['enemy_pressure_per_step'], vals['enemy_pressure_cap'], vals['capital_pressure_cap'], vals['combat_damage_per_tick'], vals['support_energy_cost'], vals['support_hp'], vals['max_catchup_ticks'], vals['reinforcement_cooldown_minutes'], vals['reinforcement_hp'], vals['reinforcement_boost_minutes'], vals['reinforcement_min_remaining_minutes'], vals['reinforcement_energy_cost'], vals['reinforcement_return_radius'], vals['reinforcement_retry_minutes'], vals['reinforcement_batch_limit'], now, now)
    if _is_sqlite(conn):
        _execute(c,"""INSERT OR IGNORE INTO polywar_squad_season_config (season_id,enabled,config_version,spawn_interval_minutes,move_interval_minutes,max_active_per_faction,ttl_minutes,max_hp,supply_distance,pressure_ttl_minutes,neutral_pressure_per_step,enemy_pressure_per_step,enemy_pressure_cap,capital_pressure_cap,combat_damage_per_tick,support_energy_cost,support_hp,max_catchup_ticks,reinforcement_cooldown_minutes,reinforcement_hp,reinforcement_boost_minutes,reinforcement_min_remaining_minutes,reinforcement_energy_cost,reinforcement_return_radius,reinforcement_retry_minutes,reinforcement_batch_limit,created_at,updated_at) VALUES (%s,%s,2,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", params)
    else:
        _execute(c,"""INSERT INTO polywar_squad_season_config (season_id,enabled,config_version,spawn_interval_minutes,move_interval_minutes,max_active_per_faction,ttl_minutes,max_hp,supply_distance,pressure_ttl_minutes,neutral_pressure_per_step,enemy_pressure_per_step,enemy_pressure_cap,capital_pressure_cap,combat_damage_per_tick,support_energy_cost,support_hp,max_catchup_ticks,reinforcement_cooldown_minutes,reinforcement_hp,reinforcement_boost_minutes,reinforcement_min_remaining_minutes,reinforcement_energy_cost,reinforcement_return_radius,reinforcement_retry_minutes,reinforcement_batch_limit,created_at,updated_at) VALUES (%s,%s,2,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id) DO NOTHING""", params)
    row=_fetchone(c,'SELECT * FROM polywar_squad_season_config WHERE season_id=%s',(season_id,))
    if row:
        logger.info('polywar_squad_season_config_created season_id=%s enabled=%s', season_id, row.get('enabled'))
        return row
    raise RuntimeError('polywar_squad_config_unavailable')

def enable_squads_for_season(conn, season_id:int):
    ensure_squad_season_config(conn, season_id); _execute(conn.cursor(),'UPDATE polywar_squad_season_config SET enabled=1,updated_at=%s WHERE season_id=%s',(_now(),season_id))
def disable_squads_for_season(conn, season_id:int):
    ensure_squad_season_config(conn, season_id); _execute(conn.cursor(),'UPDATE polywar_squad_season_config SET enabled=0,updated_at=%s WHERE season_id=%s',(_now(),season_id))

def _passable(seed,x,y,config): return m.in_bounds_with_config(x,y,config) and m.TERRAIN_COSTS.get(m.terrain_at_with_config(seed,x,y,config)) is not None

def _spawn_cell(conn,sid,fid,seed,config):
    bx,by=config.bases.get(int(fid),(None,None))
    if bx is None: return None
    if _passable(seed,bx,by,config): return bx,by
    for nx,ny in ((bx+1,by),(bx-1,by),(bx,by+1),(bx,by-1)):
        if _passable(seed,nx,ny,config) and m.owner_at_with_config(conn,sid,nx,ny,config)==fid: return nx,ny
    for r in range(2,7):
        for dx in range(-r,r+1):
            for dy in (-(r-abs(dx)), r-abs(dx)):
                x,y=bx+dx,by+dy
                if _passable(seed,x,y,config) and m.owner_at_with_config(conn,sid,x,y,config)==fid: return x,y
    return None

def _choose_target(conn,sid,fid,x,y,config):
    order=_fetchone(conn.cursor(),"SELECT x,y FROM polywar_faction_orders WHERE season_id=%s AND faction_id=%s AND active=1 ORDER BY updated_at DESC, id DESC LIMIT 1",(sid,fid))
    if order and m.in_bounds_with_config(int(order['x']), int(order['y']), config): return int(order['x']), int(order['y'])
    best=None
    for ofid,(tx,ty) in config.bases.items():
        if int(ofid)!=int(fid):
            d=abs(tx-x)+abs(ty-y)
            if best is None or d<best[0] or (d==best[0] and ofid<best[1]): best=(d,ofid,tx,ty)
    return (best[2],best[3]) if best else (x,y)

def _lock_spawn_scope(conn, season_id:int, faction_id:int):
    if _is_sqlite(conn):
        return
    _fetchone(conn.cursor(), 'SELECT * FROM polywar_squad_season_config WHERE season_id=%s FOR UPDATE', (season_id,))
    _fetchone(conn.cursor(), 'SELECT * FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s FOR UPDATE', (season_id, faction_id))

def _insert_squad_row(conn, params):
    c=conn.cursor()
    if _is_sqlite(conn):
        before=conn.total_changes
        _execute(c,"""INSERT OR IGNORE INTO polywar_faction_squads (season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (%s,%s,%s,'marching',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s,%s)""", params)
        return conn.total_changes > before
    _execute(c,"""INSERT INTO polywar_faction_squads (season_id,faction_id,spawn_index,status,x,y,previous_x,previous_y,target_x,target_y,supply_x,supply_y,hp,max_hp,move_index,blocked_ticks,spawned_at,next_move_at,expires_at,created_at,updated_at) VALUES (%s,%s,%s,'marching',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s,%s) ON CONFLICT (season_id,faction_id,spawn_index) DO NOTHING""", params)
    return polywar._rowcount(c) == 1

def spawn_due_squads_in_transaction(conn, season_id:int, now=None, cfg=None, season=None, config=None):
    now=now or _now(); cfg=cfg or ensure_squad_season_config(conn, season_id)
    if not int(cfg['enabled']): return 0
    c=conn.cursor(); total=int((_fetchone(c,"SELECT COUNT(*) AS n FROM polywar_faction_squads WHERE season_id=%s AND status IN ('spawning','marching','engaged','waiting_for_supply','waiting_for_players','retreating','awaiting_reinforcement')",(season_id,)) or {}).get('n') or 0)
    if total>=HARD_ACTIVE_CAP: return 0
    season=season or _fetchone(c,'SELECT * FROM polywar_seasons WHERE id=%s',(season_id,)); seed=season.get('secret_seed','seed'); config=config or m.load_map_config(conn, season_id=season_id); spawned=0
    factions=_fetchall(c,"SELECT f.* FROM polywar_factions f WHERE COALESCE(f.is_playable,1)=1 AND COALESCE(f.is_system,0)=0 ORDER BY f.id")
    for f in factions:
        if total+spawned>=HARD_ACTIVE_CAP: break
        fid=int(f['id']); _lock_spawn_scope(conn, season_id, fid)
        members=int((_fetchone(c,'SELECT COUNT(*) AS n FROM polywar_players WHERE season_id=%s AND faction_id=%s',(season_id,fid)) or {}).get('n') or 0)
        if members<=0: continue
        active=int((_fetchone(c,"SELECT COUNT(*) AS n FROM polywar_faction_squads WHERE season_id=%s AND faction_id=%s AND status IN ('spawning','marching','engaged','waiting_for_supply','waiting_for_players','retreating','awaiting_reinforcement')",(season_id,fid)) or {}).get('n') or 0)
        if active>=int(cfg['max_active_per_faction']): continue
        last=_fetchone(c,'SELECT MAX(spawned_at) AS t, MAX(spawn_index) AS i FROM polywar_faction_squads WHERE season_id=%s AND faction_id=%s',(season_id,fid)) or {}
        if last.get('t') and _as_dt(last.get('t')) > now - timedelta(minutes=int(cfg['spawn_interval_minutes'])): continue
        cell=_spawn_cell(conn,season_id,fid,seed,config)
        if not cell: continue
        x,y=cell; tx,ty=_choose_target(conn,season_id,fid,x,y,config); idx=int(last.get('i') or 0)+1
        inserted=_insert_squad_row(conn,(season_id,fid,idx,x,y,x,y,tx,ty,x,y,int(cfg['max_hp']),int(cfg['max_hp']),now,now+timedelta(minutes=int(cfg['move_interval_minutes'])),now+timedelta(minutes=int(cfg['ttl_minutes'])),now,now))
        if not inserted: continue
        sidrow=_fetchone(c,'SELECT id FROM polywar_faction_squads WHERE season_id=%s AND faction_id=%s AND spawn_index=%s',(season_id,fid,idx))
        _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'squad_spawned',%s,%s)",(season_id,fid,f'Faction squad spawned at {x},{y}',now))
        logger.info('polywar_squad_spawned season_id=%s faction_id=%s squad_id=%s x=%s y=%s',season_id,fid,(sidrow or {}).get('id'),x,y); spawned+=1
    return spawned

def _hash(seed,*parts): return hashlib.sha256((':'.join(map(str,(seed,)+parts))).encode()).hexdigest()
def _capital_at(conn,sid,x,y):
    return _fetchone(conn.cursor(),'SELECT * FROM polywar_capitals WHERE season_id=%s AND x=%s AND y=%s',(sid,x,y))

def _choose_step(conn,squad,cfg,seed,config):
    x,y=int(squad['x']),int(squad['y']); tx,ty=int(squad.get('target_x') or x),int(squad.get('target_y') or y); fid=int(squad['faction_id'])
    rows=[]
    for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
        if not _passable(seed,nx,ny,config): continue
        cap=_capital_at(conn,int(squad['season_id']),nx,ny)
        if cap and int(cap.get('controller_faction_id') or 0)!=fid: continue
        other=_fetchone(conn.cursor(),"SELECT * FROM polywar_faction_squads WHERE season_id=%s AND x=%s AND y=%s AND status IN ('marching','spawning','engaged','waiting_for_supply','waiting_for_players','retreating') AND id<>%s",(squad['season_id'],nx,ny,squad['id']))
        if other and int(other['faction_id'])==fid: continue
        if other and int(other['faction_id'])!=fid: return ('engage',other)
        dist=abs(tx-nx)+abs(ty-ny); cur=abs(tx-x)+abs(ty-y); terr=m.terrain_at_with_config(seed,nx,ny,config); owner=m.owner_at_with_config(conn,int(squad['season_id']),nx,ny,config)
        score=(cur-dist)*100 + (8 if terr=='road' else 0) + (5 if owner==fid else 0) - (4 if terr=='mountain' else 0) - (3 if terr=='swamp' else 0)
        if int(squad.get('previous_x') or x)==nx and int(squad.get('previous_y') or y)==ny: score-=12
        if abs(nx-int(squad['supply_x']))+abs(ny-int(squad['supply_y']))>=int(cfg['supply_distance']): score-=1000
        tie=_hash(seed,squad['id'],squad['move_index'],nx,ny)
        rows.append((score,tie,nx,ny))
    if not rows: return ('wait',None)
    rows.sort(key=lambda r:(-r[0],r[1])); return ('move',rows[0][2:])

def _apply_pressure(conn,squad,cfg,now,config):
    c=conn.cursor(); sid=int(squad['season_id']); fid=int(squad['faction_id']); x=int(squad['x']); y=int(squad['y']); owner=m.owner_at_with_config(conn,sid,x,y,config); cap=_capital_at(conn,sid,x,y); amount=0; capv=100
    if cap: amount=int(cfg['enemy_pressure_per_step']); capv=int(cfg['capital_pressure_cap'])
    elif owner is None: amount=int(cfg['neutral_pressure_per_step']); capv=100
    elif int(owner)!=fid: amount=int(cfg['enemy_pressure_per_step']); capv=int(cfg['enemy_pressure_cap'])
    if amount<=0: return 0
    expires=now+timedelta(minutes=int(cfg['pressure_ttl_minutes']))
    row=_fetchone(c,'SELECT pressure FROM polywar_squad_pressure WHERE season_id=%s AND x=%s AND y=%s AND faction_id=%s',(sid,x,y,fid)); new=min(capv,int((row or {}).get('pressure') or 0)+amount)
    if _is_sqlite(conn):
        _execute(c,'INSERT OR REPLACE INTO polywar_squad_pressure (season_id,x,y,faction_id,pressure,source_squad_id,expires_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE((SELECT created_at FROM polywar_squad_pressure WHERE season_id=%s AND x=%s AND y=%s AND faction_id=%s),%s),%s)',(sid,x,y,fid,new,squad['id'],expires,sid,x,y,fid,now,now))
    else:
        _execute(c,'INSERT INTO polywar_squad_pressure (season_id,x,y,faction_id,pressure,source_squad_id,expires_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,x,y,faction_id) DO UPDATE SET pressure=EXCLUDED.pressure,source_squad_id=EXCLUDED.source_squad_id,expires_at=EXCLUDED.expires_at,updated_at=EXCLUDED.updated_at',(sid,x,y,fid,new,squad['id'],expires,now,now))
    return 1

def _tick_index_for(cfg, dt):
    # Exact due-time key: distinct next_move_at values inside one move interval must not collide.
    return int(_as_dt(dt).timestamp())

def _tick_time_for(cfg, tick_index):
    return datetime.utcfromtimestamp(int(tick_index))

def _claim_tick(conn, season_id:int, tick:int, scheduled_at, now, cfg):
    c=conn.cursor(); stale_before=now-timedelta(minutes=SQUAD_TICK_STALE_MINUTES)
    if _is_sqlite(conn):
        before=conn.total_changes
        _execute(c,"INSERT OR IGNORE INTO polywar_squad_ticks (season_id,tick_index,scheduled_at,started_at,status,created_at) VALUES (%s,%s,%s,%s,'processing',%s)",(season_id,tick,scheduled_at,now,now))
        if conn.total_changes>before: return {'claimed':True}
    else:
        row=_fetchone(c,"INSERT INTO polywar_squad_ticks (season_id,tick_index,scheduled_at,started_at,status,created_at) VALUES (%s,%s,%s,%s,'processing',%s) ON CONFLICT (season_id,tick_index) DO NOTHING RETURNING id",(season_id,tick,scheduled_at,now,now))
        if row: return {'claimed':True}
    suffix='' if _is_sqlite(conn) else ' FOR UPDATE'
    existing=_fetchone(c,'SELECT * FROM polywar_squad_ticks WHERE season_id=%s AND tick_index=%s'+suffix,(season_id,tick))
    if not existing: return {'claimed':False,'duplicate':True,'reason':'tick_conflict'}
    status=str(existing.get('status') or '')
    if status=='completed': return {'claimed':False,'duplicate':True,'reason':'tick_completed'}
    if status=='processing' and _as_dt(existing.get('started_at')) > stale_before:
        return {'claimed':False,'duplicate':True,'reason':'tick_processing'}
    if status in {'processing','failed'}:
        prior=json.loads(existing.get('outcome_json') or '{}') if existing.get('outcome_json') else {}
        prior.setdefault('recovered_from_status', status)
        _execute(c,"UPDATE polywar_squad_ticks SET status='processing',started_at=%s,outcome_json=%s WHERE season_id=%s AND tick_index=%s",(now,json.dumps(prior),season_id,tick))
        return {'claimed':True,'recovered':True,'prior_outcome':prior}
    return {'claimed':False,'duplicate':True,'reason':'tick_unavailable'}

def _lock_due_squads(conn, season_id:int, now):
    suffix='' if _is_sqlite(conn) else ' FOR UPDATE SKIP LOCKED'
    return _fetchall(conn.cursor(),"SELECT * FROM polywar_faction_squads WHERE season_id=%s AND status IN ('spawning','marching','engaged','waiting_for_supply','waiting_for_players','retreating','awaiting_reinforcement') AND next_move_at<=%s ORDER BY id LIMIT %s"+suffix,(season_id,now,HARD_ACTIVE_CAP))

def _locked_squads_by_ids(conn, ids):
    if not ids: return {}
    ids=sorted({int(i) for i in ids})
    ph=','.join(['%s']*len(ids)); suffix='' if _is_sqlite(conn) else ' FOR UPDATE'
    rows=_fetchall(conn.cursor(),f'SELECT * FROM polywar_faction_squads WHERE id IN ({ph}) ORDER BY id'+suffix,tuple(ids))
    return {int(r['id']):r for r in rows}

def mark_squad_awaiting_reinforcement_in_transaction(conn, squad, defeated_by_squad_id, cfg, now, scheduled_at=None):
    cur=_fetchone(conn.cursor(),'SELECT * FROM polywar_faction_squads WHERE id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(int(squad['id']),))
    if not cur or cur.get('status') in {'awaiting_reinforcement','destroyed','expired'}:
        return False
    reinf=now+timedelta(minutes=int(cfg.get('reinforcement_cooldown_minutes') or DEFAULTS['reinforcement_cooldown_minutes']))
    _execute(conn.cursor(),"""UPDATE polywar_faction_squads SET hp=0,status='awaiting_reinforcement',engaged_squad_id=NULL,defeated_at=%s,reinforcement_at=%s,next_move_at=%s,defeated_by_squad_id=%s,reinforcement_boost_count=0,updated_at=%s WHERE id=%s AND status NOT IN ('awaiting_reinforcement','destroyed','expired')""",(now,reinf,reinf,defeated_by_squad_id,now,cur['id']))
    if polywar._rowcount(conn.cursor()) != 0:
        pass
    _execute(conn.cursor(),"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'squad_defeated',%s,%s)",(cur['season_id'],cur['faction_id'],'Faction squad defeated',now))
    logger.info('polywar_squad_defeated season_id=%s squad_id=%s faction_id=%s enemy_squad_id=%s reinforcement_at=%s',cur['season_id'],cur['id'],cur['faction_id'],defeated_by_squad_id,reinf)
    return True

def _safe_return_cell(conn, squad, cfg, seed, config):
    sid=int(squad['season_id']); fid=int(squad['faction_id']); radius=int(cfg.get('reinforcement_return_radius') or 0)
    anchors=[(int(squad.get('supply_x') or squad['x']), int(squad.get('supply_y') or squad['y']))]
    if fid in config.bases: anchors.append(tuple(map(int, config.bases[fid])))
    seen=set()
    for ax,ay in anchors:
        candidates=[]
        for r in range(0, radius+1):
            for dx in range(-r,r+1):
                dy=r-abs(dx)
                for sy in ({dy,-dy} if dy else {0}):
                    x,y=ax+dx,ay+sy
                    if (x,y) in seen: continue
                    seen.add((x,y))
                    if not _passable(seed,x,y,config): continue
                    if m.owner_at_with_config(conn,sid,x,y,config)!=fid: continue
                    cap=_capital_at(conn,sid,x,y)
                    if cap and int(cap.get('original_faction_id') or 0)!=fid: continue
                    occ=_fetchone(conn.cursor(),"SELECT id FROM polywar_faction_squads WHERE season_id=%s AND x=%s AND y=%s AND status IN ('spawning','marching','engaged','waiting_for_supply','waiting_for_players','retreating') AND id<>%s",(sid,x,y,squad['id']))
                    if occ: continue
                    candidates.append((abs(x-ax)+abs(y-ay),x,y))
            if candidates:
                candidates.sort(key=lambda t:(t[0],t[1],t[2])); return candidates[0][1],candidates[0][2]
    return None

def process_due_reinforcements_in_transaction(conn, season_id, cfg, now, scheduled_at, season=None, config=None):
    suffix='' if _is_sqlite(conn) else ' FOR UPDATE SKIP LOCKED'
    rows=_fetchall(conn.cursor(),"SELECT * FROM polywar_faction_squads WHERE season_id=%s AND status='awaiting_reinforcement' AND reinforcement_at<=%s AND expires_at>%s ORDER BY reinforcement_at,id LIMIT %s"+suffix,(season_id,scheduled_at,now,int(cfg.get('reinforcement_batch_limit') or HARD_ACTIVE_CAP)))
    if not rows: return 0
    season=season or _fetchone(conn.cursor(),'SELECT * FROM polywar_seasons WHERE id=%s',(season_id,)) or {}
    seed=season.get('secret_seed','seed'); config=config or m.load_map_config(conn, season_id=season_id); done=0
    for sq in rows:
        if _as_dt(sq['expires_at']) <= now:
            _execute(conn.cursor(),"UPDATE polywar_faction_squads SET status='expired',updated_at=%s WHERE id=%s",(now,sq['id'])); continue
        cell=_safe_return_cell(conn,sq,cfg,seed,config)
        if not cell:
            retry=scheduled_at+timedelta(minutes=int(cfg.get('reinforcement_retry_minutes') or 10))
            _execute(conn.cursor(),"UPDATE polywar_faction_squads SET reinforcement_at=%s,next_move_at=%s,updated_at=%s WHERE id=%s",(retry,retry,now,sq['id']))
            _execute(conn.cursor(),"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'squad_reinforcement_delayed','Reinforcement delayed: no safe return cell',%s)",(season_id,sq['faction_id'],now))
            logger.info('polywar_squad_reinforcement_delayed season_id=%s squad_id=%s faction_id=%s',season_id,sq['id'],sq['faction_id']); continue
        x,y=cell; hp=min(int(sq['max_hp']), int(cfg.get('reinforcement_hp') or 50)); next_at=scheduled_at+timedelta(minutes=int(cfg['move_interval_minutes']))
        _execute(conn.cursor(),"""UPDATE polywar_faction_squads SET x=%s,y=%s,previous_x=%s,previous_y=%s,hp=%s,status='marching',engaged_squad_id=NULL,defeated_by_squad_id=NULL,last_reinforced_at=%s,reinforcement_at=NULL,reinforcement_count=reinforcement_count+1,reinforcement_boost_count=0,next_move_at=%s,updated_at=%s WHERE id=%s AND status='awaiting_reinforcement'""",(x,y,x,y,hp,now,next_at,now,sq['id']))
        _execute(conn.cursor(),"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'squad_reinforced','Faction squad reinforced',%s)",(season_id,sq['faction_id'],now))
        logger.info('polywar_squad_reinforced season_id=%s squad_id=%s faction_id=%s x=%s y=%s hp=%s reinforcement_count=%s',season_id,sq['id'],sq['faction_id'],x,y,hp,int(sq.get('reinforcement_count') or 0)+1); done+=1
    return done

def _resolve_engaged_pair(conn, a, cfg, now, step_base=None):
    b_id=a.get('engaged_squad_id')
    if not b_id: return False
    locked=_locked_squads_by_ids(conn,[a['id'],b_id]); a2=locked.get(int(a['id'])); b2=locked.get(int(b_id))
    if not a2 or not b2: return False
    if a2.get('status')!='engaged' or b2.get('status')!='engaged': return False
    if int(a2.get('engaged_squad_id') or 0)!=int(b2['id']) or int(b2.get('engaged_squad_id') or 0)!=int(a2['id']): return False
    if int(a2['hp'])<=0 or int(b2['hp'])<=0: return False
    dmg=int(cfg['combat_damage_per_tick']); ahp=max(0,int(a2['hp'])-dmg); bhp=max(0,int(b2['hp'])-dmg); next_at=(step_base or now)+timedelta(minutes=int(cfg['move_interval_minutes']))
    if ahp<=0: mark_squad_awaiting_reinforcement_in_transaction(conn,a2,b2['id'],cfg,now,step_base)
    else: _execute(conn.cursor(),"UPDATE polywar_faction_squads SET hp=%s,status=%s,engaged_squad_id=%s,next_move_at=%s,updated_at=%s WHERE id=%s",(ahp,'marching' if bhp<=0 else 'engaged',None if bhp<=0 else int(b2['id']),next_at,now,a2['id']))
    if bhp<=0: mark_squad_awaiting_reinforcement_in_transaction(conn,b2,a2['id'],cfg,now,step_base)
    else: _execute(conn.cursor(),"UPDATE polywar_faction_squads SET hp=%s,status=%s,engaged_squad_id=%s,next_move_at=%s,updated_at=%s WHERE id=%s",(bhp,'marching' if ahp<=0 else 'engaged',None if ahp<=0 else int(a2['id']),next_at,now,b2['id']))
    return True


def _repair_broken_engagement(conn, squad, cfg, now, step_base=None):
    locked=_locked_squads_by_ids(conn,[squad['id'], squad.get('engaged_squad_id') or -1])
    cur=locked.get(int(squad['id']))
    if not cur:
        return False
    if int(cur.get('hp') or 0)<=0:
        return mark_squad_awaiting_reinforcement_in_transaction(conn, cur, None, cfg, now, step_base)
    enemy=locked.get(int(cur.get('engaged_squad_id') or -1))
    valid=bool(enemy and enemy.get('status')=='engaged' and int(enemy.get('hp') or 0)>0 and int(enemy.get('engaged_squad_id') or 0)==int(cur['id']) and cur.get('status')=='engaged')
    if valid:
        return False
    next_at=(step_base or now)+timedelta(minutes=int(cfg['move_interval_minutes']))
    _execute(conn.cursor(),"UPDATE polywar_faction_squads SET status='marching',engaged_squad_id=NULL,next_move_at=%s,updated_at=%s WHERE id=%s AND status='engaged'",(next_at,now,cur['id']))
    if enemy and int(enemy.get('hp') or 0)<=0 and enemy.get('status') not in {'destroyed','expired','awaiting_reinforcement'}:
        mark_squad_awaiting_reinforcement_in_transaction(conn, enemy, cur['id'], cfg, now, step_base)
    return True

def process_squad_tick_in_transaction(conn, season_id:int, now=None, scheduled_at=None):
    now=now or _now(); cfg=ensure_squad_season_config(conn, season_id); c=conn.cursor()
    if not int(cfg['enabled']):
        return {'processed':False,'reason':'squads_disabled','spawned_count':0,'moved_count':0,'combat_count':0,'pressure_count':0}
    season=_fetchone(c,'SELECT * FROM polywar_seasons WHERE id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
    if not season or season.get('status')!='active': return {'processed':False,'reason':'season_inactive','spawned_count':0,'moved_count':0,'combat_count':0,'pressure_count':0}
    if season.get('ends_at') and _as_dt(season.get('ends_at')) <= now: return {'processed':False,'reason':'season_ended','spawned_count':0,'moved_count':0,'combat_count':0,'pressure_count':0}
    scheduled_at=scheduled_at or now; tick=_tick_index_for(cfg, scheduled_at); next_due_at=scheduled_at+timedelta(minutes=int(cfg['move_interval_minutes']))
    claim=_claim_tick(conn,season_id,tick,scheduled_at,now,cfg)
    if not claim.get('claimed'):
        return {'processed':False,'duplicate':claim.get('duplicate',True),'reason':claim.get('reason','duplicate_tick'),'spawned_count':0,'moved_count':0,'combat_count':0,'pressure_count':0}
    logger.info('polywar_squad_tick_started season_id=%s tick_index=%s',season_id,tick)
    spawned=moved=combat=pressure=0; processed=[]; processed_pairs=set()
    seed=season.get('secret_seed','seed'); config=m.load_map_config(conn, season_id=season_id)
    spawned=spawn_due_squads_in_transaction(conn,season_id,now,cfg=cfg,season=season,config=config)
    reinforced=process_due_reinforcements_in_transaction(conn,season_id,cfg,now,scheduled_at,season=season,config=config)
    due=_lock_due_squads(conn,season_id,scheduled_at)
    for snap in due:
        s=_fetchone(c,'SELECT * FROM polywar_faction_squads WHERE id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(snap['id'],))
        if not s or s.get('status') not in ACTIVE_STATUSES: continue
        if s.get('next_move_at') and _as_dt(s.get('next_move_at')) > scheduled_at: continue
        processed.append(int(s['id']))
        if _as_dt(s['expires_at']) <= now: _execute(c,"UPDATE polywar_faction_squads SET status='expired',updated_at=%s WHERE id=%s AND status<>'destroyed'",(now,s['id'])); continue
        if int(s['hp'])<=0: mark_squad_awaiting_reinforcement_in_transaction(conn,s,None,cfg,now,scheduled_at); continue
        if s['status']=='engaged':
            if not s.get('engaged_squad_id'):
                _repair_broken_engagement(conn,s,cfg,now,scheduled_at); continue
            pair=tuple(sorted((int(s['id']),int(s['engaged_squad_id']))))
            if pair in processed_pairs: continue
            processed_pairs.add(pair)
            if _resolve_engaged_pair(conn,s,cfg,now,scheduled_at): combat+=1
            else: _repair_broken_engagement(conn,s,cfg,now,scheduled_at)
            continue
        if abs(int(s['x'])-int(s['supply_x']))+abs(int(s['y'])-int(s['supply_y']))>=int(cfg['supply_distance']):
            _execute(c,"UPDATE polywar_faction_squads SET status='waiting_for_supply',next_move_at=%s,updated_at=%s WHERE id=%s",(next_due_at,now,s['id'])); logger.info('polywar_squad_waiting_for_supply season_id=%s squad_id=%s',season_id,s['id']); continue
        kind,val=_choose_step(conn,s,cfg,seed,config)
        if kind=='engage':
            o=val; locked=_locked_squads_by_ids(conn,[s['id'],o['id']]); s2=locked.get(int(s['id'])); o2=locked.get(int(o['id']))
            if not s2 or not o2 or s2.get('status') not in ACTIVE_STATUSES or o2.get('status') not in ACTIVE_STATUSES: continue
            _execute(c,"UPDATE polywar_faction_squads SET status='engaged',engaged_squad_id=%s,next_move_at=%s,updated_at=%s WHERE id=%s",(o2['id'],next_due_at,now,s2['id']))
            _execute(c,"UPDATE polywar_faction_squads SET status='engaged',engaged_squad_id=%s,next_move_at=%s,updated_at=%s WHERE id=%s",(s2['id'],next_due_at,now,o2['id']))
            logger.info('polywar_squad_engaged season_id=%s squad_id=%s enemy_squad_id=%s',season_id,s2['id'],o2['id']); continue
        if kind=='move':
            nx,ny=val; _execute(c,"UPDATE polywar_faction_squads SET previous_x=x,previous_y=y,x=%s,y=%s,status='marching',move_index=move_index+1,blocked_ticks=0,last_moved_at=%s,next_move_at=%s,updated_at=%s WHERE id=%s AND status<>'destroyed'",(nx,ny,now,next_due_at,now,s['id'])); ns=dict(s); ns.update({'x':nx,'y':ny}); pressure+=_apply_pressure(conn,ns,cfg,now,config); moved+=1; logger.info('polywar_squad_moved season_id=%s squad_id=%s x=%s y=%s',season_id,s['id'],nx,ny)
        else:
            _execute(c,"UPDATE polywar_faction_squads SET status='waiting_for_players',blocked_ticks=blocked_ticks+1,next_move_at=%s,updated_at=%s WHERE id=%s",(next_due_at,now,s['id']))
    cleaned=cleanup_expired_pressure_in_transaction(conn,season_id,now)
    outcome={**(claim.get('prior_outcome') or {}),'processed_squad_ids':processed,'processed_pairs':[list(p) for p in processed_pairs],'expired_pressure':cleaned,'reinforced_count':reinforced,'recovered':bool(claim.get('recovered'))}
    _execute(c,"UPDATE polywar_squad_ticks SET status='completed',processed_at=%s,spawned_count=%s,moved_count=%s,combat_count=%s,pressure_count=%s,outcome_json=%s WHERE season_id=%s AND tick_index=%s",(now,spawned,moved,combat,pressure,json.dumps(outcome),season_id,tick))
    logger.info('polywar_squad_tick_completed season_id=%s tick_index=%s spawned=%s moved=%s combat=%s pressure=%s',season_id,tick,spawned,moved,combat,pressure)
    return {'processed':True,'spawned_count':spawned,'moved_count':moved,'combat_count':combat,'pressure_count':pressure,'tick_index':tick}

def ensure_squads_caught_up_in_transaction(conn, season_id:int, now=None):
    now=now or _now(); cfg=ensure_squad_season_config(conn, season_id); total={'processed_count':0,'spawned_count':0,'moved_count':0,'combat_count':0,'pressure_count':0,'results':[]}
    if not int(cfg['enabled']):
        cleanup_expired_pressure_in_transaction(conn,season_id,now)
        total.update({'processed':False,'reason':'squads_disabled'}); return total
    for _ in range(int(cfg['max_catchup_ticks'])):
        earliest=_fetchone(conn.cursor(),"SELECT MIN(next_move_at) AS due_at FROM polywar_faction_squads WHERE season_id=%s AND status IN ('spawning','marching','engaged','waiting_for_supply','waiting_for_players','retreating','awaiting_reinforcement')",(season_id,)) or {}
        due_at=_as_dt(earliest['due_at']) if earliest.get('due_at') else None
        if due_at and due_at>now:
            if total['processed_count']==0: total.update({'processed':False,'reason':'nothing_due'})
            break
        scheduled=due_at or now
        out=process_squad_tick_in_transaction(conn,season_id,now,scheduled_at=scheduled); total['results'].append(out)
        if out.get('processed'):
            total['processed_count']+=1
            for k in ('spawned_count','moved_count','combat_count','pressure_count'): total[k]+=int(out.get(k) or 0)
            continue
        reason=out.get('reason')
        if total['processed_count']==0: total.update({'processed':False,'reason':reason or 'nothing_due'})
        if reason in {'tick_completed','duplicate_tick','tick_processing'}:
            break
        break
    if total['processed_count']==0 and 'reason' not in total:
        total.update({'processed':False,'reason':'nothing_due'})
    return total

def cleanup_expired_pressure_in_transaction(conn, season_id:int, now=None):
    now=now or _now(); c=conn.cursor(); rows=_fetchall(c,'SELECT season_id,x,y,faction_id FROM polywar_squad_pressure WHERE season_id=%s AND expires_at<=%s ORDER BY expires_at LIMIT %s',(season_id,now,PRESSURE_CLEANUP_BATCH))
    for r in rows: _execute(c,'DELETE FROM polywar_squad_pressure WHERE season_id=%s AND x=%s AND y=%s AND faction_id=%s',(r['season_id'],r['x'],r['y'],r['faction_id']))
    if rows: logger.info('polywar_squad_pressure_expired season_id=%s count=%s',season_id,len(rows))
    return len(rows)

def refresh_supply_after_capture_in_transaction(conn, season_id:int, faction_id:int, x:int, y:int, radius:int=6):
    now=_now(); _execute(conn.cursor(),"UPDATE polywar_faction_squads SET supply_x=%s,supply_y=%s,status=CASE WHEN status='waiting_for_supply' THEN 'marching' ELSE status END,updated_at=%s WHERE season_id=%s AND faction_id=%s AND status IN ('marching','waiting_for_supply','waiting_for_players') AND ABS(x - %s) + ABS(y - %s) <= %s",(x,y,now,season_id,faction_id,x,y,radius))

def visible_squads(user_id:int, min_x:int, min_y:int, max_x:int, max_y:int):
    conn=polywar.get_connection()
    try:
        m.begin_polywar_readonly(conn); season=m.get_active_season_readonly(conn); sid=int(season['id']); config=m.load_map_config(conn, season=season)
        min_x=max(0,min(config.width-1,int(min_x))); max_x=max(0,min(config.width-1,int(max_x))); min_y=max(0,min(config.height-1,int(min_y))); max_y=max(0,min(config.height-1,int(max_y)))
        if max_x<min_x: min_x,max_x=max_x,min_x
        if max_y<min_y: min_y,max_y=max_y,min_y
        if (max_x-min_x+1)*(max_y-min_y+1)>20000: raise ValueError('bounds_too_large')
        c=conn.cursor(); cfg=_fetchone(c,'SELECT enabled,support_energy_cost,support_hp,reinforcement_energy_cost,reinforcement_boost_minutes,reinforcement_min_remaining_minutes,reinforcement_hp FROM polywar_squad_season_config WHERE season_id=%s',(sid,)) or {'enabled':0,'support_energy_cost':1,'support_hp':25,'reinforcement_energy_cost':1,'reinforcement_boost_minutes':15,'reinforcement_min_remaining_minutes':5,'reinforcement_hp':50}
        cost = cfg.get('support_energy_cost') if cfg.get('support_energy_cost') is not None else 1
        if not int(cfg.get('enabled') or 0):
            return {'ok':True,'season_id':sid,'server_timestamp':int(time.time()),'squads_enabled':False,'squad_rules':{'support_energy_cost':int(cost),'support_hp':int(cfg.get('support_hp') or 25),'reinforcement_energy_cost':int(cfg.get('reinforcement_energy_cost') if cfg.get('reinforcement_energy_cost') is not None else 1),'reinforcement_boost_minutes':int(cfg.get('reinforcement_boost_minutes') or 15),'reinforcement_min_remaining_minutes':int(cfg.get('reinforcement_min_remaining_minutes') or 5),'reinforcement_hp':int(cfg.get('reinforcement_hp') or 50)},'support_energy_cost':int(cost),'squads':[],'pressure':[]}
        squads=_fetchall(c,"SELECT id,faction_id,x,y,previous_x,previous_y,supply_x,supply_y,hp,max_hp,status,target_x,target_y,next_move_at,defeated_at,reinforcement_at,reinforcement_count,reinforcement_boost_count,expires_at FROM polywar_faction_squads WHERE season_id=%s AND x BETWEEN %s AND %s AND y BETWEEN %s AND %s AND status IN ('spawning','marching','engaged','waiting_for_supply','waiting_for_players','retreating','awaiting_reinforcement') ORDER BY id LIMIT 200",(sid,min_x,max_x,min_y,max_y))
        pressure=_fetchall(c,'SELECT x,y,faction_id,pressure,expires_at FROM polywar_squad_pressure WHERE season_id=%s AND x BETWEEN %s AND %s AND y BETWEEN %s AND %s AND expires_at>%s ORDER BY x,y,faction_id LIMIT 1000',(sid,min_x,max_x,min_y,max_y,_now()))
        return {'ok':True,'season_id':sid,'server_timestamp':int(time.time()),'squads_enabled':True,'squad_rules':{'support_energy_cost':int(cost),'support_hp':int(cfg.get('support_hp') or 25),'reinforcement_energy_cost':int(cfg.get('reinforcement_energy_cost') if cfg.get('reinforcement_energy_cost') is not None else 1),'reinforcement_boost_minutes':int(cfg.get('reinforcement_boost_minutes') or 15),'reinforcement_min_remaining_minutes':int(cfg.get('reinforcement_min_remaining_minutes') or 5),'reinforcement_hp':int(cfg.get('reinforcement_hp') or 50)},'support_energy_cost':int(cost),'squads':[{**dict(r),'next_move_at':_iso(r.get('next_move_at')),'defeated_at':_iso(r.get('defeated_at')),'reinforcement_at':_iso(r.get('reinforcement_at')),'reinforcement_seconds_remaining':(max(0,int((_as_dt(r.get('reinforcement_at'))-_now()).total_seconds())) if r.get('reinforcement_at') else None),'expires_at':_iso(r.get('expires_at'))} for r in squads],'pressure':[{**dict(r),'expires_at':_iso(r.get('expires_at'))} for r in pressure]}
    finally: conn.close()

def support_squad(user_id:int, squad_id:int, idempotency_key:str, support_type:str="auto"):
    if not idempotency_key or len(str(idempotency_key))>120: raise ValueError('bad_idempotency_key')
    support_type=(support_type or 'auto').strip().lower()
    if support_type not in {'auto','heal','reinforcement'}: raise ValueError('bad_support_type')
    from services import polywar_mine_service as mines
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        if _is_sqlite(conn): c.execute('BEGIN IMMEDIATE')
        else: _execute(c,'BEGIN')
        season=m.get_active_season_readonly(conn); sid=int(season['id']); now=_now()
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: conn.commit(); return dup
        prepared=polywar.prepare_gameplay_mutation_in_transaction(conn,sid,now)
        if not prepared.get('ok'): raise ValueError(prepared.get('error') or 'season_ended')
        cfg=ensure_squad_season_config(conn,sid)
        if not int(cfg['enabled']): raise ValueError('squads_disabled')
        polywar._insert_player_if_missing(conn,int(user_id),sid)
        player=_fetchone(c,'SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(user_id,sid))
        fid=player.get('faction_id') if player else None
        if not fid: raise ValueError('faction_required')
        e=polywar._energy(player)
        if e.get('is_locked'): raise ValueError('player_locked')
        sq=_fetchone(c,"SELECT * FROM polywar_faction_squads WHERE id=%s AND season_id=%s" + ('' if _is_sqlite(conn) else ' FOR UPDATE'),(squad_id,sid))
        if not sq: raise ValueError('squad_not_found')
        if int(sq['faction_id'])!=int(fid): raise ValueError('squad_not_allied')
        if sq['status'] in {'destroyed','expired'}: raise ValueError('squad_state_changed')
        if _as_dt(sq['expires_at']) <= now: raise ValueError('squad_expired')
        mode = 'reinforcement' if sq['status']=='awaiting_reinforcement' else 'heal' if sq['status'] in ACTIVE_STATUSES and int(sq.get('hp') or 0)>0 else None
        if support_type!='auto' and support_type!=mode: raise ValueError('squad_state_changed')
        if not mode: raise ValueError('squad_state_changed')
        if mode=='heal':
            cost=int(cfg.get('support_energy_cost') if cfg.get('support_energy_cost') is not None else 1); _,_,energy=mines.spend_player_energy(conn,player,cost,now); hp=min(int(sq['max_hp']), int(sq['hp'])+int(cfg['support_hp']))
            _execute(c,'UPDATE polywar_faction_squads SET hp=%s,updated_at=%s WHERE id=%s',(hp,now,squad_id))
            event_type='squad_supported'; message=f'Squad supported at {sq["x"]},{sq["y"]}'
            payload={'ok':True,'duplicate':False,'support_type':'heal','energy_cost':cost,'healed_hp':min(int(cfg['support_hp']), hp-int(sq['hp'])),'squad':{'id':squad_id,'status':sq['status'],'hp':hp,'max_hp':int(sq['max_hp'])},'energy':energy}
        else:
            if not sq.get('reinforcement_at'): raise ValueError('reinforcement_not_available')
            previous=_as_dt(sq['reinforcement_at']); boost=int(cfg.get('reinforcement_boost_minutes') if cfg.get('reinforcement_boost_minutes') is not None else 15); minimum=now+timedelta(minutes=int(cfg.get('reinforcement_min_remaining_minutes') if cfg.get('reinforcement_min_remaining_minutes') is not None else 5)); new_at=max(previous-timedelta(minutes=boost), minimum)
            if new_at >= previous: raise ValueError('reinforcement_already_due')
            cost=int(cfg.get('reinforcement_energy_cost') if cfg.get('reinforcement_energy_cost') is not None else 1); _,_,energy=mines.spend_player_energy(conn,player,cost,now)
            _execute(c,'UPDATE polywar_faction_squads SET reinforcement_at=%s,next_move_at=%s,reinforcement_boost_count=reinforcement_boost_count+1,updated_at=%s WHERE id=%s',(new_at,new_at,now,squad_id))
            event_type='squad_reinforcement_boosted'; message='Reinforcement accelerated'
            payload={'ok':True,'duplicate':False,'support_type':'reinforcement','energy_cost':cost,'boost_minutes':boost,'previous_reinforcement_at':_iso(previous),'reinforcement_at':_iso(new_at),'reinforcement_seconds_remaining':max(0,int((new_at-now).total_seconds())),'squad':{'id':squad_id,'status':'awaiting_reinforcement','hp':0,'max_hp':int(sq['max_hp'])},'energy':energy}
            logger.info('polywar_squad_reinforcement_boosted season_id=%s squad_id=%s faction_id=%s reinforcement_at=%s energy_cost=%s',sid,squad_id,fid,new_at,cost)
        _execute(c,"INSERT INTO polywar_actions (season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,'support_squad',%s,%s,%s,%s,%s)",(sid,user_id,fid,sq['x'],sq['y'],cost,idempotency_key,now))
        _execute(c,"INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)",(sid,user_id,fid,event_type,message,now))
        mines.insert_outcome(conn,sid,user_id,idempotency_key,'support_squad',int(sq['x']),int(sq['y']),event_type,cost,payload,now)
        conn.commit(); return payload
    except ValueError:
        polywar._safe_rollback(conn); raise
    except Exception:
        polywar._safe_rollback(conn); logger.exception('polywar_squad_support_failed'); raise
    finally: conn.close()

def run_squad_maintenance_once(now=None):
    conn=polywar.get_connection(); ok=False; now=now or _now()
    try:
        c=conn.cursor(); polywar.begin_serialized_transaction(conn)
        season=_fetchone(c,"SELECT * FROM polywar_seasons WHERE status=%s ORDER BY starts_at DESC LIMIT 1"+('' if _is_sqlite(conn) else ' FOR UPDATE'),('active',))
        if not season:
            conn.commit(); ok=True; return {'ok':True,'processed':False,'reason':'no_active_season'}
        from services.polywar_world_service import ensure_world_initialized_in_transaction, ensure_world_caught_up_in_transaction
        ensure_world_initialized_in_transaction(conn, int(season['id']))
        ensure_world_caught_up_in_transaction(conn, int(season['id']), now)
        out=ensure_squads_caught_up_in_transaction(conn,int(season['id']),now)
        conn.commit(); ok=True; return {'ok':True,'season_id':int(season['id']),**out}
    finally:
        if not ok: polywar._safe_rollback(conn)
        conn.close()
