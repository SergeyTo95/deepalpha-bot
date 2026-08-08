from datetime import datetime, timezone

import services.velia_mobile_commercial_period_service as period
import services.velia_mobile_commercial_service as commercial


def test_successful_order_is_primary_subscription_event_identity():
    expiry_a = datetime(2099, 1, 1, tzinfo=timezone.utc)
    expiry_b = datetime(2099, 1, 2, tzinfo=timezone.utc)

    first = period._subscription_event_key(
        token_hash="same-token-hash",
        order_id="GPA.1234-5678-9012-34567",
        expiry=expiry_a,
    )
    second = period._subscription_event_key(
        token_hash="same-token-hash",
        order_id="GPA.1234-5678-9012-34567",
        expiry=expiry_b,
    )

    assert first == "google_play:sub-order:GPA.1234-5678-9012-34567"
    assert second == first


def test_expiry_fallback_is_used_only_without_successful_order():
    expiry = datetime(2099, 1, 1, tzinfo=timezone.utc)
    key = period._subscription_event_key(
        token_hash="token-hash",
        order_id=None,
        expiry=expiry,
    )

    assert key == "google_play:sub:token-hash:2099-01-01T00:00:00+00:00"


def test_subscription_verifier_uses_latest_successful_order_for_grant(monkeypatch):
    monkeypatch.setattr(commercial, "google_play_billing_ready", lambda: True)
    monkeypatch.setattr(
        commercial,
        "_google_request",
        lambda *args, **kwargs: {
            "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
            "acknowledgementState": "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
            "externalAccountIdentifiers": {
                "obfuscatedExternalAccountId": commercial.obfuscated_account_id(17),
            },
            "lineItems": [
                {
                    "productId": "velia_plus_monthly",
                    "expiryTime": "2099-01-01T00:00:00Z",
                    "latestSuccessfulOrderId": "GPA.renewal-001",
                }
            ],
        },
    )
    grants = []

    def fake_grant(**kwargs):
        grants.append(kwargs)
        return {"ok": True, "granted": True, "credits_granted": 1200}

    monkeypatch.setattr(commercial, "_grant_event", fake_grant)
    monkeypatch.setattr(
        commercial,
        "commercial_state_for_user",
        lambda _user_id: {
            "ok": True,
            "account": {"plan_code": "plus", "credits": 1200},
        },
    )

    result = period.verify_google_play_purchase(
        17,
        "velia_plus_monthly",
        "subscription-token",
    )

    assert result["ok"] is True
    assert result["credits_granted"] == 1200
    assert grants[0]["event_key"] == "google_play:sub-order:GPA.renewal-001"
    assert grants[0]["order_id"] == "GPA.renewal-001"
