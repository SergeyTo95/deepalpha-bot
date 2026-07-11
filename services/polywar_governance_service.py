import time, threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from services import polywar_service as polywar
from services import polywar_map_service as m
from services import polywar_capital_service as capitals

_RATE_LOCK=threading.Lock(); _RATE=defaultdict(deque); RATE_WINDOW=10; RATE_MAX=30

def _setting_int(k,d,lo,hi): return polywar._setting_int(k,d,lo,hi)
def election_hours(): return _setting_int('polywar_commander_election_hours',24,1,168)
def term_hours(): return _setting_int('polywar_commander_term_hours',168,1,8760)
def min_contribution(): return _setting_int('polywar_commander_min_contribution',5,0,10**9)
def min_members(): return _setting_int('polywar_commander_min_members_for_election',2,1,1000000)
def max_statement_length(): return _setting_int('polywar_commander_max_statement_length',280,0,1000)
def max_orders(): return _setting_int('polywar_commander_order_limit',5,0,100)
def order_duration_hours(): return _setting_int('polywar_capital_order_duration_hours',24,1,168)

def public_rules(): return {'election_hours':election_hours(),'term_hours':term_hours(),'min_contribution':min_contribution(),'min_members':min_members(),'max_statement_length':max_statement_length(),'max_orders':max_orders(),'order_duration_hours':order_duration_hours()}

def _rate(uid):
    now=time.monotonic(); q=_RATE[int(uid)]
    with _RATE_LOCK:
        while q and now-q[0]>RATE_WINDOW: q.popleft()
        if len(q)>=RATE_MAX: raise ValueError('rate_limited')
        q.append(now)

