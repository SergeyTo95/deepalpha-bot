import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_commercial_routes as routes
import run_api_commercial_worker
from services import developer_api_commercial_runtime_patch as runtime_patch
from services import developer_api_commercial_service as service
from services.developer_api_billing_service import ApiBillingError


def test_commercial_and_live_gates_fail_closed(monkeypatch):
    for name in (
        "API_COMMERCIAL_LAUNCH_ENABLED",
        "API_LIVE_KEYS_ENABLED",
        "API_LIVE_ACCESS_AUTO_APPROVE_ON_PAYMENT",
    ):
        monkeypatch.delenv(name, raising=False)
    assert service.commercial_launch_enabled() is False
    assert service.live_keys_globally_enabled() is False
    assert service.live_auto_approve_on_payment() is False

    monkeypatch.setenv("API_LIVE_KEYS_ENABLED", "true")
    assert service.live_keys_globally_enabled() is False
    monkeypatch.setenv("API_COMMERCIAL_LAUNCH_ENABLED", "true")
    assert service.live_keys_globally_enabled() is True


def test_no_credit_package_or_price_is_invented(monkeypatch):
    monkeypatch.delenv("API_CREDIT_PACKAGES_JSON", raising=False)
    source = Path("services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert 'os.getenv("API_CREDIT_PACKAGES_JSON", "")' in source
    assert "if not raw:\n        return" in source
    assert "API_CREDIT_PACKAGES_JSON=[]" in env_example
    assert "No package or price is created when this is empty" in env_example
    assert "price_nano BIGINT NOT NULL" in source
    assert "CHECK (price_nano > 0)" in source


def test_invoice_reference_is_isolated_from_telegram_token_payments():
    source = Path("services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    generic_scanner = Path("app.py").read_text(encoding="utf-8")

    assert 'reference = f"api_pay_' in source
    assert 'token.startswith("api_pay_")' in source
    assert "payment_intents" not in source
    assert "fulfill_verified_payment_intent" not in source
    assert 'part.startswith("pay_")' in generic_scanner
    assert 'part.startswith("api_pay_")' not in generic_scanner


def test_invoice_schema_and_settlement_are_exactly_once():
    source = Path("services/developer_api_commercial_service.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS api_credit_invoices" in source
    assert "UNIQUE(user_id, idempotency_key)" in source
    assert "tx_hash TEXT UNIQUE" in source
    assert 'ledger_key = f"invoice:{invoice_id}"' in source
    assert "INSERT INTO api_credit_ledger" in source
    assert "'purchase'" in source
    assert "SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE" in source
    assert "SELECT * FROM api_clients WHERE id=%s FOR UPDATE" in source
    assert "UPDATE api_clients SET credit_balance=%s" in source
    assert "existing_ledger" in source


def _base_invoice(monkeypatch):
    monkeypatch.setenv("TON_NETWORK", "mainnet")
    monkeypatch.setenv("API_CREDIT_CONFIRMATION_SECONDS", "20")
    return {
        "invoice_id": "inv_0123456789abcdef0123456789abcdef",
        "treasury_address": "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c",
        "price_nano": 500_000_000,
        "network": "mainnet",
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=10),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=50),
    }


def _base_tx(invoice, *, amount=None, destination=None, aborted=False, seconds_old=60):
    return {
        "transaction_id": {"hash": "tx_hash_example", "lt": "123"},
        "utime": int((datetime.now(timezone.utc) - timedelta(seconds=seconds_old)).timestamp()),
        "aborted": aborted,
        "in_msg": {
            "source": "EQBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBByl",
            "destination": destination or invoice["treasury_address"],
            "value": invoice["price_nano"] if amount is None else amount,
        },
    }


def test_payment_validation_rejects_failed_wrong_destination_amount_and_network(monkeypatch):
    invoice = _base_invoice(monkeypatch)

    failed = service._settle_invoice_from_tx(invoice, _base_tx(invoice, aborted=True), _base_tx(invoice)["in_msg"])
    assert failed["error"] == "transaction_failed"

    wrong_destination_tx = _base_tx(invoice, destination="EQCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC")
    wrong_destination = service._settle_invoice_from_tx(invoice, wrong_destination_tx, wrong_destination_tx["in_msg"])
    assert wrong_destination["error"] == "destination_mismatch"

    wrong_amount_tx = _base_tx(invoice, amount=invoice["price_nano"] + 1)
    wrong_amount = service._settle_invoice_from_tx(invoice, wrong_amount_tx, wrong_amount_tx["in_msg"])
    assert wrong_amount["error"] == "amount_mismatch"

    wrong_network = dict(invoice, network="testnet")
    network_tx = _base_tx(wrong_network, destination=wrong_network["treasury_address"])
    network = service._settle_invoice_from_tx(wrong_network, network_tx, network_tx["in_msg"])
    assert network["error"] == "network_mismatch"


def test_payment_validation_waits_for_confirmation_and_honors_invoice_time(monkeypatch):
    invoice = _base_invoice(monkeypatch)
    recent_tx = _base_tx(invoice, seconds_old=2)
    recent = service._settle_invoice_from_tx(invoice, recent_tx, recent_tx["in_msg"])
    assert recent == {"ok": False, "error": "awaiting_confirmation", "retryable": True}

    before = dict(invoice, created_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    before_tx = _base_tx(before, seconds_old=60)
    before_result = service._settle_invoice_from_tx(before, before_tx, before_tx["in_msg"])
    assert before_result["error"] == "transaction_before_invoice"

    expired = dict(invoice, expires_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    late_tx = _base_tx(expired, seconds_old=60)
    late = service._settle_invoice_from_tx(expired, late_tx, late_tx["in_msg"])
    assert late["error"] == "invoice_expired_before_payment"


def test_monthly_spend_trigger_is_database_authoritative():
    source = Path("services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION enforce_api_monthly_spend_limit" in source
    assert "FROM api_clients WHERE id=NEW.client_id FOR UPDATE" in source
    assert "status IN ('reserved', 'charged')" in source
    assert "BEFORE INSERT ON api_credit_reservations" in source
    assert "monthly_spend_limit_exceeded" in source


def test_runtime_translates_spend_trigger_into_stable_api_error():
    def original(*_args, **_kwargs):
        raise RuntimeError("monthly_spend_limit_exceeded:100:90:20")

    guarded = runtime_patch._wrap_billed_job_creator(original)
    with pytest.raises(ApiBillingError) as exc:
        guarded(client_id=1)
    assert exc.value.code == "monthly_spend_limit_exceeded"
    assert exc.value.details == {
        "limit": 100,
        "used": 90,
        "requested": 20,
        "remaining": 10,
    }


def test_live_key_lifecycle_is_gated_and_rotation_preserves_environment():
    source = Path("services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert "if not live_keys_globally_enabled()" in source
    assert "live_access_not_approved" in source
    assert "generate_api_key(\"live\")" in source
    assert "VALUES (%s, %s, 'live'" in source
    assert 'environment = "live" if str(existing.get("environment") or "") == "live" else "test"' in source
    assert "generate_api_key(environment)" in source
    assert "commercial_status='live_enabled'" in source


def test_commercial_worker_is_production_guarded():
    assert run_api_commercial_worker.worker_disabled_reason({}) == "API_COMMERCIAL_LAUNCH_ENABLED=false"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "preview",
    }) == "non_production_environment:preview"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/other",
        "BOT_PRODUCTION_BRANCH": "feature/turbo-short-term-btc",
    }) == "non_production_branch:feature/other"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }) is None

    supervisor = Path("supervisord.conf").read_text(encoding="utf-8")
    assert "[program:commercial-worker]" in supervisor
    assert "command=python run_api_commercial_worker.py" in supervisor


