from datetime import datetime
from typing import Optional, Dict, Any, List
import psycopg2
from db.database import get_connection


def _is_expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) < datetime.utcnow()
    except Exception:
        return False


def has_user_claimed_check(check_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM analysis_check_claims WHERE check_id=%s AND user_id=%s LIMIT 1", (check_id, user_id))
        return cur.fetchone() is not None
    finally:
        conn.close()


def claim_analysis_check(code: str, user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, check_type, status, expires_at, max_activations, used_activations FROM analysis_checks WHERE code=%s FOR UPDATE", (code,))
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "unavailable"}
        check_id, check_type, status, expires_at, max_act, used_act = row
        if status != "active" or _is_expired(expires_at) or used_act >= max_act:
            return {"ok": False, "error": "unavailable"}
        cur.execute("SELECT 1 FROM analysis_check_claims WHERE check_id=%s AND user_id=%s", (check_id, user_id))
        if cur.fetchone():
            return {"ok": False, "error": "already_claimed"}
        now = datetime.utcnow().isoformat()
        cur.execute("INSERT INTO analysis_check_claims (check_id, user_id, status, claimed_at, analysis_type) VALUES (%s,%s,'claimed',%s,%s) RETURNING id", (check_id, user_id, now, check_type))
        claim_id = cur.fetchone()[0]
        cur.execute("UPDATE analysis_checks SET used_activations = used_activations + 1 WHERE id=%s", (check_id,))
        conn.commit()
        return {"ok": True, "claim_id": claim_id, "check_type": check_type}
    except psycopg2.Error:
        conn.rollback()
        return {"ok": False, "error": "unavailable"}
    finally:
        conn.close()


def get_unused_analysis_credit(user_id: int, analysis_type: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
        SELECT c.id, c.check_id, c.claimed_at, c.analysis_type
        FROM analysis_check_claims c
        WHERE c.user_id=%s
          AND c.status='claimed'
          AND c.analysis_type=%s
        ORDER BY claimed_at ASC NULLS LAST, id ASC
        LIMIT 1
        """, (user_id, analysis_type))
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "check_id": row[1], "claimed_at": row[2], "analysis_type": row[3]}
    finally:
        conn.close()


def get_check_availability(code: str, user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, check_type, status, expires_at, max_activations, used_activations, require_channel_sub, required_channel "
            "FROM analysis_checks WHERE code=%s LIMIT 1",
            (code,),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "unavailable"}
        check_id, check_type, status, expires_at, max_act, used_act, require_sub, required_channel = row
        if status != "active" or _is_expired(expires_at) or used_act >= max_act:
            return {"ok": False, "error": "unavailable"}
        cur.execute("SELECT 1 FROM analysis_check_claims WHERE check_id=%s AND user_id=%s LIMIT 1", (check_id, user_id))
        if cur.fetchone():
            return {"ok": False, "error": "already_claimed"}
        return {
            "ok": True,
            "check": {
                "id": check_id,
                "check_type": check_type,
                "require_channel_sub": bool(require_sub),
                "required_channel": required_channel or "",
            },
        }
    finally:
        conn.close()


def mark_analysis_credit_used(claim_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        cur.execute("UPDATE analysis_check_claims SET status='used', used_at=%s WHERE id=%s AND status='claimed'", (now, claim_id))
        conn.commit()
    finally:
        conn.close()


def disable_analysis_check_by_id(check_id: int):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE analysis_checks SET status='disabled' WHERE id=%s", (check_id,))
        conn.commit()
    finally:
        conn.close()


def disable_analysis_check_with_refund(check_id: int, user_id: int, is_admin: bool = False) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, created_by_user_id, created_by_admin, status, check_type,
                   max_activations, used_activations, unit_price_tokens, refunded_tokens
            FROM analysis_checks
            WHERE id=%s
            FOR UPDATE
            """,
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}
        _, owner_id, created_by_admin, status, _, max_act, used_act, unit_price, refunded_tokens = row
        if not is_admin and int(owner_id or 0) != int(user_id):
            return {"ok": False, "error": "forbidden"}
        if status != "active":
            return {"ok": False, "error": "already_disabled", "already_refunded": int(refunded_tokens or 0) > 0}

        now = datetime.utcnow().isoformat()
        cur.execute("UPDATE analysis_checks SET status='disabled', disabled_at=%s WHERE id=%s", (now, check_id))

        max_act_i = int(max_act or 0)
        used_act_i = int(used_act or 0)
        unit_price_i = int(unit_price or 0)
        refunded_i = int(refunded_tokens or 0)
        unused = max(0, max_act_i - used_act_i)
        refund = 0

        if (not is_admin) and (not bool(created_by_admin)) and unit_price_i > 0 and unused > 0 and refunded_i == 0:
            refund = unused * unit_price_i
            cur.execute("UPDATE users SET token_balance = token_balance + %s WHERE user_id=%s", (refund, owner_id))
            cur.execute("UPDATE analysis_checks SET refunded_tokens=%s WHERE id=%s", (refund, check_id))

        conn.commit()
        return {
            "ok": True,
            "refund_tokens": refund,
            "unused_activations": unused,
            "used_activations": used_act_i,
            "max_activations": max_act_i,
            "already_refunded": refunded_i > 0,
            "unit_price_tokens": unit_price_i,
            "created_by_admin": bool(created_by_admin),
        }
    except Exception:
        conn.rollback()
        return {"ok": False, "error": "failed"}
    finally:
        conn.close()


def get_user_created_checks(user_id: int, include_disabled: bool = False, limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        safe_limit = max(1, min(int(limit or 20), 100))
        if include_disabled:
            cur.execute(
                """
                SELECT id, code, check_type, max_activations, used_activations, require_channel_sub,
                       required_channel, status, created_at, expires_at, created_by_admin
                FROM analysis_checks
                WHERE created_by_user_id=%s
                ORDER BY created_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                (user_id, safe_limit),
            )
        else:
            cur.execute(
                """
                SELECT id, code, check_type, max_activations, used_activations, require_channel_sub,
                       required_channel, status, created_at, expires_at, created_by_admin
                FROM analysis_checks
                WHERE created_by_user_id=%s
                  AND status='active'
                  AND COALESCE(used_activations, 0) < COALESCE(max_activations, 1)
                ORDER BY created_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                (user_id, safe_limit),
            )
        rows = cur.fetchall() or []
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "code": row[1],
                "check_type": row[2],
                "max_activations": row[3],
                "used_activations": row[4],
                "require_channel_sub": bool(row[5]),
                "required_channel": row[6] or "",
                "status": row[7],
                "created_at": row[8],
                "expires_at": row[9],
                "created_by_admin": bool(row[10]),
            })
        return result
    finally:
        conn.close()


def try_deduct_tokens(user_id: int, amount: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET token_balance = token_balance - %s WHERE user_id=%s AND token_balance >= %s", (amount, user_id, amount))
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()
