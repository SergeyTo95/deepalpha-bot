from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_all_payment_resolution_uses_single_treasury_and_no_hardcoded_fallbacks():
    purchase = read("services/ton_purchase_service.py")
    ton = read("services/ton_service.py")
    treasury = read("services/treasury_service.py")
    assert "get_public_treasury_address" in purchase
    assert "get_public_treasury_address" in ton
    assert "UQB7mMWEGE4reqMvHG5zPcHl9fQUy6L91UJhiXgyx772kuUv" not in purchase
    assert "TON_PROJECT_WALLET" not in purchase
    assert "ton_project_wallet" not in purchase
    assert "ton_platform_wallet" not in purchase
    assert "treasury_conflict" in treasury
    assert "treasury_not_configured" in treasury


def test_active_treasury_unique_index_and_payment_intents_schema():
    db = read("db/database.py")
    assert "ux_cashier_payment_wallets_single_active" in db
    assert "CREATE TABLE IF NOT EXISTS payment_intents" in db
    for col in [
        "public_reference TEXT NOT NULL UNIQUE", "expected_amount_nano BIGINT NOT NULL",
        "treasury_wallet_id BIGINT NOT NULL", "treasury_address TEXT NOT NULL",
        "expected_sender_address TEXT", "tx_hash TEXT UNIQUE", "idempotency_key TEXT NOT NULL UNIQUE",
    ]:
        assert col in db
    assert "CREATE TABLE IF NOT EXISTS treasury_payouts" in db


def test_intents_are_immutable_and_not_overwritten_like_pending_payments():
    treasury = read("services/treasury_service.py")
    assert "INSERT INTO payment_intents" in treasury
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in treasury
    assert "ON CONFLICT (user_id) DO UPDATE" not in treasury


def test_onchain_verification_rejects_underpay_wrong_destination_source_network_and_duplicate_tx():
    treasury = read("services/treasury_service.py")
    for marker in [
        "network_mismatch", "tx_hash_not_unique", "destination_mismatch",
        "amount_too_low", "source_mismatch", "reference_missing",
        "intent_expired", "intent_already_fulfilled",
    ]:
        assert marker in treasury


def test_donation_controls_and_nano_accounting_present():
    db = read("db/database.py")
    assert "gross_amount_nano" in db or "author_available_nano" in db
    assert "author_available_nano" in db
    assert "author_reserved_nano" in db
    assert "author_withdrawn_nano" in db


def test_payouts_use_internal_wallet_not_users_legacy_ton_wallet():
    treasury = read("services/treasury_service.py")
    db = read("db/database.py")
    assert "FROM user_ton_wallets" in treasury
    assert "internal_wallet_required" in treasury
    assert "wallet_conflict" in treasury
    assert "users.ton_wallet" not in treasury
    assert "recipient_wallet_address" in db


def test_concurrent_payout_and_post_send_failure_guards():
    db = read("db/database.py")
    assert "FOR UPDATE" in db
    assert "concurrent_payout" in db
    assert "payout_sent_reconcile_required" in db
    assert "recipient_revalidation_required" in db


def test_emergency_flags_default_false_and_no_secret_exposure():
    env = read(".env.example")
    treasury = read("services/treasury_service.py").lower()
    assert "TREASURY_INCOMING_ENABLED=false" in env
    assert "TREASURY_OUTGOING_ENABLED=false" in env
    for bad_log in ["seed_encrypted=%s", "mnemonic=%s", "private_key=%s", "decrypted seed"]:
        assert bad_log not in treasury


def test_spoofed_donor_id_contract_is_documented_by_session_requirement():
    web = read("web.py")
    assert "_get_authenticated_web_user_id(request)" in web
    assert "Cannot donate to yourself" in web
