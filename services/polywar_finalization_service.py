import hashlib,json,logging,time,secrets,threading
from collections import defaultdict, deque
from datetime import datetime,timedelta
from services import polywar_service as polywar
from services.polywar_world_service import NULL_STATE_FACTION_ID
logger=logging.getLogger(__name__)
def _now(): return datetime.utcnow()
def _setting_int(k,d,lo,hi): return polywar._setting_int(k,d,lo,hi)

def _begin(conn):
    c=conn.cursor()
    if polywar._is_sqlite(conn):
        last=None
        for i in range(20):
            try:
                c.execute('BEGIN IMMEDIATE'); return c
            except Exception as exc:
                if 'locked' not in str(exc).lower(): raise
                last=exc; time.sleep(0.025*(i+1))
        raise last
    polywar._execute(c,'BEGIN'); return c
def init_finalization_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor()
    try:
        from services.polywar_world_service import init_world_schema; init_world_schema(conn)
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_season_finalizations (season_id INTEGER PRIMARY KEY,version INTEGER NOT NULL,status TEXT NOT NULL,started_at TIMESTAMP NOT NULL,completed_at TIMESTAMP NULL,error_message TEXT NULL,results_hash TEXT NULL,created_at TIMESTAMP NOT NULL,updated_at TIMESTAMP NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_season_results (season_id INTEGER NOT NULL,faction_id INTEGER NOT NULL,rank INTEGER NULL,is_system INTEGER NOT NULL DEFAULT 0,influence_score BIGINT NOT NULL,controlled_cells_count INTEGER NOT NULL,controlled_sectors_count INTEGER NOT NULL,controlled_capitals_count INTEGER NOT NULL,active_members_count INTEGER NOT NULL,total_faction_contribution BIGINT NOT NULL,rifts_sealed_count INTEGER NOT NULL DEFAULT 0,rebellions_supported_count INTEGER NOT NULL DEFAULT 0,rebellions_suppressed_count INTEGER NOT NULL DEFAULT 0,is_winner INTEGER NOT NULL DEFAULT 0,snapshot_json TEXT NOT NULL,created_at TIMESTAMP NOT NULL,UNIQUE(season_id,faction_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS polywar_player_season_rewards (season_id INTEGER NOT NULL,user_id BIGINT NOT NULL,faction_id INTEGER NOT NULL,faction_rank INTEGER NULL,faction_contribution BIGINT NOT NULL,participation_reward BIGINT NOT NULL DEFAULT 0,contribution_reward BIGINT NOT NULL DEFAULT 0,placement_reward BIGINT NOT NULL DEFAULT 0,null_state_reward BIGINT NOT NULL DEFAULT 0,total_reward BIGINT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',claim_reference TEXT NOT NULL,calculated_at TIMESTAMP NOT NULL,claim_started_at TIMESTAMP NULL,claimed_at TIMESTAMP NULL,failed_at TIMESTAMP NULL,failure_reason TEXT NULL,UNIQUE(season_id,user_id),UNIQUE(claim_reference))""")
        for sql in ['CREATE INDEX IF NOT EXISTS idx_polywar_finalizations_status ON polywar_season_finalizations(status)','CREATE INDEX IF NOT EXISTS idx_polywar_results_season ON polywar_season_results(season_id,rank)','CREATE INDEX IF NOT EXISTS idx_polywar_rewards_user ON polywar_player_season_rewards(user_id,status)','CREATE INDEX IF NOT EXISTS idx_polywar_rewards_claim_ref ON polywar_player_season_rewards(claim_reference)']: c.execute(sql)
        if own: conn.commit()
    finally:
        if own: conn.close()
def claim_stale_seconds(): return polywar._setting_int('polywar_reward_claim_stale_seconds',300,30,86400)
def public_rules(): return {'min_contribution':_setting_int('polywar_reward_min_contribution',5,0,1000000),'participation':_setting_int('polywar_reward_participation',25,0,1000000),'per_contribution':_setting_int('polywar_reward_per_contribution',1,0,1000000),'contribution_cap':_setting_int('polywar_reward_contribution_cap',500,0,1000000),'first_place':_setting_int('polywar_reward_first_place',500,0,1000000),'second_place':_setting_int('polywar_reward_second_place',250,0,1000000),'third_place':_setting_int('polywar_reward_third_place',100,0,1000000),'null_state_defeat':_setting_int('polywar_reward_null_state_defeat',100,0,1000000),'max_per_player':_setting_int('polywar_reward_max_per_player',2000,0,10000000)}
def domination_capitals_required(): return _setting_int('polywar_domination_capitals_required',7,1,100)
def domination_hold_hours(): return _setting_int('polywar_domination_hold_hours',24,0,8760)
def null_victory_capitals_required(): return _setting_int('polywar_null_victory_capitals_required',7,1,100)
def null_victory_hold_hours(): return _setting_int('polywar_null_victory_hold_hours',12,0,8760)

def maybe_finalize_in_transaction(conn,season_id:int,now=None):
    now=now or _now(); c=conn.cursor()
    season=polywar._fetchone(c,'SELECT * FROM polywar_seasons WHERE id=%s'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
    if not season or season.get('status')!='active': return {'should_finalize':False}
    caps=polywar._fetchall(c,'SELECT controller_faction_id,COUNT(*) AS n FROM polywar_capitals WHERE season_id=%s GROUP BY controller_faction_id',(season_id,))
    counts={int(r['controller_faction_id']):int(r['n']) for r in caps if r.get('controller_faction_id') is not None}
    ends=season.get('ends_at')
    if isinstance(ends,str): ends=datetime.fromisoformat(ends)
    time_due=bool(ends and now>=ends)
    def _event_once(event_type,fid,msg):
        exists=polywar._fetchone(c,'SELECT id FROM polywar_events WHERE season_id=%s AND event_type=%s AND COALESCE(faction_id,0)=COALESCE(%s,0) AND created_at=%s LIMIT 1',(season_id,event_type,fid,now))
        if not exists: polywar._execute(c,'INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s)',(season_id,fid,event_type,msg,now))
    playable=[(fid,n) for fid,n in counts.items() if fid!=NULL_STATE_FACTION_ID and n>=domination_capitals_required()]
    candidate_type=None; candidate_fid=None; hold_hours=0
    if playable:
        candidate_fid=sorted(playable,key=lambda x:(-x[1],x[0]))[0][0]; candidate_type='domination'; hold_hours=domination_hold_hours()
    elif counts.get(NULL_STATE_FACTION_ID,0)>=null_victory_capitals_required():
        candidate_fid=NULL_STATE_FACTION_ID; candidate_type='null_state'; hold_hours=null_victory_hold_hours()
    if candidate_type:
        previous_fid=int(season.get('domination_faction_id') or 0)
        started=season.get('domination_started_at') if previous_fid==int(candidate_fid) else None
        if previous_fid and previous_fid!=int(candidate_fid): _event_once('domination_cancelled',previous_fid,'Domination hold cancelled.')
        if not started:
            if time_due: return {'should_finalize':True,'victory_type':'time','winner_faction_id':None}
            polywar._execute(c,'UPDATE polywar_seasons SET domination_faction_id=%s,domination_started_at=%s WHERE id=%s',(candidate_fid,now,season_id)); _event_once('domination_started',candidate_fid,'Domination hold started.'); return {'should_finalize':False}
        if isinstance(started,str): started=datetime.fromisoformat(started)
        if now>=started+timedelta(hours=hold_hours):
            return {'should_finalize':True,'victory_type':candidate_type,'winner_faction_id':(None if candidate_type=='null_state' else candidate_fid)}
        if time_due: return {'should_finalize':True,'victory_type':'time','winner_faction_id':None}
        return {'should_finalize':False}
    if season.get('domination_faction_id'):
        polywar._execute(c,'UPDATE polywar_seasons SET domination_faction_id=NULL,domination_started_at=NULL WHERE id=%s',(season_id,)); _event_once('domination_cancelled',int(season.get('domination_faction_id') or 0) or None,'Domination hold cancelled.')
    if time_due: return {'should_finalize':True,'victory_type':'time','winner_faction_id':None}
    return {'should_finalize':False}

def maybe_finalize(season_id:int,now=None):
    conn=polywar.get_connection(); init_finalization_schema(conn); managed=False
    try:
        _begin(conn); managed=True
        decision=maybe_finalize_in_transaction(conn,season_id,now or _now())
        if decision.get('should_finalize'):
            finalize_season_in_transaction(conn,season_id,decision.get('victory_type','time'),decision.get('winner_faction_id'),now or _now())
            conn.commit(); return True
        conn.commit(); return False
    except Exception:
        if managed:
            try: conn.rollback()
            except Exception: pass
        logger.exception('polywar_maybe_finalize_failed season_id=%s',season_id)
        raise
    finally:
        try: conn.close()
        except Exception: pass

def _mark_finalization_failed(season_id:int, exc:Exception):
    conn=polywar.get_connection()
    try:
        _begin(conn); c=conn.cursor(); now=_now()
        polywar._execute(c,"INSERT INTO polywar_season_finalizations (season_id,version,status,started_at,created_at,updated_at,error_message) VALUES (%s,1,'failed',%s,%s,%s,%s) ON CONFLICT (season_id) DO UPDATE SET status='failed',error_message=%s,updated_at=%s",(season_id,now,now,now,type(exc).__name__,type(exc).__name__,now))
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception: pass
    finally:
        try: conn.close()
        except Exception: pass

def finalize_season_in_transaction(conn,season_id:int,victory_type='time',winner_faction_id=None,now=None):
    now=now or _now(); c=conn.cursor()
    season=polywar._fetchone(c,'SELECT * FROM polywar_seasons WHERE id=%s'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
    if not season: return False
    marker=polywar._fetchone(c,"SELECT * FROM polywar_season_finalizations WHERE season_id=%s"+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
    if season.get('status')=='completed' or (marker and marker.get('status')=='completed'): return False
    existing_results=polywar._fetchone(c,'SELECT COUNT(*) AS n FROM polywar_season_results WHERE season_id=%s',(season_id,))
    existing_rewards=polywar._fetchone(c,'SELECT COUNT(*) AS n FROM polywar_player_season_rewards WHERE season_id=%s',(season_id,))
    if int((existing_results or {}).get('n') or 0)>0 or int((existing_rewards or {}).get('n') or 0)>0:
        raise RuntimeError('finalization_integrity_error')
    polywar._execute(c,"INSERT INTO polywar_season_finalizations (season_id,version,status,started_at,created_at,updated_at) VALUES (%s,1,'processing',%s,%s,%s) ON CONFLICT (season_id) DO NOTHING",(season_id,now,now,now))
    polywar._execute(c,"UPDATE polywar_seasons SET status='finalizing',victory_type=%s,winner_faction_id=%s,finalization_started_at=%s WHERE id=%s AND status IN ('active','finalizing')",(victory_type,winner_faction_id,now,season_id))
    polywar._execute(c,"INSERT INTO polywar_events (season_id,event_type,message,created_at) SELECT %s,'season_finalizing','PolyWar season finalization started.',%s WHERE NOT EXISTS (SELECT 1 FROM polywar_events WHERE season_id=%s AND event_type='season_finalizing')",(season_id,now,season_id))
    stats=polywar._fetchall(c,'SELECT s.*,f.is_system FROM polywar_faction_season_stats s JOIN polywar_factions f ON f.id=s.faction_id WHERE s.season_id=%s ORDER BY s.faction_id'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
    contrib={r['faction_id']:r['total'] for r in polywar._fetchall(c,'SELECT faction_id,COALESCE(SUM(faction_contribution),0) AS total FROM polywar_players WHERE season_id=%s GROUP BY faction_id',(season_id,))}
    rift_counts={r['faction_id']:r['n'] for r in polywar._fetchall(c,"SELECT sealed_by_faction_id AS faction_id,COUNT(*) AS n FROM polywar_null_rifts WHERE season_id=%s AND status='sealed' AND sealed_by_faction_id IS NOT NULL GROUP BY sealed_by_faction_id",(season_id,))}
    sup_counts={r['faction_id']:r['n'] for r in polywar._fetchall(c,"SELECT rc.faction_id,COUNT(*) AS n FROM polywar_rebellion_contributions rc JOIN polywar_rebellions r ON r.id=rc.rebellion_id WHERE r.season_id=%s AND rc.support_contribution>0 GROUP BY rc.faction_id",(season_id,))}
    suppress_counts={r['faction_id']:r['n'] for r in polywar._fetchall(c,"SELECT rc.faction_id,COUNT(*) AS n FROM polywar_rebellion_contributions rc JOIN polywar_rebellions r ON r.id=rc.rebellion_id WHERE r.season_id=%s AND rc.suppress_contribution>0 GROUP BY rc.faction_id",(season_id,))}
    playable=sorted([s for s in stats if int(s.get('is_system') or 0)==0],key=lambda s:(-int(s.get('influence_score') or 0),-int(s.get('controlled_capitals_count') or 0),-int(s.get('controlled_sectors_count') or 0),-int(s.get('controlled_cells_count') or 0),-int(contrib.get(s['faction_id']) or 0),int(s['faction_id'])))
    ranks={s['faction_id']:i+1 for i,s in enumerate(playable)}
    if winner_faction_id is None and playable and victory_type!='null_state': winner_faction_id=playable[0]['faction_id']
    for srow in stats:
        fid=int(srow['faction_id']); is_system=1 if fid==NULL_STATE_FACTION_ID or int(srow.get('is_system') or 0) else 0; rank=None if is_system else ranks.get(fid)
        snap={'faction_id':fid,'rank':rank,'is_system':is_system,'influence_score':int(srow.get('influence_score') or 0),'controlled_cells_count':int(srow.get('controlled_cells_count') or 0),'controlled_sectors_count':int(srow.get('controlled_sectors_count') or 0),'controlled_capitals_count':int(srow.get('controlled_capitals_count') or 0),'active_members_count':int(srow.get('active_members_count') or 0),'total_faction_contribution':int(contrib.get(fid) or 0),'rifts_sealed_count':int(rift_counts.get(fid) or 0),'rebellions_supported_count':int(sup_counts.get(fid) or 0),'rebellions_suppressed_count':int(suppress_counts.get(fid) or 0),'is_winner':1 if fid==winner_faction_id else 0}
        polywar._execute(c,'INSERT INTO polywar_season_results (season_id,faction_id,rank,is_system,influence_score,controlled_cells_count,controlled_sectors_count,controlled_capitals_count,active_members_count,total_faction_contribution,rifts_sealed_count,rebellions_supported_count,rebellions_suppressed_count,is_winner,snapshot_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,faction_id) DO NOTHING',(season_id,fid,rank,is_system,snap['influence_score'],snap['controlled_cells_count'],snap['controlled_sectors_count'],snap['controlled_capitals_count'],snap['active_members_count'],snap['total_faction_contribution'],snap['rifts_sealed_count'],snap['rebellions_supported_count'],snap['rebellions_suppressed_count'],snap['is_winner'],json.dumps(snap,sort_keys=True),now))
    _calculate_rewards(conn,season_id,ranks,winner_faction_id)
    result_rows=polywar._fetchall(c,'SELECT faction_id,rank,is_system,influence_score,controlled_cells_count,controlled_sectors_count,controlled_capitals_count,active_members_count,total_faction_contribution,rifts_sealed_count,rebellions_supported_count,rebellions_suppressed_count,is_winner FROM polywar_season_results WHERE season_id=%s ORDER BY faction_id',(season_id,))
    reward_rows=polywar._fetchall(c,'SELECT user_id,faction_id,faction_rank,faction_contribution,participation_reward,contribution_reward,placement_reward,null_state_reward,total_reward,status,claim_reference FROM polywar_player_season_rewards WHERE season_id=%s ORDER BY user_id',(season_id,))
    snapshot={'season_id':season_id,'version':1,'victory_type':victory_type,'winner_faction_id':winner_faction_id,'completed_at':polywar._iso(now),'factions':result_rows,'rewards':reward_rows}
    h=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    polywar._execute(c,"UPDATE polywar_season_finalizations SET status='completed',completed_at=%s,results_hash=%s,error_message=NULL,updated_at=%s WHERE season_id=%s",(now,h,now,season_id))
    polywar._execute(c,"UPDATE polywar_seasons SET status='completed',completed_at=%s,finalized_at=%s,results_hash=%s,winner_faction_id=%s,victory_type=%s WHERE id=%s",(now,now,h,winner_faction_id,victory_type,season_id))
    polywar._execute(c,"INSERT INTO polywar_events (season_id,event_type,message,created_at) SELECT %s,'season_completed','PolyWar season completed.',%s WHERE NOT EXISTS (SELECT 1 FROM polywar_events WHERE season_id=%s AND event_type='season_completed')",(season_id,now,season_id))
    if victory_type=='null_state': polywar._execute(c,"INSERT INTO polywar_events (season_id,event_type,message,created_at) SELECT %s,'null_state_victory','The Null State has won.',%s WHERE NOT EXISTS (SELECT 1 FROM polywar_events WHERE season_id=%s AND event_type='null_state_victory')",(season_id,now,season_id))
    elif winner_faction_id: polywar._execute(c,"INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) SELECT %s,%s,'season_winner','Season winner determined.',%s WHERE NOT EXISTS (SELECT 1 FROM polywar_events WHERE season_id=%s AND event_type='season_winner')",(season_id,winner_faction_id,now,season_id))
    if not polywar._fetchone(c,"SELECT id FROM polywar_seasons WHERE status='active' LIMIT 1"):
        start=now; end=start+timedelta(days=polywar._setting_int('polywar_season_days',30,1,365)); polywar._execute(c,"INSERT INTO polywar_seasons (name,status,starts_at,ends_at,secret_seed,created_at) VALUES (%s,'active',%s,%s,%s,%s)",('Season next',start,end,secrets.token_hex(32),start))
    return True

def finalize_season(season_id:int,victory_type='time',winner_faction_id=None,now=None):
    conn=polywar.get_connection(); init_finalization_schema(conn); managed=False
    try:
        _begin(conn); managed=True
        out=finalize_season_in_transaction(conn,season_id,victory_type,winner_faction_id,now or _now())
        conn.commit(); return out
    except Exception as exc:
        if managed:
            try: conn.rollback()
            except Exception: pass
            _mark_finalization_failed(season_id, exc)
        raise
    finally:
        try: conn.close()
        except Exception: pass

def _calculate_rewards(conn,sid,ranks,winner):
    c=conn.cursor(); rules=public_rules(); out=[]; defeated=bool(polywar._fetchone(c,"SELECT 1 FROM polywar_null_state WHERE season_id=%s AND status='defeated'",(sid,)))
    players=polywar._fetchall(c,'SELECT * FROM polywar_players WHERE season_id=%s AND faction_id IS NOT NULL',(sid,))
    for p in players:
        contrib=int(p.get('faction_contribution') or 0); fid=int(p['faction_id']); rank=ranks.get(fid); eligible=contrib>=rules['min_contribution'] and fid!=NULL_STATE_FACTION_ID
        part=rules['participation'] if eligible else 0; cr=min(contrib*rules['per_contribution'],rules['contribution_cap']) if eligible else 0; place={1:rules['first_place'],2:rules['second_place'],3:rules['third_place']}.get(rank,0) if eligible else 0; ns=rules['null_state_defeat'] if eligible and defeated else 0; total=max(0,min(rules['max_per_player'],part+cr+place+ns)); status='pending' if total>0 else 'ineligible'; ref=f'polywar:season:{sid}:user:{int(p["user_id"])}'
        polywar._execute(c,'INSERT INTO polywar_player_season_rewards (season_id,user_id,faction_id,faction_rank,faction_contribution,participation_reward,contribution_reward,placement_reward,null_state_reward,total_reward,status,claim_reference,calculated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (season_id,user_id) DO NOTHING',(sid,p['user_id'],fid,rank,contrib,part,cr,place,ns,total,status,ref,_now())); out.append({'user_id':p['user_id'],'total_reward':total})
    return out
def get_results(season_id=None,user_id=None):
    conn=polywar.get_connection(); init_finalization_schema(conn)
    try:
        _begin(conn)
        if season_id:
            candidate=polywar._fetchone(conn.cursor(),'SELECT id,status FROM polywar_seasons WHERE id=%s',(season_id,))
            if candidate and candidate.get('status')=='active':
                decision=maybe_finalize_in_transaction(conn,int(season_id))
                if decision.get('should_finalize'): finalize_season_in_transaction(conn,int(season_id),decision.get('victory_type','time'),decision.get('winner_faction_id'))
        else:
            active=polywar._fetchone(conn.cursor(),"SELECT id FROM polywar_seasons WHERE status='active' ORDER BY id DESC LIMIT 1")
            if active:
                decision=maybe_finalize_in_transaction(conn,int(active['id']))
                if decision.get('should_finalize'): finalize_season_in_transaction(conn,int(active['id']),decision.get('victory_type','time'),decision.get('winner_faction_id'))
        conn.commit()
        c=conn.cursor()
        if not season_id:
            row=polywar._fetchone(c,"SELECT id FROM polywar_seasons WHERE status='completed' ORDER BY completed_at DESC LIMIT 1")
            if not row: raise ValueError('results_not_ready')
            season_id=row['id']
        season=polywar._fetchone(c,'SELECT id,name,status,completed_at,victory_type,winner_faction_id,results_hash FROM polywar_seasons WHERE id=%s',(season_id,))
        if not season or season.get('status')!='completed': raise ValueError('results_not_ready')
        rows=polywar._fetchall(c,'SELECT * FROM polywar_season_results WHERE season_id=%s ORDER BY COALESCE(rank,999),faction_id',(season_id,)); reward=polywar._fetchone(c,'SELECT * FROM polywar_player_season_rewards WHERE season_id=%s AND user_id=%s',(season_id,user_id)) if user_id else None
        return {'ok':True,'season':{k:polywar._iso(v) if str(k).endswith('_at') else v for k,v in season.items()},'faction_ranking':[r for r in rows if not int(r.get('is_system') or 0)],'null_state_result':next((r for r in rows if int(r.get('is_system') or 0)),None),'current_user_reward':reward}
    except Exception:
        polywar._safe_rollback(conn); raise
    finally:
        conn.close()

def _ledger_entry(ref):
    from services.airdrop_points_service import get_airdrop_points_ledger_entry_by_reference
    return get_airdrop_points_ledger_entry_by_reference(ref)

def _validate_ledger_reward(entry, reward, user_id):
    from services.airdrop_points_service import _to_decimal
    if int(entry.get('user_id') or 0) != int(user_id) or str(entry.get('reason')) != 'polywar_season_reward' or _to_decimal(entry.get('amount')) != _to_decimal(reward.get('total_reward')):
        raise RuntimeError('airdrop_external_reference_mismatch')


_CLAIM_RATE_LOCK=threading.Lock(); _CLAIM_RATE=defaultdict(deque)
def _claim_limiter(user_id,limit=10,window=60,max_entries=4096):
    now=time.time(); key=int(user_id or 0)
    with _CLAIM_RATE_LOCK:
        for k in list(_CLAIM_RATE.keys()):
            q=_CLAIM_RATE[k]
            while q and now-q[0]>window: q.popleft()
            if not q: _CLAIM_RATE.pop(k,None)
        if key not in _CLAIM_RATE and len(_CLAIM_RATE)>=max_entries:
            oldest=min(_CLAIM_RATE, key=lambda k: _CLAIM_RATE[k][0] if _CLAIM_RATE[k] else now)
            _CLAIM_RATE.pop(oldest,None)
        q=_CLAIM_RATE[key]
        if len(q)>=limit: raise ValueError('rate_limited')
        q.append(now)

def _claim_tx_begin():
    conn=polywar.get_connection(); _begin(conn); return conn

def claim_reward(user_id:int,season_id:int,idempotency_key:str):
    if not idempotency_key or len(str(idempotency_key))>120: raise ValueError('bad_idempotency_key')
    init_finalization_schema()
    conn=None; reward=None
    try:
        conn=_claim_tx_begin(); c=conn.cursor()
        season=polywar._fetchone(c,'SELECT status FROM polywar_seasons WHERE id=%s'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(season_id,))
        if not season or season.get('status')!='completed': raise ValueError('results_not_ready')
        r=polywar._fetchone(c,'SELECT * FROM polywar_player_season_rewards WHERE season_id=%s AND user_id=%s'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(season_id,user_id))
        if not r: raise ValueError('reward_not_found')
        if int(r.get('total_reward') or 0)<=0: raise ValueError('reward_ineligible')
        if r.get('status')=='claimed':
            conn.commit(); return {'ok':True,'claimed':True,'duplicate':True,'reward':r}
        if r.get('status')=='processing':
            ref=r['claim_reference']; entry=_ledger_entry(ref)
            if entry:
                _validate_ledger_reward(entry,r,user_id)
                polywar._execute(c,"UPDATE polywar_player_season_rewards SET status='claimed',claimed_at=COALESCE(claimed_at,%s) WHERE season_id=%s AND user_id=%s AND status='processing'",(_now(),season_id,user_id))
                polywar._execute(c,"INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) SELECT %s,%s,%s,'season_reward_claimed','Season reward claimed.',%s WHERE NOT EXISTS (SELECT 1 FROM polywar_events WHERE season_id=%s AND user_id=%s AND event_type='season_reward_claimed')",(season_id,user_id,r.get('faction_id'),_now(),season_id,user_id))
                conn.commit(); return {'ok':True,'claimed':True,'duplicate':True,'reward':r}
            started=r.get('claim_started_at')
            if isinstance(started,str):
                try: started=datetime.fromisoformat(started)
                except Exception: started=None
            if started and (_now()-started).total_seconds() < claim_stale_seconds():
                conn.commit(); return {'ok':False,'error':'reward_claim_processing'}
            polywar._execute(c,"UPDATE polywar_player_season_rewards SET status='failed',failed_at=%s,failure_reason='stale_processing' WHERE season_id=%s AND user_id=%s AND status='processing'",(_now(),season_id,user_id))
            if polywar._rowcount(c)!=1: raise ValueError('reward_claim_processing')
            r=dict(r); r['status']='failed'
        if r.get('status') not in ('pending','failed'): raise ValueError('reward_ineligible')
        _claim_limiter(user_id)
        now=_now(); polywar._execute(c,"UPDATE polywar_player_season_rewards SET status='processing',claim_started_at=%s,failed_at=NULL,failure_reason=NULL WHERE season_id=%s AND user_id=%s AND status IN ('pending','failed')",(now,season_id,user_id))
        if polywar._rowcount(c)!=1: raise ValueError('reward_claim_processing')
        conn.commit(); reward=r
    except ValueError:
        if conn: polywar._safe_rollback(conn)
        raise
    finally:
        if conn:
            try: conn.close()
            except Exception: pass
    try:
        from services.airdrop_points_service import award_airdrop_points_idempotent
        res=award_airdrop_points_idempotent(user_id,'polywar_season_reward',int(reward['total_reward']),{'season_id':season_id},reward['claim_reference'])
    except Exception as exc:
        conn=_claim_tx_begin(); c=conn.cursor()
        try:
            polywar._execute(c,"UPDATE polywar_player_season_rewards SET status='failed',failed_at=%s,failure_reason=%s WHERE season_id=%s AND user_id=%s AND status='processing'",(_now(),type(exc).__name__,season_id,user_id)); conn.commit()
        finally: conn.close()
        raise
    conn=_claim_tx_begin(); c=conn.cursor()
    try:
        r=polywar._fetchone(c,'SELECT * FROM polywar_player_season_rewards WHERE season_id=%s AND user_id=%s'+('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(season_id,user_id))
        if r and r.get('status')=='claimed':
            conn.commit(); return {'ok':True,'claimed':True,'duplicate':True,'airdrop':res}
        polywar._execute(c,"UPDATE polywar_player_season_rewards SET status='claimed',claimed_at=%s WHERE season_id=%s AND user_id=%s AND status='processing'",(_now(),season_id,user_id))
        if polywar._rowcount(c)!=1: raise ValueError('reward_claim_processing')
        polywar._execute(c,"INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) SELECT %s,%s,%s,'season_reward_claimed','Season reward claimed.',%s WHERE NOT EXISTS (SELECT 1 FROM polywar_events WHERE season_id=%s AND user_id=%s AND event_type='season_reward_claimed')",(season_id,user_id,r.get('faction_id') if r else None,_now(),season_id,user_id))
        conn.commit(); return {'ok':True,'claimed':True,'airdrop':res}
    except Exception:
        polywar._safe_rollback(conn); raise
    finally: conn.close()
