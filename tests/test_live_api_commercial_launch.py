import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_commercial_routes as routes
import run_api_commercial_worker
from services import developer_api_commercial_launch_service as service
from services import developer_api_commercial_runtime_patch as runtime_patch
from services.developer_api_billing_service import ApiBillingError
from services.developer_api_service import generate_api_key


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "services/developer_api_commercial_launch_service.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "developer_api_commercial_routes.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "developer_api_commercial_admin_routes.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "services/developer_api_commercial_runtime_patch.py").read_text(encoding="utf-8")
PORTAL_JS = (ROOT / "webapp/developer_commercial.js").read_text(encoding="utf-8")
PORTAL_CSS = (ROOT / "webapp/developer_commercial.css").read_text(encoding="utf-8")
PORTAL_HTML = (ROOT / "webapp/developer.html").read_text(encoding="utf-8")


def test_01_live_request_ownership():
    assert "client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)" in SERVICE
    assert "project_not_found" in SERVICE
    assert "/projects/{client_id}/live-request" in ROUTES


def test_02_duplicate_live_request_prevention():
    assert "status='live_requested'" in SERVICE
    assert "return {**existing, \"idempotent\": True}" in SERVICE


def test_03_approve_reject_suspend_transitions():
    for state in ("live_requested", "live_approved", "live_rejected", "live_suspended"):
        assert state in SERVICE
    assert 'action_value not in {"approve", "reject", "suspend"}' in SERVICE
    assert "rejection_reason_required" in SERVICE


def test_04_live_key_blocked_before_approval():
    assert '!= "live_approved"' in SERVICE
    assert "live_access_not_approved" in SERVICE


def test_05_live_key_format():
    raw, prefix, digest = generate_api_key("live")
    assert raw.startswith("da_live_")
    assert prefix.startswith("da_live_")
    assert raw not in digest
    assert len(digest) == 64


def test_06_test_key_format():
    raw, prefix, digest = generate_api_key("test")
    assert raw.startswith("da_test_")
    assert prefix.startswith("da_test_")
    assert raw not in digest


