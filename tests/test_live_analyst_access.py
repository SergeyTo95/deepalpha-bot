import sys
import types

psycopg2_stub = types.SimpleNamespace(connect=lambda *a, **k: None)
psycopg2_stub.extras = types.SimpleNamespace(RealDictCursor=object)
psycopg2_stub.errors = types.SimpleNamespace()
sys.modules.setdefault("psycopg2", psycopg2_stub)
sys.modules.setdefault("psycopg2.extras", psycopg2_stub.extras)
sys.modules.setdefault("psycopg2.errors", psycopg2_stub.errors)

import importlib


def _reload(monkeypatch, **env):
    for key in [
        "LIVE_ANALYST_ENABLED", "LIVE_ANALYST_ADMIN_ONLY", "LIVE_ANALYST_FREE_DAILY_LIMIT",
        "LIVE_ANALYST_REQUIRE_TOKENS", "GEMINI_VISION_ENABLED", "ADMIN_USER_IDS",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    import services.live_analyst_access as access
    return importlib.reload(access)


def test_non_admin_no_tokens_free_limit_zero_denied(monkeypatch):
    access = _reload(monkeypatch, LIVE_ANALYST_FREE_DAILY_LIMIT="0", LIVE_ANALYST_REQUIRE_TOKENS="true")
    monkeypatch.setattr(access, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(access, "get_user", lambda uid: {"user_id": uid, "token_balance": 0})
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda uid: 0)

    result = access.can_use_live_analyst(100)

    assert result["allowed"] is False
    assert result["reason"] == "free_limit_exceeded"


def test_admin_allowed_with_free_limit_zero(monkeypatch):
    access = _reload(monkeypatch, ADMIN_USER_IDS="42", LIVE_ANALYST_FREE_DAILY_LIMIT="0")
    monkeypatch.setattr(access, "ensure_user", lambda *a, **k: None)

    result = access.can_use_live_analyst(42)

    assert result["allowed"] is True
    assert result["reason"] == "admin"


def test_admin_only_non_admin_denied_admin_allowed(monkeypatch):
    access = _reload(monkeypatch, ADMIN_USER_IDS="42", LIVE_ANALYST_ADMIN_ONLY="true")
    monkeypatch.setattr(access, "ensure_user", lambda *a, **k: None)

    assert access.can_use_live_analyst(100)["allowed"] is False
    assert access.can_use_live_analyst(42)["allowed"] is True


def test_gemini_disabled_denies(monkeypatch):
    access = _reload(monkeypatch, ADMIN_USER_IDS="42", GEMINI_VISION_ENABLED="false")

    result = access.can_use_live_analyst(100)

    assert result["allowed"] is False
    assert result["reason"] == "disabled"


def test_free_limit_usage_allows_then_denies(monkeypatch):
    access = _reload(monkeypatch, LIVE_ANALYST_FREE_DAILY_LIMIT="1", LIVE_ANALYST_REQUIRE_TOKENS="true")
    monkeypatch.setattr(access, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(access, "get_user", lambda uid: {"user_id": uid, "token_balance": 0})
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda uid: 0)
    assert access.can_use_live_analyst(100)["allowed"] is True
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda uid: 1)
    result = access.can_use_live_analyst(100)
    assert result["allowed"] is False
    assert result["reason"] == "free_limit_exceeded"