async def _client():
    app = web.Application()
    routes.setup_developer_api_commercial_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_invoice_route_requires_portal_mutation_header(monkeypatch):
    monkeypatch.setattr(routes, "_require_user", lambda _request: ({"user_id": 77}, None))
    client = await _client()
    try:
        response = await client.post(
            "/app-api/v1/developer/projects/5/credit-invoices",
            json={"package_code": "starter_100", "idempotency_key": "invoice-1"},
        )
        assert response.status == 403
        payload = await response.json()
        assert payload["error"] == "portal_request_required"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invoice_route_passes_authenticated_owner_and_idempotency(monkeypatch):
    monkeypatch.setattr(routes, "_require_user", lambda _request: ({"user_id": 77}, None))
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return {
            "invoice_id": "inv_example",
            "client_id": kwargs["client_id"],
            "package_code": kwargs["package_code"],
            "idempotent": False,
        }

    monkeypatch.setattr(routes, "create_credit_invoice", create)
    client = await _client()
    try:
        response = await client.post(
            "/app-api/v1/developer/projects/5/credit-invoices",
            headers={"X-DeepAlpha-Portal": "1", "Idempotency-Key": "invoice-1"},
            json={"package_code": "starter_100"},
        )
        payload = await response.json()
        assert response.status == 201
        assert payload["invoice"]["invoice_id"] == "inv_example"
        assert captured == {
            "user_id": 77,
            "client_id": 5,
            "package_code": "starter_100",
            "idempotency_key": "invoice-1",
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_live_key_route_returns_one_time_live_secret(monkeypatch):
    monkeypatch.setattr(routes, "_require_user", lambda _request: ({"user_id": 77}, None))
    monkeypatch.setattr(routes, "issue_user_live_api_key", lambda **_kwargs: {
        "id": 9,
        "environment": "live",
        "key_prefix": "da_live_example",
        "raw_key": "da_live_one_time_secret",
        "scopes": ["account:read"],
    })
    client = await _client()
    try:
        response = await client.post(
            "/app-api/v1/developer/projects/5/live-keys",
            headers={"X-DeepAlpha-Portal": "1"},
            json={"name": "production", "scopes": ["account:read"]},
        )
        payload = await response.json()
        assert response.status == 201
        assert payload["key"]["environment"] == "live"
        assert payload["key"]["raw_key"].startswith("da_live_")
    finally:
        await client.close()


def test_portal_and_admin_commercial_surfaces_are_mounted():
    run_web = Path("run_web_process.py").read_text(encoding="utf-8")
    routes_source = Path("developer_api_commercial_routes.py").read_text(encoding="utf-8")
    admin_source = Path("developer_api_commercial_admin_routes.py").read_text(encoding="utf-8")
    html = Path("webapp/developer.html").read_text(encoding="utf-8")
    javascript = Path("webapp/developer_commercial.js").read_text(encoding="utf-8")

    assert "setup_developer_api_commercial_routes" in run_web
    assert "setup_developer_api_commercial_admin_routes" in run_web
    assert "/app-api/v1/developer/commercial/overview" in routes_source
    assert "JOIN api_client_owners" in Path("services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert "/admin/api/commercial/packages/create" in admin_source
    assert "developer_commercial.css?v=1.0" in html
    assert "developer_commercial.js?v=1.0" in html
    assert "api_pay_" in javascript
    assert "Idempotency-Key" in javascript
    assert "da_live" not in javascript


def test_public_wallet_execution_remains_closed():
    public_routes = "\n".join(
        Path(name).read_text(encoding="utf-8")
        for name in (
            "developer_api_routes.py",
            "developer_api_opportunity_routes.py",
            "developer_api_webhook_routes.py",
        )
    )
    assert "/api/v1/wallet" not in public_routes
    assert "wallet:send" not in public_routes
    commercial_routes = Path("developer_api_commercial_routes.py").read_text(encoding="utf-8")
    assert "/app-api/" in commercial_routes
    assert '"/api/v1/' not in commercial_routes
