import asyncio
from pathlib import Path

from aiohttp import web

import run_payment_worker as payment_entry
import services.payments.config as payment_config
import services.payments.repository as payment_repository
import services.payments.schema as payment_schema
import services.payments.worker as payment_worker
import services.velia_admin_payments_routes as payment_routes
from services.payments.chains.base import PollResult


class _FakeCursor:
    def __init__(self):
        self.statements = []
        self.rowcount = 0

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        self.rowcount = 1

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class _AdminStub:
    SECTIONS = [
        ("Overview", "/admin"),
        ("Economy", "/admin/economy"),
        ("Audit", "/admin/audit"),
    ]


def test_payment_schema_is_additive_and_keeps_legacy_ton_untouched(monkeypatch):
    conn = _FakeConnection()
    monkeypatch.setattr(payment_schema, "get_connection", lambda: conn)
    payment_schema.ensure_payment_tables()
    sql = "\n".join(statement for statement, _params in conn.cursor_obj.statements)
    assert "CREATE TABLE IF NOT EXISTS velia_payment_products" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_payment_intents" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_payment_events" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_payment_transactions" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_payment_fulfillments" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_payment_worker_state" in sql
    assert "ALTER TABLE payment_intents" not in sql
    assert "UPDATE payment_intents" not in sql
    assert "ALTER TABLE transactions" not in sql
    assert conn.committed is True
    assert conn.rolled_back is False


