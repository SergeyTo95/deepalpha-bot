"""Idempotent custodial purchases, verified against the Treasury's received funds."""
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone

import psycopg2.extras
import requests

from db import database as db
from services import ton_chain_service as chain
from services import ton_wallet_service as wallets

logger = logging.getLogger(__name__)


def ensure_columns(cursor):
    cursor.execute("ALTER TABLE ton_purchase_intents ADD COLUMN IF NOT EXISTS client_request_key TEXT")
    cursor.execute("ALTER TABLE ton_purchase_intents ADD COLUMN IF NOT EXISTS request_hash TEXT")
    cursor.execute("ALTER TABLE ton_purchase_intents ADD COLUMN IF NOT EXISTS payment_network TEXT")
    cursor.execute("ALTER TABLE ton_purchase_intents ADD COLUMN IF NOT EXISTS verified_tx_hash TEXT")
    cursor.execute("ALTER TABLE ton_purchase_intents ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ")
    cursor.execute("ALTER TABLE ton_purchase_intents ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ")
    cursor.execute("ALTER TABLE ton_purchase_intents ADD COLUMN IF NOT EXISTS receipt_scan_offset INTEGER NOT NULL DEFAULT 0")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_gram_purchase_request ON ton_purchase_intents(user_id,client_request_key) WHERE client_request_key IS NOT NULL")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_gram_purchase_receipt ON ton_purchase_intents(verified_tx_hash) WHERE verified_tx_hash IS NOT NULL")


def quote(user_id, amount_tokens):
    from services.gram_payment_health import payment_health
    from services.ton_purchase_service import is_ton_wallet_token_purchase_enabled, get_ton_token_price_per_internal_token_nano
    if not is_ton_wallet_token_purchase_enabled():
        return {"ok": False, "error": "ton_token_purchase_disabled"}
    health = payment_health()
    if not health["ready"]:
        return {"ok": False, "error": health["reason"]}
    if not wallets.get_ton_wallet_runtime_status().get("can_send"):
        return {"ok": False, "error": "wallet_sending_disabled"}
    raw = str(amount_tokens)
    if not re.fullmatch(r"[0-9]{1,7}", raw):
        return {"ok": False, "error": "invalid_amount_tokens"}
    count = int(raw)
    try:
        minimum = max(1, int(db.get_setting("ton_token_purchase_min_tokens", "1") or 1))
        price = get_ton_token_price_per_internal_token_nano()
        bonus_pct = int(db.get_setting("ton_token_purchase_bonus_percent", "0") or 0)
    except (ValueError, TypeError, OverflowError):
        return {"ok": False, "error": "invalid_ton_token_price"}
    if not minimum <= count <= 1_000_000:
        return {"ok": False, "error": "invalid_amount_tokens"}
    if price <= 0:
        return {"ok": False, "error": "invalid_ton_token_price"}
    if not 0 <= bonus_pct <= 100:
        return {"ok": False, "error": "invalid_bonus_configuration"}
    amount = count * price
    if not 0 < amount < 2**63:
        return {"ok": False, "error": "invalid_amount"}
    return {"ok": True, "requested_tokens": count, "bonus_tokens": count * bonus_pct // 100,
            "total_tokens": count + count * bonus_pct // 100, "amount_nano": amount,
            "price_per_token_nano": price, "project_wallet": health["address"], "network": health["network"]}


def _result(row):
    if row["status"] == "failed":
        return {"ok": False, "error": row.get("fail_reason") or "purchase_failed", "intent_id": row["id"]}
    fulfilled = row["status"] == "fulfilled"
    return {"ok": True, "intent_id": row["id"], "status": row["status"],
            "tokens_credited": int(row["total_tokens"] or 0) if fulfilled else 0,
            "total_tokens": int(row["total_tokens"] or 0), "bonus_tokens": int(row["bonus_tokens"] or 0),
            "ton_paid_display": chain.nano_to_ton_display(int(row["expected_amount_nano"])),
            "tx_hash": row.get("verified_tx_hash") or row.get("tx_hash") or "",
            "message": "" if fulfilled else "payment_submitted_waiting_confirmation"}


