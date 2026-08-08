import asyncio
from pathlib import Path

from aiohttp import web

import services.payments.config as payment_config
import services.payments.repository as payment_repository
import services.payments.schema as payment_schema
import services.payments.worker as payment_worker
import services.velia_admin_payments_routes as payment_routes


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
        "VELIA_PAYMENT_TRON_ENABLED",
        "VELIA_PAYMENT_SOLANA_ENABLED",
        "VELIA_PAYMENT_TON_ENABLED",
        "VELIA_PAYMENT_BNB_ENABLED",
        "VELIA_PAYMENT_POLYGON_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    assert payment_config.worker_enabled() is False
    configs = payment_config.all_network_configs()
    assert set(configs) == {"tron", "solana", "ton", "bnb", "polygon"}
    assert all(config.enabled is False for config in configs.values())


def test_worker_remains_foundation_blocked_even_if_tron_is_configured(monkeypatch):
    monkeypatch.setenv("VELIA_PAYMENT_WORKER_ENABLED", "true")
    monkeypatch.setenv("VELIA_PAYMENT_TRON_ENABLED", "true")
    monkeypatch.setenv("TRON_RPC_URL", "https://rpc.invalid.example")
    monkeypatch.setenv("TRON_USDT_CONTRACT", "configured-for-test")
    monkeypatch.setattr(payment_worker, "update_worker_state", lambda *args, **kwargs: None)

    worker = payment_worker.PaymentWorker()
    poll_called = {"value": False}

    async def forbidden_poll(_cursor):
        poll_called["value"] = True
        raise AssertionError("foundation must not invoke live poll")

    worker.adapters["tron"].poll = forbidden_poll
    result = asyncio.run(worker.run_once())

    assert result["tron"].configured is True
    assert result["tron"].status == "foundation_blocked"
    assert poll_called["value"] is False
    assert worker.health_snapshot()["live_money_acceptance"] is False
    assert worker.health_snapshot()["signing_capability"] is False


def test_payment_intent_validation_fails_before_database(monkeypatch):
    monkeypatch.setattr(
        payment_repository,
        "get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be used")),
    )
    assert payment_repository.create_payment_intent(
        user_id=1,
        product_code="plus",
        channel="crypto",
        idempotency_key="test",
    )["error"] == "crypto_network_asset_required"
    assert payment_repository.create_payment_intent(
        user_id=1,
        product_code="plus",
        channel="cash",
        idempotency_key="test",
    )["error"] == "invalid_channel"
    assert payment_repository.create_payment_intent(
        user_id=0,
        product_code="plus",
        channel="google_play",
        idempotency_key="test",
    )["error"] == "invalid_user"


def test_payment_payload_redaction_preserves_noncredential_token_fields():
    redacted = payment_repository._redact_payload(
        {
            "access_token": "secret-value",
            "token_balance": 123,
            "nested": {"private_key": "secret-key", "tx_hash": "abc"},
        }
    )
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["token_balance"] == 123
    assert redacted["nested"]["private_key"] == "[REDACTED]"
    assert redacted["nested"]["tx_hash"] == "abc"


def test_payments_admin_route_is_registered_after_economy():
    app = web.Application()
    admin = type("Admin", (), {"SECTIONS": list(_AdminStub.SECTIONS)})
    payment_routes.setup_velia_admin_payments_routes(app, admin)

    paths = {route.resource.canonical for route in app.router.routes()}
    assert "/admin/economy/payments" in paths
    assert ("Payments", "/admin/economy/payments") in admin.SECTIONS
    assert admin.SECTIONS.index(("Payments", "/admin/economy/payments")) == (
        admin.SECTIONS.index(("Economy", "/admin/economy")) + 1
    )


def test_payment_foundation_has_no_signing_or_direct_user_credit_boundary():
    payment_files = [
        Path("services/payments/repository.py"),
        Path("services/payments/worker.py"),
        Path("services/payments/chains/base.py"),
        Path("run_payment_worker.py"),
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in payment_files)
    assert "UPDATE users SET token_balance" not in text
    assert "seed_encrypted" not in text
    assert "send_transaction" not in text
    assert "sign_transaction" not in text
    assert 'os.getenv("PRIVATE_KEY' not in text
    assert "bot.admin" not in text
    assert "velia_chat" not in text
    # Credential field names may exist only so event/metadata sanitizers can redact them.
    assert '"private_key"' in Path("services/payments/repository.py").read_text(encoding="utf-8")


def test_fulfillment_requires_confirmed_intent_by_contract():
    source = Path("services/payments/repository.py").read_text(encoding="utf-8")
    assert "intent_not_confirmed" in source
    assert "FOR UPDATE" in source
    assert '!= "confirmed"' in source
    assert "ON CONFLICT (intent_id) DO NOTHING" in source
    assert "never mutates the user itself" in source


def test_nonprod_worker_schema_bootstrap_is_explicitly_fail_closed():
    source = Path("run_payment_worker.py").read_text(encoding="utf-8")
    assert "VELIA_PAYMENT_ALLOW_NONPROD_SCHEMA_BOOTSTRAP" in source
    assert "skipped_non_production" in source
    assert '"live_money_acceptance": False' in source


def test_economy_bootstrap_registers_payments_and_uses_serialized_schema():
    source = Path("services/velia_admin_economy_bootstrap_service.py").read_text(encoding="utf-8")
    assert "setup_velia_admin_payments_routes(app, admin_routes_module)" in source
    assert "ensure_payment_tables_serialized" in source
    assert 'app["velia_payment_bootstrap"] = "ready"' in source
