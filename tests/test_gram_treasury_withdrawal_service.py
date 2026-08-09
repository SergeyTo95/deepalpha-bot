import os
from pathlib import Path

import pytest

from services import gram_treasury_withdrawal_service as svc


def test_money_gates_default_fail_closed(monkeypatch):
    monkeypatch.delenv(svc.WITHDRAW_GATE_ENV, raising=False)
    monkeypatch.delenv(svc.USDT_WITHDRAW_GATE_ENV, raising=False)
    assert svc.treasury_withdrawal_enabled() is False
    assert svc.treasury_usdt_withdrawal_enabled() is False


def test_usdt_gate_requires_parent_gate(monkeypatch):
    monkeypatch.setenv(svc.USDT_WITHDRAW_GATE_ENV, "true")
    monkeypatch.setenv(svc.WITHDRAW_GATE_ENV, "false")
    assert svc.treasury_usdt_withdrawal_enabled() is False
    monkeypatch.setenv(svc.WITHDRAW_GATE_ENV, "true")
    assert svc.treasury_usdt_withdrawal_enabled() is True


def test_exact_amount_parsing_without_float_rounding():
    assert svc.usdt_to_raw("1") == 1_000_000
    assert svc.usdt_to_raw("1.234567") == 1_234_567
    assert svc.gram_to_raw("0.000000001") == 1
    assert svc.gram_to_raw("1.25") == 1_250_000_000
    with pytest.raises(svc.TreasuryWithdrawError, match="too_many_decimals"):
        svc.usdt_to_raw("1.2345678")
    with pytest.raises(svc.TreasuryWithdrawError, match="too_many_decimals"):
        svc.gram_to_raw("0.0000000001")


def test_canonical_usdt_is_fixed_to_official_master():
    assert svc.CANONICAL_USDT_MASTER == "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
    assert svc.USDT_DECIMALS == 6
    assert svc.USDT_TRANSFER_VALUE_NANO == 100_000_000
    assert svc.USDT_FORWARD_NANO == 20_000_000


def test_memo_is_bounded_by_utf8_bytes():
    assert svc._safe_memo("hello") == "hello"
    with pytest.raises(svc.TreasuryWithdrawError, match="memo_too_long"):
        svc._safe_memo("я" * 61)  # 122 UTF-8 bytes


def test_usdt_v3_wallet_query_accepts_only_exact_owner_and_master(monkeypatch):
    from services.ton_wallet_service import _generate_wallet_real

    _seed1, owner, _pub1 = _generate_wallet_real()
    _seed2, jetton_wallet, _pub2 = _generate_wallet_real()
    _seed3, other_owner, _pub3 = _generate_wallet_real()

    class Response:
        ok = True

        def json(self):
            return {
                "jetton_wallets": [
                    {
                        "address": jetton_wallet,
                        "balance": "1234567",
                        "owner": other_owner,
                        "jetton": svc.CANONICAL_USDT_MASTER,
                    },
                    {
                        "address": jetton_wallet,
                        "balance": "1234567",
                        "owner": owner,
                        "jetton": svc.CANONICAL_USDT_MASTER,
                    },
                ]
            }

    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return Response()

    monkeypatch.setenv("TON_NETWORK", "mainnet")
    monkeypatch.setattr(svc.requests, "get", fake_get)
    result = svc._fetch_usdt_wallet_state(owner)
    assert result["ok"] is True
    assert result["balance_raw"] == 1_234_567
    assert result["balance_display"] == "1.234567"
    assert svc._same_address(result["owner_address"], owner)
    assert seen["params"]["jetton_address"] == svc.CANONICAL_USDT_MASTER
    assert seen["params"]["owner_address"] == svc._normalize_address(owner)


def test_fake_master_row_is_not_accepted(monkeypatch):
    from services.ton_wallet_service import _generate_wallet_real

    _seed1, owner, _pub1 = _generate_wallet_real()
    _seed2, jetton_wallet, _pub2 = _generate_wallet_real()
    _seed3, fake_master, _pub3 = _generate_wallet_real()

    class Response:
        ok = True

        def json(self):
            return {
                "jetton_wallets": [
                    {
                        "address": jetton_wallet,
                        "balance": "999999999999",
                        "owner": owner,
                        "jetton": fake_master,
                    }
                ]
            }

    monkeypatch.setenv("TON_NETWORK", "mainnet")
    monkeypatch.setattr(svc.requests, "get", lambda *a, **k: Response())
    result = svc._fetch_usdt_wallet_state(owner)
    assert result["ok"] is True
    assert result["balance_raw"] == 0
    assert result["deployed"] is False


def test_tep74_payload_and_signed_outer_message_build_offline():
    from services.ton_wallet_service import _extract_boc_from_transfer, _generate_wallet_real, _wallet_from_mnemonic

    seed, source, _pub = _generate_wallet_real()
    _seed2, destination, _pub2 = _generate_wallet_real()
    _seed3, jetton_wallet, _pub3 = _generate_wallet_real()
    body = svc._build_usdt_transfer_payload(
        destination_address=destination,
        response_destination=source,
        amount_raw=1_234_567,
        memo="invoice 42",
        query_id=42,
    )
    body_boc = body.to_boc(False)
    assert body_boc and len(body_boc) > 20

    wallet, _public_key, _private_key, regenerated = _wallet_from_mnemonic(seed)
    assert svc._same_address(regenerated, source)
    transfer = svc._build_jetton_wallet_transfer(wallet, jetton_wallet, 0, body)
    signed_boc = _extract_boc_from_transfer(transfer)
    assert signed_boc and len(signed_boc) > 20


def test_source_contains_double_submit_lock_and_no_global_wallet_enable():
    source = Path("services/gram_treasury_withdrawal_service.py").read_text(encoding="utf-8")
    assert "FOR UPDATE" in source
    assert "status='submitting'" in source
    assert "withdrawal_race_blocked" in source
    assert "submission_uncertain" in source
    assert "TON_WALLET_ENABLED=true" not in source
    assert "TON_SEED_EXPORT_ENABLED=true" not in source
    assert "seed_encrypted" in source  # seed is read only from the existing encrypted custodial row
    assert "logger.warning(\"%s\", seed" not in source


def test_preflight_usdt_requires_usdt_gate_and_gram_gas():
    snapshot = {
        "ok": True,
        "runtime_ready": True,
        "gram_balance_raw": svc.USDT_TRANSFER_VALUE_NANO + svc.GRAM_FEE_RESERVE_NANO,
        "usdt_withdraw_enabled": False,
        "usdt": {"ok": True, "wallet_address": "UQfake", "balance_raw": 5_000_000},
    }
    assert svc._validate_preflight(snapshot, "usdt", 1_000_000) == "usdt_withdraw_disabled"
    snapshot["usdt_withdraw_enabled"] = True
    snapshot["gram_balance_raw"] -= 1
    assert svc._validate_preflight(snapshot, "usdt", 1_000_000) == "insufficient_gram_for_usdt_gas"
