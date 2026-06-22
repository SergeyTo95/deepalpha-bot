import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("aiogram")
import app


@pytest.mark.parametrize("value", ["on", "true", "1", "yes", "enabled", " ON ", True])
def test_is_channel_posting_enabled_accepts_enabled_values(monkeypatch, value):
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: value)

    assert app.is_channel_posting_enabled() is True


@pytest.mark.parametrize("value", ["off", "false", "0", "no", "disabled", "", False])
def test_is_channel_posting_enabled_rejects_disabled_values(monkeypatch, value):
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: value)

    assert app.is_channel_posting_enabled() is False


@pytest.mark.asyncio
async def test_post_to_channel_disabled_does_not_send(monkeypatch):
    sent = []

    async def fake_send_message(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(app, "CHANNEL_ID", "@deepalpha")
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: "off" if key == "channel_posting_enabled" else default)
    monkeypatch.setattr(app.telegram_bot.bot, "send_message", fake_send_message)

    result = await app.post_to_channel()

    assert result == {"ok": False, "reason": "disabled"}
    assert sent == []


@pytest.mark.asyncio
async def test_post_to_channel_enabled_sends_mocked_candidate(monkeypatch):
    sent = []
    settings = {"channel_posting_enabled": "on", "channel_shown_markets": "", "channel_last_category": ""}

    async def fake_send_message(*args, **kwargs):
        sent.append((args, kwargs))

    def fake_get_setting(key, default=None):
        return settings.get(key, default)

    def fake_set_setting(key, value):
        settings[key] = value

    event = {"markets": [{"active": True, "closed": False, "outcomePrices": [0.55, 0.45]}]}
    normalized = {
        "id": "event-1",
        "question": "Will the president sign the bill this month?",
        "market_probability": "55%",
        "url": "https://polymarket.com/event/event-1",
    }

    monkeypatch.setattr(app, "CHANNEL_ID", "@deepalpha")
    monkeypatch.setattr(app, "get_setting", fake_get_setting)
    monkeypatch.setattr(app, "set_setting", fake_set_setting)
    monkeypatch.setattr(app, "list_events", lambda limit=50: [event])
    monkeypatch.setattr(app, "normalize_event_for_channel", lambda item: normalized)
    monkeypatch.setattr(app.random, "choice", lambda items: items[0])
    monkeypatch.setattr(app.telegram_bot.bot, "send_message", fake_send_message)

    result = await app.post_to_channel()

    assert result == {"ok": True, "reason": "sent"}
    assert len(sent) == 1
    assert sent[0][0][0] == "@deepalpha"
    assert "Will the president sign the bill" in sent[0][0][1]
