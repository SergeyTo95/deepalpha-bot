from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import quote

import services.velia_mobile_commercial_service as commercial


def _subscription_event_key(*, token_hash: str, order_id: str | None, expiry: datetime) -> str:
    normalized_order = str(order_id or "").strip()
    if normalized_order:
        # Google documents latestSuccessfulOrderId as the latest successful order
        # associated with this subscription line item. It is therefore the primary
        # paid-period idempotency identity; expiry is only a fallback for edge/test
        # responses that legitimately do not provide an order yet.
        return f"google_play:sub-order:{normalized_order}"
    expiry_key = expiry.astimezone(timezone.utc).isoformat()
    return f"google_play:sub:{token_hash}:{expiry_key}"


def _verify_subscription(user_id: int, product: Dict[str, Any], purchase_token: str) -> Dict[str, Any]:
    package = str(
        commercial.os.getenv("VELIA_GOOGLE_PLAY_PACKAGE_NAME")
        or commercial.DEFAULT_PACKAGE_NAME
    ).strip()
    product_id = str(product["play_product_id"])
    token_q = quote(purchase_token, safe="")
    data = commercial._google_request(
        "GET",
        f"/applications/{quote(package, safe='')}/purchases/subscriptionsv2/tokens/{token_q}",
    )
    state = str(data.get("subscriptionState") or "")
    line = commercial._subscription_line(data, product_id)
    if line is None:
        return {"ok": False, "error": "product_mismatch"}

    expiry = commercial._parse_google_time(line.get("expiryTime"))
    if expiry is None or expiry <= datetime.now(timezone.utc):
        return {
            "ok": False,
            "error": "subscription_expired",
            "subscription_state": state,
        }
    allowed_states = {
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        "SUBSCRIPTION_STATE_CANCELED",
    }
    if state not in allowed_states:
        return {
            "ok": False,
            "error": "subscription_not_entitled",
            "subscription_state": state,
        }

    external = data.get("externalAccountIdentifiers") or {}
    external_id = (
        str(external.get("obfuscatedExternalAccountId") or "").strip()
        if isinstance(external, dict)
        else ""
    )
    expected_external_id = commercial.obfuscated_account_id(user_id)
    if external_id and external_id != expected_external_id:
        return {"ok": False, "error": "account_mismatch"}

    token_hash = commercial._token_hash(purchase_token)
    order_id = str(
        line.get("latestSuccessfulOrderId")
        or data.get("latestOrderId")
        or ""
    ).strip() or None
    grant = commercial._grant_event(
        event_key=_subscription_event_key(
            token_hash=token_hash,
            order_id=order_id,
            expiry=expiry,
        ),
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
            commercial._google_request(
                "POST",
                f"/applications/{quote(package, safe='')}/purchases/subscriptions/{quote(product_id, safe='')}/tokens/{token_q}:acknowledge",
                body={},
            )
        except Exception as exc:
            acknowledge_warning = str(exc)

    account_state = commercial.commercial_state_for_user(user_id)
    return {
        "ok": True,
        "granted": bool(grant.get("granted")),
        "credits_granted": int(grant.get("credits_granted") or 0),
        "acknowledge_pending": acknowledge_warning is not None,
        "subscription_state": state,
        "account": account_state.get("account") if account_state.get("ok") else None,
    }


def verify_google_play_purchase(user_id: int, product_id: str, purchase_token: str) -> Dict[str, Any]:
    if not commercial.google_play_billing_ready():
        return {"ok": False, "error": "google_play_billing_not_ready"}
    if int(user_id or 0) <= 0:
        return {"ok": False, "error": "invalid_user"}
    normalized_product = str(product_id or "").strip()
    token = str(purchase_token or "").strip()
    if normalized_product not in commercial._PRODUCT_MAP:
        return {"ok": False, "error": "unknown_product"}
    if not token or len(token) > 4096:
        return {"ok": False, "error": "invalid_purchase_token"}
    product = commercial._PRODUCT_MAP[normalized_product]
    if product["kind"] == "topup":
        return commercial._verify_topup(int(user_id), product, token)
    return _verify_subscription(int(user_id), product, token)
