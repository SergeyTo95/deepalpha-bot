import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from services import polywar_service as polywar
from services import polywar_map_service as m
from services import polywar_sector_service as sectors
from services import polywar_capital_service as capitals

_RATE_LOCK = threading.Lock(); _RATE: "OrderedDict[int, deque]" = OrderedDict(); _GET_RATE: "OrderedDict[int, deque]" = OrderedDict(); RATE_WINDOW = 10; RATE_MAX = 30; GET_RATE_MAX = 120; RATE_MAX_USERS = 5000


def _setting_int(k,d,lo,hi): return polywar._setting_int(k,d,lo,hi)
def election_hours(): return _setting_int('polywar_commander_election_hours',24,1,168)
def term_hours(): return _setting_int('polywar_commander_term_hours',168,1,8760)
def min_contribution(): return _setting_int('polywar_commander_min_contribution',5,0,10**9)
def min_members(): return _setting_int('polywar_commander_min_members_for_election',2,1,1000000)
def max_statement_length(): return _setting_int('polywar_commander_max_statement_length',280,0,1000)
def max_orders(): return _setting_int('polywar_commander_order_limit',5,0,100)
def order_duration_hours(): return _setting_int('polywar_capital_order_duration_hours',24,1,168)

def public_rules(): return {'election_hours':election_hours(),'term_hours':term_hours(),'min_contribution':min_contribution(),'min_members':min_members(),'max_statement_length':max_statement_length(),'max_orders':max_orders(),'order_duration_hours':order_duration_hours()}


def _rate_bucket(bucket, uid, maximum):
    now=time.monotonic(); uid=int(uid)
    with _RATE_LOCK:
        for key in list(bucket.keys()):
            q=bucket[key]
            while q and now-q[0]>RATE_WINDOW: q.popleft()
            if not q: bucket.pop(key,None)
        q=bucket.get(uid)
        if q is None:
            if len(bucket)>=RATE_MAX_USERS: bucket.popitem(last=False)
            q=deque(); bucket[uid]=q
        if len(q)>=maximum: raise ValueError('rate_limited')
        q.append(now); bucket.move_to_end(uid)

def _rate(uid): _rate_bucket(_RATE, uid, RATE_MAX)
def _rate_get(uid): _rate_bucket(_GET_RATE, uid, GET_RATE_MAX)


def _begin(conn,c):
    if polywar._is_sqlite(conn):
        last=None
        for i in range(20):
            try: c.execute('BEGIN IMMEDIATE'); return
            except Exception as e:
                if 'locked' not in str(e).lower(): raise
                last=e; time.sleep(.025*(i+1))
        raise last or RuntimeError('sqlite_begin_failed')
    polywar._execute(c,'BEGIN')


def _dt(v):
    if v is None or isinstance(v, datetime): return v
    return datetime.fromisoformat(str(v).replace('Z','+00:00').replace('+00:00',''))


