from datetime import datetime
from typing import Optional, Dict, Any
import psycopg2
from db.database import get_connection, get_analysis_check_by_code


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
        SELECT id, check_id, claimed_at, analysis_type FROM analysis_check_claims
        WHERE user_id=%s AND status='claimed' AND analysis_type=%s
        ORDER BY claimed_at ASC NULLS LAST, id ASC
        LIMIT 1
        """, (user_id, analysis_type))
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "check_id": row[1], "claimed_at": row[2], "analysis_type": row[3]}
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
