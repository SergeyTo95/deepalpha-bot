import logging, json
from datetime import datetime, timedelta
from services import polywar_service as polywar
from services import polywar_map_service as m
from services import polywar_mine_service as mines
from services.polywar_world_service import NULL_STATE_FACTION_ID
logger=logging.getLogger(__name__)
def _now(): return datetime.utcnow()
def _setting_int(k,d,lo,hi): return polywar._setting_int(k,d,lo,hi)
def public_rules(): return {'grace_hours':_setting_int('polywar_rebellion_grace_hours',24,0,8760),'required':_setting_int('polywar_rebellion_required',1000,1,1000000),'support_progress':_setting_int('polywar_rebellion_support_progress',100,1,100000),'support_energy_cost':_setting_int('polywar_rebellion_support_energy_cost',2,0,1000),'suppress_progress':_setting_int('polywar_rebellion_suppress_progress',75,1,100000),'suppress_energy_cost':_setting_int('polywar_rebellion_suppress_energy_cost',2,0,1000)}
def init_rebellion_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor(); id_sql='INTEGER PRIMARY KEY AUTOINCREMENT' if polywar._is_sqlite(conn) else 'SERIAL PRIMARY KEY'
    try:
        c.execute(f"""CREATE TABLE IF NOT EXISTS polywar_rebellions (id {id_sql},season_id INTEGER NOT NULL,capital_original_faction_id INTEGER NOT NULL,controller_faction_id INTEGER NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,required_progress INTEGER NOT NULL,occupation_started_at TIMESTAMP NOT NULL,eligible_at TIMESTAMP NOT NULL,started_at TIMESTAMP NULL,last_tick_at TIMESTAMP NULL,last_action_at TIMESTAMP NULL,resolved_at TIMESTAMP NULL,created_at TIMESTAMP NOT NULL,updated_at TIMESTAMP NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_rebellion_contributions (rebellion_id INTEGER NOT NULL,user_id BIGINT NOT NULL,faction_id INTEGER NOT NULL,support_contribution INTEGER NOT NULL DEFAULT 0,suppress_contribution INTEGER NOT NULL DEFAULT 0,first_action_at TIMESTAMP NOT NULL,last_action_at TIMESTAMP NOT NULL,UNIQUE(rebellion_id,user_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_polywar_rebellions_active ON polywar_rebellions(season_id,status,capital_original_faction_id)")
        try: c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_polywar_one_open_rebellion ON polywar_rebellions(season_id,capital_original_faction_id) WHERE status IN ('pending','active')")
        except Exception: pass
        if own: conn.commit()
    finally:
        if own: conn.close()

def _original_presence(conn,sid,orig,x,y):
    if _adjacent_owner(conn,sid,x,y,orig): return True
    # Bounded neighbouring-sector presence check.
    sx,sy=int(x)//64,int(y)//64
    rows=polywar._fetchall(conn.cursor(),'SELECT 1 FROM polywar_cells WHERE season_id=%s AND owner_faction_id=%s AND x>=%s AND x<%s AND y>=%s AND y<%s LIMIT 1',(sid,orig,max(0,(sx-1)*64),(sx+2)*64,max(0,(sy-1)*64),(sy+2)*64))
    return bool(rows)

def ensure_rebellions(conn,season_id:int):
    init_rebellion_schema(conn); c=conn.cursor(); now=_now(); rules=public_rules()
    if str(polywar.get_setting('polywar_rebellion_enabled','true')).lower() in {'0','false','off','no'}: return []
    season=polywar._fetchone(c,'SELECT status FROM polywar_seasons WHERE id=%s',(season_id,))
    if not season or season.get('status')!='active': return []
    try: caps=polywar._fetchall(c,'SELECT * FROM polywar_capitals WHERE season_id=%s',(season_id,))
    except Exception: return []
    made=[]
    for cap in caps:
        orig=int(cap['original_faction_id']); ctrl=int(cap['controller_faction_id'])
        open_reb=polywar._fetchone(c,"SELECT * FROM polywar_rebellions WHERE season_id=%s AND capital_original_faction_id=%s AND status IN ('pending','active')",(season_id,orig))
        if open_reb and int(open_reb.get('controller_faction_id') or 0)!=ctrl:
            polywar._execute(c,"UPDATE polywar_rebellions SET status='cancelled',resolved_at=%s,updated_at=%s WHERE id=%s AND status IN ('pending','active')",(now,now,open_reb['id'])); open_reb=None
        if open_reb and open_reb['status']=='pending' and str(open_reb['eligible_at'])<=polywar._iso(now):
            polywar._execute(c,"UPDATE polywar_rebellions SET status='active',started_at=COALESCE(started_at,%s),updated_at=%s WHERE id=%s AND status='pending'",(now,now,open_reb['id'])); continue
        if ctrl==orig or ctrl==NULL_STATE_FACTION_ID or open_reb: continue
        occ=polywar._fetchone(c,'SELECT is_playable,is_system FROM polywar_factions WHERE id=%s',(ctrl,)) or {}
        if int(occ.get('is_playable') or 0)!=1 or int(occ.get('is_system') or 0)==1: continue
        active_members=(polywar._fetchone(c,'SELECT COUNT(*) AS n FROM polywar_players WHERE season_id=%s AND faction_id=%s',(season_id,orig)) or {}).get('n') or 0
        if int(active_members)<=0: continue
        if not _original_presence(conn,season_id,orig,int(cap['x']),int(cap['y'])): continue
        started=cap.get('captured_at') or cap.get('controlled_since') or now
        if isinstance(started,str): started=datetime.fromisoformat(started)
        eligible=started+timedelta(hours=rules['grace_hours'])
        status='active' if now>=eligible else 'pending'
        polywar._execute(c,"INSERT INTO polywar_rebellions (season_id,capital_original_faction_id,controller_faction_id,status,progress,required_progress,occupation_started_at,eligible_at,started_at,created_at,updated_at) VALUES (%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s)",(season_id,orig,ctrl,status,rules['required'],started,eligible,now if status=='active' else None,now,now)); made.append(orig)
    return made

def get_public_rebellions(conn,season_id:int):
    ensure_rebellions(conn,season_id); rows=polywar._fetchall(conn.cursor(),"SELECT * FROM polywar_rebellions WHERE season_id=%s AND status IN ('pending','active') ORDER BY id",(season_id,))
    return [{k:polywar._iso(v) if str(k).endswith('_at') else v for k,v in r.items()} for r in rows]

def _adjacent_owner(conn,sid,x,y,fid):
    return any(m._owner_at(conn,sid,nx,ny)==fid for nx,ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)) if m.in_bounds(nx,ny))

def process_rebellion_tick(conn,season_id:int,now=None,limit:int=10):
    now=now or _now(); ensure_rebellions(conn,season_id); c=conn.cursor(); changed=[]
    rows=polywar._fetchall(c,"SELECT * FROM polywar_rebellions WHERE season_id=%s AND status='active' ORDER BY id LIMIT %s",(season_id,limit))
    for reb in rows:
        cap=polywar._fetchone(c,'SELECT * FROM polywar_capitals WHERE season_id=%s AND original_faction_id=%s',(season_id,reb['capital_original_faction_id']))
        if not cap or int(cap['controller_faction_id'])!=int(reb['controller_faction_id']): continue
        if not _original_presence(conn,season_id,int(reb['capital_original_faction_id']),int(cap['x']),int(cap['y'])): continue
        req=int(reb['required_progress']); after=min(req,int(reb['progress'] or 0)+_setting_int('polywar_rebellion_tick_progress',25,0,100000))
        status='active'; resolved=None; outcome='rebellion_progress'
        if after>=req:
            from services import polywar_capital_service as caps
            caps.transfer_capital_control(conn,season_id,cap,int(reb['capital_original_faction_id']),None,now)
            polywar._execute(c,'UPDATE polywar_cells SET owner_faction_id=%s WHERE season_id=%s AND x=%s AND y=%s',(int(reb['capital_original_faction_id']),season_id,cap['x'],cap['y']))
            status='succeeded'; resolved=now; outcome='rebellion_succeeded'
        polywar._execute(c,'UPDATE polywar_rebellions SET progress=%s,status=%s,last_tick_at=%s,resolved_at=%s,updated_at=%s WHERE id=%s AND status=\'active\'',(after,status,now,resolved,now,reb['id']))
        if polywar._rowcount(c):
            polywar._execute(c,'INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s)',(season_id,int(reb['capital_original_faction_id']),outcome,outcome,now)); changed.append(outcome)
    return changed

def rebellion_action(user_id:int,action_type:str,x:int,y:int,idempotency_key:str):
    if action_type not in {'support_rebellion','suppress_rebellion'}: raise ValueError('bad_action_type')
    if not idempotency_key or len(idempotency_key)>120: raise ValueError('bad_idempotency_key')
    from services import polywar_world_service as world
    conn=polywar.get_connection(); c=conn.cursor(); managed=False; ok=False
    try:
        polywar.init_polywar_schema(conn); init_rebellion_schema(conn); world.init_world_schema(conn)
        season=m._private_active_season(conn); sid=int(season['id']); world.ensure_world_initialized(conn,sid); conn.commit()
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: return dup
        managed=world._start_world_transaction(conn); world.lock_world_rows(conn,sid); polywar.assert_gameplay_mutation_allowed(conn,sid,_now())
        suffix='' if polywar._is_sqlite(conn) else ' FOR UPDATE'
        player=polywar.get_or_create_player(user_id,sid,conn)
        player=polywar._fetchone(c,'SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s'+suffix,(user_id,sid)) or player
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: ok=True; return dup
        fid=player.get('faction_id')
        if not fid: raise ValueError('faction_required')
        cap=polywar._fetchone(c,'SELECT * FROM polywar_capitals WHERE season_id=%s AND x=%s AND y=%s'+suffix,(sid,x,y))
        if not cap: raise ValueError('rebellion_required')
        _=polywar._fetchone(c,'SELECT * FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s'+suffix,(sid,x,y))
        ensure_rebellions(conn,sid)
        reb=polywar._fetchone(c,"SELECT * FROM polywar_rebellions WHERE season_id=%s AND capital_original_faction_id=%s AND controller_faction_id=%s AND status='active'"+suffix,(sid,cap['original_faction_id'],cap['controller_faction_id']))
        if not reb: raise ValueError('rebellion_inactive')
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: ok=True; return dup
        orig=int(reb['capital_original_faction_id']); ctrl=int(reb['controller_faction_id']); before=int(reb['progress']); req=int(reb['required_progress']); now=_now()
        if action_type=='support_rebellion':
            if int(fid)!=orig: raise ValueError('rebellion_support_forbidden')
            if not _adjacent_owner(conn,sid,x,y,orig): raise ValueError('rebellion_not_eligible')
            cost=public_rules()['support_energy_cost']; delta=public_rules()['support_progress']; after=min(req,before+delta); outcome='rebellion_supported'
        else:
            if int(fid)!=ctrl: raise ValueError('rebellion_suppress_forbidden')
            if not _adjacent_owner(conn,sid,x,y,ctrl): raise ValueError('rebellion_not_eligible')
            cost=public_rules()['suppress_energy_cost']; delta=-public_rules()['suppress_progress']; after=max(0,before+delta); outcome='rebellion_suppressed_progress' if after>0 else 'rebellion_fully_suppressed'
        e=polywar._energy(player)
        if e.get('is_locked'): raise ValueError('player_locked')
        if int(e['current_energy'])<cost: raise ValueError('insufficient_energy')
        _,_,energy=mines.spend_player_energy(conn,player,cost,now)
        status='active'; transfer=False
        if action_type=='suppress_rebellion' and after<=0: status='suppressed'
        if after>=req: status='succeeded'; outcome='rebellion_succeeded'; transfer=True
        polywar._execute(c,'UPDATE polywar_rebellions SET progress=%s,status=%s,last_action_at=%s,resolved_at=%s,updated_at=%s WHERE id=%s AND status=\'active\'',(after,status,now,now if status!='active' else None,now,reb['id']))
        if polywar._rowcount(c)!=1: raise ValueError('rebellion_inactive')
        polywar._execute(c,"INSERT INTO polywar_rebellion_contributions (rebellion_id,user_id,faction_id,support_contribution,suppress_contribution,first_action_at,last_action_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rebellion_id,user_id) DO UPDATE SET support_contribution=support_contribution+%s,suppress_contribution=suppress_contribution+%s,last_action_at=%s",(reb['id'],user_id,fid,delta if delta>0 else 0,-delta if delta<0 else 0,now,now,delta if delta>0 else 0,-delta if delta<0 else 0,now))
        if transfer:
            from services import polywar_capital_service as caps
            caps.transfer_capital_control(conn,sid,cap,orig,user_id,now)
            polywar._execute(c,'UPDATE polywar_cells SET owner_faction_id=%s WHERE season_id=%s AND x=%s AND y=%s',(orig,sid,x,y))
        payload={'capital':{'x':x,'y':y},'original_faction_id':orig,'controller_faction_id':ctrl,'progress_before':before,'progress_after':after,'required':req,'energy_cost':cost,'resolved_status':status,'capital_transfer':transfer,'energy':energy}
        mines.insert_outcome(conn,sid,user_id,idempotency_key,action_type,x,y,outcome,cost,payload,now); polywar._execute(c,'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,user_id,fid,outcome,outcome,now))
        ok=True; world._finish_world_transaction(conn,managed,ok); managed=False; payload.update({'ok':True,'outcome':outcome}); return payload
    except ValueError:
        raise
    finally:
        if managed: world._finish_world_transaction(conn,managed,ok)
        conn.close()