def purchase(user_id, amount_tokens, request_key, *, expected_quote=None):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", str(request_key or "")):
        return {"ok": False, "error": "idempotency_key_required"}
    if not re.fullmatch(r"[0-9]{1,7}", str(amount_tokens)):
        return {"ok": False, "error": "invalid_amount_tokens"}
    # Per-user session lock also serializes wallet seqno use for these purchases.
    conn = db.get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    locked = False
    try:
        cursor.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (f"gram:purchase:{int(user_id)}",))
        locked = True
        cursor.execute("SELECT * FROM ton_purchase_intents WHERE user_id=%s AND client_request_key=%s", (int(user_id), request_key))
        row = cursor.fetchone()
        if row:
            if int(row["requested_tokens"]) != int(amount_tokens):
                return {"ok": False, "error": "idempotency_conflict"}
            return _result(row)
        cursor.execute("SELECT id FROM ton_purchase_intents WHERE user_id=%s AND status='submitted' LIMIT 1", (int(user_id),))
        pending = cursor.fetchone()
        if pending:
            return {"ok": False, "error": "purchase_pending", "intent_id": pending["id"]}
        terms = quote(user_id, amount_tokens)
        if not terms["ok"]:
            return terms
        if expected_quote and any(str(terms.get(k)) != str(expected_quote.get(k)) for k in
                                  ("requested_tokens", "bonus_tokens", "total_tokens", "amount_nano", "project_wallet", "network")):
            return {"ok": False, "error": "quote_changed"}
        wallet = wallets.get_or_create_user_ton_wallet(user_id)
        if not wallet.get("ok"):
            return {"ok": False, "error": wallet.get("error") or "wallet_unavailable"}
        # Persist a claim before any network submission. Replays only read it,
        # including after a process crash or an ambiguous network response.
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""INSERT INTO ton_purchase_intents(user_id,product_type,wallet_address,project_wallet,
            expected_amount_nano,status,requested_tokens,bonus_tokens,total_tokens,price_per_token_nano,
            metadata_json,client_request_key,request_hash,payment_network,created_at,updated_at,submitted_at)
            VALUES (%s,'token_purchase',%s,%s,%s,'submitted',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (int(user_id), wallet["wallet_address"], terms["project_wallet"], str(terms["amount_nano"]),
             terms["requested_tokens"], terms["bonus_tokens"], terms["total_tokens"], str(terms["price_per_token_nano"]),
             json.dumps(terms), request_key, hashlib.sha256(json.dumps(terms, sort_keys=True).encode()).hexdigest(),
             terms["network"], now, now, now))
        row = dict(cursor.fetchone())
        conn.commit()
        sent = wallets.send_ton_from_user_wallet(user_id, terms["project_wallet"], terms["amount_nano"],
                                                 f"DeepAlpha token purchase:{row['id']}", allow_seqno_retry=False)
        if not sent.get("ok"):
            # A send timeout is not proof that no money moved. Keep this claim
            # for reconciliation; never retry a broadcast under the same key.
            known_no_send = {"disabled", "setup_required", "invalid_address", "invalid_amount", "wallet_not_found",
                             "wallet_conflict", "insufficient_balance", "toncenter_unavailable", "seqno_unavailable", "signing_failed"}
            error = str(sent.get("error") or "send_failed")
            if error in known_no_send:
                db.fail_ton_purchase_intent(row["id"], error)
                return {"ok": False, "error": error, "intent_id": row["id"]}
            return {**_result(row), "message": "submission_uncertain_do_not_repeat"}
        submitted = db.submit_ton_purchase_intent(row["id"], str(sent.get("tx_hash") or ""))
        if submitted:
            row = submitted
        # Confirmation belongs to the bounded background reader; HTTP/UI does
        # not declare success merely because the sender returned a message hash.
        return _result(row)
    finally:
        try:
            conn.rollback()
            if locked:
                cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (f"gram:purchase:{int(user_id)}",))
                conn.commit()
        finally:
            cursor.close()
            conn.close()


