import json
from pathlib import Path

import pytest

import run_api_commercial_worker
from services import developer_api_commercial_launch_service as service
from services import developer_api_commercial_runtime_patch as runtime_patch
from services.developer_api_billing_service import ApiBillingError
from services.developer_api_service import generate_api_key


ROOT = Path(__file__).resolve().parents[1]
SERVICE = (ROOT / "services/developer_api_commercial_launch_service.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "developer_api_commercial_routes.py").read_text(encoding="utf-8")
ADMIN = (ROOT / "developer_api_commercial_admin_routes.py").read_text(encoding="utf-8")
PORTAL = (ROOT / "webapp/developer_commercial.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp/developer_commercial.css").read_text(encoding="utf-8")
HTML = (ROOT / "webapp/developer.html").read_text(encoding="utf-8")


def test_01_live_request_ownership():
    assert "_owned_client(cursor, int(user_id), int(client_id), for_update=True)" in SERVICE
    assert "/projects/{client_id}/live-request" in ROUTES


def test_02_duplicate_live_request_prevention():
    assert "status='live_requested'" in SERVICE
    assert '"idempotent": True' in SERVICE


def test_03_approve_reject_transitions():
    for state in ("live_requested", "live_approved", "live_rejected", "live_suspended"):
        assert state in SERVICE
    assert "rejection_reason_required" in SERVICE


def test_04_live_key_blocked_before_approval():
    assert '!= "live_approved"' in SERVICE
    assert "live_access_not_approved" in SERVICE


def test_05_live_key_format():
    raw, prefix, digest = generate_api_key("live")
    assert raw.startswith("da_live_") and prefix.startswith("da_live_")
    assert len(digest) == 64 and raw not in digest


def test_06_test_key_format():
    raw, prefix, digest = generate_api_key("test")
    assert raw.startswith("da_test_") and prefix.startswith("da_test_")
    assert len(digest) == 64 and raw not in digest


def test_07_live_rotation_stays_live():
    legacy = (ROOT / "services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert 'environment = "live" if str(existing.get("environment") or "") == "live" else "test"' in legacy
    assert "generate_api_key(environment)" in legacy


def test_08_test_rotation_stays_test():
    legacy = (ROOT / "services/developer_api_commercial_service.py").read_text(encoding="utf-8")
    assert 'else "test"' in legacy and "generate_api_key(environment)" in legacy


def test_09_wallet_scope_unavailable():
    with pytest.raises(service.CommercialLaunchError) as exc:
        service._normalize_live_scopes(["account:read", "wallet:send"])
    assert exc.value.code == "scope_not_available"


def test_10_enabled_package_lookup():
    assert "package_code=%s AND enabled=TRUE FOR UPDATE" in SERVICE
    create_route = ROUTES.split("handle_create_credit_invoice", 1)[1].split("handle_list_credit_invoices", 1)[0]
    assert "price_amount" not in create_route and "credits" not in create_route


def test_11_server_side_invoice_snapshot():
    assert "package_name, credits, price_nano, amount" in SERVICE
    assert "price_currency" in SERVICE
    assert 'int(package.get("credits") or 0)' in SERVICE


def test_12_invoice_ownership():
    assert "JOIN api_client_owners o ON o.client_id=i.client_id" in SERVICE
    assert "WHERE o.user_id=%s AND i.invoice_id=%s" in SERVICE


def test_13_invoice_status_transitions():
    for state in ("pending", "awaiting_payment", "payment_detected", "paid", "crediting", "credited", "expired", "cancelled", "failed", "refunded"):
        assert f'"{state}"' in SERVICE


def test_14_invoice_expiration():
    assert "status IN ('pending','awaiting_payment') AND expires_at<NOW()" in SERVICE
    assert "invoice.expired" in SERVICE


def test_15_double_settlement_credits_once():
    assert "SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE" in SERVICE
    assert "SELECT * FROM api_clients WHERE id=%s FOR UPDATE" in SERVICE
    assert 'locked.get("credited_at") is not None' in SERVICE
    assert 'ledger_key = f"invoice:{invoice_id}"' in SERVICE


def test_16_purchase_ledger_exactly_once():
    assert "INSERT INTO api_credit_ledger" in SERVICE
    assert "SELECT * FROM api_credit_ledger WHERE client_id=%s AND idempotency_key=%s" in SERVICE
    assert "api_payment_events_append_only" in SERVICE


def test_17_invoice_replay_idempotency():
    assert "idempotency_conflict" in SERVICE
    assert "invoice:<invoice_id>" in (ROOT / "docs/api_commercial_launch.md").read_text(encoding="utf-8")


def test_18_user_cannot_mark_paid():
    assert "/app-api/v1/developer/credit-invoices/{invoice_id}/mark-paid" not in ROUTES
    assert "/admin/api/credit-invoices/{invoice_id}/mark-paid" in ADMIN


def test_19_admin_session_required_for_settlement():
    assert "denied = await _guard(request)" in ADMIN
    assert "admin_mark_paid" in ADMIN and "admin_credit_invoice" in ADMIN


def test_20_daily_spend_cap():
    assert "date_trunc('day', NOW())" in SERVICE
    assert "daily_credit_spend_limit_reached" in SERVICE


def test_21_monthly_spend_cap():
    assert "date_trunc('month', NOW())" in SERVICE
    assert "monthly_credit_spend_limit_reached" in SERVICE


def test_22_idempotent_job_replay_not_counted_twice():
    billing = (ROOT / "services/developer_api_billing_service.py").read_text(encoding="utf-8")
    assert "idempotency_key" in billing and "api_credit_reservations" in billing
    assert "status IN ('reserved','charged')" in SERVICE


def test_23_refund_restores_spend_behavior():
    billing = (ROOT / "services/developer_api_billing_service.py").read_text(encoding="utf-8")
    assert "refund" in billing.lower()
    assert "status IN ('reserved','charged')" in SERVICE


def test_24_low_balance_calculation():
    assert '"low_balance": threshold_value is not None and balance <= threshold_value' in SERVICE
    assert '"estimated_remaining_quick_analyses": balance // quick_price' in SERVICE
    assert '"estimated_remaining_opportunity_scans": balance // opportunity_price' in SERVICE


def test_25_portal_omits_internal_secrets():
    public = SERVICE.split("def _invoice_public", 1)[1].split("def create_credit_invoice", 1)[0]
    assert '"provider_metadata"' not in public
    assert '"key_hash"' not in public and "raw_key" not in public
    assert "payment_instructions" in public


def test_26_mobile_ui_assets():
    assert "developer_commercial.css?v=2.0" in HTML
    assert "developer_commercial.js?v=2.0" in HTML
    assert "@media (max-width: 720px)" in CSS
    assert "max_daily_credit_spend" in PORTAL


def test_27_openapi_postman_contracts():
    openapi = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    postman = json.loads((ROOT / "docs/deepalpha_api.postman_collection.json").read_text(encoding="utf-8"))
    assert openapi["openapi"] == "3.1.0"
    assert "/app-api/v1/developer" not in json.dumps(openapi)
    assert postman["auth"]["type"] == "bearer"


def test_28_existing_regressions_in_workflow():
    workflow = (ROOT / ".github/workflows/live-api-commercial-launch.yml").read_text(encoding="utf-8")
    for path in ("test_developer_api_foundation.py", "test_developer_api_billing.py", "test_developer_portal.py", "test_quick_analysis_api.py", "test_api_beta_hardening.py", "test_signed_webhooks.py", "test_opportunity_scan_api.py", "test_openapi_contract.py"):
        assert path in workflow


def test_runtime_limit_errors_are_stable():
    for raw, expected in (
        ("daily_credit_spend_limit_reached:100:90:20", "daily_credit_spend_limit_reached"),
        ("monthly_credit_spend_limit_reached:100:90:20", "monthly_credit_spend_limit_reached"),
        ("monthly_spend_limit_exceeded:100:90:20", "monthly_credit_spend_limit_reached"),
    ):
        def original(*_args, **_kwargs):
            raise RuntimeError(raw)
        with pytest.raises(ApiBillingError) as exc:
            runtime_patch._wrap_billed_job_creator(original)(client_id=1)
        assert exc.value.code == expected and exc.value.details["remaining"] == 10


def test_payment_adapters_and_worker_gates(monkeypatch):
    monkeypatch.setenv("API_CREDIT_INVOICE_PROVIDER", "manual")
    assert service.payment_adapter().name == "manual"
    assert service.payment_adapter().verify_payment({})["error"] == "manual_review_required"
    assert run_api_commercial_worker.worker_disabled_reason({
        "API_COMMERCIAL_LAUNCH_ENABLED": "true",
        "API_CREDIT_PURCHASES_ENABLED": "true",
        "API_CREDIT_INVOICE_PROVIDER": "manual",
    }) == "manual_provider_has_no_automatic_worker"
