import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("aiogram")
import app


@pytest.fixture(autouse=True)
def channel_defaults(monkeypatch):
    monkeypatch.setattr(app, "CHANNEL_ID", "@deepalpha")
    monkeypatch.delenv("CHANNEL_POSTING_DISABLED", raising=False)


@pytest.mark.asyncio
async def test_env_channel_posting_disabled_true_blocks_post_before_send(monkeypatch):
    calls = {"list_events": 0, "send_message": 0}
    monkeypatch.setenv("CHANNEL_POSTING_DISABLED", "true")
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: "on")
    monkeypatch.setattr(app, "list_events", lambda *a, **k: calls.__setitem__("list_events", calls["list_events"] + 1) or [])

    async def fake_send_message(*args, **kwargs):
        calls["send_message"] += 1

    monkeypatch.setattr(app.telegram_bot.bot, "send_message", fake_send_message)

    result = await app.post_to_channel()

    assert result == {"ok": False, "reason": "env_disabled"}
    assert calls == {"list_events": 0, "send_message": 0}


@pytest.mark.asyncio
async def test_missing_channel_id_blocks(monkeypatch):
    monkeypatch.setattr(app, "CHANNEL_ID", "")
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: "on")

    result = await app.post_to_channel()

    assert result == {"ok": False, "reason": "no_channel_id"}


@pytest.mark.asyncio
async def test_db_channel_posting_off_blocks(monkeypatch):
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: "off")

    result = await app.post_to_channel()

    assert result == {"ok": False, "reason": "db_disabled"}


def test_unknown_db_setting_blocks(monkeypatch):
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: "maybe")

    assert app.channel_posting_disabled_reason() == "db_unknown_disabled"
    assert app.is_channel_posting_enabled() is False


@pytest.mark.asyncio
async def test_explicit_db_on_and_env_not_disabled_allows(monkeypatch):
    sent = []
    settings = {"channel_posting_enabled": "on", "channel_shown_markets": "", "channel_last_category": ""}
    event = {"markets": [{"active": True, "closed": False, "outcomePrices": [0.55, 0.45]}]}
    normalized = {
        "id": "event-1",
        "question": "Will Bitcoin close above one hundred thousand dollars this month?",
        "market_probability": "55%",
        "url": "https://polymarket.com/event/event-1",
    }

    def fake_get_setting(key, default=None):
        return settings.get(key, default)

    async def fake_send_message(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setenv("CHANNEL_POSTING_DISABLED", "")
    monkeypatch.setattr(app, "get_setting", fake_get_setting)
    monkeypatch.setattr(app, "set_setting", lambda key, value: settings.__setitem__(key, value))
    monkeypatch.setattr(app, "list_events", lambda limit=50: [event])
    monkeypatch.setattr(app, "normalize_event_for_channel", lambda item: normalized)
    monkeypatch.setattr(app.random, "choice", lambda items: items[0])
    monkeypatch.setattr(app.telegram_bot.bot, "send_message", fake_send_message)

    result = await app.post_to_channel()

    assert result == {"ok": True, "reason": "sent"}
    assert sent and sent[0][0][0] == "@deepalpha"


@pytest.mark.asyncio
async def test_safe_send_channel_message_does_not_send_when_disabled(monkeypatch):
    calls = {"send_message": 0}
    monkeypatch.setenv("CHANNEL_POSTING_DISABLED", "true")

    async def fake_send_message(*args, **kwargs):
        calls["send_message"] += 1

    monkeypatch.setattr(app.telegram_bot.bot, "send_message", fake_send_message)

    result = await app.safe_send_channel_message("hello")

    assert result == {"ok": False, "reason": "env_disabled"}
    assert calls["send_message"] == 0
