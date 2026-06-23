import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


DB_SETTINGS = {}


def _noop(*args, **kwargs):
    return None


def _get_setting(key, default=None):
    return DB_SETTINGS.get(key, default)


def _set_setting(key, value):
    DB_SETTINGS[key] = value


@pytest.fixture()
def app_module(monkeypatch):
    DB_SETTINGS.clear()
    monkeypatch.setenv("CHANNEL_ID", "@test_channel")
    monkeypatch.delenv("CHANNEL_POSTING_DISABLED", raising=False)

    fake_telegram_bot = types.SimpleNamespace(
        dp=object(),
        bot=types.SimpleNamespace(send_message=AsyncMock()),
    )
    monkeypatch.setitem(sys.modules, "telegram_bot", fake_telegram_bot)

    fake_admin = types.ModuleType("bot.admin")
    fake_admin.register_admin = _noop
    monkeypatch.setitem(sys.modules, "bot.admin", fake_admin)

    fake_db = types.ModuleType("db.database")
    db_names = [
        "is_tx_processed", "save_transaction", "add_tokens", "ensure_user",
        "get_user", "add_referral_earnings", "get_all_pending", "delete_pending",
        "get_all_users", "get_subscribed_users", "set_subscription", "is_subscribed",
        "save_signal_cache", "get_signal_cache", "get_token_packages",
        "find_package_by_amount", "get_unresolved_predictions", "update_resolution",
        "get_active_watchlist_items", "get_watchlist_subscribers",
        "update_watchlist_probability", "mark_watchlist_notified",
        "reset_watchlist_change_notification", "close_watchlist_market",
        "cleanup_old_closed_watchlist", "set_author_status", "add_watchlist_extra_slots",
        "complete_donation", "get_donation", "get_author_profile",
    ]
    for name in db_names:
        setattr(fake_db, name, _noop)
    fake_db.get_setting = _get_setting
    fake_db.set_setting = _set_setting
    monkeypatch.setitem(sys.modules, "db.database", fake_db)

    fake_poly = types.ModuleType("services.polymarket_service")
    fake_poly.list_markets = _noop
    fake_poly.list_events = lambda limit=50: []
    fake_poly.normalize_market_data = _noop
    fake_poly.normalize_event_for_channel = _noop
    fake_poly.build_market_url = _noop
    monkeypatch.setitem(sys.modules, "services.polymarket_service", fake_poly)

    fake_resolver = types.ModuleType("services.polymarket_resolver")
    fake_resolver.resolve_prediction = _noop
    fake_resolver.fetch_market_by_slug = _noop
    fake_resolver.is_market_resolved = _noop
    monkeypatch.setitem(sys.modules, "services.polymarket_resolver", fake_resolver)

    fake_ton = types.ModuleType("services.ton_service")
    fake_ton.get_transactions = _noop
    fake_ton.parse_payment = _noop
    monkeypatch.setitem(sys.modules, "services.ton_service", fake_ton)

    fake_news = types.ModuleType("agents.news_agent")
    class NewsAgent:
        def _detect_category(self, question):
            return "Politics"
    fake_news.NewsAgent = NewsAgent
    monkeypatch.setitem(sys.modules, "agents.news_agent", fake_news)

    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    yield module
    sys.modules.pop("app", None)


@pytest.mark.parametrize("off_value", ["off", "false", "0", "no", "disabled"])
def test_is_channel_posting_enabled_respects_db_off_values(app_module, off_value):
    DB_SETTINGS["channel_posting_enabled"] = off_value

    assert app_module.is_channel_posting_enabled() is False


def test_is_channel_posting_enabled_respects_env_hard_kill(app_module, monkeypatch):
    DB_SETTINGS["channel_posting_enabled"] = "on"
    monkeypatch.setenv("CHANNEL_POSTING_DISABLED", "true")

    assert app_module.is_channel_posting_enabled() is False


@pytest.mark.parametrize("force", [False, True])
def test_post_to_channel_disabled_blocks_before_fetch_or_send(app_module, monkeypatch, force):
    DB_SETTINGS["channel_posting_enabled"] = "off"
    fetch_called = False

    def fail_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("market fetching should not happen when posting is disabled")

    monkeypatch.setattr(app_module, "list_events", fail_fetch)

    result = asyncio.run(app_module.post_to_channel(force=force))

    assert result == {"ok": False, "reason": "disabled"}
    assert fetch_called is False
    app_module.telegram_bot.bot.send_message.assert_not_awaited()


def test_post_to_channel_force_enabled_proceeds(app_module, monkeypatch):
    DB_SETTINGS["channel_posting_enabled"] = "on"
    event = {"markets": [{"active": True, "closed": False, "outcomePrices": [0.6, 0.4]}]}
    monkeypatch.setattr(app_module, "list_events", lambda limit=50: [event])
    monkeypatch.setattr(app_module.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(
        app_module,
        "normalize_event_for_channel",
        lambda raw: {
            "id": "event-1",
            "question": "Will this test market publish successfully?",
            "market_probability": "60%",
            "url": "https://example.com/market",
        },
    )

    result = asyncio.run(app_module.post_to_channel(force=True))

    assert result == {"ok": True}
    app_module.telegram_bot.bot.send_message.assert_awaited_once()


def test_no_unguarded_direct_channel_send_paths_outside_post_to_channel():
    offenders = []
    for path in Path(".").rglob("*.py"):
        if ".git" in path.parts or path.parts[:1] == ("tests",):
            continue
        text = path.read_text()
        if "send_message(\n            CHANNEL_ID" in text or "send_message(CHANNEL_ID" in text:
            if path.name != "app.py" or "async def post_to_channel" not in text:
                offenders.append(str(path))
    assert offenders == []
