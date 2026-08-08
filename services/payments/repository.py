from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg2.extras

from db.database import get_connection
from services.payments.models import ObservedTransfer


_OPEN_INTENT_STATUSES = ("created", "awaiting_payment", "detected", "confirming")
_SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "bearer_token",
    "authorization",
    "auth",
    "cookie",
    "set_cookie",
    "api_key",
    "apikey",
    "rpc_api_key",
    "secret",
    "client_secret",
    "webhook_secret",
    "password",
    "seed",
    "seed_phrase",
    "private_key",
    "signing_key",
    "session_token",
}


def _dict_row(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key or "").strip().lower().replace("-", "_")
            result[str(key)] = "[REDACTED]" if normalized in _SENSITIVE_KEYS else _redact_payload(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_payload(item) for item in value]
    if isinstance(value, bytes):
        return "[BINARY]"
    return value


def _is_nonnegative_finite(value: Any) -> bool:
    if value is None:
        return True
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return parsed.is_finite() and parsed >= 0


def create_payment_intent(
    *,
    user_id: int,
    product_code: Optional[str],
    channel: str,
    idempotency_key: str,
    network: Optional[str] = None,
    asset: Optional[str] = None,
    expected_amount_usd: Optional[float] = None,
    expected_amount_asset: Optional[float] = None,
    expected_amount_atomic: Optional[int] = None,
    asset_decimals: Optional[int] = None,
    deposit_address: Optional[str] = None,
    payment_memo: Optional[str] = None,
    expires_minutes: int = 30,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a VELIA payment intent exactly once for an idempotency key.

    Foundation callers may create records, but chain/store acceptance is still
    disabled elsewhere. No user balance/subscription mutation happens here.
    """
    key = str(idempotency_key or "").strip()
    normalized_channel = str(channel or "").strip().lower()
    if not key or len(key) > 200:
        return {"ok": False, "error": "invalid_idempotency_key"}
    if normalized_channel not in {"crypto", "google_play", "app_store"}:
        return {"ok": False, "error": "invalid_channel"}
    if int(user_id or 0) <= 0:
        return {"ok": False, "error": "invalid_user"}
    if not _is_nonnegative_finite(expected_amount_usd) or not _is_nonnegative_finite(expected_amount_asset):
        return {"ok": False, "error": "invalid_expected_amount"}

    normalized_atomic: Optional[int] = None
    if expected_amount_atomic is not None:
        try:
            normalized_atomic = int(expected_amount_atomic)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_expected_amount"}
        if normalized_atomic < 0:
            return {"ok": False, "error": "invalid_expected_amount"}

    normalized_decimals: Optional[int] = None
    if asset_decimals is not None:
        try:
            normalized_decimals = int(asset_decimals)
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "invalid_asset_decimals"}
        if normalized_decimals < 0 or normalized_decimals > 36:
            return {"ok": False, "error": "invalid_asset_decimals"}

    if normalized_channel == "crypto" and (not network or not asset):
        return {"ok": False, "error": "crypto_network_asset_required"}

    try:
        expires = max(5, min(int(expires_minutes or 30), 24 * 60))
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "error": "invalid_expiry"}

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        public_reference = "vpay_" + uuid.uuid4().hex
        cur.execute(
            """
            INSERT INTO velia_payment_intents(
                public_reference,user_id,product_code,channel,network,asset,
                expected_amount_usd,expected_amount_asset,expected_amount_atomic,
                asset_decimals,deposit_address,payment_memo,status,idempotency_key,
                expires_at,metadata_json
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'created',%s,
                NOW() + (%s * INTERVAL '1 minute'),%s
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            (
                public_reference,
                int(user_id),
                str(product_code or "").strip() or None,
                normalized_channel,
                str(network or "").strip().lower() or None,
                str(asset or "").strip().upper() or None,
                expected_amount_usd,
                expected_amount_asset,
                normalized_atomic,
                normalized_decimals,
                str(deposit_address or "").strip() or None,
                str(payment_memo or "").strip() or None,
                key,
                expires,
                json.dumps(_redact_payload(metadata or {}), ensure_ascii=False),
            ),
        )
        row = cur.fetchone()
        if row:
            conn.commit()
            return {"ok": True, "created": True, "intent": _dict_row(row)}

        cur.execute(
            "SELECT * FROM velia_payment_intents WHERE idempotency_key=%s LIMIT 1",
            (key,),
        )
        existing = cur.fetchone()
        conn.commit()
        if not existing:
            return {"ok": False, "error": "idempotency_conflict_without_row"}
        return {"ok": True, "created": False, "intent": _dict_row(existing)}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        cur.close()
        conn.close()


def list_watchable_crypto_intents(network: str, limit: int = 100) -> List[Dict[str, Any]]:
    name = str(network or "").strip().lower()
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT * FROM velia_payment_intents
            WHERE channel='crypto'
              AND network=%s
              AND status = ANY(%s)
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (name, list(_OPEN_INTENT_STATUSES), max(1, min(int(limit), 500))),
        )
        return [dict(row) for row in (cur.fetchall() or [])]
    finally:
        cur.close()
        conn.close()


