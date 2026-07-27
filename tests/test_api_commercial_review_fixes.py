from pathlib import Path

from services import developer_api_commercial_launch_v2_service as service
import developer_api_commercial_routes_v2 as routes_v2


ROOT = Path(__file__).resolve().parents[1]
V2_SOURCE = (ROOT / "services/developer_api_commercial_launch_v2_service.py").read_text(encoding="utf-8")
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
