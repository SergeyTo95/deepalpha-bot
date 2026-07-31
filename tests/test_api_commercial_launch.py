from pathlib import Path

import pytest

import run_api_commercial_worker
from services import developer_api_commercial_launch_v2_service as service
from services import developer_api_commercial_runtime_patch as runtime_patch
from services.developer_api_billing_service import ApiBillingError


ROOT = Path(__file__).resolve().parents[1]
BASE_SOURCE = (ROOT / "services/developer_api_commercial_launch_service.py").read_text(encoding="utf-8")
V2_SOURCE = (ROOT / "services/developer_api_commercial_launch_v2_service.py").read_text(encoding="utf-8")


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


def test_purchase_gate_stays_closed_when_only_launch_is_enabled(monkeypatch):
    monkeypatch.setenv("API_COMMERCIAL_LAUNCH_ENABLED", "true")
    monkeypatch.delenv("API_CREDIT_PURCHASES_ENABLED", raising=False)
    assert service.credit_purchases_enabled() is False


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
    assert "credited_at TIMESTAMP" in BASE_SOURCE
    assert "SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE" in BASE_SOURCE
    assert "SELECT * FROM api_clients WHERE id=%s FOR UPDATE" in BASE_SOURCE
    assert 'ledger_key = f"invoice:{invoice_id}"' in BASE_SOURCE
    assert "INSERT INTO api_credit_ledger" in BASE_SOURCE
    assert "api_payment_events_append_only" in BASE_SOURCE


def test_ton_validation_is_reused_not_duplicated():
    assert "legacy._transaction_success" in BASE_SOURCE
    assert "normalize_ton_address" in BASE_SOURCE
    assert "tx_hash_not_unique" in BASE_SOURCE
    assert "destination_mismatch" in BASE_SOURCE
    assert "amount_mismatch" in BASE_SOURCE
    assert "network_mismatch" in BASE_SOURCE


def test_daily_and_monthly_spend_trigger_is_database_authoritative():
    assert "CREATE OR REPLACE FUNCTION enforce_api_credit_spend_limits" in BASE_SOURCE
    assert "FROM api_clients WHERE id=NEW.client_id FOR UPDATE" in BASE_SOURCE
    assert "status IN ('reserved','charged')" in BASE_SOURCE
    assert "daily_credit_spend_limit_reached" in BASE_SOURCE
    assert "monthly_credit_spend_limit_reached" in BASE_SOURCE


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


def test_live_lifecycle_key_rotation_and_suspend_revocation_are_safe():
    legacy_source = (ROOT / "services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    for state in ("test_only", "live_requested", "live_approved", "live_rejected", "live_suspended"):
        assert state in BASE_SOURCE
    assert "generate_api_key(\"live\")" in BASE_SOURCE
    assert 'environment = "live" if str(existing.get("environment") or "") == "live" else "test"' in legacy_source
    assert "generate_api_key(environment)" in legacy_source
    assert "environment='live' AND status='active'" in V2_SOURCE
    assert "SET status='revoked', revoked_at=COALESCE(revoked_at,NOW())" in V2_SOURCE
    assert "revoked_live_key_ids" in V2_SOURCE


def test_documented_package_json_schema_is_seeded_after_v2_columns():
    assert "_ORIGINAL_ENSURE()" in V2_SOURCE
    assert "price_amount" in V2_SOURCE
    assert "price_currency" in V2_SOURCE
    assert "ON CONFLICT (package_code) DO NOTHING" in V2_SOURCE
    assert "_seed_configured_packages(cursor)" in V2_SOURCE


def test_old_invoice_fingerprint_remains_replayable():
    assert 'old_fingerprint = legacy._canonical_fingerprint(' in V2_SOURCE
    assert '{"client_id": int(client_id), "package_code": code}' in V2_SOURCE
    assert "stored_provider != requested_provider" in V2_SOURCE
    assert '"idempotent": True' in V2_SOURCE


def test_database_initialization_remains_inside_guarded_startup():
    run_web = (ROOT / "run_web_process.py").read_text(encoding="utf-8")
    runtime_v2 = (ROOT / "services/developer_api_commercial_runtime_v2_patch.py").read_text(encoding="utf-8")
    assert "from services.developer_api_commercial_runtime_v2_patch" in run_web
    guarded = run_web.split("try:", 1)[1]
    assert 'with serialized_developer_api_schema_bootstrap("webapp"):' in guarded
    locked = guarded.split('with serialized_developer_api_schema_bootstrap("webapp"):', 1)[1]
    assert "ensure_developer_api_tables()" in locked
    assert "ensure_commercial_launch_tables()" in locked
    install_body = runtime_v2.split("def install() -> None:", 1)[1]
    assert "ensure_commercial_launch_tables" not in install_body


def test_worker_stays_available_for_existing_ton_settlement():
    assert run_api_commercial_worker.worker_disabled_reason({}) == "API_COMMERCIAL_LAUNCH_ENABLED=false"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
    }) is None
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "API_CREDIT_PURCHASES_ENABLED": "false",
        "API_CREDIT_INVOICE_PROVIDER": "manual",
    }) is None
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "API_COMMERCIAL_WORKER_ENABLED": "false",
    }) == "API_COMMERCIAL_WORKER_ENABLED=false"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "API_CREDIT_PURCHASES_ENABLED": "true",
        "API_CREDIT_INVOICE_PROVIDER": "ton_treasury",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }) is None
    worker = (ROOT / "run_api_commercial_worker.py").read_text(encoding="utf-8")
    assert "already-issued `ton_treasury` invoices must remain settleable" in worker
    assert "developer_api_commercial_final_service" in worker


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
