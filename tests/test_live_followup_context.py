from datetime import datetime, timedelta, timezone

from services import live_context_memory as memory


def setup_function():
    memory.clear_live_context_memory()


def test_crypto_followup_with_previous_btc_context():
    memory.save_live_context(
        1,
        mode="crypto",
        original_user_text="BTCUSDT 15m есть вход?",
        normalized_query="BTCUSDT 15m есть вход?",
        asset_pair="BTCUSDT",
        timeframe="15m",
        key_levels={"support": [62000], "resistance": [62500, 63095]},
        last_final_answer="Decision: WATCH",
    )

    result = memory.resolve_live_followup(1, "а если лонг от 64500?")

    assert result["is_followup"] is True
    assert result["followup_type"] == "long_position"
    assert result["followup_level"] == "64500"
    assert result["followup_timeframe"] == "15m"
    assert "BTCUSDT" in result["resolved_query"]
    assert "15m" in result["resolved_query"]
    assert "LONG POSITION" in result["resolved_query"]
    assert "not a long-term forecast" in result["resolved_query"]
    assert "long scenario from 64500" not in result["resolved_query"]


def test_crypto_timeframe_followup_mentions_new_timeframe_and_previous_context():
    memory.save_live_context(2, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", last_final_answer="Decision: WATCH")

    result = memory.resolve_live_followup(2, "а на 5m лучше?")

    assert result["is_followup"] is True
    assert result["previous_context"]["asset_pair"] == "BTCUSDT"
    assert "BTCUSDT" in result["resolved_query"]
    assert "5m" in result["resolved_query"]


def test_sports_odds_followup_uses_previous_event_market_and_new_odds():
    memory.save_live_context(3, mode="sports", original_user_text="Lakers — Celtics handicap", normalized_query="Lakers — Celtics handicap", teams_event="Lakers — Celtics", market="handicap", last_final_answer="Decision: WATCH")

    result = memory.resolve_live_followup(3, "а кэф 1.95?")

    assert result["mode"] == "sports"
    assert "Lakers — Celtics" in result["resolved_query"]
    assert "1.95" in result["resolved_query"]
    assert "handicap" in result["resolved_query"]


def test_missing_context_returns_need_context_without_resolved_asset():
    result = memory.resolve_live_followup(4, "а где стоп?")

    assert result["is_followup"] is True
    assert result["need_context"] is True
    assert "предыдущий Live-контекст" in result["message"]
    assert "BTC" not in result.get("resolved_query", "")


def test_context_ttl_expires_and_requests_refresh():
    memory.save_live_context(5, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", last_final_answer="Decision: WATCH")
    memory._contexts[5]["updated_at"] = datetime.now(timezone.utc) - timedelta(minutes=61)

    result = memory.resolve_live_followup(5, "а где стоп?")

    assert result["is_followup"] is True
    assert result["need_context"] is True
    assert "предыдущий Live-контекст" in result["message"]