def init_polywar_governance_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor(); id_sql='INTEGER PRIMARY KEY AUTOINCREMENT' if polywar._is_sqlite(conn) else 'SERIAL PRIMARY KEY'
    try:
        capitals.init_polywar_capital_schema(conn)
        c.execute(f'''CREATE TABLE IF NOT EXISTS polywar_commander_elections (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, status TEXT NOT NULL, starts_at TIMESTAMP NOT NULL, ends_at TIMESTAMP NOT NULL, finalized_at TIMESTAMP NULL, winner_user_id BIGINT NULL, created_at TIMESTAMP NOT NULL)''')
        # Drop incorrect old unique index if present, then create partial unique for open only.
        try: c.execute('DROP INDEX IF EXISTS idx_polywar_open_election')
        except Exception: pass
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_polywar_one_open_election ON polywar_commander_elections(season_id,faction_id) WHERE status = 'open'")
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_commander_candidates (election_id INTEGER NOT NULL, user_id BIGINT NOT NULL, faction_id INTEGER NOT NULL, statement TEXT NULL, contribution_at_nomination BIGINT NOT NULL DEFAULT 0, nominated_at TIMESTAMP NOT NULL, withdrawn_at TIMESTAMP NULL, UNIQUE(election_id,user_id))''')
        c.execute('''CREATE TABLE IF NOT EXISTS polywar_commander_votes (election_id INTEGER NOT NULL, voter_user_id BIGINT NOT NULL, candidate_user_id BIGINT NOT NULL, faction_id INTEGER NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, UNIQUE(election_id,voter_user_id))''')
        c.execute(f'''CREATE TABLE IF NOT EXISTS polywar_faction_orders (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, commander_user_id BIGINT NOT NULL, order_type TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, sector_x INTEGER NOT NULL, sector_y INTEGER NOT NULL, message TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TIMESTAMP NOT NULL, expires_at TIMESTAMP NOT NULL, cancelled_at TIMESTAMP NULL, updated_at TIMESTAMP NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_polywar_orders_scope ON polywar_faction_orders(season_id,faction_id,active,expires_at)')
        if own: conn.commit()
    finally:
        if own: conn.close()


def _active_election(conn,sid,fid,lock=False):
    return polywar._fetchone(conn.cursor(), "SELECT * FROM polywar_commander_elections WHERE season_id=%s AND faction_id=%s AND status='open' ORDER BY id DESC LIMIT 1" + ('' if polywar._is_sqlite(conn) or not lock else ' FOR UPDATE'), (sid,fid))


def _lock_stat(conn,sid,fid):
    return polywar._fetchone(conn.cursor(),'SELECT * FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(sid,fid)) or {}


def _ensure_election(conn,sid,fid,now):
    stat=_lock_stat(conn,sid,fid)
    if stat.get('commander_user_id') and _dt(stat.get('commander_term_ends_at')) and _dt(stat.get('commander_term_ends_at')) <= now:
        cur = conn.cursor()
        polywar._execute(cur,'UPDATE polywar_faction_season_stats SET commander_user_id=NULL, commander_since=NULL, commander_term_ends_at=NULL WHERE season_id=%s AND faction_id=%s AND commander_user_id IS NOT NULL',(sid,fid))
        if polywar._rowcount(cur) != 0:
            polywar._execute(conn.cursor(),'INSERT INTO polywar_events (season_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s)',(sid,fid,'commander_term_ended','Commander term ended',now))
        stat=_lock_stat(conn,sid,fid)
    if stat.get('commander_user_id') and _dt(stat.get('commander_term_ends_at')) and _dt(stat.get('commander_term_ends_at')) > now: return None
    if int(stat.get('active_members_count') or 0) < min_members(): return None
    e=_active_election(conn,sid,fid,lock=True)
    if e: return e
    if polywar._is_sqlite(conn):
        polywar._execute(conn.cursor(),'INSERT OR IGNORE INTO polywar_commander_elections (season_id,faction_id,status,starts_at,ends_at,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,fid,'open',now,now+timedelta(hours=election_hours()),now))
    else:
        polywar._execute(conn.cursor(),'INSERT INTO polywar_commander_elections (season_id,faction_id,status,starts_at,ends_at,created_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(sid,fid,'open',now,now+timedelta(hours=election_hours()),now))
    return _active_election(conn,sid,fid,lock=True)


def finalize_due(conn,sid,fid,now):
    _lock_stat(conn,sid,fid); e=_active_election(conn,sid,fid,lock=True)
    if not e or _dt(e['ends_at']) > now: return e
    rows=polywar._fetchall(conn.cursor(),'''SELECT c.user_id,c.contribution_at_nomination,c.nominated_at,COUNT(v.voter_user_id) votes FROM polywar_commander_candidates c LEFT JOIN polywar_commander_votes v ON v.election_id=c.election_id AND v.candidate_user_id=c.user_id WHERE c.election_id=%s AND c.withdrawn_at IS NULL GROUP BY c.user_id,c.contribution_at_nomination,c.nominated_at''',(e['id'],))
    winner=None
    if rows:
        rows.sort(key=lambda r:(-int(r['votes'] or 0),-int(r['contribution_at_nomination'] or 0),_dt(r['nominated_at']) or datetime.min,int(r['user_id'])))
        winner=int(rows[0]['user_id'])
    cur = conn.cursor()
    polywar._execute(cur,"UPDATE polywar_commander_elections SET status='finalized', finalized_at=%s, winner_user_id=%s WHERE id=%s AND status='open'",(now,winner,e['id']))
    if polywar._rowcount(cur) == 1 and winner:
        polywar._execute(conn.cursor(),'UPDATE polywar_faction_season_stats SET commander_user_id=%s, commander_since=%s, commander_term_ends_at=%s WHERE season_id=%s AND faction_id=%s',(winner,now,now+timedelta(hours=term_hours()),sid,fid))
        polywar._execute(conn.cursor(),'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,winner,fid,'commander_elected','Commander elected',now))
    return None if winner else _ensure_election(conn,sid,fid,now)


def _prepare_context_before_transaction(conn):
    polywar.init_polywar_schema(conn)
    capitals.init_polywar_capital_schema(conn)
    init_polywar_governance_schema(conn)
    season = polywar.ensure_active_season(conn)
    return int(season['id'])

def _governance_context_in_transaction(conn, user_id, season_id):
    polywar._insert_player_if_missing(conn,user_id,season_id)
    return polywar._fetchone(conn.cursor(),'SELECT * FROM polywar_players WHERE user_id=%s AND season_id=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(user_id,season_id))


def _prepare_faction(conn,sid,fid):
    now=datetime.utcnow(); finalize_due(conn,sid,int(fid),now); _ensure_election(conn,sid,int(fid),now)


def get_governance(user_id:int):
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        _rate_get(user_id)
        sid = _prepare_context_before_transaction(conn)
        _begin(conn,c); p=_governance_context_in_transaction(conn,user_id,sid); fid=p.get('faction_id')
        if fid: _prepare_faction(conn,sid,fid)
        conn.commit()
        if not fid: return {'ok':True,'season_id':sid,'faction_required':True,'rules':public_rules()}
        e=_active_election(conn,sid,fid); stat=polywar._fetchone(conn.cursor(),'SELECT commander_user_id,commander_since,commander_term_ends_at FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s',(sid,fid)) or {}
        candidates=[]; vote=None
        if e:
            rows=polywar._fetchall(conn.cursor(),'''SELECT c.user_id,c.statement,c.contribution_at_nomination,c.nominated_at,c.withdrawn_at,COUNT(v.voter_user_id) vote_count FROM polywar_commander_candidates c LEFT JOIN polywar_commander_votes v ON v.election_id=c.election_id AND v.candidate_user_id=c.user_id WHERE c.election_id=%s GROUP BY c.user_id,c.statement,c.contribution_at_nomination,c.nominated_at,c.withdrawn_at''',(e['id'],))
            candidates=[dict(r) for r in rows]; vr=polywar._fetchone(conn.cursor(),'SELECT candidate_user_id FROM polywar_commander_votes WHERE election_id=%s AND voter_user_id=%s',(e['id'],user_id)); vote=vr and vr['candidate_user_id']
        return {'ok':True,'season_id':sid,'commander':stat,'active_election':e,'candidates':candidates,'current_user_vote':vote,'current_user_is_candidate':any(int(c['user_id'])==user_id and not c.get('withdrawn_at') for c in candidates),'nomination_eligibility':{'eligible':int(p.get('faction_contribution') or 0)>=min_contribution()},'orders':list_orders(conn,sid,fid),'rules':public_rules(),'server_timestamp':int(time.time())}
    except Exception:
        polywar._safe_rollback(conn); raise
    finally: conn.close()


def nominate(user_id:int, statement:str='', active=True):
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        if not isinstance(active,bool): raise ValueError('invalid_active')
        if active and len(statement or '') > max_statement_length(): raise ValueError('invalid_statement')
        sid = _prepare_context_before_transaction(conn)
        _begin(conn,c); p=_governance_context_in_transaction(conn,user_id,sid); fid=p.get('faction_id')
        if not fid: raise ValueError('faction_required')
        _prepare_faction(conn,sid,fid); e=_active_election(conn,sid,fid,lock=True)
        if not e: raise ValueError('election_unavailable')
        if _dt(e['ends_at']) <= datetime.utcnow(): finalize_due(conn,sid,fid,datetime.utcnow()); raise ValueError('election_closed')
        row=polywar._fetchone(c,'SELECT * FROM polywar_commander_candidates WHERE election_id=%s AND user_id=%s',(e['id'],user_id))
        duplicate = bool(row and ((active and not row.get('withdrawn_at') and (row.get('statement') or '') == (statement or '')) or ((not active) and row.get('withdrawn_at'))))
        if duplicate: conn.commit(); res=get_governance(user_id); res['duplicate']=True; return res
        _rate(user_id); now=datetime.utcnow()
        if active:
            if int(p.get('faction_contribution') or 0)<min_contribution(): raise ValueError('contribution_required')
            polywar._execute(c,'INSERT INTO polywar_commander_candidates (election_id,user_id,faction_id,statement,contribution_at_nomination,nominated_at,withdrawn_at) VALUES (%s,%s,%s,%s,%s,%s,NULL) ON CONFLICT (election_id,user_id) DO UPDATE SET statement=excluded.statement, withdrawn_at=NULL',(e['id'],user_id,fid,statement or '',int(p.get('faction_contribution') or 0),now))
        else:
            polywar._execute(c,'UPDATE polywar_commander_candidates SET withdrawn_at=COALESCE(withdrawn_at,%s) WHERE election_id=%s AND user_id=%s',(now,e['id'],user_id))
        conn.commit(); return get_governance(user_id)
    except ValueError:
        polywar._safe_rollback(conn); raise
    finally: conn.close()


def vote(user_id:int,candidate_user_id:int):
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        sid = _prepare_context_before_transaction(conn)
        _begin(conn,c); p=_governance_context_in_transaction(conn,user_id,sid); fid=p.get('faction_id')
        if not fid: raise ValueError('faction_required')
        _prepare_faction(conn,sid,fid); e=_active_election(conn,sid,fid,lock=True)
        if not e: raise ValueError('election_unavailable')
        if _dt(e['ends_at']) <= datetime.utcnow(): finalize_due(conn,sid,fid,datetime.utcnow()); raise ValueError('election_closed')
        cand=polywar._fetchone(c,'SELECT * FROM polywar_commander_candidates WHERE election_id=%s AND user_id=%s AND faction_id=%s',(e['id'],candidate_user_id,fid))
        if not cand: raise ValueError('candidate_not_found')
        if cand.get('withdrawn_at'): raise ValueError('candidate_withdrawn')
        now=datetime.utcnow(); existing=polywar._fetchone(c,'SELECT candidate_user_id FROM polywar_commander_votes WHERE election_id=%s AND voter_user_id=%s',(e['id'],user_id)); dup=existing and int(existing['candidate_user_id'])==int(candidate_user_id)
        if dup: conn.commit(); res=get_governance(user_id); res['duplicate']=True; return res
        _rate(user_id)
        polywar._execute(c,'INSERT INTO polywar_commander_votes (election_id,voter_user_id,candidate_user_id,faction_id,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (election_id,voter_user_id) DO UPDATE SET candidate_user_id=excluded.candidate_user_id, updated_at=excluded.updated_at',(e['id'],user_id,candidate_user_id,fid,now,now))
        conn.commit(); return get_governance(user_id)
    except ValueError:
        polywar._safe_rollback(conn); raise
    finally: conn.close()


def _is_commander(conn,sid,fid,user_id):
    r=polywar._fetchone(conn.cursor(),'SELECT commander_user_id,commander_term_ends_at FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s',(sid,fid))
    return r and int(r.get('commander_user_id') or 0)==int(user_id) and _dt(r.get('commander_term_ends_at')) and _dt(r.get('commander_term_ends_at'))>datetime.utcnow()


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


def _owner(conn,sid,x,y): return m._owner_at(conn,sid,x,y)

def _valid_order_target(conn,sid,fid,order_type,x,y):
    owner=_owner(conn,sid,x,y)
    cell=polywar._fetchone(conn.cursor(),'SELECT * FROM polywar_cells WHERE season_id=%s AND x=%s AND y=%s',(sid,x,y)) or {}
    contested=int(cell.get('contest_progress') or 0)>0
    if order_type=='attack': return (owner is not None and int(owner)!=int(fid)) or (contested and cell.get('contesting_faction_id')==fid)
    if order_type=='defend': return owner==fid or (contested and owner==fid)
    if order_type=='rally': return owner==fid
    if order_type=='recon':
        from services import polywar_mine_service as mines
        seed = polywar._fetchone(conn.cursor(),'SELECT secret_seed FROM polywar_seasons WHERE id=%s',(sid,))['secret_seed']
        return owner is None and m.TERRAIN_COSTS[m.terrain_at(seed,x,y)] is not None and (mines._area_has_own(conn,sid,fid,x,y,3) or mines._near_own_territory(conn,sid,fid,x,y,5))
    if order_type=='siege':
        cap=capitals.get_capital_at(conn,sid,x,y); return bool(cap and int(cap['controller_faction_id'])!=int(fid))
    return False


def upsert_order(user_id:int, order_id, order_type, x:int, y:int, message:str='', active=True):
    conn=polywar.get_connection(); c=conn.cursor()
    try:
        if not isinstance(active,bool): raise ValueError('invalid_active')
        if len(message or '')>280: raise ValueError('invalid_statement')
        sid = _prepare_context_before_transaction(conn)
        _begin(conn,c); p=_governance_context_in_transaction(conn,user_id,sid); fid=p.get('faction_id')
        if not fid: raise ValueError('faction_required')
        _prepare_faction(conn,sid,fid)
        if not _is_commander(conn,sid,fid,user_id): raise ValueError('commander_required')
        now=datetime.utcnow()
        if active is False:
            row=polywar._fetchone(c,'SELECT * FROM polywar_faction_orders WHERE id=%s AND season_id=%s AND faction_id=%s AND commander_user_id=%s',(order_id,sid,fid,user_id))
            if not row: raise ValueError('invalid_order_target')
            dup = not bool(row.get('active')) or row.get('cancelled_at')
            if dup: conn.commit(); res=get_governance(user_id); res['duplicate']=True; return res
            _rate(user_id); polywar._execute(c,'UPDATE polywar_faction_orders SET active=0,cancelled_at=%s,updated_at=%s WHERE id=%s AND season_id=%s AND faction_id=%s',(now,now,order_id,sid,fid)); conn.commit(); return get_governance(user_id)
        if order_type not in {'attack','defend','rally','recon','siege'} or not m.in_bounds(x,y): raise ValueError('invalid_order_target')
        capitals.ensure_capitals_initialized(conn,sid)
        if not _valid_order_target(conn,sid,fid,order_type,x,y): raise ValueError('invalid_order_target')
        _rate(user_id); sx,sy=sectors.sector_coords(x,y); exp=now+timedelta(hours=order_duration_hours()); msg=message or ''
        if order_id:
            row=polywar._fetchone(c,'SELECT * FROM polywar_faction_orders WHERE id=%s AND season_id=%s AND faction_id=%s AND commander_user_id=%s AND active=1 AND cancelled_at IS NULL AND expires_at>%s',(order_id,sid,fid,user_id,now))
            if not row: raise ValueError('invalid_order_target')
            polywar._execute(c,'UPDATE polywar_faction_orders SET order_type=%s,x=%s,y=%s,sector_x=%s,sector_y=%s,message=%s,active=1,expires_at=%s,cancelled_at=NULL,updated_at=%s WHERE id=%s',(order_type,x,y,sx,sy,msg,exp,now,order_id))
        else:
            cnt=polywar._fetchone(c,'SELECT COUNT(*) n FROM polywar_faction_orders WHERE season_id=%s AND faction_id=%s AND active=1 AND cancelled_at IS NULL AND expires_at>%s',(sid,fid,now))['n']
            if int(cnt)>=max_orders(): raise ValueError('order_limit')
            polywar._execute(c,'INSERT INTO polywar_faction_orders (season_id,faction_id,commander_user_id,order_type,x,y,sector_x,sector_y,message,active,created_at,expires_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)',(sid,fid,user_id,order_type,x,y,sx,sy,msg,now,exp,now))
        conn.commit(); return get_governance(user_id)
    except ValueError:
        polywar._safe_rollback(conn); raise
    finally: conn.close()
