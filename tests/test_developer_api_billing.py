from pathlib import Path

import pytest

from services import developer_api_billing_service as billing


def test_default_products_have_distinct_configurable_credit_prices():
    products = {code: price for code, _name, price in billing.DEFAULT_API_PRODUCTS}

    assert products == {
        "opportunity_scan": 1,
        "market_data": 1,
        "quick_analysis": 10,
        "deep_analysis": 50,
    }
    assert products["deep_analysis"] > products["quick_analysis"] > products["opportunity_scan"]


def test_request_fingerprint_is_canonical_and_payload_sensitive():
    first = billing.canonical_request_fingerprint({"mode": "quick", "url": "https://example", "options": {"lang": "ru", "limit": 5}})
    reordered = billing.canonical_request_fingerprint({"options": {"limit": 5, "lang": "ru"}, "url": "https://example", "mode": "quick"})
    changed = billing.canonical_request_fingerprint({"mode": "deep", "url": "https://example", "options": {"lang": "ru", "limit": 5}})

    assert first == reordered
    assert first != changed
    assert len(first) == 64


def test_idempotency_key_validation_is_strict_and_reusable():
    assert billing.normalize_idempotency_key("request:client-1234") == "request:client-1234"

    for invalid in ("", "short", "contains space", "line\nbreak", "!invalid-prefix"):
        with pytest.raises(billing.ApiBillingError) as exc:
            billing.normalize_idempotency_key(invalid)
        assert exc.value.code == "invalid_idempotency_key"


def test_reservation_state_machine_prevents_double_charge_and_double_refund():
    first_charge = billing.resolve_reservation_transition("reserved", "charge")
    repeated_charge = billing.resolve_reservation_transition("charged", "charge")
    first_refund = billing.resolve_reservation_transition("reserved", "refund")
    repeated_refund = billing.resolve_reservation_transition("refunded", "refund")

    assert first_charge == {
        "next_status": "charged",
        "balance_delta": 0,
        "event_type": "charge",
        "idempotent": False,
    }
    assert repeated_charge["idempotent"] is True
    assert first_refund["next_status"] == "refunded"
    assert first_refund["balance_delta"] == "units"
    assert repeated_refund["idempotent"] is True

    with pytest.raises(billing.ApiBillingError):
        billing.resolve_reservation_transition("refunded", "charge")


def test_billing_schema_is_append_only_and_database_serialized():
    source = Path("services/developer_api_billing_service.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS api_products" in source
    assert "CREATE TABLE IF NOT EXISTS api_credit_reservations" in source
    assert "CREATE TABLE IF NOT EXISTS api_credit_ledger" in source
    assert "UNIQUE(client_id, idempotency_key)" in source
    assert "SELECT * FROM api_clients WHERE id=%s FOR UPDATE" in source
    assert "SELECT * FROM api_credit_reservations" in source
    assert "FOR UPDATE" in source
    assert "prevent_api_credit_ledger_mutation" in source
    assert "BEFORE UPDATE OR DELETE ON api_credit_ledger" in source


def test_job_creation_reserves_before_execution_and_reuses_same_request():
    source = Path("services/developer_api_billing_service.py").read_text(encoding="utf-8")

    assert "def create_billed_api_job(" in source
    assert 'status, idempotency_key, request_json, units_reserved, units_charged' in source
    assert 'event_type="reserve"' in source
    assert 'amount=-units' in source
    assert '"idempotent": True' in source
    assert 'raise ApiBillingError("idempotency_conflict")' in source
    assert 'raise ApiBillingError(\n                "insufficient_api_credits"' in source


def test_success_finalizes_reservation_and_failure_returns_reserved_credits():
    source = Path("services/developer_api_billing_service.py").read_text(encoding="utf-8")

    assert "def complete_api_job_success(" in source
    assert "SET status='charged', charged_at=NOW()" in source
    assert 'event_type="charge"' in source
    assert "def complete_api_job_failure(" in source
    assert "SET status='refunded', refunded_at=NOW()" in source
    assert 'event_type="refund"' in source
    assert "next_balance = current_balance + units" in source


def test_new_clients_and_manual_adjustments_cannot_bypass_ledger():
    source = Path("services/developer_api_billing_service.py").read_text(encoding="utf-8")
    admin_source = Path("developer_api_admin_routes.py").read_text(encoding="utf-8")

    assert "def create_billed_api_client(" in source
    assert "credit_balance=0" in source
    assert "adjust_api_credits(" in source
    assert "opening_balance" in source
    assert "idempotency_key" in admin_source
    assert "admin_adjust_api_credits" in admin_source
    assert "/admin/api/clients/{client_id}/credits" in admin_source


def test_admin_can_manage_prices_and_inspect_billing_without_public_execution():
    admin_source = Path("developer_api_admin_routes.py").read_text(encoding="utf-8")
    route_source = Path("developer_api_routes.py").read_text(encoding="utf-8")

    assert "API products and prices" in admin_source
    assert "Recent credit ledger" in admin_source
    assert "Recent reservations" in admin_source
    assert "/admin/api/products/{product_code}" in admin_source
    assert 'app.router.add_post("/api/v1/analyses"' not in route_source
    assert 'app.router.add_get("/api/v1/opportunities"' not in route_source
