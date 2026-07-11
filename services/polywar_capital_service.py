import logging, threading, time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, List, Tuple

from services import polywar_service as polywar
from services import polywar_map_service as m
from services import polywar_mine_service as mines
from services import polywar_sector_service as sectors

logger=logging.getLogger(__name__)
_RATE_LOCK=threading.Lock(); _RATE=defaultdict(deque); RATE_WINDOW=10; RATE_MAX=30

def _setting_int(k,d,lo,hi): return polywar._setting_int(k,d,lo,hi)
def siege_required(): return _setting_int('polywar_capital_siege_required',1000,100,100000)
def siege_power(): return _setting_int('polywar_capital_siege_progress_per_action',100,1,10000)
def siege_extra_energy(): return _setting_int('polywar_capital_siege_extra_energy',2,0,100)
def repair_cost(): return _setting_int('polywar_capital_repair_energy_cost',2,0,100)
def repair_power(): return _setting_int('polywar_capital_repair_progress_per_action',75,1,10000)
def influence_value(): return _setting_int('polywar_capital_influence_value',1000,0,1000000)
def event_cooldown(): return _setting_int('polywar_capital_event_cooldown_seconds',30,0,3600)

def public_rules():
    return {'siege_required':siege_required(),'siege_progress_per_action':siege_power(),'siege_extra_energy':siege_extra_energy(),'repair_energy_cost':repair_cost(),'repair_progress_per_action':repair_power(),'influence_value':influence_value()}

def _rate(uid):
    now=time.monotonic()
    with _RATE_LOCK:
        q=_RATE[int(uid)]
        while q and now-q[0]>RATE_WINDOW: q.popleft()
        if len(q)>=RATE_MAX: raise ValueError('rate_limited')
        q.append(now)

def init_polywar_capital_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor()
    id_sql='INTEGER PRIMARY KEY AUTOINCREMENT' if polywar._is_sqlite(conn) else 'SERIAL PRIMARY KEY'
    try:
        c.execute(f'''CREATE TABLE IF NOT EXISTS polywar_capitals (id {id_sql}, season_id INTEGER NOT NULL, original_faction_id INTEGER NOT NULL, controller_faction_id INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, besieging_faction_id INTEGER NULL, siege_progress INTEGER NOT NULL DEFAULT 0, siege_started_at TIMESTAMP NULL, last_siege_at TIMESTAMP NULL, last_siege_by_user_id BIGINT NULL, captured_at TIMESTAMP NULL, controlled_since TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, UNIQUE(season_id,original_faction_id), UNIQUE(season_id,x,y))''')
        for spec in ['controlled_capitals_count INTEGER NOT NULL DEFAULT 0','commander_user_id BIGINT NULL','commander_since TIMESTAMP NULL','commander_term_ends_at TIMESTAMP NULL']:
            sectors._add_col(conn,'polywar_faction_season_stats',spec)
        for sql in ['CREATE INDEX IF NOT EXISTS idx_polywar_capitals_controller ON polywar_capitals(season_id,controller_faction_id)','CREATE INDEX IF NOT EXISTS idx_polywar_capitals_besieger ON polywar_capitals(season_id,besieging_faction_id)','CREATE INDEX IF NOT EXISTS idx_polywar_capitals_xy ON polywar_capitals(season_id,x,y)']:
            c.execute(sql)
        conn.commit()
    finally:
        if own: conn.close()

def recalc_influence(conn,sid,fid,now):
    polywar._execute(conn.cursor(),'UPDATE polywar_faction_season_stats SET influence_score=controlled_cells_count + controlled_sectors_count * %s + controlled_capitals_count * %s, updated_at=%s WHERE season_id=%s AND faction_id=%s',(sectors.influence_value(), influence_value(), now, sid, fid))

def ensure_capitals_initialized(conn, season_id:int):
    init_polywar_capital_schema(conn); sectors.ensure_starting_territories_bootstrap(conn, season_id)
    now=datetime.utcnow(); c=conn.cursor()
    for fid,(x,y) in m.faction_base_positions().items():
        if polywar._is_sqlite(conn):
            polywar._execute(c,'INSERT OR IGNORE INTO polywar_capitals (season_id,original_faction_id,controller_faction_id,x,y,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',(season_id,fid,fid,x,y,now,now))
            polywar._execute(c,'INSERT OR IGNORE INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s)',(season_id,x,y,fid,now))
        else:
            polywar._execute(c,'INSERT INTO polywar_capitals (season_id,original_faction_id,controller_faction_id,x,y,controlled_since,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,original_faction_id) DO NOTHING',(season_id,fid,fid,x,y,now,now))
            polywar._execute(c,'INSERT INTO polywar_cells (season_id,x,y,owner_faction_id,capture_progress,updated_at) VALUES (%s,%s,%s,%s,100,%s) ON CONFLICT (season_id,x,y) DO NOTHING',(season_id,x,y,fid,now))
    rows=polywar._fetchall(c,'SELECT controller_faction_id,COUNT(*) n FROM polywar_capitals WHERE season_id=%s GROUP BY controller_faction_id',(season_id,))
    polywar._execute(c,'UPDATE polywar_faction_season_stats SET controlled_capitals_count=0 WHERE season_id=%s',(season_id,))
    for r in rows:
        polywar._execute(c,'UPDATE polywar_faction_season_stats SET controlled_capitals_count=%s WHERE season_id=%s AND faction_id=%s',(int(r['n']),season_id,int(r['controller_faction_id'])))
        recalc_influence(conn,season_id,int(r['controller_faction_id']),now)

