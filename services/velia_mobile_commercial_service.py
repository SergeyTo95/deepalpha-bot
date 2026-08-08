from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import psycopg2.extras
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from db.database import get_connection


CATALOG_VERSION = "economy-v0.2-live1"
DEFAULT_PACKAGE_NAME = "ai.deepalpha.android"
ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
GOOGLE_API_BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3"

# Public VELIA catalog. These are the approved Economy v0.2 values promoted to
# a versioned live snapshot. Draft/admin rows are intentionally not read on each
# request so an owner-side draft edit can never silently change live checkout.
PLANS: Dict[str, Dict[str, Any]] = {
    "free": {
        "id": "free",
        "name": "Free",
        "store_price_usd": 0.0,
        "monthly_credits": 100,
        "welcome_credits": 50,
        "core_access": "light_fair_use",
        "play_product_id": None,
        "play_product_type": None,
    },
    "plus": {
        "id": "plus",
        "name": "Plus",
        "store_price_usd": 14.99,
        "monthly_credits": 1200,
        "welcome_credits": 0,
        "core_access": "included_fair_use",
        "play_product_id": "velia_plus_monthly",
        "play_product_type": "subs",
    },
    "pro": {
        "id": "pro",
        "name": "Pro",
        "store_price_usd": 29.99,
        "monthly_credits": 3000,
        "welcome_credits": 0,
        "core_access": "included_high_fair_use",
        "play_product_id": "velia_pro_monthly",
        "play_product_type": "subs",
    },
}

TOPUPS: Tuple[Dict[str, Any], ...] = (
    {"credits": 100, "store_price_usd": 2.49, "play_product_id": "velia_credits_100", "play_product_type": "inapp"},
    {"credits": 250, "store_price_usd": 4.99, "play_product_id": "velia_credits_250", "play_product_type": "inapp"},
    {"credits": 800, "store_price_usd": 12.99, "play_product_id": "velia_credits_800", "play_product_type": "inapp"},
    {"credits": 2000, "store_price_usd": 27.99, "play_product_id": "velia_credits_2000", "play_product_type": "inapp"},
    {"credits": 5000, "store_price_usd": 59.99, "play_product_id": "velia_credits_5000", "play_product_type": "inapp"},
    {"credits": 10000, "store_price_usd": 109.99, "play_product_id": "velia_credits_10000", "play_product_type": "inapp"},
)

_PRODUCT_MAP: Dict[str, Dict[str, Any]] = {
    str(plan["play_product_id"]): {"kind": "subscription", "plan_code": code, **plan}
    for code, plan in PLANS.items()
    if plan.get("play_product_id")
}
for _pack in TOPUPS:
    _PRODUCT_MAP[str(_pack["play_product_id"])] = {"kind": "topup", **_pack}

