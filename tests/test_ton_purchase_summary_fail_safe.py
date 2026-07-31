from services import ton_purchase_service as service
from services import treasury_service


def test_missing_treasury_does_not_raise_from_public_wallet_resolver(monkeypatch):
    monkeypatch.setattr(
        treasury_service,
        "get_public_treasury_address",
        lambda: {"ok": False, "error": "treasury_not_configured"},
    )

    assert service.resolve_ton_purchase_project_wallet() == ""


def test_token_purchase_flag_is_fail_closed_without_treasury(monkeypatch):
    monkeypatch.setenv("TON_WALLET_TOKEN_PURCHASE_ENABLED", "true")
    monkeypatch.setattr(
        treasury_service,
        "get_public_treasury_address",
        lambda: {"ok": False, "error": "treasury_not_configured"},
    )

    assert service.is_ton_wallet_token_purchase_enabled() is False


def test_token_purchase_flag_remains_enabled_with_treasury(monkeypatch):
    monkeypatch.setenv("TON_WALLET_TOKEN_PURCHASE_ENABLED", "true")
    monkeypatch.setattr(
        treasury_service,
        "get_public_treasury_address",
        lambda: {"ok": True, "address": "EQC_valid_treasury_address"},
    )

    assert service.resolve_ton_purchase_project_wallet() == "EQC_valid_treasury_address"
    assert service.is_ton_wallet_token_purchase_enabled() is True
