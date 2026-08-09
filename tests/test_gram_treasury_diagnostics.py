from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ADMIN = (ROOT / "bot" / "admin.py").read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    match = re.search(rf"^def {re.escape(name)}\(.*?(?=^def |\Z)", ADMIN, flags=re.M | re.S)
    assert match, f"function {name} not found"
    return match.group(0)


def test_treasury_diagnostics_are_public_metadata_only():
    src = _function_source("_get_cashier_payment_wallet_diagnostics")
    assert "wallet_address" in src
    assert "network" in src
    assert "status" in src
    assert "CASE WHEN seed_encrypted IS NULL THEN 'watch-only' ELSE 'managed'" in src
    assert "SELECT id,wallet_address,network,status" in src
    assert "mnemonic" not in src.lower()
    assert "private_key" not in src.lower()
    assert "decrypt" not in src.lower()


def test_treasury_diagnostics_surface_exists_in_admin_ui():
    assert '🧾 Treasury diagnostics' in ADMIN
    assert 'admin_gram_wallets_treasury_diag' in ADMIN
    assert '🧾 Gram Treasury diagnostics' in ADMIN
    assert 'Public metadata only. Seed/private-key material is never queried or shown.' in ADMIN


def test_treasury_setup_failure_persists_safe_error_class_and_sqlstate():
    src = _function_source("_admin_set_watch_only_treasury_tx")
    assert 'error_class' in src
    assert 'sqlstate' in src
    assert 'exc.__class__.__name__' in src
    assert 'getattr(exc, "pgcode"' in src
    assert 'str(exc)' not in src
    assert 'repr(exc)' not in src
    assert 'Last Treasury setup error:' in ADMIN
    assert 'No secret values are displayed.' in ADMIN


def test_diagnostics_do_not_mutate_treasury_or_money_flags():
    src = _function_source("_get_cashier_payment_wallet_diagnostics")
    forbidden = [
        "INSERT INTO cashier_payment_wallets",
        "UPDATE cashier_payment_wallets",
        "DELETE FROM cashier_payment_wallets",
        "TREASURY_INCOMING_ENABLED=true",
        "TREASURY_OUTGOING_ENABLED=true",
        "VELIA_USDT_CHECKOUT_ENABLED=true",
        "VELIA_PAYMENT_TON_ENABLED=true",
    ]
    for marker in forbidden:
        assert marker not in src
