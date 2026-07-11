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
        if own: conn.commit()
    finally:
        if own: conn.close()

def ensure_rebellions(conn,season_id:int):
    init_rebellion_schema(conn); c=conn.cursor(); now=_now(); rules=public_rules()
    try: caps=polywar._fetchall(c,'SELECT * FROM polywar_capitals WHERE season_id=%s',(season_id,))
    except Exception: return []
    made=[]
    for cap in caps:
        orig=int(cap['original_faction_id']); ctrl=int(cap['controller_faction_id'])
        if ctrl==orig or ctrl==NULL_STATE_FACTION_ID: continue
        exists=polywar._fetchone(c,"SELECT * FROM polywar_rebellions WHERE season_id=%s AND capital_original_faction_id=%s AND status IN ('pending','active')",(season_id,orig))
        if exists: continue
        started=cap.get('captured_at') or cap.get('controlled_since') or now
        if isinstance(started,str): started=datetime.fromisoformat(started)
        eligible=started+timedelta(hours=rules['grace_hours'])
        status='active' if now>=eligible else 'pending'
        polywar._execute(c,"INSERT INTO polywar_rebellions (season_id,capital_original_faction_id,controller_faction_id,status,progress,required_progress,occupation_started_at,eligible_at,started_at,created_at,updated_at) VALUES (%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s)",(season_id,orig,ctrl,status,rules['required'],started,eligible,now if status=='active' else None,now,now)); made.append(orig)
    return made

def get_public_rebellions(conn,season_id:int):
    ensure_rebellions(conn,season_id); rows=polywar._fetchall(conn.cursor(),"SELECT * FROM polywar_rebellions WHERE season_id=%s AND status IN ('pending','active') ORDER BY id",(season_id,))
    return [{k:polywar._iso(v) if str(k).endswith('_at') else v for k,v in r.items()} for r in rows]

def rebellion_action(user_id:int,action_type:str,x:int,y:int,idempotency_key:str):
    if action_type not in {'support_rebellion','suppress_rebellion'}: raise ValueError('bad_action_type')
    if not idempotency_key or len(idempotency_key)>120: raise ValueError('bad_idempotency_key')
    from services import polywar_world_service as world
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        polywar.init_polywar_schema(conn); init_rebellion_schema(conn); season=m._private_active_season(conn); sid=int(season['id']); world.ensure_world_caught_up(conn,sid)
        dup=mines.duplicate_outcome_response(conn,sid,user_id,idempotency_key)
        if dup: return dup
        player=polywar.get_or_create_player(user_id,sid,conn); fid=player.get('faction_id')
        if not fid: raise ValueError('faction_required')
        e=polywar._energy(player)
        if e.get('is_locked'): raise ValueError('player_locked')
        cap=polywar._fetchone(c,'SELECT * FROM polywar_capitals WHERE season_id=%s AND x=%s AND y=%s',(sid,x,y))
        if not cap: raise ValueError('rebellion_required')
        ensure_rebellions(conn,sid); reb=polywar._fetchone(c,"SELECT * FROM polywar_rebellions WHERE season_id=%s AND capital_original_faction_id=%s AND controller_faction_id=%s AND status='active'",(sid,cap['original_faction_id'],cap['controller_faction_id']))
        if not reb: raise ValueError('rebellion_inactive')
        orig=int(reb['capital_original_faction_id']); ctrl=int(reb['controller_faction_id']); before=int(reb['progress']); req=int(reb['required_progress']); now=_now()
        if action_type=='support_rebellion':
            if int(fid)!=orig: raise ValueError('rebellion_support_forbidden')
            cost=public_rules()['support_energy_cost']; delta=public_rules()['support_progress']; after=min(req,before+delta); outcome='rebellion_supported'
        else:
            if int(fid)!=ctrl: raise ValueError('rebellion_suppress_forbidden')
            cost=public_rules()['suppress_energy_cost']; delta=-public_rules()['suppress_progress']; after=max(0,before+delta); outcome='rebellion_suppressed_progress' if after>0 else 'rebellion_fully_suppressed'
        if int(e['current_energy'])<cost: raise ValueError('insufficient_energy')
        _,_,energy=mines.spend_player_energy(conn,player,cost,now)
        status='active'; transfer=False
        if after>=req:
            status='succeeded'; outcome='rebellion_succeeded'; transfer=True
        polywar._execute(c,'UPDATE polywar_rebellions SET progress=%s,status=%s,last_action_at=%s,resolved_at=%s,updated_at=%s WHERE id=%s',(after,status,now,now if status!='active' else None,now,reb['id']))
        polywar._execute(c,"INSERT INTO polywar_rebellion_contributions (rebellion_id,user_id,faction_id,support_contribution,suppress_contribution,first_action_at,last_action_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rebellion_id,user_id) DO UPDATE SET support_contribution=support_contribution+%s,suppress_contribution=suppress_contribution+%s,last_action_at=%s",(reb['id'],user_id,fid,delta if delta>0 else 0,-delta if delta<0 else 0,now,now,delta if delta>0 else 0,-delta if delta<0 else 0,now))
        if transfer:
            from services import polywar_capital_service as caps
            caps.transfer_capital_control(conn,sid,orig,orig,user_id,now) if hasattr(caps,'transfer_capital_control') else polywar._execute(c,'UPDATE polywar_capitals SET controller_faction_id=%s WHERE id=%s',(orig,cap['id']))
            polywar._execute(c,'UPDATE polywar_cells SET owner_faction_id=%s WHERE season_id=%s AND x=%s AND y=%s',(orig,sid,x,y))
        payload={'capital':{'x':x,'y':y},'original_faction_id':orig,'controller_faction_id':ctrl,'progress_before':before,'progress_after':after,'required':req,'energy_cost':cost,'resolved_status':status,'capital_transfer':transfer,'energy':energy}
        mines.insert_outcome(conn,sid,user_id,idempotency_key,action_type,x,y,outcome,cost,payload,now); polywar._execute(c,'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,user_id,fid,outcome,outcome,now)); conn.commit(); payload.update({'ok':True,'outcome':outcome}); return payload
    except ValueError: polywar._safe_rollback(conn); raise
    finally: conn.close()