_SCHEMA_LOCK_ID = 1_450_731_739
_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: Dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _service_account_config() -> Optional[Dict[str, str]]:
    raw = str(os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON") or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    client_email = str(data.get("client_email") or "").strip()
    private_key = str(data.get("private_key") or "").strip()
    token_uri = str(data.get("token_uri") or "https://oauth2.googleapis.com/token").strip()
    if not client_email or "BEGIN PRIVATE KEY" not in private_key or not token_uri.startswith("https://"):
        return None
    return {
        "client_email": client_email,
        "private_key": private_key,
        "token_uri": token_uri,
    }


def google_play_billing_ready() -> bool:
    return _env_bool("VELIA_GOOGLE_PLAY_BILLING_ENABLED", False) and _service_account_config() is not None


def mobile_catalog() -> Dict[str, Any]:
    return {
        "ok": True,
        "catalog_version": CATALOG_VERSION,
        "billing_enabled": google_play_billing_ready(),
        "billing_channel": "google_play",
        "plans": [dict(PLANS[key]) for key in ("free", "plus", "pro")],
        "topups": [dict(item) for item in TOPUPS],
    }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _google_access_token() -> str:
    config = _service_account_config()
    if config is None:
        raise RuntimeError("google_play_service_account_unavailable")

    now = time.time()
    cached = str(_TOKEN_CACHE.get("access_token") or "")
    if cached and float(_TOKEN_CACHE.get("expires_at") or 0.0) > now + 60:
        return cached

    with _TOKEN_LOCK:
        now = time.time()
        cached = str(_TOKEN_CACHE.get("access_token") or "")
        if cached and float(_TOKEN_CACHE.get("expires_at") or 0.0) > now + 60:
            return cached

        issued_at = int(now)
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": config["client_email"],
            "scope": ANDROID_PUBLISHER_SCOPE,
            "aud": config["token_uri"],
            "iat": issued_at,
            "exp": issued_at + 3600,
        }
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
            + "."
            + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        )
        private_key = serialization.load_pem_private_key(
            config["private_key"].encode("utf-8"),
            password=None,
        )
        signature = private_key.sign(
            signing_input.encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        assertion = signing_input + "." + _b64url(signature)
        response = requests.post(
            config["token_uri"],
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(f"google_oauth_http_{response.status_code}")
        data = response.json()
        access_token = str(data.get("access_token") or "").strip()
        expires_in = int(data.get("expires_in") or 3600)
        if not access_token:
            raise RuntimeError("google_oauth_empty_token")
        _TOKEN_CACHE["access_token"] = access_token
        _TOKEN_CACHE["expires_at"] = now + max(300, min(expires_in, 3600))
        return access_token


def _google_request(method: str, path: str, *, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    token = _google_access_token()
    response = requests.request(
        method,
        GOOGLE_API_BASE + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=body,
        timeout=20,
    )
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"google_play_http_{response.status_code}")
    if not response.content:
        return {}
    data = response.json()
    return data if isinstance(data, dict) else {}


def _token_hash(purchase_token: str) -> str:
    return hashlib.sha256(str(purchase_token or "").encode("utf-8")).hexdigest()


def obfuscated_account_id(user_id: int) -> str:
    # This is a correlation identifier, not a secret. Purchase-token uniqueness
    # and server-side Google verification remain the authorization boundary.
    return hashlib.sha256(f"velia:{int(user_id)}".encode("utf-8")).hexdigest()


def ensure_commercial_runtime_tables() -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_user_commercial_state (
                user_id BIGINT PRIMARY KEY,
                plan_code TEXT NOT NULL DEFAULT 'free',
                source TEXT NOT NULL DEFAULT 'free',
                subscription_until TIMESTAMPTZ,
                last_synced_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_fulfillment_events (
                id BIGSERIAL PRIMARY KEY,
                event_key TEXT NOT NULL UNIQUE,
                channel TEXT NOT NULL,
                purchase_token_hash TEXT,
                user_id BIGINT NOT NULL,
                product_id TEXT NOT NULL,
                product_kind TEXT NOT NULL,
                plan_code TEXT,
                credits_granted BIGINT NOT NULL DEFAULT 0,
                order_id TEXT,
                purchase_state TEXT,
                entitlement_until TIMESTAMPTZ,
                metadata_json TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_commercial_fulfillment_user_created "
            "ON velia_commercial_fulfillment_events(user_id, created_at DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_commercial_fulfillment_token "
            "ON velia_commercial_fulfillment_events(purchase_token_hash)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def ensure_commercial_runtime_tables_serialized() -> None:
    lock_conn = get_connection()
    cur = lock_conn.cursor()
    locked = False
    try:
        cur.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_LOCK_ID,))
        locked = True
        ensure_commercial_runtime_tables()
    finally:
        if locked:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_LOCK_ID,))
            except Exception:
                pass
        cur.close()
        lock_conn.close()


def _parse_google_time(value: Any) -> Optional[datetime]:
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


def _active_plan_from_state(plan_code: str, subscription_until: Any) -> str:
    normalized = str(plan_code or "free").lower()
    expiry = _parse_google_time(subscription_until)
    if normalized in {"plus", "pro"} and expiry and expiry > datetime.now(timezone.utc):
        return normalized
    return "free"


def commercial_state_for_user(user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT u.user_id,u.token_balance,u.subscription_until,
                   s.plan_code,s.source,s.subscription_until AS commercial_subscription_until
            FROM users u
            LEFT JOIN velia_user_commercial_state s ON s.user_id=u.user_id
            WHERE u.user_id=%s
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "user_not_found"}
        expiry = row.get("commercial_subscription_until") or row.get("subscription_until")
        plan_code = _active_plan_from_state(row.get("plan_code") or "free", expiry)
        return {
            "ok": True,
            "account": {
                "plan_code": plan_code,
                "subscription_until": expiry,
                "credits": int(row.get("token_balance") or 0),
                "source": row.get("source") if plan_code != "free" else "free",
            },
        }
    finally:
        cur.close()
        conn.close()


def _claim_purchase_token_for_user(cur: Any, *, token_hash: str, user_id: int) -> bool:
    cur.execute(
        """
        SELECT user_id FROM velia_commercial_fulfillment_events
        WHERE purchase_token_hash=%s
        ORDER BY id ASC
        LIMIT 1
        """,
        (token_hash,),
    )
    row = cur.fetchone()
    if not row:
        return True
    existing_user_id = row[0] if not isinstance(row, dict) else row.get("user_id")
    return int(existing_user_id or 0) == int(user_id)


