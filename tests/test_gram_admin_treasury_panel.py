from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ADMIN = (ROOT / "bot" / "admin.py").read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    match = re.search(rf"^def {re.escape(name)}\(.*?(?=^def |\Z)", ADMIN, flags=re.M | re.S)
    assert match, f"function {name} not found"
    return match.group(0)


def test_gram_admin_panel_exposes_clear_wallet_roles_and_gram_copy():
    for marker in [
        "💼 Payment routing",
        "👤 Admin custodial:",
        "🏦 Treasury / payments:",
        "💸 Referral payout:",
        "🪙 USDT on Gram:",
        "admin_gram_wallets_show:admin",
        "admin_gram_wallets_show:treasury",
        "admin_gram_wallets_show:referral",
        "Public address only. Long-press it to copy.",
    ]:
        assert marker in ADMIN


def test_watch_only_treasury_assignment_never_copies_custodial_seed():
    src = _function_source("_admin_set_watch_only_treasury_tx")
    selected_wallet_query = src.split("FROM user_ton_wallets", 1)[0]
    assert "seed_encrypted" not in selected_wallet_query
    assert "VALUES (%s,NULL,%s,'active'" in src
    assert "seed_encrypted IS NULL" in src
    assert "managed_treasury_row_requires_manual_review" in src
    assert "The seed from user_ton_wallets is intentionally never selected or copied" in src


def test_watch_only_treasury_assignment_is_locked_and_fail_closed():
    src = _function_source("_admin_set_watch_only_treasury_tx")
    for marker in [
        "FOR UPDATE",
        "admin_wallet_conflict",
        "wallet_selection_stale",
        "admin_wallet_not_active",
        "invalid_gram_address",
        "treasury_requires_mainnet",
        "treasury_conflict",
        "treasury_already_configured",
        "treasury_address_conflict",
    ]:
        assert marker in src
    assert "UPDATE cashier_payment_wallets" in src
    assert "WHERE id=%s AND seed_encrypted IS NULL" in src
    assert "DELETE FROM cashier_payment_wallets" not in src


def test_watch_only_treasury_mainnet_gate_uses_runtime_not_legacy_wallet_metadata():
    src = _function_source("_admin_set_watch_only_treasury_tx")
    assert 'runtime_network = str(os.getenv("TON_NETWORK", "testnet") or "testnet").strip().lower()' in src
    assert 'legacy_wallet_network = str(row[3] or "").strip().lower()' in src
    assert 'if runtime_network != "mainnet"' in src
    assert 'canonical_network = "mainnet"' in src
    assert 'GRAM_ADMIN_TREASURY_LEGACY_NETWORK_METADATA' in src
    assert '"test" in network' not in src
    assert 'network not in {"mainnet", "-239"}' not in src
    assert "UPDATE user_ton_wallets SET network" not in src


def test_treasury_promotion_requires_two_step_admin_confirmation():
    assert "admin_gram_wallets_treasury_prepare:" in ADMIN
    assert "admin_gram_wallets_treasury_confirm:" in ADMIN
    assert "✅ Confirm watch-only Treasury" in ADMIN
    assert "The custodial seed is NOT copied." in ADMIN
    assert "Outgoing transfers are NOT enabled by this action." in ADMIN
    assert "Existing different Treasury addresses are never replaced automatically." in ADMIN


def test_admin_wallet_resolution_fails_closed_on_duplicates():
    src = _function_source("_get_admin_gram_wallet_summary")
    assert "ADMIN_ID <= 0" in src
    assert "len(rows) > 1" in src
    assert "admin_wallet_conflict" in src
    assert "admin_wallet_not_active" in src


def test_gram_admin_assignment_does_not_enable_money_runtime_flags():
    src = _function_source("_admin_set_watch_only_treasury_tx")
    forbidden = [
        "TREASURY_INCOMING_ENABLED=true",
        "TREASURY_OUTGOING_ENABLED=true",
        "VELIA_USDT_CHECKOUT_ENABLED=true",
        "VELIA_PAYMENT_TON_ENABLED=true",
        "set_setting(\"TREASURY_INCOMING_ENABLED\"",
        "set_setting(\"TREASURY_OUTGOING_ENABLED\"",
    ]
    for marker in forbidden:
        assert marker not in src


def test_admin_panel_never_prints_seed_material():
    panel = _function_source("admin_gram_wallets_text")
    show = ADMIN[ADMIN.index("async def admin_gram_wallets_show"):ADMIN.index("async def admin_gram_wallets_treasury_prepare")]
    for forbidden in ["seed_encrypted}", "mnemonic", "private_key", "recovery phrase"]:
        assert forbidden not in panel.lower()
        assert forbidden not in show.lower()
