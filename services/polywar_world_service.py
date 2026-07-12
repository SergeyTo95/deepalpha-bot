import hashlib, json, logging, time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from services import polywar_service as polywar
from services import polywar_map_service as m
from services import polywar_sector_service as sectors

logger=logging.getLogger(__name__)
NULL_STATE_FACTION_ID=8
NULL_STATE_NAME='The Null State'

def _now(): return datetime.utcnow()
def _setting_int(k,d,lo,hi): return polywar._setting_int(k,d,lo,hi)
def enabled(): return str(polywar.get_setting('polywar_null_state_enabled','true')).lower() not in {'0','false','off','no'}
def rift_count(): return _setting_int('polywar_null_rift_count',4,1,32)
def rift_health(): return _setting_int('polywar_null_rift_health',1000,1,1000000)
def activation_hours(): return _setting_int('polywar_null_activation_hours',72,0,24*365)
def tick_minutes(): return _setting_int('polywar_null_tick_minutes',10,1,24*60)
def max_catchup_ticks(): return _setting_int('polywar_null_max_catchup_ticks',6,1,100)
def expansions_per_tick(): return _setting_int('polywar_null_expansions_per_tick',4,0,1000)
def seal_cost(): return _setting_int('polywar_null_rift_seal_energy_cost',3,0,1000)
def seal_progress(): return _setting_int('polywar_null_rift_seal_progress',100,1,1000000)
def capital_siege_per_tick(): return _setting_int('polywar_null_capital_siege_per_tick',50,1,100000)
def min_distance(): return _setting_int('polywar_null_rift_min_distance',500,0,100000)

def _is_sqlite(conn): return polywar._is_sqlite(conn)
def _execute(c,sql,params=()): return polywar._execute(c,sql,params)
def _fetchone(c,sql,params=()): return polywar._fetchone(c,sql,params)
def _fetchall(c,sql,params=()): return polywar._fetchall(c,sql,params)

def begin_world_transaction(conn):
    c=conn.cursor()
    if _is_sqlite(conn):
        last=None
        for i in range(20):
            try:
                c.execute('BEGIN IMMEDIATE'); return c
            except Exception as exc:
                if 'locked' not in str(exc).lower(): raise
                last=exc; time.sleep(0.025*(i+1))
        raise last
    _execute(c,'BEGIN')
    return c


def _start_world_transaction(conn):
    begin_world_transaction(conn)
    return True

def _finish_world_transaction(conn, managed, ok=True):
    if not managed:
        return
    if ok:
        conn.commit()
    else:
        polywar._safe_rollback(conn)

def lock_world_rows(conn, season_id:int):
    c=conn.cursor()
    suffix='' if _is_sqlite(conn) else ' FOR UPDATE'
    season=_fetchone(c,'SELECT * FROM polywar_seasons WHERE id=%s'+suffix,(season_id,))
    state=_fetchone(c,'SELECT * FROM polywar_null_state WHERE season_id=%s'+suffix,(season_id,))
    return season,state