def get_capital_at(conn,sid,x,y):
    return polywar._fetchone(conn.cursor(),'SELECT * FROM polywar_capitals WHERE season_id=%s AND x=%s AND y=%s',(sid,x,y))

def _has_adjacent(conn,sid,x,y,fid):
    return any(m.in_bounds(nx,ny) and m._owner_at(conn,sid,nx,ny)==fid for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)))

def _begin(conn,c):
    if polywar._is_sqlite(conn):
        for i in range(20):
            try: c.execute('BEGIN IMMEDIATE'); return
            except Exception as e:
                if 'locked' not in str(e).lower(): raise
                time.sleep(.025*(i+1))
    else: polywar._execute(c,'BEGIN')

def transfer_capital_control(conn,sid,cap,new_controller,user_id,now):
    old=int(cap['controller_faction_id']); x=int(cap['x']); y=int(cap['y']); c=conn.cursor()
    if old==new_controller: return None
    polywar._execute(c,'UPDATE polywar_capitals SET controller_faction_id=%s, besieging_faction_id=NULL, siege_progress=0, siege_started_at=NULL, captured_at=%s, controlled_since=%s, updated_at=%s WHERE id=%s',(new_controller,now,now,now,cap['id']))
    polywar._execute(c,'UPDATE polywar_cells SET owner_faction_id=%s, contesting_faction_id=NULL, contest_progress=0, updated_at=%s, updated_by_user_id=%s WHERE season_id=%s AND x=%s AND y=%s',(new_controller,now,user_id,sid,x,y))
    change=sectors.transfer_cell_ownership(conn,sid,x,y,old,new_controller,user_id,now)
    decr=sectors._decrement_expr(conn,'controlled_capitals_count')
    polywar._execute(c,f'UPDATE polywar_faction_season_stats SET controlled_capitals_count={decr}, updated_at=%s WHERE season_id=%s AND faction_id=%s',(now,sid,old))
    polywar._execute(c,'UPDATE polywar_faction_season_stats SET controlled_capitals_count=controlled_capitals_count+1, updated_at=%s WHERE season_id=%s AND faction_id=%s',(now,sid,new_controller))
    recalc_influence(conn,sid,old,now); recalc_influence(conn,sid,new_controller,now)
    et='capital_recaptured' if int(cap['original_faction_id'])==new_controller else 'capital_captured'
    polywar._execute(c,'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,user_id,new_controller,et,f'Capital {x},{y} changed controller',now))
    return change

def capital_action(user_id:int, action_type:str, x:int, y:int, idempotency_key:str):
    if action_type not in {'siege','repair_capital'}: raise ValueError('bad_action_type')
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        polywar.init_polywar_schema(conn); init_polywar_capital_schema(conn); season=m._private_active_season(conn); sid=int(season['id']); seed=season['secret_seed']
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: return dup
        _rate(user_id); _begin(conn,c); dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: conn.commit(); return dup
        polywar._insert_player_if_missing(conn,user_id,sid); player=polywar._fetchone(c,'SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(user_id,sid))
        fid=player.get('faction_id')
        if not fid: raise ValueError('faction_required')
        if polywar._energy(player).get('is_locked'): raise ValueError('player_locked')
        ensure_capitals_initialized(conn,sid)
        cap=get_capital_at(conn,sid,x,y)
        if not cap: raise ValueError('capital_required')
        cap=polywar._fetchone(c,'SELECT * FROM polywar_capitals WHERE id=%s'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(cap['id'],))
        terr=m.terrain_at(seed,x,y); base=m.TERRAIN_COSTS[terr]
        if base is None: raise ValueError('not_capturable')
        if not _has_adjacent(conn,sid,x,y,fid): raise ValueError('capital_not_frontline')
        now=datetime.utcnow(); before=int(cap.get('siege_progress') or 0); previous=int(cap['controller_faction_id']); bes=cap.get('besieging_faction_id'); transfer=None
        if action_type=='siege':
            if previous==fid: raise ValueError('own_capital_cannot_be_sieged')
            cost=int(base)+siege_extra_energy(); _,_,energy=mines.spend_player_energy(conn,player,cost,now); power=siege_power(); req=siege_required()
            if bes and int(bes)!=fid:
                after=max(0,before-power); outcome='rival_siege_reduced' if after>0 else 'rival_siege_cleared'; new_bes=bes if after>0 else None; started=cap.get('siege_started_at') if after>0 else None
                polywar._execute(c,'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=%s, last_siege_at=%s,last_siege_by_user_id=%s, updated_at=%s WHERE id=%s',(after,new_bes,started,now,user_id,now,cap['id']))
            else:
                after=min(req,before+power); outcome='siege_started' if before==0 else 'siege_progress'
                if after>=req:
                    outcome='capital_captured'; transfer=transfer_capital_control(conn,sid,cap,int(fid),user_id,now); bes_after=None
                else:
                    bes_after=fid; polywar._execute(c,'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=COALESCE(siege_started_at,%s), last_siege_at=%s,last_siege_by_user_id=%s, updated_at=%s WHERE id=%s',(after,fid,now,now,user_id,now,cap['id']))
        else:
            if previous!=fid: raise ValueError('not_capital_controller')
            if before<=0: raise ValueError('capital_not_under_siege')
            cost=repair_cost(); _,_,energy=mines.spend_player_energy(conn,player,cost,now); after=max(0,before-repair_power()); outcome='capital_repaired' if after>0 else 'capital_siege_cleared'
            polywar._execute(c,'UPDATE polywar_capitals SET siege_progress=%s, besieging_faction_id=%s, siege_started_at=%s, updated_at=%s WHERE id=%s',(after, bes if after>0 else None, cap.get('siege_started_at') if after>0 else None, now, cap['id']))
        polywar._execute(c,'INSERT INTO polywar_actions (season_id,user_id,faction_id,action_type,x,y,energy_cost,idempotency_key,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',(sid,user_id,fid,action_type,x,y,cost,idempotency_key,now))
        payload={'action_type':action_type,'capital':{'x':x,'y':y,'original_faction_id':cap['original_faction_id'],'previous_controller_faction_id':previous,'controller_faction_id':fid if outcome=='capital_captured' else previous,'besieging_faction_id':bes,'siege_progress_before':before,'siege_progress_after':after,'siege_required':siege_required(),'energy_cost':cost},'capital_transfer':transfer,'energy':energy}
        mines.insert_outcome(conn,sid,user_id,idempotency_key,action_type,x,y,outcome,cost,payload,now)
        if outcome!='capital_captured': polywar._execute(c,'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,user_id,fid,outcome,f'Capital action {outcome} at {x},{y}',now))
        conn.commit(); payload.update({'ok':True,'outcome':outcome}); return payload
    except ValueError:
        polywar._safe_rollback(conn); raise
    except Exception:
        polywar._safe_rollback(conn); logger.exception('capital action failed'); raise
    finally: conn.close()

def get_capitals(user_id:int=None):
    conn=polywar.get_connection()
    try:
        polywar.init_polywar_schema(conn); season=polywar.ensure_active_season(conn); sid=int(season['id']); ensure_capitals_initialized(conn,sid); conn.commit()
        rows=polywar._fetchall(conn.cursor(),'SELECT * FROM polywar_capitals WHERE season_id=%s ORDER BY original_faction_id',(sid,)); req=siege_required()
        return {'ok':True,'season_id':sid,'siege_required':req,'capitals':[{'original_faction_id':r['original_faction_id'],'controller_faction_id':r['controller_faction_id'],'x':r['x'],'y':r['y'],'besieging_faction_id':r.get('besieging_faction_id'),'siege_progress':int(r.get('siege_progress') or 0),'siege_required':req,'siege_percent':min(100,int((int(r.get('siege_progress') or 0)*100)/req)),'siege_started_at':polywar._iso(r.get('siege_started_at')),'controlled_since':polywar._iso(r.get('controlled_since')),'captured_at':polywar._iso(r.get('captured_at')),'is_under_siege':int(r.get('siege_progress') or 0)>0} for r in rows],'server_timestamp':int(time.time())}
    finally: conn.close()

def enrich_chunks(conn,sid,chunks):
    req=siege_required(); c=conn.cursor()
    for ch in chunks:
        x0,y0,w,h=ch['chunk_x']*ch['chunk_size'],ch['chunk_y']*ch['chunk_size'],ch['width'],ch['height']
        rows=polywar._fetchall(c,'SELECT x,y,original_faction_id,controller_faction_id,besieging_faction_id,siege_progress FROM polywar_capitals WHERE season_id=%s AND x >= %s AND x < %s AND y >= %s AND y < %s',(sid,x0,x0+w,y0,y0+h))
        ch['capitals']=[{'x':r['x'],'y':r['y'],'original_faction_id':r['original_faction_id'],'controller_faction_id':r['controller_faction_id'],'besieging_faction_id':r.get('besieging_faction_id'),'siege_progress':int(r.get('siege_progress') or 0),'siege_required':req,'is_under_siege':int(r.get('siege_progress') or 0)>0} for r in rows]
    return chunks
