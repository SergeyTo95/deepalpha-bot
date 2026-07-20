import os
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from db.database import get_connection, get_setting
from services.ton_chain_service import normalize_ton_address

logger = logging.getLogger(__name__)

NANO = 1_000_000_000


def _flag(name: str) -> bool:
    return str(os.getenv(name, "false")).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def incoming_enabled() -> bool:
    return _flag("TREASURY_INCOMING_ENABLED")


def outgoing_enabled() -> bool:
    return _flag("TREASURY_OUTGOING_ENABLED")


def _row_dict(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return dict(row) if hasattr(row, "keys") else None


def get_active_treasury_wallet(for_update: bool = False) -> Dict[str, Any]:
    conn = get_connection(); cur = conn.cursor()
    try:
        sql = "SELECT id,wallet_address,network,status,created_by,created_at,updated_at FROM cashier_payment_wallets WHERE status='active' ORDER BY id ASC"
        if for_update:
            sql += " FOR UPDATE"
        cur.execute(sql)
        rows = cur.fetchall() or []
        if len(rows) == 0:
            return {"ok": False, "error": "treasury_not_configured"}
        if len(rows) > 1:
            return {"ok": False, "error": "treasury_conflict"}
        row = rows[0]
        if hasattr(row, "keys"):
            data = dict(row)
        else:
            data = {"id": row[0], "wallet_address": row[1], "network": row[2], "status": row[3]}
        return {"ok": True, "wallet": data}
    except Exception:
        logger.exception("treasury_status_failed")
        return {"ok": False, "error": "treasury_lookup_failed"}
    finally:
        conn.close()


def get_public_treasury_address() -> Dict[str, Any]:
    res = get_active_treasury_wallet()
    if not res.get("ok"):
        return res
    wallet = res["wallet"]
    return {"ok": True, "address": wallet.get("wallet_address"), "network": wallet.get("network"), "wallet_id": wallet.get("id")}


def get_treasury_balance() -> Dict[str, Any]:
    res = get_public_treasury_address()
    if not res.get("ok"):
        return res
    # Balance backend is deliberately pluggable; never expose secrets.
    return {"ok": True, "address": res["address"], "network": res.get("network"), "balance_nano": 0}


def get_treasury_runtime_status() -> Dict[str, Any]:
    res = get_public_treasury_address()
    return {
        "ok": bool(res.get("ok")),
        "error": res.get("error"),
        "address": res.get("address"),
        "network": res.get("network") or os.getenv("TON_NETWORK", "mainnet"),
        "incoming_enabled": incoming_enabled(),
        "outgoing_enabled": outgoing_enabled(),
    }


def create_payment_intent(user_id: int, product_type: str, product_ref: str, amount_nano: int,
                          expected_sender_address: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
                          idempotency_key: Optional[str] = None, ttl_minutes: int = 30) -> Dict[str, Any]:
    if not incoming_enabled():
        return {"ok": False, "error": "treasury_incoming_disabled"}
    treasury = get_public_treasury_address()
    if not treasury.get("ok"):
        return treasury
    ref = "pay_" + uuid.uuid4().hex
    idem = idempotency_key or f"{user_id}:{product_type}:{product_ref}:{uuid.uuid4().hex}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO payment_intents (public_reference,user_id,product_type,product_ref,expected_amount_nano,
              treasury_wallet_id,treasury_address,expected_sender_address,status,expires_at,metadata_json,idempotency_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id, public_reference
            """,
            (ref, int(user_id), product_type, str(product_ref), int(amount_nano), int(treasury["wallet_id"]), treasury["address"], expected_sender_address, expires_at, json.dumps(metadata or {}, ensure_ascii=False), idem),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return {"ok": False, "error": "idempotency_conflict"}
        return {"ok": True, "id": row[0], "public_reference": row[1], "treasury_address": treasury["address"], "amount_nano": int(amount_nano)}
    except Exception:
        conn.rollback(); logger.exception("payment_intent_create_failed")
        return {"ok": False, "error": "payment_intent_create_failed"}
    finally:
        conn.close()


def verify_payment_intent(intent_id: int, tx: Dict[str, Any]) -> Dict[str, Any]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id,public_reference,expected_amount_nano,treasury_address,expected_sender_address,status,expires_at FROM payment_intents WHERE id=%s FOR UPDATE", (int(intent_id),))
        row = cur.fetchone()
        if not row: return {"ok": False, "error": "intent_not_found"}
        iid, ref, amount, dst, src, status, expires_at = row
        if status == "fulfilled": return {"ok": False, "error": "intent_already_fulfilled"}
        if expires_at and datetime.now(timezone.utc) > expires_at.replace(tzinfo=timezone.utc): return {"ok": False, "error": "intent_expired"}
        if str(tx.get("network") or "").lower() != str(os.getenv("TON_NETWORK", "mainnet")).lower(): return {"ok": False, "error": "network_mismatch"}
        tx_hash = str(tx.get("tx_hash") or "").strip()
        if not tx_hash: return {"ok": False, "error": "tx_hash_required"}
        cur.execute("SELECT id FROM payment_intents WHERE tx_hash=%s AND id<>%s", (tx_hash, iid))
        if cur.fetchone(): return {"ok": False, "error": "tx_hash_not_unique"}
        if normalize_ton_address(tx.get("destination")) != normalize_ton_address(dst): return {"ok": False, "error": "destination_mismatch"}
        if int(tx.get("amount_nano") or 0) < int(amount): return {"ok": False, "error": "amount_too_low"}
        if src and normalize_ton_address(tx.get("source")) != normalize_ton_address(src): return {"ok": False, "error": "source_mismatch"}
        if str(ref) not in str(tx.get("comment") or tx.get("payload") or ""): return {"ok": False, "error": "reference_missing"}
        cur.execute("UPDATE payment_intents SET status='verified',tx_hash=%s,verified_at=NOW() WHERE id=%s AND status='pending'", (tx_hash, iid))
        if cur.rowcount != 1: conn.rollback(); return {"ok": False, "error": "intent_state_changed"}
        conn.commit(); return {"ok": True, "tx_hash": tx_hash}
    except Exception:
        conn.rollback(); logger.exception("payment_intent_verify_failed"); return {"ok": False, "error": "verification_failed"}
    finally:
        conn.close()


def resolve_internal_payout_wallet(user_id: int, conn=None, for_update: bool = False) -> Dict[str, Any]:
    own = conn is None
    if own: conn = get_connection()
    cur = conn.cursor()
    try:
        sql = "SELECT id,user_id,wallet_address,status,COALESCE(archived_at,NULL) FROM user_ton_wallets WHERE user_id=%s AND network='production' ORDER BY id ASC"
        if for_update: sql += " FOR UPDATE"
        cur.execute(sql, (int(user_id),))
        rows = cur.fetchall() or []
        if len(rows) == 0: return {"ok": False, "error": "internal_wallet_required"}
        if len(rows) > 1: return {"ok": False, "error": "wallet_conflict"}
        r = rows[0]; wid, uid, addr, status = r[0], r[1], r[2], r[3]
        if status != "active" or not normalize_ton_address(addr): return {"ok": False, "error": "internal_wallet_required"}
        cur.execute("SELECT COUNT(*) FROM user_ton_wallets WHERE wallet_address=%s", (addr,))
        if int((cur.fetchone() or [0])[0] or 0) > 1: return {"ok": False, "error": "wallet_conflict"}
        return {"ok": True, "wallet_id": wid, "wallet_address": addr}
    finally:
        if own: conn.close()


def send_from_treasury(recipient_address: str, amount_nano: int, comment: str = "") -> Dict[str, Any]:
    if not outgoing_enabled(): return {"ok": False, "error": "treasury_outgoing_disabled"}
    treasury = get_public_treasury_address()
    if not treasury.get("ok"): return treasury
    if not normalize_ton_address(recipient_address): return {"ok": False, "error": "invalid_recipient_wallet"}
    return {"ok": True, "tx_hash": "pending_external_submit", "treasury_address": treasury["address"]}
