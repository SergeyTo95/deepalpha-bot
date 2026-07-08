import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.watchlist_ai_summary_service import build_watchlist_ai_summary

aiogram = pytest.importorskip("aiogram")
import app


class SentBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, user_id, text, disable_web_page_preview=True):
        self.messages.append(text)


def run(coro):
    return asyncio.run(coro)


def patch_common(monkeypatch, bot, charge_allowed=True):
    monkeypatch.setattr(app.telegram_bot, "bot", bot)
    monkeypatch.setattr(app, "update_watchlist_probability", lambda *a, **k: None)
    monkeypatch.setattr(app, "reset_watchlist_change_notification", lambda *a, **k: None)
    monkeypatch.setattr(app, "mark_watchlist_notified", lambda *a, **k: None)
    monkeypatch.setattr(app, "get_setting", lambda key, default="": "on" if key in {"watchlist_ai_summary_enabled", "paid_mode"} else default)
    monkeypatch.setattr(app, "_charge_watchlist_alert", lambda user_id: {"allowed": charge_allowed, "status": "charged", "price": 5, "balance": 10} if charge_allowed else {"allowed": False, "status": "insufficient_tokens"})


def test_ai_summary_not_called_before_successful_billing(monkeypatch):
    bot = SentBot()
    patch_common(monkeypatch, bot, charge_allowed=False)
    calls = []
    monkeypatch.setattr(app, "build_watchlist_ai_summary", lambda **kwargs: calls.append(kwargs) or {})

    run(app._check_subscriber_notifications(
        {"id": 1, "user_id": 10, "initial_probability": 10, "notify_enabled": True, "notified_change": False},
        {"question": "Will X happen?", "market_url": "u", "market_slug": "s"},
        30,
        5,
        24,
    ))

    assert calls == []
    assert bot.messages == []


def test_insufficient_tokens_does_not_call_ai_summary(monkeypatch):
    bot = SentBot()
    patch_common(monkeypatch, bot, charge_allowed=False)
    calls = []
    monkeypatch.setattr(app, "build_watchlist_ai_summary", lambda **kwargs: calls.append(kwargs) or {})

    end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    run(app._check_subscriber_notifications(
        {"id": 1, "user_id": 10, "initial_probability": 10, "notify_enabled": True, "notified_change": True, "notified_closing_soon": False, "market_end_date": end},
        {"question": "Will X happen?", "market_url": "u", "market_slug": "s"},
        11,
        5,
        24,
    ))

    assert calls == []
    assert bot.messages == []


def test_probability_change_alert_includes_deepalpha_view(monkeypatch):
    bot = SentBot()
    patch_common(monkeypatch, bot)
    monkeypatch.setattr(app, "build_watchlist_ai_summary", lambda **kwargs: {"summary": "Move matters.", "label": "WATCH", "watch_next": ["News"], "data_quality": "medium"})

    run(app._check_subscriber_notifications(
        {"id": 1, "user_id": 10, "initial_probability": 10, "notify_enabled": True, "notified_change": False},
        {"question": "Will X happen?", "market_url": "u", "market_slug": "s"},
        30,
        5,
        24,
    ))

    assert "🧠 DeepAlpha view:" in bot.messages[0]


def test_closing_soon_alert_includes_deepalpha_view(monkeypatch):
    bot = SentBot()
    patch_common(monkeypatch, bot)
    monkeypatch.setattr(app, "build_watchlist_ai_summary", lambda **kwargs: {"summary": "Close matters.", "label": "DATA NEEDED", "watch_next": ["News"], "data_quality": "limited"})
    end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    run(app._check_subscriber_notifications(
        {"id": 1, "user_id": 10, "initial_probability": 10, "notify_enabled": True, "notified_change": True, "notified_closing_soon": False, "market_end_date": end},
        {"question": "Will X happen?", "market_url": "u", "market_slug": "s"},
        11,
        5,
        24,
    ))

    assert "🧠 DeepAlpha view:" in bot.messages[0]


def test_resolved_recap_alert_includes_deepalpha_view(monkeypatch):
    bot = SentBot()
    patch_common(monkeypatch, bot)
    monkeypatch.setattr(app, "get_watchlist_subscribers", lambda slug: [{"id": 1, "user_id": 10, "notify_enabled": True, "notified_resolved": False}])
    monkeypatch.setattr(app, "close_watchlist_market", lambda slug: 1)
    monkeypatch.setattr("services.polymarket_resolver.extract_actual_outcome", lambda data: "Yes")
    monkeypatch.setattr(app, "build_watchlist_ai_summary", lambda **kwargs: {"summary": "Resolved.", "label": "NO EDGE", "watch_next": ["Review"], "data_quality": "strong"})

    run(app._handle_resolved_market("s", {"question": "Will X happen?", "market_url": "u"}, {}))

    assert "🧠 DeepAlpha view:" in bot.messages[0]


def test_provider_failure_returns_fallback_and_still_sends(monkeypatch):
    bot = SentBot()
    patch_common(monkeypatch, bot)
    monkeypatch.setattr("services.watchlist_ai_summary_service._generate_text", lambda prompt: (_ for _ in ()).throw(RuntimeError("down")))

    run(app._check_subscriber_notifications(
        {"id": 1, "user_id": 10, "initial_probability": 10, "notify_enabled": True, "notified_change": False},
        {"question": "Will X happen?", "market_url": "u", "market_slug": "s"},
        30,
        5,
        24,
    ))

    assert "🧠 DeepAlpha view:" in bot.messages[0]


def test_forbidden_words_not_present_in_fallback_text(monkeypatch):
    monkeypatch.setattr("services.watchlist_ai_summary_service._generate_text", lambda prompt: "")
    summary = build_watchlist_ai_summary("probability_change", "Question", initial_probability=10, current_probability=30, probability_change=20)
    text = " ".join([summary["summary"], *summary["watch_next"]]).lower()
    for word in ("bet", "buy", "guaranteed", "100%"):
        assert word not in text