def _grant_event(
    *,
    event_key: str,
    token_hash: str,
    user_id: int,
    product_id: str,
    product_kind: str,
    plan_code: Optional[str],
    credits: int,
    order_id: Optional[str],
    purchase_state: str,
    entitlement_until: Optional[datetime],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT user_id,token_balance,subscription_until FROM users WHERE user_id=%s FOR UPDATE", (int(user_id),))
        user = cur.fetchone()
        if not user:
            conn.rollback()
            return {"ok": False, "error": "user_not_found"}
        if not _claim_purchase_token_for_user(cur, token_hash=token_hash, user_id=user_id):
            conn.rollback()
            return {"ok": False, "error": "purchase_claimed_by_another_user"}

        cur.execute(
            """
            INSERT INTO velia_commercial_fulfillment_events(
                event_key,channel,purchase_token_hash,user_id,product_id,product_kind,
                plan_code,credits_granted,order_id,purchase_state,entitlement_until,metadata_json
            ) VALUES (%s,'google_play',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
            """,
            (
                event_key,
                token_hash,
                int(user_id),
                product_id,
                product_kind,
                plan_code,
                max(0, int(credits)),
                str(order_id or "")[:160] or None,
                str(purchase_state or "")[:80],
                entitlement_until,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        inserted = cur.fetchone() is not None

        if inserted and credits > 0:
            cur.execute(
                "UPDATE users SET token_balance=COALESCE(token_balance,0)+%s,updated_at=%s WHERE user_id=%s",
                (int(credits), datetime.utcnow().isoformat(), int(user_id)),
            )

        if plan_code in {"plus", "pro"} and entitlement_until is not None:
            entitlement_iso = entitlement_until.astimezone(timezone.utc).isoformat()
            existing_expiry = _parse_google_time(user.get("subscription_until"))
            if not existing_expiry or entitlement_until > existing_expiry:
                cur.execute(
                    "UPDATE users SET subscription_until=%s,updated_at=%s WHERE user_id=%s",
                    (entitlement_iso, datetime.utcnow().isoformat(), int(user_id)),
                )

            cur.execute(
                "SELECT plan_code,subscription_until FROM velia_user_commercial_state WHERE user_id=%s FOR UPDATE",
                (int(user_id),),
            )
            current_state = cur.fetchone()
            current_plan = str((current_state or {}).get("plan_code") or "free") if isinstance(current_state, dict) else "free"
            current_until = _parse_google_time((current_state or {}).get("subscription_until")) if isinstance(current_state, dict) else None
            rank = {"free": 0, "plus": 1, "pro": 2}
            selected_plan = plan_code
            selected_until = entitlement_until
            if current_until and current_until > datetime.now(timezone.utc) and rank.get(current_plan, 0) > rank.get(plan_code, 0):
                selected_plan = current_plan
                selected_until = current_until
            cur.execute(
                """
                INSERT INTO velia_user_commercial_state(user_id,plan_code,source,subscription_until,last_synced_at,updated_at)
                VALUES (%s,%s,'google_play',%s,NOW(),NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    plan_code=EXCLUDED.plan_code,
                    source='google_play',
                    subscription_until=EXCLUDED.subscription_until,
                    last_synced_at=NOW(),
                    updated_at=NOW()
                """,
                (int(user_id), selected_plan, selected_until),
            )

        conn.commit()
        return {"ok": True, "granted": inserted, "credits_granted": int(credits) if inserted else 0}
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        cur.close()
        conn.close()


def _verify_topup(user_id: int, product: Dict[str, Any], purchase_token: str) -> Dict[str, Any]:
    package = str(os.getenv("VELIA_GOOGLE_PLAY_PACKAGE_NAME") or DEFAULT_PACKAGE_NAME).strip()
    product_id = str(product["play_product_id"])
    token_q = quote(purchase_token, safe="")
    data = _google_request(
        "GET",
        f"/applications/{quote(package, safe='')}/purchases/products/{quote(product_id, safe='')}/tokens/{token_q}",
    )
    if int(data.get("purchaseState", -1)) != 0:
        return {"ok": False, "error": "purchase_not_completed", "purchase_state": data.get("purchaseState")}
    if str(data.get("productId") or product_id) != product_id:
        return {"ok": False, "error": "product_mismatch"}
    external_id = str(data.get("obfuscatedExternalAccountId") or "").strip()
    expected_external_id = obfuscated_account_id(user_id)
    if external_id and external_id != expected_external_id:
        return {"ok": False, "error": "account_mismatch"}

    token_hash = _token_hash(purchase_token)
    grant = _grant_event(
        event_key=f"google_play:inapp:{token_hash}",
        token_hash=token_hash,
        user_id=user_id,
        product_id=product_id,
        product_kind="topup",
        plan_code=None,
        credits=int(product["credits"]),
        order_id=str(data.get("orderId") or "") or None,
        purchase_state="PURCHASED",
        entitlement_until=None,
        metadata={"region_code": data.get("regionCode")},
    )
    if not grant.get("ok"):
        return grant

    consume_warning = None
    try:
        _google_request(
            "POST",
            f"/applications/{quote(package, safe='')}/purchases/products/{quote(product_id, safe='')}/tokens/{token_q}:consume",
            body={},
        )
    except Exception as exc:
        consume_warning = str(exc)

    state = commercial_state_for_user(user_id)
    return {
        "ok": True,
        "granted": bool(grant.get("granted")),
        "credits_granted": int(grant.get("credits_granted") or 0),
        "consume_pending": consume_warning is not None,
        "account": state.get("account") if state.get("ok") else None,
    }


def _subscription_line(data: Dict[str, Any], product_id: str) -> Optional[Dict[str, Any]]:
    for line in data.get("lineItems") or []:
        if isinstance(line, dict) and str(line.get("productId") or "") == product_id:
            return line
    return None


def _verify_subscription(user_id: int, product: Dict[str, Any], purchase_token: str) -> Dict[str, Any]:
    package = str(os.getenv("VELIA_GOOGLE_PLAY_PACKAGE_NAME") or DEFAULT_PACKAGE_NAME).strip()
    product_id = str(product["play_product_id"])
    token_q = quote(purchase_token, safe="")
    data = _google_request(
        "GET",
        f"/applications/{quote(package, safe='')}/purchases/subscriptionsv2/tokens/{token_q}",
    )
    state = str(data.get("subscriptionState") or "")
    line = _subscription_line(data, product_id)
    if line is None:
        return {"ok": False, "error": "product_mismatch"}
    expiry = _parse_google_time(line.get("expiryTime"))
    if expiry is None or expiry <= datetime.now(timezone.utc):
        return {"ok": False, "error": "subscription_expired", "subscription_state": state}
    allowed_states = {
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        "SUBSCRIPTION_STATE_CANCELED",
    }
    if state not in allowed_states:
        return {"ok": False, "error": "subscription_not_entitled", "subscription_state": state}

    external = data.get("externalAccountIdentifiers") or {}
    external_id = str(external.get("obfuscatedExternalAccountId") or "").strip() if isinstance(external, dict) else ""
    expected_external_id = obfuscated_account_id(user_id)
    if external_id and external_id != expected_external_id:
        return {"ok": False, "error": "account_mismatch"}

    token_hash = _token_hash(purchase_token)
    expiry_key = expiry.astimezone(timezone.utc).isoformat()
    order_id = str(line.get("latestSuccessfulOrderId") or data.get("latestOrderId") or "") or None
    grant = _grant_event(
        event_key=f"google_play:sub:{token_hash}:{expiry_key}",
        token_hash=token_hash,
        user_id=user_id,
        product_id=product_id,
        product_kind="subscription",
        plan_code=str(product["plan_code"]),
        credits=int(product["monthly_credits"]),
        order_id=order_id,
        purchase_state=state,
        entitlement_until=expiry,
        metadata={"region_code": data.get("regionCode")},
    )
    if not grant.get("ok"):
        return grant

    acknowledge_warning = None
    if str(data.get("acknowledgementState") or "") == "ACKNOWLEDGEMENT_STATE_PENDING":
        try:
            _google_request(
                "POST",
                f"/applications/{quote(package, safe='')}/purchases/subscriptions/{quote(product_id, safe='')}/tokens/{token_q}:acknowledge",
                body={},
            )
        except Exception as exc:
            acknowledge_warning = str(exc)

    account_state = commercial_state_for_user(user_id)
    return {
        "ok": True,
        "granted": bool(grant.get("granted")),
        "credits_granted": int(grant.get("credits_granted") or 0),
        "acknowledge_pending": acknowledge_warning is not None,
        "subscription_state": state,
        "account": account_state.get("account") if account_state.get("ok") else None,
    }


def verify_google_play_purchase(user_id: int, product_id: str, purchase_token: str) -> Dict[str, Any]:
    if not google_play_billing_ready():
        return {"ok": False, "error": "google_play_billing_not_ready"}
    if int(user_id or 0) <= 0:
        return {"ok": False, "error": "invalid_user"}
    normalized_product = str(product_id or "").strip()
    token = str(purchase_token or "").strip()
    if normalized_product not in _PRODUCT_MAP:
        return {"ok": False, "error": "unknown_product"}
    if not token or len(token) > 4096:
        return {"ok": False, "error": "invalid_purchase_token"}
    product = _PRODUCT_MAP[normalized_product]
    if product["kind"] == "topup":
        return _verify_topup(int(user_id), product, token)
    return _verify_subscription(int(user_id), product, token)