def init_polywar_governance_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor(); id_sql='INTEGER PRIMARY KEY AUTOINCREMENT' if polywar._is_sqlite(conn) else 'SERIAL PRIMARY KEY'
    try:
        capitals.init_polywar_capital_schema(conn)
        c.execute(f'''CREATE TABLE IF NOT EXISTS polywar_commander_elections (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, status TEXT NOT NULL, starts_at TIMESTAMP NOT NULL, ends_at TIMESTAMP NOT NULL, finalized_at TIMESTAMP NULL, winner_user_id BIGINT NULL, created_at TIMESTAMP NOT NULL)''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_polywar_open_election ON polywar_commander_elections(season_id,faction_id,status)')
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_commander_candidates (election_id INTEGER NOT NULL, user_id BIGINT NOT NULL, faction_id INTEGER NOT NULL, statement TEXT NULL, contribution_at_nomination BIGINT NOT NULL DEFAULT 0, nominated_at TIMESTAMP NOT NULL, withdrawn_at TIMESTAMP NULL, UNIQUE(election_id,user_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_commander_votes (election_id INTEGER NOT NULL, voter_user_id BIGINT NOT NULL, candidate_user_id BIGINT NOT NULL, faction_id INTEGER NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, UNIQUE(election_id,voter_user_id))''')
        c.execute(f'''CREATE TABLE IF NOT EXISTS polywar_faction_orders (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, commander_user_id BIGINT NOT NULL, order_type TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, sector_x INTEGER NOT NULL, sector_y INTEGER NOT NULL, message TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMP NOT NULL, expires_at TIMESTAMP NOT NULL, cancelled_at TIMESTAMP NULL, updated_at TIMESTAMP NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_polywar_orders_scope ON polywar_faction_orders(season_id,faction_id,active,expires_at)')
        conn.commit()
    finally:
        if own: conn.close()

def _active_election(conn,sid,fid): return polywar._fetchone(conn.cursor(),"SELECT * FROM polywar_commander_elections WHERE season_id=%s AND faction_id=%s AND status='open' ORDER BY id DESC LIMIT 1",(sid,fid))

def _ensure_election(conn,sid,fid,now):
    stat=polywar._fetchone(conn.cursor(),'SELECT * FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s',(sid,fid)) or {}
    if stat.get('commander_user_id') and (not stat.get('commander_term_ends_at') or str(stat.get('commander_term_ends_at'))>polywar._iso(now)): return None
    if stat.get('commander_user_id'):
        polywar._execute(conn.cursor(),'UPDATE polywar_faction_season_stats SET commander_user_id=NULL, commander_since=NULL, commander_term_ends_at=NULL WHERE season_id=%s AND faction_id=%s',(sid,fid))
    if int(stat.get('active_members_count') or 0)<min_members(): return None
    e=_active_election(conn,sid,fid)
    if e: return e
    starts=now; ends=now+timedelta(hours=election_hours())
    polywar._execute(conn.cursor(),'INSERT INTO polywar_commander_elections (season_id,faction_id,status,starts_at,ends_at,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,fid,'open',starts,ends,now))
    return _active_election(conn,sid,fid)

def finalize_due(conn,sid,fid,now):
    e=_active_election(conn,sid,fid)
    if not e or str(e['ends_at'])>polywar._iso(now): return e
    rows=polywar._fetchall(conn.cursor(),'''SELECT c.user_id,c.contribution_at_nomination,c.nominated_at,COUNT(v.voter_user_id) votes FROM polywar_commander_candidates c LEFT JOIN polywar_commander_votes v ON v.election_id=c.election_id AND v.candidate_user_id=c.user_id WHERE c.election_id=%s AND c.withdrawn_at IS NULL GROUP BY c.user_id,c.contribution_at_nomination,c.nominated_at''',(e['id'],))
    winner=None
    if rows:
        rows.sort(key=lambda r:(-int(r['votes'] or 0),-int(r['contribution_at_nomination'] or 0),str(r['nominated_at']),int(r['user_id'])))
        winner=int(rows[0]['user_id'])
        polywar._execute(conn.cursor(),'UPDATE polywar_faction_season_stats SET commander_user_id=%s, commander_since=%s, commander_term_ends_at=%s WHERE season_id=%s AND faction_id=%s',(winner,now,now+timedelta(hours=term_hours()),sid,fid))
        polywar._execute(conn.cursor(),'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,winner,fid,'commander_elected','Commander elected',now))
    polywar._execute(conn.cursor(),"UPDATE polywar_commander_elections SET status='finalized', finalized_at=%s, winner_user_id=%s WHERE id=%s",(now,winner,e['id']))
    return _ensure_election(conn,sid,fid,now) if winner is None else None

def _ctx(conn,user_id):
    polywar.init_polywar_schema(conn); init_polywar_governance_schema(conn); s=polywar.ensure_active_season(conn); sid=int(s['id']); polywar._insert_player_if_missing(conn,user_id,sid); p=polywar.get_or_create_player(user_id,sid,conn); fid=p.get('faction_id')
    if fid: finalize_due(conn,sid,int(fid),datetime.utcnow()); _ensure_election(conn,sid,int(fid),datetime.utcnow()); conn.commit()
    return sid,p

def get_governance(user_id:int):
    conn=polywar.get_connection()
    try:
        sid,p=_ctx(conn,user_id); fid=p.get('faction_id')
        if not fid: return {'ok':True,'season_id':sid,'faction_required':True,'rules':public_rules()}
        e=_active_election(conn,sid,fid); stat=polywar._fetchone(conn.cursor(),'SELECT commander_user_id,commander_since,commander_term_ends_at FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s',(sid,fid)) or {}
        candidates=[]; vote=None
        if e:
            rows=polywar._fetchall(conn.cursor(),'''SELECT c.user_id,c.statement,c.contribution_at_nomination,c.nominated_at,c.withdrawn_at,COUNT(v.voter_user_id) vote_count FROM polywar_commander_candidates c LEFT JOIN polywar_commander_votes v ON v.election_id=c.election_id AND v.candidate_user_id=c.user_id WHERE c.election_id=%s GROUP BY c.user_id,c.statement,c.contribution_at_nomination,c.nominated_at,c.withdrawn_at''',(e['id'],))
            candidates=[dict(r) for r in rows]; vr=polywar._fetchone(conn.cursor(),'SELECT candidate_user_id FROM polywar_commander_votes WHERE election_id=%s AND voter_user_id=%s',(e['id'],user_id)); vote=vr and vr['candidate_user_id']
        return {'ok':True,'season_id':sid,'commander':stat,'active_election':e,'candidates':candidates,'current_user_vote':vote,'current_user_is_candidate':any(int(c['user_id'])==user_id and not c.get('withdrawn_at') for c in candidates),'nomination_eligibility':{'eligible':int(p.get('faction_contribution') or 0)>=min_contribution()},'orders':list_orders(conn,sid,fid),'rules':public_rules(),'server_timestamp':int(time.time())}
    finally: conn.close()

def nominate(user_id:int, statement:str='', active=True):
    _rate(user_id); conn=polywar.get_connection()
    try:
        sid,p=_ctx(conn,user_id); fid=p.get('faction_id')
        if not fid: raise ValueError('faction_required')
        e=_active_election(conn,sid,fid)
        if not e: raise ValueError('election_unavailable')
        now=datetime.utcnow(); stmt=(statement or '')[:max_statement_length()]
        if active:
            if int(p.get('faction_contribution') or 0)<min_contribution(): raise ValueError('contribution_required')
            polywar._execute(conn.cursor(),'INSERT INTO polywar_commander_candidates (election_id,user_id,faction_id,statement,contribution_at_nomination,nominated_at,withdrawn_at) VALUES (%s,%s,%s,%s,%s,%s,NULL) ON CONFLICT (election_id,user_id) DO UPDATE SET statement=excluded.statement, withdrawn_at=NULL',(e['id'],user_id,fid,stmt,int(p.get('faction_contribution') or 0),now))
        else:
            polywar._execute(conn.cursor(),'UPDATE polywar_commander_candidates SET withdrawn_at=COALESCE(withdrawn_at,%s) WHERE election_id=%s AND user_id=%s',(now,e['id'],user_id))
        conn.commit(); return get_governance(user_id)
    finally: conn.close()

def vote(user_id:int,candidate_user_id:int):
    _rate(user_id); conn=polywar.get_connection()
    try:
        sid,p=_ctx(conn,user_id); fid=p.get('faction_id')
        if not fid: raise ValueError('faction_required')
        e=_active_election(conn,sid,fid)
        if not e: raise ValueError('election_unavailable')
        cand=polywar._fetchone(conn.cursor(),'SELECT * FROM polywar_commander_candidates WHERE election_id=%s AND user_id=%s AND faction_id=%s',(e['id'],candidate_user_id,fid))
        if not cand: raise ValueError('candidate_not_found')
        if cand.get('withdrawn_at'): raise ValueError('candidate_withdrawn')
        now=datetime.utcnow(); existing=polywar._fetchone(conn.cursor(),'SELECT candidate_user_id FROM polywar_commander_votes WHERE election_id=%s AND voter_user_id=%s',(e['id'],user_id)); dup=existing and int(existing['candidate_user_id'])==int(candidate_user_id)
        polywar._execute(conn.cursor(),'INSERT INTO polywar_commander_votes (election_id,voter_user_id,candidate_user_id,faction_id,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (election_id,voter_user_id) DO UPDATE SET candidate_user_id=excluded.candidate_user_id, updated_at=excluded.updated_at',(e['id'],user_id,candidate_user_id,fid,now,now))
        conn.commit(); res=get_governance(user_id); res['duplicate']=bool(dup); return res
    finally: conn.close()

def _is_commander(conn,sid,fid,user_id):
    r=polywar._fetchone(conn.cursor(),'SELECT commander_user_id,commander_term_ends_at FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s',(sid,fid))
    return r and int(r.get('commander_user_id') or 0)==int(user_id) and str(r.get('commander_term_ends_at'))>polywar._iso(datetime.utcnow())

def list_orders(conn,sid,fid):
    now=datetime.utcnow()
    return polywar._fetchall(conn.cursor(),'SELECT id,order_type,x,y,message,expires_at,created_at FROM polywar_faction_orders WHERE season_id=%s AND faction_id=%s AND active=1 AND cancelled_at IS NULL AND expires_at>%s ORDER BY created_at DESC',(sid,fid,now))

def enrich_chunks(conn,sid,fid,chunks):
    if not fid:
        for ch in chunks: ch['orders']=[]
        return chunks
    for ch in chunks:
        x0,y0,w,h=ch['chunk_x']*ch['chunk_size'],ch['chunk_y']*ch['chunk_size'],ch['width'],ch['height']
        ch['orders']=polywar._fetchall(conn.cursor(),'SELECT id,order_type,x,y,message,expires_at,created_at FROM polywar_faction_orders WHERE season_id=%s AND faction_id=%s AND active=1 AND cancelled_at IS NULL AND expires_at>CURRENT_TIMESTAMP AND x >= %s AND x < %s AND y >= %s AND y < %s',(sid,fid,x0,x0+w,y0,y0+h))
    return chunks

def upsert_order(user_id:int, order_id, order_type, x:int, y:int, message:str='', active=True):
    _rate(user_id); conn=polywar.get_connection()
    try:
        sid,p=_ctx(conn,user_id); fid=p.get('faction_id')
        if not fid: raise ValueError('faction_required')
        if not _is_commander(conn,sid,fid,user_id): raise ValueError('commander_required')
        now=datetime.utcnow(); c=conn.cursor()
        if active is False:
            polywar._execute(c,'UPDATE polywar_faction_orders SET active=0,cancelled_at=COALESCE(cancelled_at,%s),updated_at=%s WHERE id=%s AND season_id=%s AND faction_id=%s',(now,now,order_id,sid,fid)); conn.commit(); return get_governance(user_id)
        if not m.in_bounds(x,y): raise ValueError('invalid_order_target')
        cnt=polywar._fetchone(c,'SELECT COUNT(*) n FROM polywar_faction_orders WHERE season_id=%s AND faction_id=%s AND active=1 AND cancelled_at IS NULL AND expires_at>%s',(sid,fid,now))['n']
        if int(cnt)>=max_orders(): raise ValueError('order_limit')
        if order_type not in {'attack','defend','rally','recon','siege'}: raise ValueError('invalid_order_target')
        sx,sy=x//100,y//100; exp=now+timedelta(hours=order_duration_hours()); msg=(message or '')[:280]
        polywar._execute(c,'INSERT INTO polywar_faction_orders (season_id,faction_id,commander_user_id,order_type,x,y,sector_x,sector_y,message,active,created_at,expires_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)',(sid,fid,user_id,order_type,x,y,sx,sy,msg,now,exp,now))
        conn.commit(); return get_governance(user_id)
    finally: conn.close()
