from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

import psycopg2.extras

from db.database import get_connection
from services.payments.config import (
    PHASE1_USDT_NETWORKS,
    USDT_DECIMALS,
    configured_phase1_networks,
    crypto_checkout_enabled,
)


CRYPTO_DISCOUNT_PERCENT = 30
QUOTE_EXPIRY_MINUTES = 30
_AMOUNT_SCALE = 10 ** USDT_DECIMALS
_QUOTE_LOCK_NAMESPACE = "velia-usdt-quote-v1"
_OPEN_STATUSES = ("created", "awaiting_payment", "detected", "confirming")


@dataclass(frozen=True)
class UsdtProduct:
    code: str
    kind: str
    name: str
    store_price_usd: Decimal
    usdt_price: Decimal
    credits: int
    subscription_days: int = 0
    plan_code: Optional[str] = None

    @property
    def usdt_base_atomic(self) -> int:
        return int(self.usdt_price * _AMOUNT_SCALE)


USDT_PRODUCTS: Dict[str, UsdtProduct] = {
    "plus": UsdtProduct(
        code="plus",
        kind="subscription",
        name="VELIA Plus",
        store_price_usd=Decimal("14.99"),
        usdt_price=Decimal("10.49"),
        credits=1200,
        subscription_days=30,
        plan_code="plus",
    ),
    "pro": UsdtProduct(
        code="pro",
        kind="subscription",
        name="VELIA Pro",
        store_price_usd=Decimal("29.99"),
        usdt_price=Decimal("20.99"),
        credits=3000,
        subscription_days=30,
        plan_code="pro",
    ),
    "credits_100": UsdtProduct("credits_100", "credits", "100 Credits", Decimal("2.49"), Decimal("1.74"), 100),
    "credits_250": UsdtProduct("credits_250", "credits", "250 Credits", Decimal("4.99"), Decimal("3.49"), 250),
    "credits_800": UsdtProduct("credits_800", "credits", "800 Credits", Decimal("12.99"), Decimal("9.09"), 800),
    "credits_2000": UsdtProduct("credits_2000", "credits", "2,000 Credits", Decimal("27.99"), Decimal("19.59"), 2000),
    "credits_5000": UsdtProduct("credits_5000", "credits", "5,000 Credits", Decimal("59.99"), Decimal("41.99"), 5000),
    "credits_10000": UsdtProduct("credits_10000", "credits", "10,000 Credits", Decimal("109.99"), Decimal("76.99"), 10000),
}


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _product_payload(product: UsdtProduct) -> Dict[str, Any]:
    return {
        "code": product.code,
        "kind": product.kind,
        "name": product.name,
        "store_price_usd": _decimal_text(product.store_price_usd),
        "usdt_price": _decimal_text(product.usdt_price),
        "discount_percent": CRYPTO_DISCOUNT_PERCENT,
        "discount_label": "USDT -30%",
        "credits": product.credits,
        "subscription_days": product.subscription_days,
        "plan_code": product.plan_code,
    }


def usdt_checkout_catalog() -> Dict[str, Any]:
    configured = configured_phase1_networks()
    enabled = crypto_checkout_enabled()
    return {
        "ok": True,
        "channel": "usdt_direct",
        "discount_percent": CRYPTO_DISCOUNT_PERCENT,
        "discount_label": "USDT -30%",
        "checkout_enabled": bool(enabled and configured),
        "products": [_product_payload(product) for product in USDT_PRODUCTS.values()],
        "networks": [
            {
                "network": name,
                "asset": "USDT",
                "asset_identifier": config.canonical_asset_identifier,
                "asset_decimals": config.asset_decimals,
                "configured": bool(config.configured),
            }
            for name, config in configured.items()
            if name in PHASE1_USDT_NETWORKS
        ],
    }


def _intent_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "public_reference": row.get("public_reference"),
        "product_code": row.get("product_code"),
        "network": row.get("network"),
        "asset": row.get("asset"),
        "amount_usdt": str(row.get("expected_amount_asset") or ""),
        "amount_atomic": int(row.get("expected_amount_atomic") or 0),
        "asset_decimals": int(row.get("asset_decimals") or USDT_DECIMALS),
        "deposit_address": row.get("deposit_address"),
        "status": row.get("status"),
        "expires_at": row.get("expires_at"),
        "confirmed_at": row.get("confirmed_at"),
        "fulfilled_at": row.get("fulfilled_at"),
    }