def test_all_payment_networks_default_disabled(monkeypatch):
    for name in (
        "VELIA_PAYMENT_WORKER_ENABLED",
        "VELIA_USDT_CHECKOUT_ENABLED",
        "VELIA_PAYMENT_TRON_ENABLED",
        "VELIA_PAYMENT_SOLANA_ENABLED",
        "VELIA_PAYMENT_TON_ENABLED",
        "VELIA_PAYMENT_BNB_ENABLED",
        "VELIA_PAYMENT_POLYGON_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    assert payment_config.worker_enabled() is False
    assert payment_config.crypto_checkout_enabled() is False
    configs = payment_config.all_network_configs()
    assert set(configs) == {"tron", "solana", "ton", "bnb", "polygon"}
    assert all(config.enabled is False for config in configs.values())


def test_only_canonical_phase1_network_can_be_ready(monkeypatch):
    monkeypatch.setenv("VELIA_PAYMENT_TRON_ENABLED", "true")
    monkeypatch.setenv("TRON_RPC_URL", "https://rpc.invalid.example")
    monkeypatch.setenv("VELIA_PAYMENT_TRON_DEPOSIT_ADDRESS", "TDepositForTest")
    monkeypatch.setenv("TRON_USDT_CONTRACT", "not-the-canonical-contract")
    assert payment_config.network_config("tron").configured is False

    monkeypatch.setenv(
        "TRON_USDT_CONTRACT",
        payment_config.CANONICAL_USDT_IDENTIFIERS["tron"],
    )
    assert payment_config.network_config("tron").configured is True

    monkeypatch.setenv("VELIA_PAYMENT_BNB_ENABLED", "true")
    monkeypatch.setenv("BNB_RPC_URL", "https://rpc.invalid.example")
    monkeypatch.setenv("VELIA_PAYMENT_BNB_DEPOSIT_ADDRESS", "0xdeadbeef")
    monkeypatch.setenv("BNB_USDT_CONTRACT", "0xanything")
    assert payment_config.network_config("bnb").configured is False


def test_worker_polls_reviewed_tron_adapter_when_globally_enabled(monkeypatch):
    monkeypatch.setenv("VELIA_PAYMENT_WORKER_ENABLED", "true")
    monkeypatch.setenv("VELIA_PAYMENT_TRON_ENABLED", "true")
    monkeypatch.setenv("TRON_RPC_URL", "https://rpc.invalid.example")
    monkeypatch.setenv("VELIA_PAYMENT_TRON_DEPOSIT_ADDRESS", "TDepositForTest")
    monkeypatch.delenv("TRON_USDT_CONTRACT", raising=False)
    monkeypatch.setattr(payment_worker, "update_worker_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(payment_worker, "process_finalized_transfer", lambda transfer: {"ok": True, "matched": False})

    worker = payment_worker.PaymentWorker()
    poll_called = {"value": False}

    async def fake_poll(_cursor):
        poll_called["value"] = True
        return PollResult(transfers=[], next_cursor="cursor-1")

    worker.adapters["tron"].poll = fake_poll
    result = asyncio.run(worker.run_once())

    assert result["tron"].configured is True
    assert result["tron"].status == "ready"
    assert poll_called["value"] is True
    assert worker.health_snapshot()["live_money_acceptance"] is True
    assert worker.health_snapshot()["signing_capability"] is False


def test_disabled_worker_does_not_start_background_poll_loop(monkeypatch):
    monkeypatch.setattr(payment_entry, "_schema_bootstrap_allowed", lambda: True)
    monkeypatch.setattr(payment_entry, "worker_enabled", lambda: False)
    monkeypatch.setattr(payment_entry, "ensure_payment_tables_serialized", lambda: None)
    monkeypatch.setattr(payment_entry, "ensure_commercial_runtime_tables_serialized", lambda: None)
    app = web.Application()
    asyncio.run(payment_entry._startup(app))
    assert app["velia_payment_schema"] == "ready"
    assert app["velia_payment_worker_task"] is None


def test_payment_intent_validation_fails_before_database(monkeypatch):
    monkeypatch.setattr(payment_repository, "get_connection", lambda: (_ for _ in ()).throw(AssertionError("database should not be used")))
    assert payment_repository.create_payment_intent(user_id=1, product_code="plus", channel="crypto", idempotency_key="test")["error"] == "crypto_network_asset_required"
    assert payment_repository.create_payment_intent(user_id=1, product_code="plus", channel="cash", idempotency_key="test")["error"] == "invalid_channel"
    assert payment_repository.create_payment_intent(user_id=0, product_code="plus", channel="google_play", idempotency_key="test")["error"] == "invalid_user"
    assert payment_repository.create_payment_intent(user_id=1, product_code="plus", channel="google_play", idempotency_key="test", expected_amount_usd=-1)["error"] == "invalid_expected_amount"
    assert payment_repository.create_payment_intent(user_id=1, product_code="plus", channel="crypto", network="tron", asset="USDT", idempotency_key="test", expected_amount_asset=float("nan"))["error"] == "invalid_expected_amount"
    assert payment_repository.create_payment_intent(user_id=1, product_code="plus", channel="crypto", network="tron", asset="USDT", idempotency_key="test", asset_decimals=37)["error"] == "invalid_asset_decimals"


def test_payment_payload_redaction_preserves_noncredential_token_fields():
    redacted = payment_repository._redact_payload({
        "access_token": "secret-value",
        "client_secret": "store-secret",
        "bearer-token": "bearer-secret",
        "token_balance": 123,
        "nested": {"private_key": "secret-key", "webhook_secret": "hook", "tx_hash": "abc"},
    })
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["client_secret"] == "[REDACTED]"
    assert redacted["bearer-token"] == "[REDACTED]"
    assert redacted["token_balance"] == 123
    assert redacted["nested"]["private_key"] == "[REDACTED]"
    assert redacted["nested"]["webhook_secret"] == "[REDACTED]"
    assert redacted["nested"]["tx_hash"] == "abc"


def test_payments_admin_route_is_registered_after_economy():
    app = web.Application()
    admin = type("Admin", (), {"SECTIONS": list(_AdminStub.SECTIONS)})
    payment_routes.setup_velia_admin_payments_routes(app, admin)
    paths = {route.resource.canonical for route in app.router.routes()}
    assert "/admin/economy/payments" in paths
    assert ("Payments", "/admin/economy/payments") in admin.SECTIONS
    assert admin.SECTIONS.index(("Payments", "/admin/economy/payments")) == admin.SECTIONS.index(("Economy", "/admin/economy")) + 1


def test_live_payment_runtime_still_has_no_signing_or_wallet_secret_boundary():
    payment_files = list(Path("services/payments").rglob("*.py")) + [Path("run_payment_worker.py")]
    text = "\n".join(path.read_text(encoding="utf-8") for path in payment_files)
    assert "seed_encrypted" not in text
    assert "sign_transaction" not in text
    assert 'os.getenv("PRIVATE_KEY' not in text
    assert "mnemonic" not in text.lower()
    assert "send_raw_transaction" not in text.lower()
    # Direct balance mutation is allowed only in the reviewed idempotent
    # fulfillment module; repository/chain adapters remain observation-only.
    assert "UPDATE users SET token_balance" in Path("services/payments/live_runtime.py").read_text(encoding="utf-8")
    assert "UPDATE users SET token_balance" not in Path("services/payments/repository.py").read_text(encoding="utf-8")
    for path in Path("services/payments/chains").rglob("*.py"):
        assert "UPDATE users" not in path.read_text(encoding="utf-8")


def test_fulfillment_requires_exact_finalized_transfer_contract():
    source = Path("services/payments/live_runtime.py").read_text(encoding="utf-8")
    assert 'transfer.finality != "finalized"' in source
    assert "expected_amount_atomic=%s" in source
    assert "deposit_address=%s" in source
    assert "FOR UPDATE" in source
    assert "status='fulfilled'" in source
    assert "velia_payment_fulfillments" in source


def test_nonprod_worker_schema_bootstrap_is_explicitly_fail_closed():
    source = Path("run_payment_worker.py").read_text(encoding="utf-8")
    assert "VELIA_PAYMENT_ALLOW_NONPROD_SCHEMA_BOOTSTRAP" in source
    assert "skipped_non_production" in source
    assert '"live_money_acceptance": False' in source
    assert "if not worker_enabled():" in source
    assert "ensure_commercial_runtime_tables_serialized" in source


def test_economy_bootstrap_registers_payments_and_uses_serialized_schema():
    source = Path("services/velia_admin_economy_bootstrap_service.py").read_text(encoding="utf-8")
    assert "setup_velia_admin_payments_routes(app, admin_routes_module)" in source
    assert "ensure_payment_tables_serialized" in source
    assert 'app["velia_payment_bootstrap"] = "ready"' in source
