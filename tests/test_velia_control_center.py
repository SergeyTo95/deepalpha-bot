import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import admin_routes
from services import http_security_service
from services import velia_admin_control_service
from services import velia_admin_security_service
from services import velia_admin_telegram_auth_service
from services import velia_admin_telegram_bridge


class CodeCursor:
    def __init__(self):
        self.executions = []
        self.rowcount = 0
        self.closed = False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.executions.append((normalized, params))
        self.rowcount = 1 if normalized.startswith("INSERT INTO velia_admin_login_codes") else 0

    def close(self):
        self.closed = True


class CodeConnection:
    def __init__(self):
        self.cursor_instance = CodeCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class ConsumeCursor:
    def __init__(self, admin_user_id):
        self.admin_user_id = admin_user_id
        self.executions = []
        self.rowcount = 1
        self.closed = False
        self._last_query = ""

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.executions.append((normalized, params))
        self._last_query = normalized

    def fetchone(self):
        if self._last_query.startswith("SELECT admin_user_id, expires_at, consumed_at"):
            return (self.admin_user_id, datetime.utcnow() + timedelta(minutes=3), None)
        return None

    def close(self):
        self.closed = True


class ConsumeConnection:
    def __init__(self, admin_user_id):
        self.cursor_instance = ConsumeCursor(admin_user_id)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_owner_allowlist_is_server_side_admin_id(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "123456789")
    assert velia_admin_security_service.configured_admin_id() == 123456789
    assert velia_admin_security_service.is_admin_user(123456789) is True
    assert velia_admin_security_service.is_admin_user(123456788) is False
    monkeypatch.setenv("ADMIN_ID", "not-an-id")
    assert velia_admin_security_service.configured_admin_id() == 0


def test_admin_telegram_start_payload_is_exact_and_bot_username_is_sanitized(monkeypatch):
    assert velia_admin_telegram_auth_service.is_velia_admin_login_start("/start velia_admin_login") is True
    assert velia_admin_telegram_auth_service.is_velia_admin_login_start("/start other") is False
    assert velia_admin_telegram_auth_service.is_velia_admin_login_start("hello velia_admin_login") is False
    monkeypatch.setenv("BOT_USERNAME", "@DeepAlphaAI_bot<script>")
    assert velia_admin_telegram_auth_service.build_admin_login_url() == "https://t.me/DeepAlphaAI_bot?start=velia_admin_login"


def test_admin_login_code_is_hashed_one_time_and_five_minutes(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "123456789")
    fake = CodeConnection()
    monkeypatch.setattr(velia_admin_security_service, "ensure_velia_admin_tables", lambda: None)
    monkeypatch.setattr(velia_admin_security_service, "get_connection", lambda: fake)
    monkeypatch.setattr(velia_admin_security_service, "_new_login_code", lambda: "ABCDEFGHJKLMNPQR")

    result = velia_admin_security_service.create_admin_login_code(123456789)

    assert result["ok"] is True
    assert result["login_code"] == "ABCD-EFGH-JKLM-NPQR"
    assert result["expires_in"] == 300
    assert fake.commits == 1
    queries = fake.cursor_instance.executions
    assert any(q.startswith("UPDATE velia_admin_login_codes SET consumed_at") for q, _ in queries)
    insert = next(item for item in queries if item[0].startswith("INSERT INTO velia_admin_login_codes"))
    params = insert[1]
    assert "ABCDEFGHJKLMNPQR" not in [str(value) for value in params]
    assert params[0] == hashlib.sha256(b"ABCDEFGHJKLMNPQR").hexdigest()


def test_consumed_admin_code_creates_hashed_session_and_csrf(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "123456789")
    fake = ConsumeConnection(123456789)
    monkeypatch.setattr(velia_admin_security_service, "ensure_velia_admin_tables", lambda: None)
    monkeypatch.setattr(velia_admin_security_service, "get_connection", lambda: fake)

    result = velia_admin_security_service.consume_admin_login_code(
        "ABCD-EFGH-JKLM-NPQR",
        user_agent="pytest",
        ip="127.0.0.1",
    )

    assert result["ok"] is True
    assert result["admin_user_id"] == 123456789
    assert len(result["session_token"]) > 40
    assert len(result["csrf_token"]) > 30
    session_insert = next(
        item for item in fake.cursor_instance.executions
        if item[0].startswith("INSERT INTO velia_admin_sessions")
    )
    stored = [str(value) for value in session_insert[1]]
    assert result["session_token"] not in stored
    assert result["csrf_token"] not in stored
    assert hashlib.sha256(result["session_token"].encode()).hexdigest() in stored
    assert hashlib.sha256(result["csrf_token"].encode()).hexdigest() in stored