def get_usdt_intent_for_user(user_id: int, public_reference: str) -> Dict[str, Any]:
    reference = str(public_reference or "").strip()
    if int(user_id or 0) <= 0 or not reference.startswith("vpay_") or len(reference) > 80:
        return {"ok": False, "error": "invalid_intent_reference"}
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT * FROM velia_payment_intents
            WHERE public_reference=%s AND user_id=%s AND channel='crypto'
            LIMIT 1
            """,
            (reference, int(user_id)),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "intent_not_found"}
        return {"ok": True, "intent": _intent_payload(dict(row))}
    finally:
        cur.close()
        conn.close()


def create_usdt_payment_intent(
    *,
    user_id: int,
    product_code: str,
    network: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    uid = int(user_id or 0)
    code = str(product_code or "").strip().lower()
    network_name = str(network or "").strip().lower()
    idem = str(idempotency_key or "").strip()
    product = USDT_PRODUCTS.get(code)
    if uid <= 0:
        return {"ok": False, "error": "invalid_user"}
    if product is None:
        return {"ok": False, "error": "unknown_product"}
    if network_name not in PHASE1_USDT_NETWORKS:
        return {"ok": False, "error": "unsupported_usdt_network"}
    if not idem or len(idem) > 200:
        return {"ok": False, "error": "invalid_idempotency_key"}
    if not crypto_checkout_enabled():
        return {"ok": False, "error": "usdt_checkout_disabled"}

    config = configured_phase1_networks().get(network_name)
    if config is None or not config.configured:
        return {"ok": False, "error": "usdt_network_unavailable"}

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # One quote generator per recipient/network at a time. The unique
        # micro-USDT fingerprint prevents concurrent users buying the same SKU
        # from becoming ambiguous on a shared watch-only deposit address.
        lock_key = f"{_QUOTE_LOCK_NAMESPACE}:{network_name}:{config.deposit_address}"
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))

        cur.execute(
            "SELECT * FROM velia_payment_intents WHERE idempotency_key=%s LIMIT 1",
            (idem,),
        )
        existing = cur.fetchone()
        if existing:
            row = dict(existing)
            if int(row.get("user_id") or 0) != uid:
                conn.rollback()
                return {"ok": False, "error": "idempotency_key_owned_by_another_user"}
            conn.commit()
            return {"ok": True, "created": False, "intent": _intent_payload(row)}

        cur.execute("SELECT 1 FROM users WHERE user_id=%s LIMIT 1", (uid,))
        if not cur.fetchone():
            conn.rollback()
            return {"ok": False, "error": "user_not_found"}

        # Reserve one of 999 micro-USDT fingerprints. A collision query includes
        # expiry so old completed/expired invoices never consume the namespace.
        selected_atomic = None
        for _ in range(80):
            suffix = secrets.randbelow(999) + 1
            candidate = product.usdt_base_atomic + suffix
            cur.execute(
                """
                SELECT 1 FROM velia_payment_intents
                WHERE channel='crypto'
                  AND network=%s
                  AND asset='USDT'
                  AND deposit_address=%s
                  AND expected_amount_atomic=%s
                  AND status = ANY(%s)
                  AND (expires_at IS NULL OR expires_at > NOW())
                LIMIT 1
                """,
                (network_name, config.deposit_address, candidate, list(_OPEN_STATUSES)),
            )
            if not cur.fetchone():
                selected_atomic = candidate
                break
        if selected_atomic is None:
            conn.rollback()
            return {"ok": False, "error": "quote_capacity_exhausted"}

        quote_amount = Decimal(selected_atomic) / Decimal(_AMOUNT_SCALE)
        public_reference = "vpay_" + uuid.uuid4().hex
        metadata = {
            "catalog": "economy-v0.2-usdt-v1",
            "discount_percent": CRYPTO_DISCOUNT_PERCENT,
            "base_usdt_price": _decimal_text(product.usdt_price),
            "amount_fingerprint_atomic": selected_atomic - product.usdt_base_atomic,
            "canonical_asset_identifier": config.canonical_asset_identifier,
            "watch_only": True,
        }
        cur.execute(
            """
            INSERT INTO velia_payment_intents(
                public_reference,user_id,product_code,channel,network,asset,
                expected_amount_usd,expected_amount_asset,expected_amount_atomic,
                asset_decimals,deposit_address,status,idempotency_key,expires_at,
                metadata_json,created_at,updated_at
            ) VALUES (
                %s,%s,%s,'crypto',%s,'USDT',%s,%s,%s,%s,%s,
                'awaiting_payment',%s,NOW() + (%s * INTERVAL '1 minute'),%s,NOW(),NOW()
            )
            RETURNING *
            """,
            (
                public_reference,
                uid,
                product.code,
                network_name,
                product.usdt_price,
                quote_amount,
                selected_atomic,
                USDT_DECIMALS,
                config.deposit_address,
                idem,
                QUOTE_EXPIRY_MINUTES,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return {"ok": True, "created": True, "intent": _intent_payload(dict(row))}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        cur.close()
        conn.close()
