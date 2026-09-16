"""Monthly DeepAlpha manager revenue-share proposals with owner approval before transfer."""
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN

import psycopg2.extras

from db.database import get_connection
from services.velia_admin_security_service import configured_admin_id, is_admin_user

logger = logging.getLogger(__name__)
NANO = 1_000_000_000


def ensure_tables(cursor):
    cursor.execute("ALTER TABLE deepalpha_project_viewers ADD COLUMN IF NOT EXISTS share_bps INTEGER NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE deepalpha_project_viewers ADD COLUMN IF NOT EXISTS payout_enabled BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("""CREATE TABLE IF NOT EXISTS deepalpha_manager_payouts (
        id BIGSERIAL PRIMARY KEY,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        manager_user_id BIGINT NOT NULL REFERENCES users(user_id),
        share_bps INTEGER NOT NULL,
        gross_revenue_nano BIGINT NOT NULL,
        suggested_amount_nano BIGINT NOT NULL,
        amount_nano BIGINT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        treasury_payout_id BIGINT UNIQUE,
        notified_at TIMESTAMPTZ,
        approved_by BIGINT,
        approved_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(period_start, manager_user_id)
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_deepalpha_manager_payouts_status ON deepalpha_manager_payouts(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_deepalpha_manager_payouts_manager ON deepalpha_manager_payouts(manager_user_id, period_start DESC)")


def _bootstrap_pairs():
    result = []
    for item in str(os.getenv("DEEPALPHA_MANAGER_BOOTSTRAP", "") or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        raw_uid, raw_bps = item.split(":", 1)
        if not raw_uid.isdigit() or not raw_bps.isdigit():
            continue
        uid, bps = int(raw_uid), int(raw_bps)
        if 0 < uid < 2**63 and 0 <= bps <= 10000:
            result.append((uid, bps))
    return result[:2]


def bootstrap_configured_managers():
    """Grant configured managers only when no row exists; revocations are never undone."""
    pairs = _bootstrap_pairs()
    if not pairs:
        return []
    conn = get_connection()
    inserted = []
    try:
        with conn.cursor() as cur:
            for uid, bps in pairs:
                cur.execute("SELECT 1 FROM users WHERE user_id=%s", (uid,))
                if not cur.fetchone():
                    continue
                cur.execute("SELECT 1 FROM deepalpha_project_viewers WHERE user_id=%s", (uid,))
                if cur.fetchone():
                    continue
                cur.execute("""INSERT INTO deepalpha_project_viewers(user_id,active,granted_by,share_bps,payout_enabled)
                    VALUES (%s,TRUE,%s,%s,TRUE)""", (uid, configured_admin_id(), bps))
                cur.execute("INSERT INTO deepalpha_viewer_audit(actor_id,user_id,action) VALUES (%s,%s,'grant')",
                            (configured_admin_id(), uid))
                inserted.append(uid)
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        logger.warning("DEEPALPHA_MANAGER_BOOTSTRAP_FAILED", exc_info=True)
        return []
    finally:
        conn.close()


def previous_month_period(now=None):
    now = now or datetime.now(timezone.utc)
    current_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if current_start.month == 1:
        start = datetime(current_start.year - 1, 12, 1, tzinfo=timezone.utc)
    else:
        start = datetime(current_start.year, current_start.month - 1, 1, tzinfo=timezone.utc)
    return start, current_start


def _gross_revenue(cur, start, end):
    cur.execute("""SELECT COALESCE(SUM(expected_amount_nano),0) FROM payment_intents
        WHERE status='fulfilled' AND fulfilled_at IS NOT NULL AND fulfilled_at >= %s AND fulfilled_at < %s""", (start, end))
    modern = int((cur.fetchone() or [0])[0] or 0)
    cur.execute("""SELECT COALESCE(SUM(NULLIF(expected_amount_nano,'')::numeric),0) FROM ton_purchase_intents
        WHERE status='fulfilled' AND NULLIF(fulfilled_at,'') IS NOT NULL
          AND NULLIF(fulfilled_at,'')::timestamptz >= %s AND NULLIF(fulfilled_at,'')::timestamptz < %s""", (start, end))
    custodial = int((cur.fetchone() or [0])[0] or 0)
    return modern + custodial


def ensure_previous_month_proposals(now=None):
    start, end = previous_month_period(now)
    conn = get_connection()
    created = []
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        gross = _gross_revenue(cur, start, end)
        cur.execute("""SELECT user_id,share_bps FROM deepalpha_project_viewers
            WHERE active=TRUE AND payout_enabled=TRUE AND share_bps > 0 ORDER BY user_id""")
        managers = cur.fetchall() or []
        for row in managers:
            suggested = gross * int(row["share_bps"]) // 10000
            if suggested <= 0:
                continue
            cur.execute("""INSERT INTO deepalpha_manager_payouts(
                period_start,period_end,manager_user_id,share_bps,gross_revenue_nano,suggested_amount_nano,amount_nano)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(period_start,manager_user_id) DO NOTHING RETURNING *""",
                (start.date(), end.date(), int(row["user_id"]), int(row["share_bps"]), gross, suggested, suggested))
            inserted = cur.fetchone()
            if inserted:
                created.append(dict(inserted))
        conn.commit()
        return created
    except Exception:
        conn.rollback()
        logger.warning("DEEPALPHA_MANAGER_PAYOUT_PROPOSAL_FAILED", exc_info=True)
        return []
    finally:
        conn.close()


def list_pending(actor_id):
    if not is_admin_user(actor_id):
        raise PermissionError("owner_required")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT p.*,u.username,u.first_name FROM deepalpha_manager_payouts p
            JOIN users u ON u.user_id=p.manager_user_id
            WHERE p.status IN ('pending','approved','submitted','review_required')
            ORDER BY p.period_start DESC,p.manager_user_id""")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def list_for_manager(user_id, limit=12):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT period_start,period_end,share_bps,gross_revenue_nano,suggested_amount_nano,amount_nano,status,paid_at
            FROM deepalpha_manager_payouts LEFT JOIN treasury_payouts t ON t.id=deepalpha_manager_payouts.treasury_payout_id
            WHERE manager_user_id=%s ORDER BY period_start DESC LIMIT %s""", (int(user_id), max(1, min(int(limit), 24))))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def pending_notifications():
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT p.*,u.username,u.first_name FROM deepalpha_manager_payouts p
            JOIN users u ON u.user_id=p.manager_user_id
            WHERE p.status='pending' AND p.notified_at IS NULL ORDER BY p.period_start,p.manager_user_id LIMIT 20""")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def mark_notified(payout_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE deepalpha_manager_payouts SET notified_at=NOW(),updated_at=NOW() WHERE id=%s AND notified_at IS NULL", (int(payout_id),))
        conn.commit()
    finally:
        conn.close()


def set_amount(actor_id, payout_id, gram_amount):
    if not is_admin_user(actor_id):
        raise PermissionError("owner_required")
    try:
        dec = Decimal(str(gram_amount).replace(",", ".")).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_amount")
    amount_nano = int(dec * NANO)
    if amount_nano <= 0:
        raise ValueError("invalid_amount")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM deepalpha_manager_payouts WHERE id=%s FOR UPDATE", (int(payout_id),))
        row = cur.fetchone()
        if not row or row["status"] != "pending":
            raise ValueError("payout_not_editable")
        if amount_nano > int(row["gross_revenue_nano"]):
            raise ValueError("amount_exceeds_period_revenue")
        cur.execute("UPDATE deepalpha_manager_payouts SET amount_nano=%s,updated_at=NOW() WHERE id=%s RETURNING *", (amount_nano, int(payout_id)))
        updated = dict(cur.fetchone())
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel(actor_id, payout_id):
    if not is_admin_user(actor_id):
        raise PermissionError("owner_required")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE deepalpha_manager_payouts SET status='cancelled',updated_at=NOW() WHERE id=%s AND status='pending'", (int(payout_id),))
            if cur.rowcount != 1:
                raise ValueError("payout_not_cancellable")
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def approve_and_send(actor_id, payout_id):
    """Owner confirmation is the only path that can trigger the Treasury transfer."""
    if not is_admin_user(actor_id):
        raise PermissionError("owner_required")
    from services import treasury_service as treasury
    from services.ton_wallet_service import get_or_create_user_ton_wallet

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (900000000000 + int(payout_id),))
        cur.execute("SELECT * FROM deepalpha_manager_payouts WHERE id=%s FOR UPDATE", (int(payout_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("payout_not_found")
        if row["status"] in ("submitted", "paid"):
            return {"ok": True, "status": row["status"], "already_submitted": True}
        if row["status"] != "pending":
            raise ValueError("payout_not_approvable")
        wallet = get_or_create_user_ton_wallet(int(row["manager_user_id"]))
        if not wallet.get("ok"):
            return {"ok": False, "error": wallet.get("error") or "internal_wallet_required"}
        resolved = treasury.resolve_internal_payout_wallet(int(row["manager_user_id"]), conn=conn, for_update=True)
        if not resolved.get("ok"):
            return resolved
        treas = treasury.get_public_treasury_address()
        if not treas.get("ok"):
            return treas
        idem = f"manager-share:{int(row['id'])}"
        cur.execute("""INSERT INTO treasury_payouts(payout_type,source_record_id,recipient_user_id,recipient_wallet_id,
            recipient_wallet_address,treasury_wallet_id,treasury_address,amount_nano,status,idempotency_key,approved_at)
            VALUES ('manager_share',%s,%s,%s,%s,%s,%s,%s,'approved',%s,NOW())
            ON CONFLICT(idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key
            RETURNING id""", (int(row["id"]), int(row["manager_user_id"]), int(resolved["wallet_id"]), resolved["wallet_address"],
                               int(treas["wallet_id"]), treas["address"], int(row["amount_nano"]), idem))
        treasury_payout_id = int(cur.fetchone()[0])
        cur.execute("""UPDATE deepalpha_manager_payouts SET status='approved',treasury_payout_id=%s,
            approved_by=%s,approved_at=NOW(),updated_at=NOW() WHERE id=%s""",
            (treasury_payout_id, int(actor_id), int(row["id"])))
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

    sent = treasury.send_from_treasury(resolved["wallet_address"], int(row["amount_nano"]), f"payout:{treasury_payout_id}")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if sent.get("ok") and sent.get("tx_hash"):
                cur.execute("""UPDATE treasury_payouts SET status='submitted',tx_hash=%s,submitted_at=NOW()
                    WHERE id=%s AND status='approved'""", (str(sent["tx_hash"]), treasury_payout_id))
                cur.execute("UPDATE deepalpha_manager_payouts SET status='submitted',updated_at=NOW() WHERE id=%s", (int(payout_id),))
                result = {"ok": True, "status": "submitted", "tx_hash": str(sent["tx_hash"])}
            else:
                error = str(sent.get("error") or "submission_uncertain")
                cur.execute("UPDATE treasury_payouts SET status='review_required',fail_reason=%s WHERE id=%s", (error, treasury_payout_id))
                cur.execute("UPDATE deepalpha_manager_payouts SET status='review_required',updated_at=NOW() WHERE id=%s", (int(payout_id),))
                result = {"ok": False, "error": error, "status": "review_required"}
        conn.commit()
        return result
    finally:
        conn.close()


def reconcile_submitted():
    from services import treasury_service as treasury
    conn = get_connection()
    paid = []
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""SELECT t.*,m.id AS manager_payout_id FROM treasury_payouts t
            JOIN deepalpha_manager_payouts m ON m.treasury_payout_id=t.id
            WHERE t.payout_type='manager_share' AND t.status='submitted' ORDER BY t.id LIMIT 20""")
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    for row in rows:
        verified = treasury.verify_treasury_payout_onchain(row)
        if not verified.get("ok"):
            continue
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE treasury_payouts SET status='paid',paid_at=NOW() WHERE id=%s AND status='submitted'", (int(row["id"]),))
                if cur.rowcount == 1:
                    cur.execute("UPDATE deepalpha_manager_payouts SET status='paid',updated_at=NOW() WHERE id=%s", (int(row["manager_payout_id"]),))
                    paid.append(row)
            conn.commit()
        finally:
            conn.close()
    return paid


def format_gram(nano):
    value = Decimal(int(nano or 0)) / Decimal(NANO)
    return format(value.normalize(), "f")
