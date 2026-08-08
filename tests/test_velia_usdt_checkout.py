import asyncio
from pathlib import Path

from aiohttp import web

from services.payments.chains.solana import SolanaUSDTAdapter
from services.payments.chains.ton import TonUSDTAdapter
from services.payments.chains.tron import TronUSDTAdapter
from services.payments.config import (
    CANONICAL_USDT_IDENTIFIERS,
    NetworkConfig,
    USDT_DECIMALS,
)
import services.velia_usdt_checkout_routes as checkout_routes
import services.velia_usdt_checkout_service as checkout


def _config(network: str, deposit: str = "deposit") -> NetworkConfig:
    canonical = CANONICAL_USDT_IDENTIFIERS[network]
    assert canonical
    return NetworkConfig(
        network=network,
        asset="USDT",
        enabled=True,
        rpc_url="https://rpc.invalid.example",
        asset_identifier=canonical,
        canonical_asset_identifier=canonical,
        deposit_address=deposit,
        asset_decimals=USDT_DECIMALS,
    )


def test_public_usdt_catalog_is_exact_approved_minus_30_copy(monkeypatch):
    monkeypatch.delenv("VELIA_USDT_CHECKOUT_ENABLED", raising=False)
    catalog = checkout.usdt_checkout_catalog()
    assert catalog["discount_percent"] == 30
    assert catalog["discount_label"] == "USDT -30%"
    assert catalog["checkout_enabled"] is False
    by_code = {item["code"]: item for item in catalog["products"]}
    assert by_code["plus"]["store_price_usd"] == "14.99"
    assert by_code["plus"]["usdt_price"] == "10.49"
    assert by_code["pro"]["store_price_usd"] == "29.99"
    assert by_code["pro"]["usdt_price"] == "20.99"
    assert by_code["credits_100"]["usdt_price"] == "1.74"
    assert by_code["credits_10000"]["usdt_price"] == "76.99"


def test_checkout_page_visibly_says_usdt_minus_30_and_hides_address_without_intent():
    html = checkout_routes._checkout_page(
        {
            "checkout_enabled": False,
            "products": [
                {
                    "code": "plus",
                    "name": "VELIA Plus",
                    "store_price_usd": "14.99",
                    "usdt_price": "10.49",
                }
            ],
            "networks": [],
        },
        "csrf-token-for-test",
    )
    assert "USDT −30%" in html
    assert "Store <s>$14.99</s>" in html
    assert "10.49 USDT" in html
    assert "Адрес оплаты не выдаётся" in html
    assert "Google" not in html


