from pathlib import Path

from services import developer_api_commercial_launch_v2_service as service
from services import developer_api_commercial_final_service as final_service
import developer_api_commercial_routes_v2 as routes_v2
import developer_api_commercial_admin_routes_v2 as admin_v2


ROOT = Path(__file__).resolve().parents[1]
V2_SOURCE = (ROOT / "services/developer_api_commercial_launch_v2_service.py").read_text(encoding="utf-8")
FINAL_SOURCE = (ROOT / "services/developer_api_commercial_final_service.py").read_text(encoding="utf-8")
RUN_WEB = (ROOT / "run_web_process.py").read_text(encoding="utf-8")


def test_commercial_estimates_query_actual_product_unit_price():
    snapshot_body = V2_SOURCE.split("def spend_snapshot", 1)[1].split("# Patch globals", 1)[0]
    assert "SELECT product_code, unit_price" in snapshot_body
    assert 'item.get("unit_price")' in snapshot_body
    assert "price_credits" not in snapshot_body
    assert "base.spend_snapshot = spend_snapshot" in V2_SOURCE


def test_partial_patch_distinguishes_omitted_from_explicit_null():
    payload = {"max_daily_credit_spend": 25, "max_monthly_credit_spend": None}
    assert routes_v2._control(payload, "max_daily_credit_spend") == 25
    assert routes_v2._control(payload, "max_monthly_credit_spend") is None
    assert routes_v2._control(payload, "low_balance_threshold") is service.UNSET
    assert service._control_value(service.UNSET, 500, "monthly_spend_limit_credits") == 500
    assert service._control_value(None, 500, "monthly_spend_limit_credits") is None
    assert "developer_api_commercial_routes_v2" in RUN_WEB


def test_manual_provider_without_address_hides_legacy_sentinel():
    invoice = {
        "invoice_id": "inv_manual_review",
        "client_id": 1,
        "package_code": "starter",
        "package_name": "Starter",
        "credits": 100,
        "amount": "1",
        "currency": "TON",
        "price_nano": 1_000_000_000,
        "status": "awaiting_payment",
        "payment_provider": "manual",
        "payment_reference": "api_pay_manual_review",
        "payment_address": None,
        "treasury_address": "manual",
        "checkout_url": None,
    }
    public = service._invoice_public(invoice)
    assert public["payment_address"] is None
    assert public["checkout_url"] is None
    assert public["payment_reference"] == "api_pay_manual_review"


def test_explicit_manual_address_remains_visible():
    invoice = {
        "invoice_id": "inv_manual_address",
        "client_id": 1,
        "package_code": "starter",
        "package_name": "Starter",
        "credits": 100,
        "amount": "1",
        "currency": "TON",
        "price_nano": 1_000_000_000,
        "status": "awaiting_payment",
        "payment_provider": "manual",
        "payment_reference": "api_pay_manual_address",
        "payment_address": "EQ-configured-address",
        "treasury_address": "EQ-configured-address",
        "checkout_url": None,
    }
    public = service._invoice_public(invoice)
    assert public["payment_address"] == "EQ-configured-address"


def test_zero_spend_caps_are_enforced_and_only_null_disables():
    assert "IF v_daily IS NOT NULL THEN" in FINAL_SOURCE
    assert "IF v_monthly IS NOT NULL THEN" in FINAL_SOURCE
    trigger_body = FINAL_SOURCE.split("CREATE OR REPLACE FUNCTION enforce_api_credit_spend_limits", 1)[1]
    trigger_body = trigger_body.split('"""', 1)[0]
    assert "COALESCE(v_daily, 0) > 0" not in trigger_body
    assert "COALESCE(v_monthly, 0) > 0" not in trigger_body
    assert service._control_value(0, 500, "daily_spend_limit_credits") == 0


def test_admin_commercial_forms_preserve_query_key_and_hidden_field():
    html = (
        "<form method='post' action='/admin/api/credit-invoices/inv_1/mark-paid'>"
        "<button>Mark paid</button></form>"
    )
    secured = admin_v2._inject_admin_key(html, "secret value")
    assert "action='/admin/api/credit-invoices/inv_1/mark-paid?key=secret+value'" in secured
    assert "name='key' value='secret value'" in secured
    assert "developer_api_commercial_admin_routes_v2" in RUN_WEB


def test_health_uses_migrated_invoice_and_live_request_states():
    health_body = FINAL_SOURCE.split("def get_commercial_runtime_health", 1)[1]
    assert "'awaiting_payment','payment_detected','paid','crediting'" in health_body
    assert "status='credited' AND credited_at" in health_body
    assert "status='live_requested'" in health_body
    assert "status='pending' AS pending" not in health_body


def test_worker_warning_only_applies_to_automatic_ton_mode(monkeypatch):
    monkeypatch.setenv("API_COMMERCIAL_LAUNCH_ENABLED", "true")
    monkeypatch.setenv("API_CREDIT_PURCHASES_ENABLED", "true")
    monkeypatch.setenv("API_CREDIT_INVOICE_PROVIDER", "manual")
    assert final_service.automatic_payment_worker_required() is False
    monkeypatch.setenv("API_CREDIT_INVOICE_PROVIDER", "ton_treasury")
    assert final_service.automatic_payment_worker_required() is True
    monkeypatch.setenv("API_CREDIT_PURCHASES_ENABLED", "false")
    assert final_service.automatic_payment_worker_required() is False
    health_body = FINAL_SOURCE.split("def get_commercial_runtime_health", 1)[1]
    assert "if worker_required and fresh == 0" in health_body
    assert "if worker_required and not legacy.incoming_enabled()" in health_body
