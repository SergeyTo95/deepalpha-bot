import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))
sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("services.live_analyst_admin_service", types.SimpleNamespace(get_max_image_size_bytes=lambda: 8 * 1024 * 1024))

from services import live_analyst_access as access
from services import live_analyst_image_service as image_svc


def _env(monkeypatch, **values):
    keys = [
        "LIVE_ANALYST_ENABLED", "LIVE_ANALYST_ADMIN_ONLY", "LIVE_ANALYST_FREE_DAILY_LIMIT",
        "LIVE_ANALYST_REQUIRE_TOKENS", "GEMINI_VISION_ENABLED", "ADMIN_USER_IDS",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))


def test_non_admin_limit_zero_no_tokens_denied(monkeypatch):
    _env(monkeypatch, LIVE_ANALYST_FREE_DAILY_LIMIT="0")
    monkeypatch.setattr(access, "get_user", lambda user_id: {"user_id": user_id, "token_balance": 0})
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda user_id: 0)
    result = access.can_use_live_analyst(10, "u")
    assert result["allowed"] is False
    assert result["reason"] == "free_limit_exceeded"


def test_admin_allowed(monkeypatch):
    _env(monkeypatch, ADMIN_USER_IDS="10", LIVE_ANALYST_FREE_DAILY_LIMIT="0")
    result = access.can_use_live_analyst(10, "admin")
    assert result["allowed"] is True
    assert result["reason"] == "admin"


def test_admin_only(monkeypatch):
    _env(monkeypatch, ADMIN_USER_IDS="10", LIVE_ANALYST_ADMIN_ONLY="true")
    monkeypatch.setattr(access, "get_user", lambda user_id: {"user_id": user_id, "token_balance": 100})
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda user_id: 0)
    assert access.can_use_live_analyst(11)["allowed"] is False
    assert access.can_use_live_analyst(10)["reason"] == "admin"


def test_gemini_disabled_denied_before_gemini(monkeypatch):
    _env(monkeypatch, GEMINI_VISION_ENABLED="false", ADMIN_USER_IDS="10")
    result = access.can_use_live_analyst(10)
    assert result["allowed"] is False
    assert result["reason"] == "gemini_disabled"


def test_free_daily_limit(monkeypatch):
    _env(monkeypatch, LIVE_ANALYST_FREE_DAILY_LIMIT="1", LIVE_ANALYST_REQUIRE_TOKENS="false")
    monkeypatch.setattr(access, "get_user", lambda user_id: {"user_id": user_id, "token_balance": 0})
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda user_id: 0)
    assert access.can_use_live_analyst(20)["reason"] == "quota_available"
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda user_id: 1)
    denied = access.can_use_live_analyst(20)
    assert denied["allowed"] is False
    assert denied["reason"] == "free_limit_exceeded"


def test_token_balance_allowed(monkeypatch):
    _env(monkeypatch, LIVE_ANALYST_FREE_DAILY_LIMIT="0", LIVE_ANALYST_REQUIRE_TOKENS="true")
    monkeypatch.setattr(access, "get_user", lambda user_id: {"user_id": user_id, "token_balance": 1})
    monkeypatch.setattr(access, "count_live_analyst_usage_today", lambda user_id: 5)
    result = access.can_use_live_analyst(30)
    assert result["allowed"] is True
    assert result["reason"] == "paid_tokens_available"


def test_direct_gemini_helper_without_access_checked_blocks(monkeypatch):
    called = False
    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(status_code=200, text='{}', json=lambda: {})
    monkeypatch.setattr(image_svc.requests, "post", fake_post)
    text, finish = image_svc._call_gemini_vision("key", "gemini-2.0-flash", 1, "p", b"x", "image/png", 32, user_id=99)
    assert text == ""
    assert finish == "access_not_checked"
    assert called is False


def test_telegram_image_handler_denied_no_download_no_gemini():
    source = open("telegram_bot.py", encoding="utf-8").read()
    start = source.index("async def live_image_handler")
    end = source.index("async def", start + 1)
    handler = source[start:end]
    assert "can_use_live_analyst(uid" in handler
    assert handler.index("can_use_live_analyst(uid") < handler.index("bot.get_file")
    assert handler.index("can_use_live_analyst(uid") < handler.index("analyze_image_bytes")
    denial_branch = handler[handler.index("if not access.get") : handler.index("cost = get_live_request_cost")]
    assert "bot.get_file" not in denial_branch
    assert "analyze_image_bytes" not in denial_branch