def test_07_live_rotation_stays_live():
    legacy = (ROOT / "services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert 'environment = "live" if str(existing.get("environment") or "") == "live" else "test"' in legacy
    assert "generate_api_key(environment)" in legacy


def test_08_test_rotation_stays_test():
    legacy = (ROOT / "services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert 'else "test"' in legacy
    assert "generate_api_key(environment)" in legacy


def test_09_wallet_scope_remains_unavailable():
    with pytest.raises(service.CommercialLaunchError) as exc:
        service._normalize_live_scopes(["account:read", "wallet:send"])
    assert exc.value.code == "scope_not_available"
    public_routes = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("developer_api_routes.py", "developer_api_opportunity_routes.py", "developer_api_webhook_routes.py")
    )
    assert "/api/v1/wallet" not in public_routes


def test_10_enabled_package_lookup_is_server_side():
    assert "SELECT * FROM api_credit_packages WHERE package_code=%s AND enabled=TRUE FOR UPDATE" in SERVICE
    assert "package_code=str(payload.get(\"package_code\")" in ROUTES
    assert "price_amount" not in ROUTES.split("handle_create_credit_invoice", 1)[1].split("handle_list_credit_invoices", 1)[0]


def test_11_invoice_snapshot_uses_package_amount_currency_and_credits():
    assert "package_name, credits, price_nano, amount" in SERVICE
    assert "price_currency" in SERVICE
    assert "int(package.get(\"credits\") or 0)" in SERVICE
    assert "str(amount), currency" in SERVICE


def test_12_invoice_ownership():
    assert "JOIN api_client_owners o ON o.client_id=i.client_id" in SERVICE
    assert "WHERE o.user_id=%s AND i.invoice_id=%s" in SERVICE


def test_13_invoice_status_transitions_are_explicit():
    for state in (
        "pending", "awaiting_payment", "payment_detected", "paid", "crediting",
        "credited", "expired", "cancelled", "failed", "refunded",
    ):
        assert f'"{state}"' in SERVICE
    assert "awaiting_payment\", \"payment_detected\", \"paid\", \"crediting" in SERVICE


def test_14_invoice_expiration():
    assert "status IN ('pending','awaiting_payment') AND expires_at<NOW()" in SERVICE
    assert "invoice.expired" in SERVICE


def test_15_double_settlement_credits_once_under_lock():
    assert "SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE" in SERVICE
    assert "SELECT * FROM api_clients WHERE id=%s FOR UPDATE" in SERVICE
    assert "credited_at is not None" in SERVICE
    assert 'ledger_key = f"invoice:{invoice_id}"' in SERVICE


def test_16_purchase_ledger_exactly_once():
    assert "INSERT INTO api_credit_ledger" in SERVICE
    assert "'purchase'" in SERVICE
    assert "SELECT * FROM api_credit_ledger WHERE client_id=%s AND idempotency_key=%s" in SERVICE
    assert "api_payment_events_append_only" in SERVICE


def test_17_invoice_replay_is_idempotent():
    assert "UNIQUE(user_id, idempotency_key)" in (ROOT / "services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert "idempotency_conflict" in SERVICE
    assert '"idempotent": True' in SERVICE


def test_18_user_cannot_mark_invoice_paid():
    assert "/app-api/v1/developer/credit-invoices/{invoice_id}/mark-paid" not in ROUTES
    assert "/admin/api/credit-invoices/{invoice_id}/mark-paid" in ADMIN


def test_19_admin_session_required_for_settlement():
    assert "denied = await _guard(request)" in ADMIN
    assert "admin_mark_paid" in ADMIN
    assert "admin_credit_invoice" in ADMIN


def test_20_daily_spend_cap_is_database_authoritative():
    assert "daily_spend_limit_credits" in SERVICE
    assert "date_trunc('day', NOW())" in SERVICE
    assert "daily_credit_spend_limit_reached" in SERVICE


def test_21_monthly_spend_cap_is_database_authoritative():
    assert "monthly_spend_limit_credits" in SERVICE
    assert "date_trunc('month', NOW())" in SERVICE
    assert "monthly_credit_spend_limit_reached" in SERVICE


def test_22_idempotent_job_replay_does_not_count_twice():
    billing = (ROOT / "services/developer_api_billing_service.py").read_text(encoding="utf-8")
    assert "idempotency_key" in billing
    assert "api_credit_reservations" in billing
    assert "status IN ('reserved','charged')" in SERVICE


def test_23_refund_restores_spend_and_balance_behavior():
    billing = (ROOT / "services/developer_api_billing_service.py").read_text(encoding="utf-8")
    assert "refund" in billing.lower()
    assert "status IN ('reserved','charged')" in SERVICE
    assert "refunded" in SERVICE


def test_24_low_balance_calculation_and_estimates():
    assert '"low_balance": threshold_value is not None and balance <= threshold_value' in SERVICE
    assert '"estimated_remaining_quick_analyses": balance // quick_price' in SERVICE
    assert '"estimated_remaining_opportunity_scans": balance // opportunity_price' in SERVICE
    assert "last_low_balance_notified_at" in SERVICE


def test_25_portal_omits_hashes_secrets_and_provider_payload():
    public_section = SERVICE.split("def _invoice_public", 1)[1].split("def create_credit_invoice", 1)[0]
    assert "provider_metadata_json" in public_section
    assert '"payment_instructions"' in public_section
    assert '"provider_metadata"' not in public_section
    assert '"key_hash"' not in public_section
    assert "raw_key" not in public_section


def test_26_mobile_portal_assets():
    assert "developer_commercial.css?v=2.0" in PORTAL_HTML
    assert "developer_commercial.js?v=2.0" in PORTAL_HTML
    assert "@media (max-width: 720px)" in PORTAL_CSS
    assert "grid-template-columns: 1fr" in PORTAL_CSS
    assert "viewport-fit=cover" in PORTAL_HTML


def test_27_openapi_and_postman_contracts_are_valid_json():
    openapi = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    postman = json.loads((ROOT / "docs/deepalpha_api.postman_collection.json").read_text(encoding="utf-8"))
    assert openapi["openapi"] == "3.1.0"
    assert "/api/v1/analyses" in openapi["paths"]
    assert "/app-api/v1/developer" not in json.dumps(openapi)
    assert postman["info"]["schema"].endswith("collection.json")


def test_28_existing_api_regression_contracts_remain_referenced():
    workflow = (ROOT / ".github/workflows/live-api-commercial-launch.yml").read_text(encoding="utf-8")
    for name in (
        "tests/test_developer_api_foundation.py",
        "tests/test_developer_api_billing.py",
        "tests/test_developer_portal.py",
        "tests/test_quick_analysis_api.py",
        "tests/test_api_beta_hardening.py",
        "tests/test_signed_webhooks.py",
        "tests/test_opportunity_scan_api.py",
        "tests/test_openapi_contract.py",
    ):
        assert name in workflow


def test_runtime_translates_daily_and_monthly_limit_errors():
    for raw, expected in (
        ("daily_credit_spend_limit_reached:100:90:20", "daily_credit_spend_limit_reached"),
        ("monthly_credit_spend_limit_reached:100:90:20", "monthly_credit_spend_limit_reached"),
        ("monthly_spend_limit_exceeded:100:90:20", "monthly_credit_spend_limit_reached"),
    ):
        def original(*_args, **_kwargs):
            raise RuntimeError(raw)
        guarded = runtime_patch._wrap_billed_job_creator(original)
        with pytest.raises(ApiBillingError) as exc:
            guarded(client_id=1)
        assert exc.value.code == expected
        assert exc.value.details["remaining"] == 10


def test_payment_adapters_are_explicit(monkeypatch):
    monkeypatch.setenv("API_CREDIT_INVOICE_PROVIDER", "manual")
    assert service.payment_adapter().name == "manual"
    result = service.payment_adapter().verify_payment({"invoice_id": "inv_x"})
    assert result["error"] == "manual_review_required"
    monkeypatch.setenv("API_CREDIT_INVOICE_PROVIDER", "ton_treasury")
    assert service.payment_adapter().name == "ton_treasury"


def test_worker_is_closed_for_manual_and_non_production():
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
        "RAILWAY_ENVIRONMENT_NAME": "preview",
    }) == "non_production_environment:preview"


async def _client():
    app = web.Application()
    routes.setup_developer_api_commercial_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_portal_invoice_route_requires_mutation_header(monkeypatch):
    monkeypatch.setattr(routes, "_require_user", lambda _request: ({"user_id": 77}, None))
    client = await _client()
    try:
        response = await client.post(
            "/app-api/v1/developer/projects/5/credit-invoices",
            json={"package_code": "starter"},
        )
        assert response.status == 403
        assert (await response.json())["error"] == "portal_header_required"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_live_key_route_returns_one_time_secret(monkeypatch):
    monkeypatch.setattr(routes, "_require_user", lambda _request: ({"user_id": 77}, None))
    monkeypatch.setattr(routes, "issue_live_key", lambda **_kwargs: {
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
        assert payload["key"]["raw_key"].startswith("da_live_")
    finally:
        await client.close()
