"""Guardian protections must not inspect or slow down model conversations."""
from types import SimpleNamespace

from services import velia_guardian_service as guardian


def fake_request(path, method="POST", ip="192.0.2.5"):
    return SimpleNamespace(path=path, method=method, remote=ip)


def test_auth_throttling_is_scoped_to_login_only(monkeypatch):
    monkeypatch.setenv("VELIA_GUARDIAN_ENABLED", "true")
    with guardian._COUNTER_LOCK:
        guardian._COUNTERS.clear()
        guardian._COUNTER_LAST_CLEAN = 0.0
    chat = fake_request("/mobile-api/v1/conversations/abc/messages", "POST")
    for _ in range(250):
        assert guardian.incoming_decision(chat, timestamp=100.0) is None
    login = fake_request("/mobile-api/v1/auth/exchange", "POST")
    for _ in range(30):
        assert guardian.incoming_decision(login, timestamp=100.0) is None
    blocked = guardian.incoming_decision(login, timestamp=100.0)
    assert blocked["status"] == 429
    assert blocked["retry_after"] > 0
    # Another client's login is not affected.
    assert guardian.incoming_decision(fake_request(
        "/mobile-api/v1/auth/exchange", "POST", "192.0.2.6"
    ), timestamp=100.0) is None


def test_internet_probe_blocked_without_chat_inspection(monkeypatch):
    monkeypatch.setenv("VELIA_GUARDIAN_ENABLED", "true")
    assert guardian.incoming_decision(fake_request("/.env", "GET"))["status"] == 404
    assert guardian.incoming_decision(fake_request("/wp-admin/", "GET"))["status"] == 404
    assert guardian.incoming_decision(fake_request("/mobile-api/v1/conversations", "GET")) is None


def test_disabled_guardian_changes_nothing(monkeypatch):
    monkeypatch.setenv("VELIA_GUARDIAN_ENABLED", "false")
    assert guardian.incoming_decision(fake_request("/.env", "GET")) is None
    assert guardian.incoming_decision(fake_request("/mobile-api/v1/auth/exchange")) is None


def test_guardian_never_persists_untrusted_detail(monkeypatch):
    monkeypatch.setenv("VELIA_GUARDIAN_ENABLED", "true")
    saved = []

    class Cursor:
        rowcount = 1
        def execute(self, sql, args=()):
            saved.append((sql, args))
        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()
        def commit(self):
            pass
        def rollback(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(guardian, "get_connection", Connection)
    assert guardian.record(
        42, "mobile_login", source="android",
        detail="<script>alert(1)</script> secret:foo",
    )
    inserted = saved[0][1]
    assert inserted[1] == 42
    assert "<" not in inserted[5]
    assert ">" not in inserted[5]
    assert guardian.record(42, "unknown_kind") is False
    assert len(saved) == 1