def test_audit_sanitizer_redacts_credentials_and_seeds():
    value = {
        "api_key": "secret-key",
        "Authorization": "Bearer dangerous",
        "nested": {"seed_encrypted": "seed", "safe": "visible"},
        "token_balance": 100,
    }
    sanitized = velia_admin_security_service._sanitize(value)
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["Authorization"] == "[REDACTED]"
    assert sanitized["nested"]["seed_encrypted"] == "[REDACTED]"
    assert sanitized["nested"]["safe"] == "visible"
    assert sanitized["token_balance"] == "[REDACTED]"


def test_control_center_disables_legacy_shared_secret_auth_and_url_keys():
    security_source = Path("services/http_security_service.py").read_text(encoding="utf-8")
    admin_source = Path("admin_routes.py").read_text(encoding="utf-8")
    assert "ADMIN_SECRET_KEY" not in security_source
    assert "deepalpha_admin_session" not in security_source
    assert "?key=" not in admin_source
    assert admin_routes.CONTROL_CENTER_AUTH_V2 is True
    assert "Legacy admin URL secrets are disabled" in security_source


def test_layout_injects_csrf_into_all_post_forms_without_exposing_admin_secret():
    page = admin_routes._layout("Test", "Overview", "csrf-test-value", "<form method='post' action='/admin/x'></form>")
    assert "velia-csrf" in page
    assert "input.name='_csrf'" in page
    assert "csrf-test-value" in page
    assert "ADMIN_SECRET_KEY" not in page
    assert "?key=" not in page


def test_user_mutations_require_explicit_confirmation_in_route_source():
    source = Path("admin_routes.py").read_text(encoding="utf-8")
    assert 'form.get("confirmed", "")' in source
    assert "Explicit confirmation required" in source
    assert "data-confirm" in source


def test_deployment_snapshot_never_substitutes_github_head(monkeypatch):
    for name in (
        "RAILWAY_GIT_COMMIT_SHA", "RAILWAY_GIT_COMMIT", "GIT_COMMIT_SHA",
        "RAILWAY_GIT_BRANCH", "GIT_BRANCH",
    ):
        monkeypatch.delenv(name, raising=False)
    snapshot = velia_admin_control_service.deployment_snapshot()
    assert snapshot["deployed_commit_sha"] is None
    assert snapshot["deployed_branch"] is None

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "runtime-sha-123")
    monkeypatch.setenv("RAILWAY_GIT_BRANCH", "feature/turbo-short-term-btc")
    snapshot = velia_admin_control_service.deployment_snapshot()
    assert snapshot["deployed_commit_sha"] == "runtime-sha-123"
    assert snapshot["deployed_branch"] == "feature/turbo-short-term-btc"


def test_memory_health_is_explicitly_unavailable_without_runtime_endpoint(monkeypatch):
    monkeypatch.delenv("VELIA_MEMORY_ENDPOINT", raising=False)
    result = velia_admin_control_service.velyon_memory_health()
    assert result == {"status": "unavailable", "reason": "endpoint_not_configured"}


def test_telegram_admin_bridge_rebinds_only_admin_module_names():
    target = SimpleNamespace(
        set_user_ban=object(),
        set_user_vip=object(),
        add_tokens=object(),
        set_tokens=object(),
    )
    velia_admin_telegram_bridge.install(target)
    assert target.set_user_ban is velia_admin_telegram_bridge._set_user_ban
    assert target.set_user_vip is velia_admin_telegram_bridge._set_user_vip
    assert target.add_tokens is velia_admin_telegram_bridge._add_tokens
    assert target.set_tokens is velia_admin_telegram_bridge._set_tokens
    assert target._velia_admin_shared_mutations_installed is True


def test_http_security_requires_control_center_auth_v2():
    app = web_application_for_test()
    bad_module = SimpleNamespace(CONTROL_CENTER_AUTH_V2=False)
    try:
        http_security_service.install_http_security(app, bad_module)
    except RuntimeError as exc:
        assert "identity auth is required" in str(exc)
    else:
        raise AssertionError("legacy admin auth must fail closed")


def web_application_for_test():
    from aiohttp import web

    return web.Application()