def test_quote_source_uses_locked_unique_micro_usdt_fingerprint():
    source = Path("services/velia_usdt_checkout_service.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in source
    assert "secrets.randbelow(999) + 1" in source
    assert "expected_amount_atomic=%s" in source
    assert "amount_fingerprint_atomic" in source
    assert "QUOTE_EXPIRY_MINUTES = 30" in source


def test_tron_adapter_accepts_only_canonical_confirmed_recipient(monkeypatch):
    config = _config("tron", "TDeposit")
    adapter = TronUSDTAdapter(config)
    monkeypatch.setattr(
        adapter,
        "_fetch",
        lambda: {
            "data": [
                {
                    "transaction_id": "tx-tron-1",
                    "from": "TSender",
                    "to": "TDeposit",
                    "value": "10490137",
                    "block_timestamp": 2_000_000_000_000,
                    "token_info": {"address": CANONICAL_USDT_IDENTIFIERS["tron"]},
                },
                {
                    "transaction_id": "fake-token",
                    "from": "TSender",
                    "to": "TDeposit",
                    "value": "10490137",
                    "block_timestamp": 2_000_000_000_000,
                    "token_info": {"address": "TFake"},
                },
            ]
        },
    )
    result = asyncio.run(adapter.poll(None))
    assert len(result.transfers) == 1
    transfer = result.transfers[0]
    assert transfer.tx_hash == "tx-tron-1"
    assert transfer.amount_atomic == 10490137
    assert transfer.recipient_address == "TDeposit"
    assert transfer.finality == "finalized"


def test_solana_adapter_uses_finalized_owner_mint_delta(monkeypatch):
    config = _config("solana", "DepositOwner")
    adapter = SolanaUSDTAdapter(config)

    def fake_rpc(method, params):
        if method == "getSignaturesForAddress":
            return [{"signature": "sig1", "err": None, "slot": 55, "blockTime": 2_000_000_000}]
        if method == "getTransaction":
            return {
                "slot": 55,
                "blockTime": 2_000_000_000,
                "meta": {
                    "err": None,
                    "preTokenBalances": [
                        {
                            "owner": "DepositOwner",
                            "mint": CANONICAL_USDT_IDENTIFIERS["solana"],
                            "uiTokenAmount": {"amount": "1000000"},
                        }
                    ],
                    "postTokenBalances": [
                        {
                            "owner": "DepositOwner",
                            "mint": CANONICAL_USDT_IDENTIFIERS["solana"],
                            "uiTokenAmount": {"amount": "11490137"},
                        }
                    ],
                },
            }
        raise AssertionError(method)

    monkeypatch.setattr(adapter, "_rpc", fake_rpc)
    result = adapter._poll_sync(None)
    assert len(result.transfers) == 1
    assert result.transfers[0].amount_atomic == 10490137
    assert result.transfers[0].finality == "finalized"
    assert result.next_cursor == "sig1"


def test_ton_adapter_filters_master_and_aborted_transfers(monkeypatch):
    config = _config("ton", "EQDeposit")
    adapter = TonUSDTAdapter(config)
    monkeypatch.setattr(
        adapter,
        "_fetch",
        lambda cursor: {
            "jetton_transfers": [
                {
                    "transaction_hash": "ton-tx-1",
                    "transaction_lt": "100",
                    "transaction_now": 2_000_000_000,
                    "source": "EQSender",
                    "destination": "EQDeposit",
                    "amount": "20990111",
                    "query_id": "1",
                    "jetton_master": CANONICAL_USDT_IDENTIFIERS["ton"],
                    "transaction_aborted": False,
                },
                {
                    "transaction_hash": "ton-fake",
                    "transaction_lt": "101",
                    "transaction_now": 2_000_000_001,
                    "source": "EQSender",
                    "destination": "EQDeposit",
                    "amount": "20990111",
                    "query_id": "2",
                    "jetton_master": "EQFake",
                    "transaction_aborted": False,
                },
            ]
        },
    )
    result = asyncio.run(adapter.poll(None))
    assert len(result.transfers) == 1
    assert result.transfers[0].tx_hash == "ton-tx-1"
    assert result.transfers[0].amount_atomic == 20990111
    assert result.transfers[0].finality == "finalized"


def test_usdt_routes_register_direct_web_and_separate_mobile_api():
    class MobileStub:
        @staticmethod
        def _mobile_api_available():
            return True

        @staticmethod
        def _require_mobile_auth(_request):
            return None

    app = web.Application()
    checkout_routes.setup_velia_usdt_checkout_routes(app, MobileStub, lambda _request: 0)
    paths = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/velia/pay") in paths
    assert ("POST", "/velia/pay") in paths
    assert ("GET", "/mobile-api/v1/economy/usdt/catalog") in paths
    assert ("POST", "/mobile-api/v1/economy/usdt/intents") in paths


def test_crypto_runtime_has_fulfillment_and_no_signing_capability():
    runtime = Path("services/payments/live_runtime.py").read_text(encoding="utf-8")
    worker = Path("services/payments/worker.py").read_text(encoding="utf-8")
    assert 'transfer.finality != "finalized"' in runtime
    assert "created_at <= to_timestamp" in runtime
    assert "expires_at" in runtime
    assert "status='fulfilled'" in runtime
    assert "velia_user_commercial_state" in runtime
    assert "UPDATE users SET token_balance" in runtime
    assert '"signing_capability": False' in worker
    assert "private_key" not in runtime.lower()
    assert "mnemonic" not in runtime.lower()
