import html
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

from services import polywar_service as polywar

logger = logging.getLogger(__name__)

HISTORY_REASONS = {'first_leader','contribution_overtake','previous_inactive','previous_left_faction','previous_banned','season_started','leader_vacated','recalculated'}


def leadership_mode():
    try:
        value = polywar.get_setting('polywar_leadership_mode', 'contribution')
    except Exception:
        value = 'contribution'
    return str(value or 'contribution').strip().lower() or 'contribution'

def is_contribution_mode(): return leadership_mode() != 'election'
def inactivity_hours(): return polywar._setting_int('polywar_leader_inactivity_hours', 168, 1, 24*365)
def refresh_seconds(): return polywar._setting_int('polywar_leader_refresh_seconds', 30, 0, 3600)


def sanitize_telegram_avatar_url(value):
    url = str(value or '').strip()
    if not url: return ''
    if len(url) > 1024:
        logger.warning('polywar_invalid_telegram_avatar_url reason=too_long')
        return ''
    p = urlparse(url)
    if p.scheme.lower() != 'https' or not p.netloc:
        logger.warning('polywar_invalid_telegram_avatar_url reason=scheme')
        return ''
    return url


def display_name(row):
    username = str((row or {}).get('username') or '').strip().lstrip('@')[:64]
    first = str((row or {}).get('first_name') or '').strip()[:80]
    if username: return '@' + html.escape(username, quote=False)
    if first: return html.escape(first, quote=False)
    return 'Player ' + str((row or {}).get('user_id') or '')[-6:]


