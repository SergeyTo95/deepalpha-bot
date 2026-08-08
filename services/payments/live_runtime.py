from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import psycopg2.extras

from db.database import get_connection
from services.payments.config import network_config
from services.payments.models import ObservedTransfer
from services.payments.repository import record_observed_transfer, record_payment_event
from services.velia_usdt_checkout_service import USDT_PRODUCTS


_OPEN_INTENT_STATUSES = ("created", "awaiting_payment", "detected", "confirming")
_PLAN_RANK = {"free": 0, "plus": 1, "pro": 2}


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _already_processed_transfer(cur: Any, transfer: ObservedTransfer) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT t.intent_id,i.public_reference,i.status
        FROM velia_payment_transactions t
        LEFT JOIN velia_payment_intents i ON i.id=t.intent_id
        WHERE t.network=%s AND t.asset=%s AND t.tx_hash=%s AND t.transfer_index=%s
        LIMIT 1
        """,
        (
            transfer.network.lower(),
            transfer.asset.upper(),
            transfer.tx_hash,
            int(transfer.transfer_index),
        ),
    )
    row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    if data.get("intent_id") and str(data.get("status") or "") == "fulfilled":
        return {
            "ok": True,
            "matched": True,
            "fulfilled": True,
            "already_processed": True,
            "public_reference": data.get("public_reference"),
        }
    return None


def _fulfill_locked_intent(cur: Any, intent: Dict[str, Any]) -> Dict[str, Any]:
    intent_id = int(intent["id"])
    user_id = int(intent["user_id"])
    product = USDT_PRODUCTS.get(str(intent.get("product_code") or "").lower())
    if product is None:
        raise RuntimeError("unknown_product")

    cur.execute("SELECT * FROM velia_payment_fulfillments WHERE intent_id=%s FOR UPDATE", (intent_id,))
    existing_fulfillment = cur.fetchone()
    if existing_fulfillment and str(existing_fulfillment.get("status") or "") == "fulfilled":
        cur.execute(
            "UPDATE velia_payment_intents SET status='fulfilled',fulfilled_at=COALESCE(fulfilled_at,NOW()),updated_at=NOW() WHERE id=%s",
            (intent_id,),
        )
        return {"fulfilled": True, "already_fulfilled": True, "credits_granted": 0}

    if not existing_fulfillment:
        cur.execute(
            """
            INSERT INTO velia_payment_fulfillments(
                intent_id,fulfillment_key,fulfillment_type,token_delta,subscription_days,status,attempts
            ) VALUES (%s,%s,%s,%s,%s,'pending',0)
            ON CONFLICT (intent_id) DO NOTHING
            """,
            (
                intent_id,
                f"intent:{intent_id}",
                product.kind,
                int(product.credits),
                int(product.subscription_days or 0) or None,
            ),
        )

    cur.execute("SELECT user_id,token_balance,subscription_until FROM users WHERE user_id=%s FOR UPDATE", (user_id,))
    user = cur.fetchone()
    if not user:
        raise RuntimeError("user_not_found")

    cur.execute(
        "UPDATE users SET token_balance=COALESCE(token_balance,0)+%s,updated_at=%s WHERE user_id=%s",
        (int(product.credits), datetime.utcnow().isoformat(), user_id),
    )

    entitlement_until = None
    selected_plan = None
    if product.kind == "subscription" and product.plan_code:
        now = datetime.now(timezone.utc)
        cur.execute(
            "SELECT plan_code,subscription_until FROM velia_user_commercial_state WHERE user_id=%s FOR UPDATE",
            (user_id,),
        )
        commercial = cur.fetchone()
        current_plan = str((commercial or {}).get("plan_code") or "free") if commercial else "free"
        current_expiry = _parse_time((commercial or {}).get("subscription_until")) if commercial else None
        if current_expiry is None:
            current_expiry = _parse_time(user.get("subscription_until"))
        base_time = current_expiry if current_expiry and current_expiry > now else now
        entitlement_until = base_time + timedelta(days=int(product.subscription_days))
        selected_plan = product.plan_code
        if current_expiry and current_expiry > now and _PLAN_RANK.get(current_plan, 0) > _PLAN_RANK.get(product.plan_code, 0):
            # Checkout prevents intentional downgrades, but if funds were already
            # received we never strand them. Preserve the stronger active plan.
            selected_plan = current_plan

        entitlement_iso = entitlement_until.isoformat()
        cur.execute(
            "UPDATE users SET subscription_until=%s,updated_at=%s WHERE user_id=%s",
            (entitlement_iso, datetime.utcnow().isoformat(), user_id),
        )
        cur.execute(
            """
            INSERT INTO velia_user_commercial_state(
                user_id,plan_code,source,subscription_until,last_synced_at,updated_at
            ) VALUES (%s,%s,'crypto',%s,NOW(),NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                plan_code=EXCLUDED.plan_code,
                source='crypto',
                subscription_until=EXCLUDED.subscription_until,
                last_synced_at=NOW(),
                updated_at=NOW()
            """,
            (user_id, selected_plan, entitlement_until),
        )

    cur.execute(
        """
        UPDATE velia_payment_fulfillments
        SET status='fulfilled',attempts=attempts+1,last_error_code=NULL,fulfilled_at=NOW(),updated_at=NOW()
        WHERE intent_id=%s
        """,
        (intent_id,),
    )
    cur.execute(
        """
        UPDATE velia_payment_intents
        SET status='fulfilled',fulfilled_at=NOW(),failure_code=NULL,updated_at=NOW()
        WHERE id=%s
        """,
        (intent_id,),
    )
    return {
        "fulfilled": True,
        "already_fulfilled": False,
        "credits_granted": int(product.credits),
        "plan_code": selected_plan,
        "subscription_until": entitlement_until,
    }


def process_finalized_transfer(transfer: ObservedTransfer) -> Dict[str, Any]:
    """Match one finalized allow-listed transfer and fulfill it atomically.

    A transfer is accepted only when network, canonical asset, recipient, exact
    fingerprint amount and chain timestamp all match exactly one unexpired VELIA
    intent. No private key or signing capability exists in this path.
    """
    network = str(transfer.network or "").lower()
    if transfer.asset.upper() != "USDT" or transfer.finality != "finalized":
        return {"ok": True, "matched": False, "reason": "not_finalized_usdt"}
    try:
        config = network_config(network)
    except ValueError:
        return {"ok": True, "matched": False, "reason": "unsupported_network"}
    if not config.configured:
        return {"ok": True, "matched": False, "reason": "network_not_configured"}
    if transfer.recipient_address != config.deposit_address:
        return {"ok": True, "matched": False, "reason": "recipient_mismatch"}
    if int(transfer.amount_atomic or 0) <= 0 or not transfer.block_timestamp:
        return {"ok": True, "matched": False, "reason": "invalid_transfer_shape"}

    persisted = record_observed_transfer(transfer)
    if not persisted.get("ok"):
        return persisted

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        already = _already_processed_transfer(cur, transfer)
        if already:
            conn.commit()
            return already

        # The transfer must have landed after invoice creation and before expiry.
        # Five minutes of clock tolerance is allowed only on the lower bound.
        cur.execute(
            """
            SELECT * FROM velia_payment_intents
            WHERE channel='crypto'
              AND network=%s
              AND asset='USDT'
              AND deposit_address=%s
              AND expected_amount_atomic=%s
              AND status = ANY(%s)
              AND created_at <= to_timestamp(%s) + INTERVAL '5 minutes'
              AND (expires_at IS NULL OR expires_at >= to_timestamp(%s))
            ORDER BY created_at ASC
            LIMIT 2
            FOR UPDATE
            """,
            (
                network,
                config.deposit_address,
                int(transfer.amount_atomic),
                list(_OPEN_INTENT_STATUSES),
                int(transfer.block_timestamp),
                int(transfer.block_timestamp),
            ),
        )
        matches = [dict(row) for row in (cur.fetchall() or [])]
        if len(matches) != 1:
            conn.commit()
            reason = "no_matching_intent" if not matches else "ambiguous_matching_intents"
            record_payment_event(
                event_key=f"chain:{network}:{transfer.tx_hash}:{transfer.transfer_index}:unmatched",
                source=f"{network}_watcher",
                event_type=reason,
                payload={"amount_atomic": int(transfer.amount_atomic)},
            )
            return {"ok": True, "matched": False, "reason": reason}

        intent = matches[0]
        intent_id = int(intent["id"])
        cur.execute(
            """
            UPDATE velia_payment_transactions
            SET intent_id=%s,confirmations=%s,finality='finalized',confirmed_at=NOW()
            WHERE network=%s AND asset='USDT' AND tx_hash=%s AND transfer_index=%s
            """,
            (
                intent_id,
                max(1, int(transfer.confirmations or 0)),
                network,
                transfer.tx_hash,
                int(transfer.transfer_index),
            ),
        )
        cur.execute(
            """
            UPDATE velia_payment_intents
            SET status='confirmed',detected_at=COALESCE(detected_at,NOW()),confirmed_at=NOW(),updated_at=NOW()
            WHERE id=%s
            """,
            (intent_id,),
        )
        result = _fulfill_locked_intent(cur, intent)
        conn.commit()

        record_payment_event(
            event_key=f"chain:{network}:{transfer.tx_hash}:{transfer.transfer_index}:fulfilled",
            source=f"{network}_watcher",
            event_type="payment_fulfilled",
            intent_id=intent_id,
            payload={
                "public_reference": intent.get("public_reference"),
                "product_code": intent.get("product_code"),
                "amount_atomic": int(transfer.amount_atomic),
                "credits_granted": result.get("credits_granted"),
            },
        )
        return {
            "ok": True,
            "matched": True,
            "fulfilled": True,
            "public_reference": intent.get("public_reference"),
            **result,
        }
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        cur.close()
        conn.close()
