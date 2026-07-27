from pathlib import Path

import pytest

import run_api_commercial_worker
from services import developer_api_commercial_launch_service as service
from services import developer_api_commercial_runtime_patch as runtime_patch
from services.developer_api_billing_service import ApiBillingError


ROOT = Path(__file__).resolve().parents[1]


def test_commercial_gates_fail_closed(monkeypatch):
    for name in (
        "API_COMMERCIAL_LAUNCH_ENABLED",
        "API_CREDIT_PURCHASES_ENABLED",
        "API_LIVE_KEYS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    assert service.legacy.commercial_launch_enabled() is False
    assert service.credit_purchases_enabled() is False
    assert service.legacy.live_keys_globally_enabled() is False


def test_provider_modes_are_explicit(monkeypatch):
    monkeypatch.setenv("API_CREDIT_INVOICE_PROVIDER", "manual")
    assert service.invoice_provider_name() == "manual"
    assert service.payment_adapter().name == "manual"
    monkeypatch.setenv("API_CREDIT_INVOICE_PROVIDER", "ton_treasury")
    assert service.invoice_provider_name() == "ton_treasury"
    assert service.payment_adapter().name == "ton_treasury"


def test_no_fake_client_settlement_route_exists():
    routes = (ROOT / "developer_api_commercial_routes.py").read_text(encoding="utf-8")
    admin = (ROOT / "developer_api_commercial_admin_routes.py").read_text(encoding="utf-8")
    assert "/app-api/v1/developer/credit-invoices/{invoice_id}/mark-paid" not in routes
    assert "/admin/api/credit-invoices/{invoice_id}/mark-paid" in admin
    assert '"paid": true' not in routes.lower()


def test_invoice_schema_and_settlement_are_exactly_once():
    source = (ROOT / "services/developer_api_commercial_launch_service.py").read_text(encoding="utf-8")
    assert "credited_at TIMESTAMP" in source
    assert "SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE" in source
    assert "SELECT * FROM api_clients WHERE id=%s FOR UPDATE" in source
    assert 'ledger_key = f"invoice:{invoice_id}"' in source
    assert "INSERT INTO api_credit_ledger" in source
    assert "api_payment_events_append_only" in source


def test_ton_validation_is_reused_not_duplicated():
    source = (ROOT / "services/developer_api_commercial_launch_service.py").read_text(encoding="utf-8")
    assert "legacy._transaction_success" in source
    assert "normalize_ton_address" in source
    assert "tx_hash_not_unique" in source
    assert "destination_mismatch" in source
    assert "amount_mismatch" in source
    assert "network_mismatch" in source


def test_daily_and_monthly_spend_trigger_is_database_authoritative():
    source = (ROOT / "services/developer_api_commercial_launch_service.py").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION enforce_api_credit_spend_limits" in source
    assert "FROM api_clients WHERE id=NEW.client_id FOR UPDATE" in source
    assert "status IN ('reserved','charged')" in source
    assert "daily_credit_spend_limit_reached" in source
    assert "monthly_credit_spend_limit_reached" in source


def test_runtime_translates_old_and_new_spend_errors():
    cases = {
        "daily_credit_spend_limit_reached:100:90:20": "daily_credit_spend_limit_reached",
        "monthly_credit_spend_limit_reached:100:90:20": "monthly_credit_spend_limit_reached",
        "monthly_spend_limit_exceeded:100:90:20": "monthly_credit_spend_limit_reached",
    }
    for raw, expected in cases.items():
        def original(*_args, **_kwargs):
            raise RuntimeError(raw)
        guarded = runtime_patch._wrap_billed_job_creator(original)
        with pytest.raises(ApiBillingError) as exc:
            guarded(client_id=1)
        assert exc.value.code == expected
        assert exc.value.details == {"limit": 100, "used": 90, "requested": 20, "remaining": 10}


def test_live_lifecycle_and_key_rotation_are_safe():
    source = (ROOT / "services/developer_api_commercial_launch_service.py").read_text(encoding="utf-8")
    legacy = (ROOT / "services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    for state in ("test_only", "live_requested", "live_approved", "live_rejected", "live_suspended"):
        assert state in source
    assert "generate_api_key(\"live\")" in source
    assert 'environment = "live" if str(existing.get("environment") or "") == "live" else "test"' in legacy
    assert "generate_api_key(environment)" in legacy


def test_worker_is_provider_and_production_guarded():
    assert run_api_commercial_worker.worker_disabled_reason({}) == "API_COMMERCIAL_LAUNCH_ENABLED=false"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
    }) == "API_CREDIT_PURCHASES_ENABLED=false"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "API_CREDIT_PURCHASES_ENABLED": "true",
        "API_CREDIT_INVOICE_PROVIDER": "manual",
    }) == "manual_provider_has_no_automatic_worker"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "API_CREDIT_PURCHASES_ENABLED": "true",
        "API_CREDIT_INVOICE_PROVIDER": "ton_treasury",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }) is None


def test_portal_admin_and_mobile_assets_are_mounted():
    run_web = (ROOT / "run_web_process.py").read_text(encoding="utf-8")
    routes = (ROOT / "developer_api_commercial_routes.py").read_text(encoding="utf-8")
    admin = (ROOT / "developer_api_commercial_admin_routes.py").read_text(encoding="utf-8")
    html = (ROOT / "webapp/developer.html").read_text(encoding="utf-8")
    css = (ROOT / "webapp/developer_commercial.css").read_text(encoding="utf-8")
    assert "ensure_commercial_launch_tables" in run_web
    assert "/app-api/v1/developer/projects/{client_id}/live-request" in routes
    assert "/admin/api/credit-invoices/{invoice_id}/credit" in admin
    assert "developer_commercial.css?v=2.0" in html
    assert "developer_commercial.js?v=2.0" in html
    assert "@media (max-width: 720px)" in css


def test_public_wallet_execution_remains_closed():
    public_routes = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("developer_api_routes.py", "developer_api_opportunity_routes.py", "developer_api_webhook_routes.py")
    )
    assert "/api/v1/wallet" not in public_routes
    assert "wallet:send" not in public_routes