def init_polywar_leader_schema(conn=None):
    own=conn is None; conn=conn or polywar.get_connection(); c=conn.cursor(); id_sql='INTEGER PRIMARY KEY AUTOINCREMENT' if polywar._is_sqlite(conn) else 'SERIAL PRIMARY KEY'
    try:
        c.execute('CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, is_banned INTEGER DEFAULT 0)')
        c.execute('CREATE TABLE IF NOT EXISTS web_accounts (user_id BIGINT NOT NULL, provider TEXT NOT NULL, provider_sub TEXT, name TEXT, avatar_url TEXT, UNIQUE(provider, provider_sub))')
        from services.polywar_sector_service import _add_col
        for spec in ['leader_user_id BIGINT NULL','leader_since TIMESTAMP NULL','leader_contribution BIGINT NOT NULL DEFAULT 0','leader_last_evaluated_at TIMESTAMP NULL']:
            try: _add_col(conn, 'polywar_faction_season_stats', spec)
            except Exception: logger.exception('polywar_leader_migration_failure table=polywar_faction_season_stats')
        c.execute(f'''CREATE TABLE IF NOT EXISTS polywar_faction_leader_history (id {id_sql}, season_id INTEGER NOT NULL, faction_id INTEGER NOT NULL, leader_user_id BIGINT NULL, previous_leader_user_id BIGINT NULL, contribution_at_change BIGINT NOT NULL DEFAULT 0, reason TEXT NOT NULL, created_at TIMESTAMP NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_polywar_leader_history_scope ON polywar_faction_leader_history(season_id,faction_id,created_at)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_polywar_leader_candidates ON polywar_players(season_id,faction_id,faction_contribution,last_active_at,joined_at,user_id)')
        if own: conn.commit()
    finally:
        if own: conn.close()


def _lock_stat(conn,sid,fid):
    return polywar._fetchone(conn.cursor(),'SELECT * FROM polywar_faction_season_stats WHERE season_id=%s AND faction_id=%s' + ('' if polywar._is_sqlite(conn) else ' FOR UPDATE'),(sid,fid)) or {}

def _playable(conn,fid):
    f=polywar._fetchone(conn.cursor(),'SELECT id,name,color,is_playable,is_system FROM polywar_factions WHERE id=%s',(fid,))
    return f if f and int(f.get('is_playable') or 0)==1 and int(f.get('is_system') or 0)==0 else None


def _best_candidate(conn,sid,fid,now):
    cutoff=now-timedelta(hours=inactivity_hours())
    return polywar._fetchone(conn.cursor(),'''SELECT p.user_id,p.faction_id,p.faction_contribution,p.last_active_at,p.joined_at,u.username,u.first_name,wa.avatar_url
        FROM polywar_players p LEFT JOIN users u ON u.user_id=p.user_id LEFT JOIN web_accounts wa ON wa.user_id=p.user_id AND wa.provider='telegram'
        WHERE p.season_id=%s AND p.faction_id=%s AND p.faction_id IS NOT NULL AND COALESCE(u.is_banned,0)=0 AND p.last_active_at>=%s
        ORDER BY p.faction_contribution DESC, p.last_active_at DESC, p.joined_at ASC, p.user_id ASC LIMIT 1''',(sid,fid,cutoff))


def _reason(conn,sid,fid,prev,new,now):
    if not prev and new: return 'first_leader'
    if prev and not new: return 'leader_vacated'
    if prev and new:
        prow=polywar._fetchone(conn.cursor(),'''SELECT p.faction_id,p.last_active_at,COALESCE(u.is_banned,0) is_banned FROM polywar_players p LEFT JOIN users u ON u.user_id=p.user_id WHERE p.season_id=%s AND p.user_id=%s''',(sid,prev))
        if not prow or prow.get('faction_id') is None or int(prow.get('faction_id') or 0)!=int(fid): return 'previous_left_faction'
        if int(prow.get('is_banned') or 0): return 'previous_banned'
        la=polywar_governance_dt(prow.get('last_active_at'))
        if la and la < now-timedelta(hours=inactivity_hours()): return 'previous_inactive'
        return 'contribution_overtake'
    return 'recalculated'

def polywar_governance_dt(v):
    if v is None or isinstance(v, datetime): return v
    return datetime.fromisoformat(str(v).replace('Z','+00:00').replace('+00:00',''))


def refresh_faction_leader_in_transaction(conn, season_id, faction_id, now=None, force=False):
    init_polywar_leader_schema(conn); now=now or datetime.utcnow(); sid=int(season_id); fid=int(faction_id)
    if not _playable(conn,fid): return None
    stat=_lock_stat(conn,sid,fid); last=polywar_governance_dt(stat.get('leader_last_evaluated_at'))
    if not force and last and (now-last).total_seconds() < refresh_seconds():
        return get_faction_leader(conn,sid,fid,refresh=False)
    prev=stat.get('leader_user_id') if stat.get('leader_user_id') is not None else stat.get('commander_user_id')
    prev=int(prev) if prev else None
    cand=_best_candidate(conn,sid,fid,now); new=int(cand['user_id']) if cand else None; contrib=int(cand.get('faction_contribution') or 0) if cand else 0
    if prev==new:
        polywar._execute(conn.cursor(),'UPDATE polywar_faction_season_stats SET leader_last_evaluated_at=%s, leader_contribution=%s, commander_user_id=%s, commander_since=COALESCE(commander_since,leader_since), commander_term_ends_at=NULL WHERE season_id=%s AND faction_id=%s',(now,contrib,new,sid,fid))
        return get_faction_leader(conn,sid,fid,refresh=False)
    reason=_reason(conn,sid,fid,prev,new,now)
    event='faction_leader_vacated' if new is None else ('faction_leader_appointed' if prev is None else 'faction_leader_changed')
    since=now if new else None
    polywar._execute(conn.cursor(),'UPDATE polywar_faction_season_stats SET leader_user_id=%s, leader_since=%s, leader_contribution=%s, leader_last_evaluated_at=%s, commander_user_id=%s, commander_since=%s, commander_term_ends_at=NULL WHERE season_id=%s AND faction_id=%s',(new,since,contrib,now,new,since,sid,fid))
    polywar._execute(conn.cursor(),'INSERT INTO polywar_faction_leader_history (season_id,faction_id,leader_user_id,previous_leader_user_id,contribution_at_change,reason,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',(sid,fid,new,prev,contrib,reason,now))
    polywar._execute(conn.cursor(),'INSERT INTO polywar_events (season_id,user_id,faction_id,event_type,message,created_at) VALUES (%s,%s,%s,%s,%s,%s)',(sid,new,fid,event,'Faction Leader updated' if new else 'Faction Leader position vacant',now))
    logger.info('polywar_faction_leader_changed season_id=%s faction_id=%s previous_leader_user_id=%s leader_user_id=%s reason=%s',sid,fid,prev,new,reason)
    return get_faction_leader(conn,sid,fid,refresh=False)


def refresh_all_faction_leaders_in_transaction(conn, season_id, now=None):
    rows=polywar._fetchall(conn.cursor(),'SELECT id FROM polywar_factions WHERE COALESCE(is_playable,1)=1 AND COALESCE(is_system,0)=0 ORDER BY id',())
    return [refresh_faction_leader_in_transaction(conn,season_id,r['id'],now=now,force=True) for r in rows]


def get_faction_leader(conn, season_id, faction_id, refresh=True):
    if refresh and is_contribution_mode(): refresh_faction_leader_in_transaction(conn,season_id,faction_id)
    row=polywar._fetchone(conn.cursor(),'''SELECT s.leader_user_id user_id,s.faction_id,s.leader_since,p.faction_contribution,p.last_active_at,u.username,u.first_name,wa.avatar_url,f.name faction_name,f.color faction_color
        FROM polywar_faction_season_stats s LEFT JOIN polywar_players p ON p.season_id=s.season_id AND p.user_id=s.leader_user_id LEFT JOIN users u ON u.user_id=s.leader_user_id LEFT JOIN web_accounts wa ON wa.user_id=s.leader_user_id AND wa.provider='telegram' LEFT JOIN polywar_factions f ON f.id=s.faction_id
        WHERE s.season_id=%s AND s.faction_id=%s AND s.leader_user_id IS NOT NULL''',(season_id,faction_id))
    if not row: return None
    return {'user_id':int(row['user_id']),'faction_id':int(row['faction_id']),'display_name':display_name(row),'username':(str(row.get('username') or '').strip().lstrip('@')[:64] or None),'first_name':(str(row.get('first_name') or '').strip()[:80] or None),'avatar_url':sanitize_telegram_avatar_url(row.get('avatar_url')) or None,'faction_contribution':int(row.get('faction_contribution') or 0),'last_active_at':polywar._iso(row.get('last_active_at')),'leader_since':polywar._iso(row.get('leader_since')),'is_active':True,'faction_name':row.get('faction_name'),'faction_color':row.get('faction_color')}


def get_faction_leaderboard(conn, season_id, faction_id, current_user_id=None, limit=5):
    cutoff=datetime.utcnow()-timedelta(hours=inactivity_hours())
    rows=polywar._fetchall(conn.cursor(),'''SELECT p.user_id,p.faction_id,p.faction_contribution,p.last_active_at,u.username,u.first_name,wa.avatar_url,s.leader_user_id
        FROM polywar_players p LEFT JOIN users u ON u.user_id=p.user_id LEFT JOIN web_accounts wa ON wa.user_id=p.user_id AND wa.provider='telegram' LEFT JOIN polywar_faction_season_stats s ON s.season_id=p.season_id AND s.faction_id=p.faction_id
        WHERE p.season_id=%s AND p.faction_id=%s AND COALESCE(u.is_banned,0)=0 AND p.last_active_at>=%s ORDER BY p.faction_contribution DESC,p.last_active_at DESC,p.joined_at ASC,p.user_id ASC LIMIT %s''',(season_id,faction_id,cutoff,int(limit)))
    out=[]
    for i,r in enumerate(rows,1):
        out.append({'rank':i,'user_id':int(r['user_id']),'display_name':display_name(r),'username':(str(r.get('username') or '').strip().lstrip('@')[:64] or None),'first_name':(str(r.get('first_name') or '').strip()[:80] or None),'avatar_url':sanitize_telegram_avatar_url(r.get('avatar_url')) or None,'faction_contribution':int(r.get('faction_contribution') or 0),'last_active_at':polywar._iso(r.get('last_active_at')),'is_leader':int(r.get('leader_user_id') or 0)==int(r['user_id']),'is_current_user': current_user_id is not None and int(current_user_id)==int(r['user_id'])})
    rank=None; contrib=0
    if current_user_id:
        allr=polywar._fetchall(conn.cursor(),'''SELECT user_id,faction_contribution FROM polywar_players WHERE season_id=%s AND faction_id=%s ORDER BY faction_contribution DESC,last_active_at DESC,joined_at ASC,user_id ASC''',(season_id,faction_id))
        for i,r in enumerate(allr,1):
            if int(r['user_id'])==int(current_user_id): rank=i; contrib=int(r.get('faction_contribution') or 0); break
    return out, rank, contrib