def record_payment_event(
    *,
    event_key: str,
    source: str,
    event_type: str,
    intent_id: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    key = str(event_key or "").strip()
    if not key:
        return False
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO velia_payment_events(event_key,intent_id,source,event_type,payload_json,occurred_at)
            VALUES (%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (event_key) DO NOTHING
            """,
            (
                key[:240],
                None if intent_id is None else int(intent_id),
                str(source or "unknown")[:80],
                str(event_type or "unknown")[:80],
                json.dumps(_redact_payload(payload or {}), ensure_ascii=False),
            ),
        )
        inserted = cur.rowcount == 1
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()


def record_observed_transfer(transfer: ObservedTransfer, intent_id: Optional[int] = None) -> Dict[str, Any]:
    """Persist one observed transfer idempotently without confirming/crediting it."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            INSERT INTO velia_payment_transactions(
                intent_id,network,asset,tx_hash,transfer_index,block_ref,
                sender_address,recipient_address,amount_atomic,confirmations,finality
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (network,asset,tx_hash,transfer_index) DO NOTHING
            RETURNING *
            """,
            (
                None if intent_id is None else int(intent_id),
                transfer.network.lower(),
                transfer.asset.upper(),
                transfer.tx_hash,
                int(transfer.transfer_index),
                transfer.block_ref,
                transfer.sender_address,
                transfer.recipient_address,
                int(transfer.amount_atomic),
                int(transfer.confirmations),
                transfer.finality,
            ),
        )
        inserted = cur.fetchone()
        if inserted:
            conn.commit()
            return {"ok": True, "created": True, "transaction": dict(inserted)}
        cur.execute(
            """
            SELECT * FROM velia_payment_transactions
            WHERE network=%s AND asset=%s AND tx_hash=%s AND transfer_index=%s
            LIMIT 1
            """,
            (
                transfer.network.lower(),
                transfer.asset.upper(),
                transfer.tx_hash,
                int(transfer.transfer_index),
            ),
        )
        existing = cur.fetchone()
        conn.commit()
        return {"ok": True, "created": False, "transaction": _dict_row(existing)}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        cur.close()
        conn.close()


def queue_fulfillment_for_confirmed_intent(
    *,
    intent_id: int,
    fulfillment_type: str,
    token_delta: Optional[int] = None,
    subscription_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Create an idempotent pending fulfillment; never mutates the user itself."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id,status FROM velia_payment_intents WHERE id=%s FOR UPDATE",
            (int(intent_id),),
        )
        intent = cur.fetchone()
        if not intent:
            conn.rollback()
            return {"ok": False, "error": "intent_not_found"}
        if str(intent.get("status") or "") != "confirmed":
            conn.rollback()
            return {"ok": False, "error": "intent_not_confirmed"}
        key = f"intent:{int(intent_id)}"
        cur.execute(
            """
            INSERT INTO velia_payment_fulfillments(
                intent_id,fulfillment_key,fulfillment_type,token_delta,subscription_days,status
            ) VALUES (%s,%s,%s,%s,%s,'pending')
            ON CONFLICT (intent_id) DO NOTHING
            RETURNING *
            """,
            (
                int(intent_id),
                key,
                str(fulfillment_type or "unknown")[:80],
                token_delta,
                subscription_days,
            ),
        )
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT * FROM velia_payment_fulfillments WHERE intent_id=%s", (int(intent_id),))
            row = cur.fetchone()
        conn.commit()
        return {"ok": True, "fulfillment": _dict_row(row)}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        cur.close()
        conn.close()


def update_worker_state(
    network: str,
    *,
    enabled: bool,
    mode: str,
    status: str,
    cursor_value: Optional[str] = None,
    chain_height: Optional[int] = None,
    lag_blocks: Optional[int] = None,
    error_code: Optional[str] = None,
    success: bool = False,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO velia_payment_worker_state(
                network,asset,enabled,mode,status,cursor_value,chain_height,lag_blocks,
                last_poll_at,last_success_at,last_error_code,updated_at
            ) VALUES (%s,'USDT',%s,%s,%s,%s,%s,%s,NOW(),CASE WHEN %s THEN NOW() ELSE NULL END,%s,NOW())
            ON CONFLICT (network) DO UPDATE SET
                enabled=EXCLUDED.enabled,
                mode=EXCLUDED.mode,
                status=EXCLUDED.status,
                cursor_value=COALESCE(EXCLUDED.cursor_value,velia_payment_worker_state.cursor_value),
                chain_height=COALESCE(EXCLUDED.chain_height,velia_payment_worker_state.chain_height),
                lag_blocks=COALESCE(EXCLUDED.lag_blocks,velia_payment_worker_state.lag_blocks),
                last_poll_at=NOW(),
                last_success_at=CASE WHEN %s THEN NOW() ELSE velia_payment_worker_state.last_success_at END,
                last_error_code=EXCLUDED.last_error_code,
                updated_at=NOW()
            """,
            (
                str(network or "").lower(),
                bool(enabled),
                str(mode or "")[:80],
                str(status or "")[:80],
                cursor_value,
                chain_height,
                lag_blocks,
                bool(success),
                None if not error_code else str(error_code)[:160],
                bool(success),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
