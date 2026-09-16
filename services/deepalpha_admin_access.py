"""Telegram project viewers. The owner identity remains exclusively ADMIN_ID."""
import logging
from contextlib import contextmanager

from db.database import get_connection
from services.velia_admin_security_service import configured_admin_id, is_admin_user

logger = logging.getLogger(__name__)
MAX_VIEWERS = 2


def ensure_tables(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS deepalpha_project_viewers (
        user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
        active BOOLEAN NOT NULL DEFAULT TRUE,
        granted_by BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS deepalpha_viewer_audit (
        id BIGSERIAL PRIMARY KEY, actor_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('grant','revoke')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    from services.deepalpha_manager_payout_service import ensure_tables as ensure_manager_payout_tables
    ensure_manager_payout_tables(cursor)


@contextmanager
def _transaction():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def can_view_project(user_id):
    if is_admin_user(user_id):
        return True
    try:
        with _transaction() as cur:
            cur.execute("SELECT 1 FROM deepalpha_project_viewers WHERE user_id=%s AND active=TRUE", (int(user_id),))
            return bool(cur.fetchone())
    except Exception:
        logger.warning("DEEPALPHA_VIEWER_ACCESS_UNAVAILABLE")
        return False


def _require_owner(actor_id):
    if not is_admin_user(actor_id):
        raise PermissionError("owner_required")


def viewer_candidate(actor_id, user_id):
    _require_owner(actor_id)
    if isinstance(user_id, bool) or not str(user_id).isdigit() or not 0 < int(user_id) < 2**63:
        raise ValueError("invalid_telegram_id")
    if int(user_id) == configured_admin_id():
        raise ValueError("owner_role_is_fixed")
    with _transaction() as cur:
        cur.execute("SELECT user_id,username,first_name FROM users WHERE user_id=%s", (int(user_id),))
        row = cur.fetchone()
    if not row:
        raise ValueError("user_must_start_bot")
    return {"user_id": row[0], "username": row[1] or "", "first_name": row[2] or ""}


def list_viewers(actor_id):
    _require_owner(actor_id)
    with _transaction() as cur:
        cur.execute("""SELECT v.user_id,u.username,u.first_name,v.share_bps,v.payout_enabled
            FROM deepalpha_project_viewers v JOIN users u ON u.user_id=v.user_id
            WHERE v.active=TRUE ORDER BY v.user_id""")
        return [{"user_id": r[0], "username": r[1] or "", "first_name": r[2] or "",
                 "share_bps": int(r[3] or 0), "payout_enabled": bool(r[4])} for r in cur.fetchall()]


def viewer_profile(user_id):
    with _transaction() as cur:
        cur.execute("""SELECT v.user_id,u.username,u.first_name,v.share_bps,v.payout_enabled,v.active
            FROM deepalpha_project_viewers v JOIN users u ON u.user_id=v.user_id WHERE v.user_id=%s""", (int(user_id),))
        row = cur.fetchone()
    if not row or not row[5]:
        raise PermissionError("project_access_denied")
    return {"user_id": row[0], "username": row[1] or "", "first_name": row[2] or "",
            "share_bps": int(row[3] or 0), "payout_enabled": bool(row[4])}


def set_viewer(actor_id, user_id, *, active):
    _require_owner(actor_id)
    candidate = viewer_candidate(actor_id, user_id)
    if not isinstance(active, bool):
        raise ValueError("invalid_role_action")
    with _transaction() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (configured_admin_id(),))
        cur.execute("SELECT active FROM deepalpha_project_viewers WHERE user_id=%s FOR UPDATE", (int(user_id),))
        existing = cur.fetchone()
        if bool(existing and existing[0]) == active:
            return candidate
        if active:
            cur.execute("SELECT COUNT(*) FROM deepalpha_project_viewers WHERE active=TRUE")
            if cur.fetchone()[0] >= MAX_VIEWERS:
                raise ValueError("viewer_limit_reached")
        cur.execute("""INSERT INTO deepalpha_project_viewers(user_id,active,granted_by)
            VALUES (%s,%s,%s) ON CONFLICT(user_id) DO UPDATE
            SET active=EXCLUDED.active,granted_by=EXCLUDED.granted_by,updated_at=NOW()""",
            (int(user_id), active, int(actor_id)))
        cur.execute("INSERT INTO deepalpha_viewer_audit(actor_id,user_id,action) VALUES (%s,%s,%s)",
                    (int(actor_id), int(user_id), "grant" if active else "revoke"))
    return candidate


def set_share_bps(actor_id, user_id, share_bps):
    _require_owner(actor_id)
    if isinstance(share_bps, bool) or not str(share_bps).isdigit():
        raise ValueError("invalid_share")
    share_bps = int(share_bps)
    if not 0 <= share_bps <= 10000:
        raise ValueError("invalid_share")
    with _transaction() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (configured_admin_id(),))
        cur.execute("SELECT active FROM deepalpha_project_viewers WHERE user_id=%s FOR UPDATE", (int(user_id),))
        row = cur.fetchone()
        if not row or not row[0]:
            raise ValueError("viewer_not_active")
        cur.execute("SELECT COALESCE(SUM(share_bps),0) FROM deepalpha_project_viewers WHERE active=TRUE AND user_id<>%s", (int(user_id),))
        other = int((cur.fetchone() or [0])[0] or 0)
        if other + share_bps > 10000:
            raise ValueError("total_share_exceeds_100")
        cur.execute("UPDATE deepalpha_project_viewers SET share_bps=%s,updated_at=NOW() WHERE user_id=%s", (share_bps, int(user_id)))
    return share_bps


def project_snapshot(user_id):
    if not can_view_project(user_id):
        raise PermissionError("project_access_denied")
    with _transaction() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '5s'")
        cur.execute("SELECT COUNT(*), COALESCE(SUM(token_balance),0) FROM users")
        users, tokens = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM analyses")
        analyses = cur.fetchone()[0]
        cur.execute("""SELECT COUNT(*) FILTER (WHERE status='fulfilled'),
            COALESCE(SUM(expected_amount_nano) FILTER (WHERE status='fulfilled'),0),
            COUNT(*) FILTER (WHERE status IN ('pending','verified')) FROM payment_intents""")
        modern = cur.fetchone()
        cur.execute("""SELECT COUNT(*) FILTER (WHERE status='fulfilled'),
            COALESCE(SUM(NULLIF(expected_amount_nano,'')::numeric) FILTER (WHERE status='fulfilled'),0),
            COUNT(*) FILTER (WHERE status='submitted') FROM ton_purchase_intents""")
        legacy = cur.fetchone()
    return {"users": users, "tokens": int(tokens), "analyses": analyses,
            "payments": modern[0] + legacy[0], "revenue_nano": int(modern[1] + legacy[1]),
            "pending": modern[2] + legacy[2]}
