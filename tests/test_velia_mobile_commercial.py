import asyncio
import hashlib
from pathlib import Path

from aiohttp import web

import services.velia_mobile_commercial_routes as commercial_routes
import services.velia_mobile_commercial_service as commercial


class _SchemaCursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))

    def close(self):
        pass


class _SchemaConnection:
    def __init__(self):
        self.cursor_obj = _SchemaCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class _MobileRoutesStub:
    @staticmethod
    def _mobile_api_available():
        return True

    @staticmethod
    def _require_mobile_auth(_request):
        return None


def test_catalog_is_fail_closed_without_server_credentials(monkeypatch):
    monkeypatch.setenv("VELIA_GOOGLE_PLAY_BILLING_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", raising=False)

    catalog = commercial.mobile_catalog()

    assert catalog["ok"] is True
    assert catalog["billing_enabled"] is False
    assert catalog["billing_channel"] == "google_play"
    assert [plan["id"] for plan in catalog["plans"]] == ["free", "plus", "pro"]
    assert catalog["plans"][1]["play_product_id"] == "velia_plus_monthly"
    assert catalog["plans"][2]["play_product_id"] == "velia_pro_monthly"
    assert [item["credits"] for item in catalog["topups"]] == [100, 250, 800, 2000, 5000, 10000]


def test_unknown_product_rejected_before_google_or_database(monkeypatch):
    monkeypatch.setattr(commercial, "google_play_billing_ready", lambda: True)
    monkeypatch.setattr(
        commercial,
        "_google_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Google must not be called")),
    )
    monkeypatch.setattr(
        commercial,
        "get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be called")),
    )

    result = commercial.verify_google_play_purchase(7, "attacker_product", "token")

    assert result == {"ok": False, "error": "unknown_product"}


def test_pending_one_time_purchase_never_grants_credits(monkeypatch):
    monkeypatch.setattr(commercial, "google_play_billing_ready", lambda: True)
    monkeypatch.setattr(commercial, "_google_request", lambda *args, **kwargs: {"purchaseState": 2})
    monkeypatch.setattr(
        commercial,
        "_grant_event",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("pending purchase must not grant")),
    )

    result = commercial.verify_google_play_purchase(9, "velia_credits_100", "pending-token")

    assert result["ok"] is False
    assert result["error"] == "purchase_not_completed"


def test_completed_topup_is_server_verified_then_granted_and_consumed(monkeypatch):
    monkeypatch.setattr(commercial, "google_play_billing_ready", lambda: True)
    calls = []

    def fake_google(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return {
                "purchaseState": 0,
                "productId": "velia_credits_100",
                "orderId": "GPA.test-order",
                "obfuscatedExternalAccountId": commercial.obfuscated_account_id(11),
            }
        return {}

    grants = []

    def fake_grant(**kwargs):
        grants.append(kwargs)
        return {"ok": True, "granted": True, "credits_granted": 100}

    monkeypatch.setattr(commercial, "_google_request", fake_google)
    monkeypatch.setattr(commercial, "_grant_event", fake_grant)
    monkeypatch.setattr(
        commercial,
        "commercial_state_for_user",
        lambda _user_id: {"ok": True, "account": {"plan_code": "free", "credits": 100}},
    )

    result = commercial.verify_google_play_purchase(11, "velia_credits_100", "purchase-token")

    assert result["ok"] is True
    assert result["credits_granted"] == 100
    assert grants[0]["product_id"] == "velia_credits_100"
    assert grants[0]["credits"] == 100
    assert grants[0]["token_hash"] == hashlib.sha256(b"purchase-token").hexdigest()
    assert any(method == "POST" and path.endswith(":consume") for method, path, _body in calls)
    assert all("purchase-token" not in repr(grant) for grant in grants)


def test_account_mismatch_rejects_purchase_before_grant(monkeypatch):
    monkeypatch.setattr(commercial, "google_play_billing_ready", lambda: True)
    monkeypatch.setattr(
        commercial,
        "_google_request",
        lambda *args, **kwargs: {
            "purchaseState": 0,
            "productId": "velia_credits_250",
            "obfuscatedExternalAccountId": commercial.obfuscated_account_id(999),
        },
    )
    monkeypatch.setattr(
        commercial,
        "_grant_event",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("mismatched account must not grant")),
    )

    result = commercial.verify_google_play_purchase(12, "velia_credits_250", "purchase-token")

    assert result == {"ok": False, "error": "account_mismatch"}


def test_subscription_requires_matching_active_line_item(monkeypatch):
    monkeypatch.setattr(commercial, "google_play_billing_ready", lambda: True)
    monkeypatch.setattr(
        commercial,
        "_google_request",
        lambda *args, **kwargs: {
            "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
            "lineItems": [{"productId": "different_subscription", "expiryTime": "2099-01-01T00:00:00Z"}],
        },
    )
    monkeypatch.setattr(
        commercial,
        "_grant_event",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("mismatch must not grant")),
    )

    result = commercial.verify_google_play_purchase(13, "velia_plus_monthly", "subscription-token")

    assert result == {"ok": False, "error": "product_mismatch"}


def test_commercial_schema_is_additive_and_has_idempotency_keys(monkeypatch):
    conn = _SchemaConnection()
    monkeypatch.setattr(commercial, "get_connection", lambda: conn)

    commercial.ensure_commercial_runtime_tables()

    sql = "\n".join(statement for statement, _params in conn.cursor_obj.statements)
    assert "CREATE TABLE IF NOT EXISTS velia_user_commercial_state" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_commercial_fulfillment_events" in sql
    assert "event_key TEXT NOT NULL UNIQUE" in sql
    assert "purchase_token_hash TEXT" in sql
    assert "ALTER TABLE users" not in sql
    assert "DROP TABLE" not in sql
    assert conn.committed is True
    assert conn.rolled_back is False


def test_mobile_commercial_routes_require_existing_mobile_auth():
    app = web.Application()
    commercial_routes.setup_velia_mobile_commercial_routes(app, _MobileRoutesStub)

    route_map = {(route.method, route.resource.canonical): route for route in app.router.routes()}
    assert ("GET", "/mobile-api/v1/economy/catalog") in route_map
    assert ("GET", "/mobile-api/v1/economy/me") in route_map
    assert ("POST", "/mobile-api/v1/economy/google-play/verify") in route_map

    request = object()
    response = asyncio.run(route_map[("GET", "/mobile-api/v1/economy/catalog")].handler(request))
    assert response.status == 401


def test_commercial_source_keeps_raw_purchase_tokens_out_of_persistence():
    source = Path("services/velia_mobile_commercial_service.py").read_text(encoding="utf-8")
    routes = Path("services/velia_mobile_commercial_routes.py").read_text(encoding="utf-8")

    assert "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON" in source
    assert "purchase_token_hash" in source
    assert "purchase_token TEXT" not in source
    assert "purchase_token TEXT" not in routes
    assert "private_key" not in routes
    assert "UPDATE users SET token_balance" in source
    assert "google_play_billing_ready()" in source
