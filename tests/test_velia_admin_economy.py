from pathlib import Path

from aiohttp import web

import services.velia_admin_economy_routes as routes
import services.velia_admin_economy_service as economy


class _FakeCursor:
    def __init__(self):
        self.statements = []
        self._last = ""

    def execute(self, statement, params=None):
        self._last = str(statement)
        self.statements.append((str(statement), params))

    def fetchone(self):
        if "FROM pg_trigger" in self._last:
            return None
        return None

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
        ("Users", "/admin/users"),
        ("Audit", "/admin/audit"),
    ]


def test_token_definition_is_internal_credit_not_crypto():
    definition = economy.TOKEN_DEFINITION
    assert definition["kind"] == "internal_usage_credit"
    assert definition["economics_status"] == "draft"
    assert definition["fixed_usd_value"] is None
    text = definition["description"].lower()
    assert "not a blockchain token" in text
    assert "not a cryptocurrency" in text
    assert "no redemption value" in text


def test_economy_schema_installs_fail_open_balance_ledger(monkeypatch):
    conn = _FakeConnection()
    monkeypatch.setattr(economy, "get_connection", lambda: conn)

    economy.ensure_economy_tables()

    sql = "\n".join(statement for statement, _params in conn.cursor_obj.statements)
    assert "CREATE TABLE IF NOT EXISTS velia_token_ledger" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_commercial_draft_plans" in sql
    assert "CREATE TABLE IF NOT EXISTS velia_commercial_draft_features" in sql
    assert "CREATE OR REPLACE FUNCTION velia_capture_token_balance_change" in sql
    assert "EXCEPTION WHEN OTHERS THEN" in sql
    assert "NULL;" in sql
    assert "AFTER UPDATE OF token_balance ON users" in sql
    assert conn.committed is True
    assert conn.rolled_back is False


def test_draft_pricing_defaults_do_not_guess_economics():
    assert [row[0] for row in economy._PLAN_DEFAULTS] == ["free", "plus", "pro"]
    assert all(row[2] is None and row[3] is None for row in economy._PLAN_DEFAULTS)
    assert all(row[2] is None for row in economy._FEATURE_DEFAULTS)


def test_invalid_draft_updates_fail_before_database_access(monkeypatch):
    monkeypatch.setattr(
        economy,
        "get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be used")),
    )
    assert economy.update_draft_plan(
        admin_user_id=1,
        code="enterprise-does-not-exist",
        monthly_price_usd=10,
        monthly_tokens=100,
        notes="",
    )["error"] == "unknown_plan"
    assert economy.update_draft_feature(
        admin_user_id=1,
        code="unknown-feature",
        tokens_per_action=1,
        notes="",
    )["error"] == "unknown_feature"
    assert economy.update_draft_plan(
        admin_user_id=1,
        code="plus",
        monthly_price_usd=-1,
        monthly_tokens=100,
        notes="",
    )["error"] == "invalid_price"
    assert economy.update_draft_feature(
        admin_user_id=1,
        code="velia_chat",
        tokens_per_action=-1,
        notes="",
    )["error"] == "invalid_tokens"


def test_economy_routes_are_owner_admin_routes_and_nav_is_inserted():
    app = web.Application()
    admin = type("Admin", (), {"SECTIONS": list(_AdminStub.SECTIONS)})

    routes.setup_velia_admin_economy_routes(app, admin)

    paths = {route.resource.canonical for route in app.router.routes()}
    assert "/admin/economy" in paths
    assert "/admin/economy/draft/plan/{code}" in paths
    assert "/admin/economy/draft/feature/{code}" in paths
    assert ("Economy", "/admin/economy") in admin.SECTIONS
    assert admin.SECTIONS.index(("Economy", "/admin/economy")) < admin.SECTIONS.index(("Audit", "/admin/audit"))


def test_security_installer_registers_economy_without_exempting_mutations():
    source = Path("services/http_security_service.py").read_text(encoding="utf-8")
    assert "setup_velia_admin_economy_routes(app, admin_routes_module)" in source
    assert 'normalized != "/admin/login"' in source
    assert '"/admin/economy"' not in source.split("def _admin_mutation_requires_origin", 1)[1].split("async def", 1)[0]


def test_economy_service_never_changes_runtime_prices_or_balances_directly():
    source = Path("services/velia_admin_economy_service.py").read_text(encoding="utf-8")
    assert "UPDATE settings" not in source
    assert "DELETE FROM token_packages" not in source
    assert "UPDATE token_packages" not in source
    assert "UPDATE users SET token_balance" not in source
    assert "velia_commercial_draft_plans" in source
    assert "velia_commercial_draft_features" in source


def test_economy_page_labels_analytics_scope_and_draft_boundary():
    source = Path("services/velia_admin_economy_routes.py").read_text(encoding="utf-8")
    assert "DRAFT ONLY · NOT ENFORCED" in source
    assert "VELIA Chat DAU" in source
    assert "velia_messages_only" not in source  # internal scope value is not exposed as a fake product metric
    assert "Current production pricing · enforced today" in source
    assert "It is not a blockchain token" not in source  # supplied from the single token-definition service