def fetch_receipts(address, start_time, *, start_offset=0):
    base = chain._base_url().rstrip("/")
    base = base[:-7] if base.endswith("/api/v2") else base[:-7] if base.endswith("/api/v3") else base
    headers = {"X-API-Key": chain._params().get("api_key", "")}
    result = []
    for offset in range(start_offset, start_offset + 300, 100):
        started = time.monotonic()
        with requests.get(base + "/api/v3/transactions", params={"account": address, "start_utime": max(0, start_time - 60),
                          "limit": 100, "offset": offset, "sort": "asc"}, headers=headers,
                          timeout=(4, 10), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise RuntimeError("receipt_provider_unavailable")
            body = bytearray()
            for chunk in response.iter_content(32768):
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024 or time.monotonic() - started > 15:
                    raise RuntimeError("receipt_response_limit")
            payload = json.loads(body)
        rows = payload.get("transactions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("receipt_response_invalid")
        result.extend(r for r in rows[:100] if isinstance(r, dict))
        if len(rows) < 100:
            break
    return result


def _created_time(value):
    parsed = datetime.fromisoformat(str(value))
    return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp())


def matching_receipt(intent, tx):
    try:
        message = tx.get("in_msg") or {}
        description = tx.get("description") or {}
        if tx.get("emulated") is True or tx.get("finality") not in (None, "finalized"):
            return False
        if int(tx.get("mc_block_seqno") or 0) <= 0 or description.get("aborted") is not False:
            return False
        if message.get("bounced") is not False or description.get("bounce"):
            return False
        compute = description.get("compute_ph") or {}
        if compute.get("success") is not True or compute.get("exit_code") not in (0, 1):
            return False
        action = description.get("action")
        if action and (action.get("success") is not True or action.get("result_code") != 0):
            return False
        if int(tx.get("now") or 0) < _created_time(intent["created_at"]):
            return False
        same = lambda a, b: bool(a and b and chain.normalize_ton_address(a) == chain.normalize_ton_address(b))
        if not same(tx.get("account"), intent["project_wallet"]) or not same(message.get("destination"), intent["project_wallet"]):
            return False
        if not same(message.get("source"), intent["wallet_address"]) or int(message.get("value") or 0) != int(intent["expected_amount_nano"]):
            return False
        decoded = (message.get("message_content") or {}).get("decoded") or {}
        comment = decoded.get("comment") if isinstance(decoded, dict) else None
        if comment is None:
            from services.treasury_service import decode_ton_text_comment_from_msg
            comment = decode_ton_text_comment_from_msg(message)
        return bool(tx.get("hash") and comment == f"DeepAlpha token purchase:{intent['id']}")
    except (TypeError, ValueError, KeyError):
        return False


def verify_purchase(intent_id, *, receipts=None):
    conn = db.get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM ton_purchase_intents WHERE id=%s", (int(intent_id),))
        row = cur.fetchone()
        conn.rollback()
        if not row or row["status"] != "submitted":
            return {"ok": False, "error": "intent_not_submitted"}
        if row.get("payment_network") and row["payment_network"] != chain._network():
            return {"ok": False, "error": "network_mismatch"}
        if receipts is None:
            receipts = fetch_receipts(row["project_wallet"], _created_time(row["created_at"]))
        matched = next((tx for tx in receipts if matching_receipt(row, tx)), None)
        if not matched:
            return {"ok": False, "error": "receipt_not_confirmed"}
        # The unique receiving-transaction hash prevents reusing one receipt.
        cur.execute("""UPDATE ton_purchase_intents SET verified_tx_hash=%s,verified_at=NOW(),payment_network=%s
            WHERE id=%s AND status='submitted' AND (verified_tx_hash IS NULL OR verified_tx_hash=%s)""",
            (matched["hash"], chain._network(), int(intent_id), matched["hash"]))
        changed = cur.rowcount == 1
        conn.commit()
        return {"ok": changed, "tx_hash": matched["hash"]}
    except Exception:
        conn.rollback()
        logger.warning("GRAM_PURCHASE_VERIFICATION_UNAVAILABLE intent_id=%s", int(intent_id))
        return {"ok": False, "error": "verification_unavailable"}
    finally:
        conn.close()


def reconcile_pending_purchases():
    conn = db.get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM ton_purchase_intents WHERE status='submitted' ORDER BY last_checked_at NULLS FIRST,id LIMIT 50")
        rows = cur.fetchall()
        if not rows:
            return []
    finally:
        conn.close()
    groups = {}
    completed = []
    for row in rows:
        # Legacy records use the currently configured network; actual receipt
        # proof still comes from that network, never the stored sender hash.
        if row.get("payment_network") and row["payment_network"] != chain._network():
            continue
        # A stable day boundary lets records share bounded pages while retaining
        # progress through busy Treasury history across worker restarts.
        start = _created_time(row["created_at"]) // 86400 * 86400
        groups.setdefault((row["project_wallet"], start, int(row.get("receipt_scan_offset") or 0)), []).append(row)
    for (address, start, offset), intents in list(groups.items())[:2]:
        try:
            ids = [r["id"] for r in intents]
            _mark_scan(ids)
            receipts = fetch_receipts(address, start, start_offset=offset)
            # Overlap the tip for delayed indexing; never skip a failed page.
            _mark_scan(ids, offset + (300 if len(receipts) >= 300 else max(0, len(receipts) - 20)))
            for row in intents:
                if verify_purchase(row["id"], receipts=receipts).get("ok"):
                    fulfilled = db.fulfill_ton_purchase_intent(row["id"])
                    if fulfilled and not fulfilled.get("already_fulfilled"):
                        try:
                            from services.referral_rewards_service import process_token_purchase_referral_reward
                            process_token_purchase_referral_reward(buyer_user_id=int(fulfilled["user_id"]),
                                purchase_amount_nano=int(fulfilled["expected_amount_nano"]), purchase_ref=str(fulfilled["id"]))
                        except Exception:
                            logger.warning("GRAM_PURCHASE_REFERRAL_RETRY_REQUIRED intent_id=%s", fulfilled["id"])
                        completed.append(fulfilled)
        except Exception:
            logger.warning("GRAM_PURCHASE_RECONCILIATION_UNAVAILABLE")
    return completed


def _mark_scan(ids, offset=None):
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE ton_purchase_intents SET last_checked_at=NOW(),receipt_scan_offset=COALESCE(%s,receipt_scan_offset) WHERE id=ANY(%s)", (offset, ids))
        conn.commit()
    finally:
        conn.close()