def init_world_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor(); id_sql='INTEGER PRIMARY KEY AUTOINCREMENT' if _is_sqlite(conn) else 'SERIAL PRIMARY KEY'
    try:
        _add_polywar_columns(conn)
        try: sectors.init_polywar_sector_schema(conn)
        except Exception: logger.exception('polywar_sector_schema_init_failed'); raise
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_null_state (season_id INTEGER PRIMARY KEY,status TEXT NOT NULL,activation_at TIMESTAMP NOT NULL,activated_at TIMESTAMP NULL,next_tick_at TIMESTAMP NOT NULL,last_tick_at TIMESTAMP NULL,tick_index BIGINT NOT NULL DEFAULT 0,corruption_level INTEGER NOT NULL DEFAULT 0,controlled_cells_count INTEGER NOT NULL DEFAULT 0,controlled_sectors_count INTEGER NOT NULL DEFAULT 0,controlled_capitals_count INTEGER NOT NULL DEFAULT 0,defeated_at TIMESTAMP NULL,created_at TIMESTAMP NOT NULL,updated_at TIMESTAMP NOT NULL)""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_null_rifts (id {id_sql},season_id INTEGER NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,status TEXT NOT NULL,health INTEGER NOT NULL,max_health INTEGER NOT NULL,spawned_at TIMESTAMP NOT NULL,last_action_at TIMESTAMP NULL,sealed_at TIMESTAMP NULL,sealed_by_user_id BIGINT NULL,sealed_by_faction_id INTEGER NULL,created_at TIMESTAMP NOT NULL,updated_at TIMESTAMP NOT NULL,UNIQUE(season_id,x,y))""")
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_null_frontier (season_id INTEGER NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,source_rift_id INTEGER NULL,discovered_at TIMESTAMP NOT NULL,priority INTEGER NOT NULL DEFAULT 0,UNIQUE(season_id,x,y))""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_world_ticks (id {id_sql},season_id INTEGER NOT NULL,tick_index BIGINT NOT NULL,scheduled_at TIMESTAMP NOT NULL,started_at TIMESTAMP NOT NULL,processed_at TIMESTAMP NULL,status TEXT NOT NULL,actions_count INTEGER NOT NULL DEFAULT 0,outcome_json TEXT NULL,created_at TIMESTAMP NOT NULL,UNIQUE(season_id,tick_index))""")
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_rift_contributions (season_id INTEGER NOT NULL,rift_id INTEGER NOT NULL,user_id BIGINT NOT NULL,faction_id INTEGER NOT NULL,contribution INTEGER NOT NULL DEFAULT 0,first_contributed_at TIMESTAMP NOT NULL,last_contributed_at TIMESTAMP NOT NULL,UNIQUE(rift_id,user_id))""")
        for sql in ['CREATE INDEX IF NOT EXISTS idx_polywar_null_state_due ON polywar_null_state(status,next_tick_at)','CREATE INDEX IF NOT EXISTS idx_polywar_null_rifts_status ON polywar_null_rifts(season_id,status)','CREATE INDEX IF NOT EXISTS idx_polywar_null_rifts_xy ON polywar_null_rifts(season_id,x,y)','CREATE INDEX IF NOT EXISTS idx_polywar_null_frontier_lookup ON polywar_null_frontier(season_id,priority,x,y)','CREATE INDEX IF NOT EXISTS idx_polywar_world_ticks ON polywar_world_ticks(season_id,tick_index,status)']:
            c.execute(sql)
        if own: conn.commit()
    finally:
        if own: conn.close()

def _add_col(conn,table,spec):
    c=conn.cursor(); name=spec.split()[0]
    try:
        if _is_sqlite(conn):
            cols=[r[1] for r in c.execute(f'PRAGMA table_info({table})').fetchall()]
            if name not in cols: c.execute(f'ALTER TABLE {table} ADD COLUMN {spec}')
        else: c.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {spec}')
    except Exception: logger.exception('polywar_world_add_column_failed table=%s col=%s',table,name); raise

def _add_polywar_columns(conn):
    _add_col(conn,'polywar_factions','is_playable INTEGER NOT NULL DEFAULT 1'); _add_col(conn,'polywar_factions','is_system INTEGER NOT NULL DEFAULT 0')
    for spec in ['winner_faction_id INTEGER NULL','victory_type TEXT NULL','finalization_started_at TIMESTAMP NULL','finalized_at TIMESTAMP NULL','domination_faction_id INTEGER NULL','domination_started_at TIMESTAMP NULL','results_hash TEXT NULL','finalization_version INTEGER NOT NULL DEFAULT 1']:
        _add_col(conn,'polywar_seasons',spec)

def ensure_null_faction(conn,season_id:int):
    c=conn.cursor(); now=_now()
    _execute(c,"""INSERT INTO polywar_factions (id,name,slug,color,description,is_playable,is_system,created_at) VALUES (%s,%s,%s,%s,%s,0,1,%s) ON CONFLICT (id) DO NOTHING""",(NULL_STATE_FACTION_ID,NULL_STATE_NAME,'null-state','void','A system NPC faction spreading from deterministic rifts.',now))
    _execute(c,"UPDATE polywar_factions SET is_playable=0,is_system=1 WHERE id=%s",(NULL_STATE_FACTION_ID,))
    _execute(c,"""INSERT INTO polywar_faction_season_stats (season_id,faction_id,influence_score,active_members_count,controlled_cells_count,controlled_sectors_count,created_at,updated_at) VALUES (%s,%s,0,0,0,0,%s,%s) ON CONFLICT (season_id,faction_id) DO NOTHING""",(season_id,NULL_STATE_FACTION_ID,now,now))

def _digest(seed,*parts): return hashlib.sha256((':'.join([str(seed),*map(str,parts)])).encode()).digest()
def _too_close(x,y,coords,dist): return any((x-a)*(x-a)+(y-b)*(y-b)<dist*dist for a,b in coords)
def _valid_rift(seed,x,y,coords):
    if not m.in_bounds(x,y) or m.TERRAIN_COSTS.get(m.terrain_at(seed,x,y)) is None: return False
    radius=m.starting_area_size()
    for bx,by in m.faction_base_positions().values():
        if (x,y)==(bx,by) or (abs(x-bx)<=radius and abs(y-by)<=radius): return False
    return not _too_close(x,y,coords,min_distance())

def choose_rift_coordinates(seed,count=None):
    count=count or rift_count(); w,h=m.map_width(),m.map_height(); coords=[]
    for i in range(count):
        picked=None
        for retry in range(2048):
            d=_digest(seed,'rift',i,retry); rx=int.from_bytes(d[:4],'big')/2**32; ry=int.from_bytes(d[4:8],'big')/2**32
            margin=max(5,min(w,h)//8); x=margin+int(rx*max(1,w-2*margin)); y=margin+int(ry*max(1,h-2*margin))
            if _valid_rift(seed,x,y,coords): picked=(x,y); break
        if not picked:
            for x in range(0,w,max(1,w//97)):
                for y in range(0,h,max(1,h//89)):
                    if _valid_rift(seed,x,y,coords): picked=(x,y); break
                if picked: break
        if picked: coords.append(picked)
    return coords

def ensure_world_initialized_in_transaction(conn,season_id:int):
    ensure_null_faction(conn,season_id)
    c=conn.cursor(); season=_fetchone(c,'SELECT * FROM polywar_seasons WHERE id=%s',(season_id,)); now=_now(); start=season.get('starts_at') or now
    if isinstance(start,str): start=datetime.fromisoformat(start)
    activation=start+timedelta(hours=activation_hours())
    _execute(c,"""INSERT INTO polywar_null_state (season_id,status,activation_at,next_tick_at,tick_index,created_at,updated_at) VALUES (%s,'dormant',%s,%s,0,%s,%s) ON CONFLICT (season_id) DO NOTHING""",(season_id,activation,activation,now,now))
    existing=_fetchone(c,'SELECT COUNT(*) AS n FROM polywar_null_rifts WHERE season_id=%s',(season_id,)) or {'n':0}
    if int(existing.get('n') or 0)<rift_count():
        for x,y in choose_rift_coordinates(season.get('secret_seed','seed'),rift_count()):
            _execute(c,"""INSERT INTO polywar_null_rifts (season_id,x,y,status,health,max_health,spawned_at,created_at,updated_at) VALUES (%s,%s,%s,'dormant',%s,%s,%s,%s,%s) ON CONFLICT (season_id,x,y) DO NOTHING""",(season_id,x,y,rift_health(),rift_health(),activation,now,now))
    logger.info('polywar_world_initialized season_id=%s',season_id)


def ensure_world_initialized(season_id:int):
    conn=polywar.get_connection(); ok=False
    try:
        init_world_schema(conn); conn.commit(); begin_world_transaction(conn)
        out=ensure_world_initialized_in_transaction(conn,int(season_id))
        conn.commit(); ok=True; return out
    finally:
        if not ok: polywar._safe_rollback(conn)
        conn.close()

def is_rift(conn,season_id,x,y,status=None):
    sql='SELECT * FROM polywar_null_rifts WHERE season_id=%s AND x=%s AND y=%s'; params=[season_id,x,y]
    if status: sql+=' AND status=%s'; params.append(status)
    return _fetchone(conn.cursor(),sql,tuple(params))

def is_safe_zone(conn,season_id,x,y):
    if is_rift(conn,season_id,x,y): return True
    return (x,y) in set(m.faction_base_positions().values())

def _upsert_frontier(conn,sid,x,y,source=None,priority=0):
    if not m.in_bounds(x,y): return
    _execute(conn.cursor(),"INSERT INTO polywar_null_frontier (season_id,x,y,source_rift_id,discovered_at,priority) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,x,y) DO NOTHING",(sid,x,y,source,_now(),priority))

def update_frontier_for_cell(conn,sid,x,y,source=None):
    for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
        if m.in_bounds(nx,ny): _upsert_frontier(conn,sid,nx,ny,source,0)
    _execute(conn.cursor(),'DELETE FROM polywar_null_frontier WHERE season_id=%s AND x=%s AND y=%s',(sid,x,y))

def activate_if_due_in_transaction(conn,season_id:int,now=None):
    now=now or _now()
    if not enabled(): return False
    ensure_world_initialized_in_transaction(conn,season_id); lock_world_rows(conn,season_id); c=conn.cursor()
    st=_fetchone(c,'SELECT * FROM polywar_null_state WHERE season_id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
    if not st or st['status']!='dormant' or str(st['activation_at'])>polywar._iso(now): return False
    _execute(c,"UPDATE polywar_null_state SET status='active',activated_at=%s,updated_at=%s WHERE season_id=%s AND status='dormant'",(now,now,season_id))
    if polywar._rowcount(c)!=1: return False
    seed=(_fetchone(c,'SELECT secret_seed FROM polywar_seasons WHERE id=%s',(season_id,)) or {}).get('secret_seed','seed')
    for r in _fetchall(c,"SELECT * FROM polywar_null_rifts WHERE season_id=%s AND status='dormant'"+('' if _is_sqlite(conn) else ' FOR UPDATE'),(season_id,)):
        old_owner=m._owner_at(conn,season_id,r['x'],r['y'])
        _execute(c,"UPDATE polywar_null_rifts SET status='active',updated_at=%s WHERE id=%s",(now,r['id']))
        _execute(c,"INSERT INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s) ON CONFLICT (season_id,x,y) DO UPDATE SET owner_faction_id=%s,capture_progress=100,contesting_faction_id=NULL,contest_progress=0,contested_at=NULL,updated_at=%s",(season_id,r['x'],r['y'],NULL_STATE_FACTION_ID,now,NULL_STATE_FACTION_ID,now))
        sectors.transfer_cell_ownership(conn,season_id,r['x'],r['y'],old_owner,NULL_STATE_FACTION_ID,None,now); update_frontier_for_cell(conn,season_id,r['x'],r['y'],r['id'])
        _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'rift_opened',%s,%s)",(season_id,NULL_STATE_FACTION_ID,f'Null rift opened at {r["x"]},{r["y"]}',now))
    _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'null_state_activated','The Null State has activated.',%s)",(season_id,NULL_STATE_FACTION_ID,now))
    _recount(conn,season_id); logger.info('polywar_null_state_activated season_id=%s',season_id); return True


def activate_if_due(season_id:int,now=None):
    conn=polywar.get_connection(); ok=False
    try:
        init_world_schema(conn); conn.commit(); begin_world_transaction(conn)
        out=activate_if_due_in_transaction(conn,int(season_id),now)
        conn.commit(); ok=True; return out
    finally:
        if not ok: polywar._safe_rollback(conn)
        conn.close()

def _ordered_frontier(conn,sid,seed,tick,limit):
    rows=_fetchall(conn.cursor(),'SELECT * FROM polywar_null_frontier WHERE season_id=%s ORDER BY priority DESC,x,y LIMIT %s',(sid,max(limit*20,limit)))
    return sorted(rows,key=lambda r: hashlib.sha256(f'{seed}:{tick}:{r["x"]}:{r["y"]}'.encode()).hexdigest())[:limit]

def _apply_null_capital_pressure(conn, sid, cap, now):
    from services import polywar_capital_service as caps
    c=conn.cursor(); cap=_fetchone(c,'SELECT * FROM polywar_capitals WHERE id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(cap['id'],)) or cap
    before=int(cap.get('siege_progress') or 0); bes=cap.get('besieging_faction_id'); req=caps.siege_required(); power=capital_siege_per_tick()
    if int(cap.get('controller_faction_id') or 0)==NULL_STATE_FACTION_ID:
        if before or bes:
            _execute(c,'UPDATE polywar_capitals SET siege_progress=0, besieging_faction_id=NULL, siege_started_at=NULL, last_siege_at=NULL, updated_at=%s WHERE id=%s',(now,cap['id']))
        _execute(c,'DELETE FROM polywar_null_frontier WHERE season_id=%s AND x=%s AND y=%s',(sid,cap['x'],cap['y']))
        update_frontier_for_cell(conn,sid,cap['x'],cap['y'],None)
        return {'type':'null_capital_already_controlled','x':cap['x'],'y':cap['y']}
    if bes and int(bes)!=NULL_STATE_FACTION_ID:
        after=max(0,before-power); new_bes=bes if after>0 else None
        _execute(c,'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=%s, last_siege_at=%s, updated_at=%s WHERE id=%s',(after,new_bes,cap.get('siege_started_at') if after>0 else None,now,now,cap['id']))
        _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'null_capital_siege',%s,%s)",(sid,NULL_STATE_FACTION_ID,f'The Null State weakened rival siege at {cap["x"]},{cap["y"]}',now))
        return {'type':'null_capital_siege','x':cap['x'],'y':cap['y'],'progress_after':after,'rival_reduced':True}
    after=min(req,before+power)
    if after>=req:
        caps.transfer_capital_control(conn,sid,cap,NULL_STATE_FACTION_ID,None,now)
        _execute(c,'DELETE FROM polywar_null_frontier WHERE season_id=%s AND x=%s AND y=%s',(sid,cap['x'],cap['y']))
        update_frontier_for_cell(conn,sid,cap['x'],cap['y'],None)
        _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'null_capital_captured',%s,%s)",(sid,NULL_STATE_FACTION_ID,f'The Null State captured capital {cap["x"]},{cap["y"]}',now))
        return {'type':'null_capital_captured','x':cap['x'],'y':cap['y']}
    _execute(c,'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=COALESCE(siege_started_at,%s), last_siege_at=%s, updated_at=%s WHERE id=%s',(after,NULL_STATE_FACTION_ID,now,now,now,cap['id']))
    _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'null_capital_siege',%s,%s)",(sid,NULL_STATE_FACTION_ID,f'The Null State pressured capital {cap["x"]},{cap["y"]}',now))
    return {'type':'null_capital_siege','x':cap['x'],'y':cap['y'],'progress_after':after}

def process_due_tick_in_transaction(conn,season_id:int,now=None):
    now=now or _now()
    if not enabled(): return {'processed':False,'reason':'null_state_disabled'}
    ensure_world_initialized_in_transaction(conn,season_id); lock_world_rows(conn,season_id); c=conn.cursor()
    from services import polywar_finalization_service as finalization
    decision=finalization.maybe_finalize_in_transaction(conn,season_id,now)
    if decision.get('should_finalize'):
        finalization.finalize_season_in_transaction(conn,season_id,decision.get('victory_type','time'),decision.get('winner_faction_id'),now)
        return {'processed':False,'reason':'season_ended'}
    season_row=_fetchone(c,'SELECT status,ends_at FROM polywar_seasons WHERE id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(season_id,)) or {}
    if season_row.get('status')!='active': return {'processed':False,'reason':'season_ended'}
    ends=season_row.get('ends_at')
    if isinstance(ends,str): ends=datetime.fromisoformat(ends)
    if ends and now>=ends: return {'processed':False,'reason':'season_ended'}
    activate_if_due_in_transaction(conn,season_id,now); st=_fetchone(c,'SELECT * FROM polywar_null_state WHERE season_id=%s'+('' if _is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
    if not st or st['status']!='active' or str(st['next_tick_at'])>polywar._iso(now): return {'processed':False,'reason':'not_due'}
    tick=int(st.get('tick_index') or 0)+1
    stale_before=now-timedelta(minutes=max(2,tick_minutes()*2))
    old=_fetchone(c,"SELECT * FROM polywar_world_ticks WHERE season_id=%s AND tick_index=%s",(season_id,tick))
    if old and old.get('status')=='processing' and str(old.get('started_at'))>polywar._iso(stale_before): return {'processed':False,'reason':'world_tick_conflict'}
    if old and old.get('status')=='processing':
        _execute(c,"UPDATE polywar_world_ticks SET status='failed',processed_at=%s,outcome_json=%s WHERE season_id=%s AND tick_index=%s",(now,json.dumps({'recovered_stale_processing':True}),season_id,tick))
        _execute(c,"DELETE FROM polywar_world_ticks WHERE season_id=%s AND tick_index=%s AND status='failed'",(season_id,tick))
        old=None
    if _is_sqlite(conn):
        _execute(c,"INSERT OR IGNORE INTO polywar_world_ticks (season_id,tick_index,scheduled_at,started_at,status,created_at) VALUES (%s,%s,%s,%s,'processing',%s)",(season_id,tick,st['next_tick_at'],now,now))
    else:
        _execute(c,"INSERT INTO polywar_world_ticks (season_id,tick_index,scheduled_at,started_at,status,created_at) VALUES (%s,%s,%s,%s,'processing',%s) ON CONFLICT (season_id,tick_index) DO NOTHING",(season_id,tick,st['next_tick_at'],now,now))
    if polywar._rowcount(c)!=1 and not (old and old.get('status')=='processing'):
        return {'processed':False,'reason':'world_tick_conflict'}
    season=_fetchone(c,'SELECT secret_seed FROM polywar_seasons WHERE id=%s',(season_id,)); actions=[]
    for cand in _ordered_frontier(conn,season_id,season.get('secret_seed','seed'),tick,expansions_per_tick()):
        x,y=int(cand['x']),int(cand['y'])
        if m.TERRAIN_COSTS.get(m.terrain_at(season.get('secret_seed','seed'),x,y)) is None: continue
        if is_rift(conn,season_id,x,y,'sealed'): continue
        if not any(m._owner_at(conn,season_id,nx,ny)==NULL_STATE_FACTION_ID for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if m.in_bounds(nx,ny)):
            _execute(c,'DELETE FROM polywar_null_frontier WHERE season_id=%s AND x=%s AND y=%s',(season_id,x,y)); continue
        from services import polywar_capital_service as caps
        cap=caps.get_capital_at(conn,season_id,x,y)
        if cap:
            actions.append(_apply_null_capital_pressure(conn,season_id,cap,now)); update_frontier_for_cell(conn,season_id,x,y,None); continue
        owner=m._owner_at(conn,season_id,x,y)
        if owner==NULL_STATE_FACTION_ID: continue
        _execute(c,"INSERT INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s) ON CONFLICT (season_id,x,y) DO UPDATE SET owner_faction_id=%s,contesting_faction_id=NULL,contest_progress=0,contested_at=NULL,updated_at=%s",(season_id,x,y,NULL_STATE_FACTION_ID,now,NULL_STATE_FACTION_ID,now))
        sectors.transfer_cell_ownership(conn,season_id,x,y,owner,NULL_STATE_FACTION_ID,None,now); update_frontier_for_cell(conn,season_id,x,y,cand.get('source_rift_id')); actions.append({'type':'null_cell_captured','x':x,'y':y,'old_owner':owner})
        _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'null_cell_captured',%s,%s)",(season_id,NULL_STATE_FACTION_ID,f'The Null State captured {x},{y}',now))
    
    from services import polywar_rebellion_service as rebellion
    for ev in rebellion.process_rebellion_tick(conn,season_id,now,limit=10): actions.append({'type':ev})
    prev=st.get('next_tick_at')
    if isinstance(prev,str): prev=datetime.fromisoformat(prev)
    nxt=prev+timedelta(minutes=tick_minutes()); _execute(c,"UPDATE polywar_world_ticks SET status='completed',processed_at=%s,actions_count=%s,outcome_json=%s WHERE season_id=%s AND tick_index=%s",(now,len(actions),json.dumps(actions),season_id,tick))
    _execute(c,"UPDATE polywar_null_state SET tick_index=%s,last_tick_at=%s,next_tick_at=%s,corruption_level=corruption_level+%s,updated_at=%s WHERE season_id=%s",(tick,now,nxt,len(actions),now,season_id)); _recount(conn,season_id); check_defeat(conn,season_id,now)
    from services import polywar_finalization_service as finalization
    decision=finalization.maybe_finalize_in_transaction(conn,season_id,now)
    if decision.get('should_finalize'):
        finalization.finalize_season_in_transaction(conn,season_id,decision.get('victory_type','time'),decision.get('winner_faction_id'),now)
    return {'processed':True,'tick_index':tick,'actions_count':len(actions)}



def process_due_tick(season_id:int,now=None):
    conn=polywar.get_connection(); ok=False
    try:
        init_world_schema(conn); conn.commit(); begin_world_transaction(conn)
        out=process_due_tick_in_transaction(conn,int(season_id),now)
        conn.commit(); ok=True; return out
    except Exception:
        logger.exception('polywar_world_tick_failed season_id=%s',season_id); raise
    finally:
        if not ok: polywar._safe_rollback(conn)
        conn.close()


def ensure_world_caught_up_in_transaction(conn,season_id:int,now=None):
    now=now or _now(); out=[]
    for _ in range(max_catchup_ticks()):
        r=process_due_tick_in_transaction(conn,season_id,now); out.append(r)
        if not r.get('processed'): break
    return out


def ensure_world_caught_up(season_id:int,now=None):
    conn=polywar.get_connection(); ok=False
    try:
        init_world_schema(conn); conn.commit(); begin_world_transaction(conn)
        out=ensure_world_caught_up_in_transaction(conn,int(season_id),now)
        conn.commit(); ok=True; return out
    finally:
        if not ok: polywar._safe_rollback(conn)
        conn.close()

def _recount(conn,sid):
    c=conn.cursor(); cells=(_fetchone(c,'SELECT COUNT(*) AS n FROM polywar_cells WHERE season_id=%s AND owner_faction_id=%s',(sid,NULL_STATE_FACTION_ID)) or {}).get('n') or 0
    sec=(_fetchone(c,'SELECT COUNT(*) AS n FROM polywar_sectors WHERE season_id=%s AND controller_faction_id=%s',(sid,NULL_STATE_FACTION_ID)) or {}).get('n') or 0
    caps=(_fetchone(c,'SELECT COUNT(*) AS n FROM polywar_capitals WHERE season_id=%s AND controller_faction_id=%s',(sid,NULL_STATE_FACTION_ID)) or {}).get('n') or 0
    _execute(c,'UPDATE polywar_null_state SET controlled_cells_count=%s,controlled_sectors_count=%s,controlled_capitals_count=%s,updated_at=%s WHERE season_id=%s',(cells,sec,caps,_now(),sid))
    _execute(c,'UPDATE polywar_faction_season_stats SET controlled_cells_count=%s,controlled_sectors_count=%s,controlled_capitals_count=%s,updated_at=%s WHERE season_id=%s AND faction_id=%s',(cells,sec,caps,_now(),sid,NULL_STATE_FACTION_ID))
    sectors.recalc_influence(conn,sid,NULL_STATE_FACTION_ID,_now())

def check_defeat(conn,sid,now=None):
    now=now or _now(); c=conn.cursor(); openr=(_fetchone(c,"SELECT COUNT(*) AS n FROM polywar_null_rifts WHERE season_id=%s AND status!='sealed'",(sid,)) or {}).get('n') or 0; st=_fetchone(c,'SELECT * FROM polywar_null_state WHERE season_id=%s',(sid,))
    if st and st['status']!='defeated' and int(openr)==0 and int(st.get('controlled_cells_count') or 0)<=_setting_int('polywar_null_defeat_cell_threshold',0,0,1000000) and int(st.get('controlled_capitals_count') or 0)==0:
        _execute(c,"UPDATE polywar_null_state SET status='defeated',defeated_at=%s,updated_at=%s WHERE season_id=%s",(now,now,sid)); _execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,'null_state_defeated','The Null State has been defeated.',%s)",(sid,NULL_STATE_FACTION_ID,now)); return True
    return False

def get_public_world_state(conn,season_id:int):
    c=conn.cursor(); st=_fetchone(c,'SELECT * FROM polywar_null_state WHERE season_id=%s',(season_id,)) or {}; rifts=_fetchall(c,'SELECT * FROM polywar_null_rifts WHERE season_id=%s ORDER BY id',(season_id,))
    def rr(r):
        hp=0 if not r.get('max_health') else round(100*int(r.get('health') or 0)/int(r.get('max_health') or 1),2)
        return {'x':r['x'],'y':r['y'],'status':r['status'],'health':r['health'],'max_health':r['max_health'],'health_percent':hp,'sealed_by_faction_id':r.get('sealed_by_faction_id'),'sealed_at':polywar._iso(r.get('sealed_at'))}
    season=polywar._fetchone(c,'SELECT domination_faction_id,domination_started_at FROM polywar_seasons WHERE id=%s',(season_id,)) or {}
    hold_hours=0; cand=None
    if season.get('domination_faction_id'):
        cand='null_state' if int(season.get('domination_faction_id') or 0)==NULL_STATE_FACTION_ID else 'domination'; hold_hours=_setting_int('polywar_null_victory_hold_hours' if cand=='null_state' else 'polywar_domination_hold_hours',12 if cand=='null_state' else 24,0,8760)
    started=season.get('domination_started_at')
    if isinstance(started,str):
        try: started=datetime.fromisoformat(started)
        except Exception: started=None
    hold_until=(started+timedelta(hours=hold_hours)) if started else None
    remaining=max(0,int((hold_until-_now()).total_seconds())) if hold_until else 0
    try:
        from services import polywar_rebellion_service as rebellion
        rebellions=rebellion.get_public_rebellions_readonly(conn,season_id)
    except Exception:
        logger.exception('polywar_world_rebellion_public_read_failed season_id=%s',season_id)
        raise
    return {'season_id':season_id,'status':('disabled' if not enabled() else st.get('status')),'activation_at':polywar._iso(st.get('activation_at')),'activated_at':polywar._iso(st.get('activated_at')),'next_tick_at':polywar._iso(st.get('next_tick_at')),'tick_index':st.get('tick_index',0),'corruption_level':st.get('corruption_level',0),'controlled_cells_count':st.get('controlled_cells_count',0),'controlled_sectors_count':st.get('controlled_sectors_count',0),'controlled_capitals_count':st.get('controlled_capitals_count',0),'active_rifts':[rr(r) for r in rifts if r['status']=='active'],'sealed_rifts':[rr(r) for r in rifts if r['status']=='sealed'],'rifts':[rr(r) for r in rifts],'rebellions':rebellions,'defeated_at':polywar._iso(st.get('defeated_at')),'domination_faction_id':season.get('domination_faction_id'),'domination_started_at':polywar._iso(started),'domination_hold_hours':hold_hours,'domination_hold_until':polywar._iso(hold_until),'domination_remaining_seconds':remaining,'victory_candidate_type':cand,'server_timestamp':int(time.time()),'rules':public_rules()}

def public_rules(): return {'seal_energy_cost':seal_cost(),'seal_progress':seal_progress(),'tick_minutes':tick_minutes(),'expansions_per_tick':expansions_per_tick(),'capital_siege_per_tick':capital_siege_per_tick()}

def reconcile_polywar_season(conn,season_id:int,fix:bool=False):
    c=conn.cursor(); report={'ok':True,'season_id':season_id,'mismatches':[],'fixed':False}
    stored=_fetchone(c,'SELECT controlled_cells_count,controlled_sectors_count,controlled_capitals_count FROM polywar_null_state WHERE season_id=%s',(season_id,)) or {}
    actual_cells=(_fetchone(c,'SELECT COUNT(*) AS n FROM polywar_cells WHERE season_id=%s AND owner_faction_id=%s',(season_id,NULL_STATE_FACTION_ID)) or {}).get('n') or 0
    if int(stored.get('controlled_cells_count') or 0)!=int(actual_cells): report['mismatches'].append({'type':'null_cells','expected':actual_cells,'actual':stored.get('controlled_cells_count')})
    for cap in _fetchall(c,'SELECT x,y,controller_faction_id FROM polywar_capitals WHERE season_id=%s',(season_id,)):
        cell=_fetchone(c,'SELECT owner_faction_id FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s',(season_id,cap['x'],cap['y'])) or {}
        if cell.get('owner_faction_id') is not None and int(cell.get('owner_faction_id'))!=int(cap.get('controller_faction_id') or 0): report['mismatches'].append({'type':'capital_cell_owner','coordinates':{'x':cap['x'],'y':cap['y']},'expected':cap.get('controller_faction_id'),'actual':cell.get('owner_faction_id')})
    report['ok']=not report['mismatches']
    if fix and report['mismatches']:
        season=_fetchone(c,'SELECT status FROM polywar_seasons WHERE id=%s',(season_id,)) or {}
        if season.get('status')!='active': raise ValueError('reconciliation_fix_forbidden')
    return report

def seal_rift_action(user_id:int,x:int,y:int,idempotency_key:str):
    if not idempotency_key or len(str(idempotency_key))>120: raise ValueError('bad_idempotency_key')
    from services import polywar_mine_service as mines
    conn=polywar.get_connection(); c=conn.cursor(); managed=False; ok=False
    try:
        # Schema/world preparation is intentionally outside the gameplay transaction.
        polywar.init_polywar_schema(conn); m.init_polywar_map_schema(conn); init_world_schema(conn)
        season=m._private_active_season(conn); sid=int(season['id']); original_season_id=sid
        if not enabled(): raise ValueError('null_state_disabled')
        ensure_world_initialized_in_transaction(conn,sid); conn.commit()
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: return dup
        conn.close()
        ensure_world_caught_up(sid,_now())
        conn=polywar.get_connection(); c=conn.cursor()
        polywar.init_polywar_schema(conn); m.init_polywar_map_schema(conn); init_world_schema(conn)
        season=m._private_active_season(conn); sid=int(season['id'])
        if int(sid)!=int(original_season_id):
            return {'ok': False, 'error': 'season_ended', 'season_id': original_season_id, 'current_season_id': sid}
        ensure_world_initialized_in_transaction(conn,sid); conn.commit()
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: return dup
        managed=_start_world_transaction(conn)
        lock_world_rows(conn,sid)
        prepared=polywar.prepare_gameplay_mutation_in_transaction(conn,sid,_now())
        if not prepared.get('ok'):
            if prepared.get('season_finalized'):
                ok=True; _finish_world_transaction(conn,managed,ok); managed=False
                return {'ok': False, 'error': prepared.get('error') or 'season_ended', 'season_finalized': True}
            raise ValueError(prepared.get('error') or 'season_ended')
        suffix='' if _is_sqlite(conn) else ' FOR UPDATE'
        player=polywar.get_or_create_player(user_id,sid,conn)
        player=_fetchone(c,'SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s'+suffix,(user_id,sid)) or player
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: ok=True; return dup
        fid=player.get('faction_id')
        if not fid or int(fid)==NULL_STATE_FACTION_ID: raise ValueError('faction_required')
        st=_fetchone(c,'SELECT * FROM polywar_null_state WHERE season_id=%s'+suffix,(sid,))
        if not st or st.get('status')!='active': raise ValueError('null_state_dormant')
        r=_fetchone(c,'SELECT * FROM polywar_null_rifts WHERE season_id=%s AND x=%s AND y=%s'+suffix,(sid,x,y))
        if not r: raise ValueError('rift_required')
        _fetchone(c,'SELECT * FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s'+suffix,(sid,x,y))
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: ok=True; return dup
        if r['status']=='sealed': raise ValueError('rift_already_sealed')
        if r['status']!='active': raise ValueError('rift_inactive')
        if not any(m._owner_at(conn,sid,nx,ny)==fid for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if m.in_bounds(nx,ny)): raise ValueError('rift_not_frontline')
        e=polywar._energy(player)
        if e.get('is_locked'): raise ValueError('player_locked')
        cost=seal_cost()
        if int(e['current_energy'])<cost: raise ValueError('insufficient_energy')
        now=_now(); before=int(r['health']); after=max(0,before-seal_progress()); _,_,energy=mines.spend_player_energy(conn,player,cost,now); sealed=after<=0
        status='sealed' if sealed else 'active'; outcome='rift_sealed' if sealed else 'rift_damaged'
        _execute(c,'UPDATE polywar_null_rifts SET health=%s,status=%s,last_action_at=%s,sealed_at=%s,sealed_by_user_id=%s,sealed_by_faction_id=%s,updated_at=%s WHERE id=%s AND status=%s',(after,status,now,now if sealed else None,user_id if sealed else None,fid if sealed else None,now,r['id'],r['status']))
        if polywar._rowcount(c)!=1: raise ValueError('rift_inactive')
        _execute(c,"INSERT INTO polywar_rift_contributions (season_id,rift_id,user_id,faction_id,contribution,first_contributed_at,last_contributed_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rift_id,user_id) DO UPDATE SET contribution=contribution+%s,last_contributed_at=%s",(sid,r['id'],user_id,fid,before-after,now,now,before-after,now))
        if sealed:
            old_owner=m._owner_at(conn,sid,x,y)
            _execute(c,"UPDATE polywar_cells SET owner_faction_id=%s,updated_at=%s WHERE season_id=%s AND x=%s AND y=%s",(fid,now,sid,x,y)); sectors.transfer_cell_ownership(conn,sid,x,y,old_owner,fid,user_id,now); update_frontier_for_cell(conn,sid,x,y,None)
        payload={'coordinate':{'x':x,'y':y},'rift_status':status,'health_before':before,'health_after':after,'progress':before-after,'energy_cost':cost,'sealed':sealed,'sealing_faction_id':fid if sealed else None,'energy':energy}
        _execute(c,'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,user_id,fid,outcome,outcome,now)); _recount(conn,sid); check_defeat(conn,sid,now)
        mines.insert_outcome(conn,sid,user_id,idempotency_key,'seal_rift',x,y,outcome,cost,payload,now)
        ok=True; _finish_world_transaction(conn,managed,ok); managed=False; payload.update({'ok':True,'outcome':outcome}); return payload
    except ValueError:
        raise
    finally:
        if managed: _finish_world_transaction(conn,managed,ok)
        conn.close()
