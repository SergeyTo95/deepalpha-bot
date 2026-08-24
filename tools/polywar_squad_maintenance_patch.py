from pathlib import Path


path = Path("services/polywar_squad_service.py")
text = path.read_text(encoding="utf-8")

old = '''def run_squad_maintenance_once(now=None):
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
'''

new = '''def _maintenance_schema_ready(conn):
    c=conn.cursor()
    if _is_sqlite(conn):
        row=_fetchone(c,"SELECT name FROM sqlite_master WHERE type='table' AND name=%s LIMIT 1",('polywar_seasons',))
        return bool(row)
    row=_fetchone(c,'SELECT to_regclass(%s) AS table_name',('polywar_seasons',))
    return bool(row and row.get('table_name'))

def run_squad_maintenance_once(now=None):
    conn=polywar.get_connection(); ok=False; now=now or _now()
    try:
        c=conn.cursor(); polywar.begin_serialized_transaction(conn)
        if not _maintenance_schema_ready(conn):
            conn.commit(); ok=True
            logger.debug('polywar_squad_maintenance_skipped_schema_uninitialized')
            return {'ok':True,'processed':False,'reason':'no_active_season'}
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
'''

if text.count(old) != 1:
    raise SystemExit(f"expected exactly one maintenance block, found {text.count(old)}")

path.write_text(text.replace(old, new), encoding="utf-8")
