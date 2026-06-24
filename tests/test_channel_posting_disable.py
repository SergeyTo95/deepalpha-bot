import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("aiogram")
import app


@pytest.mark.parametrize("value", ["off", "false", "0", "no", "disabled"])
def test_is_channel_posting_enabled_db_disabled_values(monkeypatch, value):
    monkeypatch.delenv("CHANNEL_POSTING_DISABLED", raising=False)
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: value)

    assert app.is_channel_posting_enabled() is False


def test_is_channel_posting_enabled_env_hard_kill_overrides_db_enabled(monkeypatch):
    monkeypatch.setenv("CHANNEL_POSTING_DISABLED", "true")
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: "on")

    assert app.is_channel_posting_enabled() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_post_to_channel_disabled_does_not_fetch_or_send(monkeypatch, force):
    calls = {"list_events": 0, "send_message": 0}

    def fake_list_events(*args, **kwargs):
        calls["list_events"] += 1
        return []

    async def fake_send_message(*args, **kwargs):
        calls["send_message"] += 1

    monkeypatch.delenv("CHANNEL_POSTING_DISABLED", raising=False)
    monkeypatch.setattr(app, "CHANNEL_ID", "@deepalpha")
    monkeypatch.setattr(app, "get_setting", lambda key, default=None: "off" if key == "channel_posting_enabled" else default)
    monkeypatch.setattr(app, "list_events", fake_list_events)
    monkeypatch.setattr(app.telegram_bot.bot, "send_message", fake_send_message)

    result = await app.post_to_channel(force=force)

    assert result == {"ok": False, "reason": "db_disabled"}
    assert calls == {"list_events": 0, "send_message": 0}


@pytest.mark.asyncio
async def test_post_to_channel_force_enabled_sends_message(monkeypatch):
    sent = []
    settings = {
        "channel_posting_enabled": "on",
        "channel_shown_markets": "",
        "channel_last_category": "",
    }

    async def fake_send_message(*args, **kwargs):
        sent.append((args, kwargs))

    def fake_get_setting(key, default=None):
        return settings.get(key, default)

    def fake_set_setting(key, value):
        settings[key] = value

    event = {"markets": [{"active": True, "closed": False, "outcomePrices": [0.55, 0.45]}]}
    normalized = {
        "id": "event-1",
        "question": "Will Bitcoin close above one hundred thousand dollars this month?",
        "market_probability": "55%",
        "url": "https://polymarket.com/event/event-1",
    }

    monkeypatch.delenv("CHANNEL_POSTING_DISABLED", raising=False)
    monkeypatch.setattr(app, "CHANNEL_ID", "@deepalpha")
    monkeypatch.setattr(app, "get_setting", fake_get_setting)
    monkeypatch.setattr(app, "set_setting", fake_set_setting)
    monkeypatch.setattr(app, "list_events", lambda limit=50: [event])
    monkeypatch.setattr(app, "normalize_event_for_channel", lambda item: normalized)
    monkeypatch.setattr(app.random, "choice", lambda items: items[0])
    monkeypatch.setattr(app.telegram_bot.bot, "send_message", fake_send_message)

    result = await app.post_to_channel(force=True)

    assert result == {"ok": True, "reason": "sent"}
    assert len(sent) == 1
    assert sent[0][0][0] == "@deepalpha"
    assert "Will Bitcoin close above" in sent[0][0][1]


def test_no_direct_channel_send_message_outside_post_to_channel():
    source = Path(app.__file__).read_text()
    post_start = source.index("async def post_to_channel")
    worker_start = source.index("async def channel_worker")
    outside_post_to_channel = source[:post_start] + source[worker_start:]

    assert "send_message(\n            CHANNEL_ID" not in outside_post_to_channel
    assert "send_message(CHANNEL_ID" not in outside_post_to_channel
