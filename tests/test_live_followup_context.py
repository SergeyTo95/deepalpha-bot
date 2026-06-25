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


def test_reconstruct_crypto_context_from_recent_messages_and_resolve_followup():
    recent = [
        {"role": "user", "content": "BTCUSDT 15m есть вход?"},
        {"role": "assistant", "content": "Данные:\n\n- Цена: $59,670\n- Поддержка: $59,500 / $59,339\n- Сопротивление: $60,000 / $63,239\n- Зона лучше: $59,500\n  Decision: WATCH"},
    ]

    reconstructed = memory.reconstruct_live_context_from_recent_messages(recent, 11)

    assert reconstructed is not None
    assert reconstructed["mode"] == "crypto"
    assert reconstructed["asset_pair"] == "BTCUSDT"
    assert reconstructed["timeframe"] == "15m"
    assert reconstructed["key_levels"]["current_price"] == 59670
    assert reconstructed["key_levels"]["support"] == [59500, 59339]
    assert reconstructed["key_levels"]["resistance"] == [60000, 63239]

    memory.save_live_context(11, **{k: v for k, v in reconstructed.items() if k != "user_id"})
    result = memory.resolve_live_followup(11, "а если лонг от 64500?")

    assert result.get("need_context") is not True
    assert "LONG POSITION" in result["resolved_query"]
    assert "64500" in result["resolved_query"]
    assert result["followup_type"] == "long_position"
    assert result["followup_level"] == "64500"


def test_reconstruct_crypto_answer_without_pair_does_not_hallucinate_btc():
    recent = [
        {"role": "user", "content": "15m есть вход?"},
        {"role": "assistant", "content": "- Цена: $59,670\n- Поддержка: $59,500 / $59,339\nDecision: WATCH"},
    ]

    reconstructed = memory.reconstruct_live_context_from_recent_messages(recent, 12)

    assert reconstructed is None
    result = memory.resolve_live_followup(12, "а где стоп?")
    assert result["need_context"] is True
    assert "BTC" not in result.get("resolved_query", "")


def test_exit_live_does_not_clear_context_memory():
    memory.save_live_context(13, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", last_final_answer="Decision: WATCH")

    result = memory.resolve_live_followup(13, "а где стоп?")

    assert result.get("need_context") is not True
    assert "BTCUSDT" in result["resolved_query"]


def test_reset_live_clears_context_memory():
    memory.save_live_context(14, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", last_final_answer="Decision: WATCH")
    memory.clear_live_context(14)

    result = memory.resolve_live_followup(14, "а где стоп?")

    assert result["need_context"] is True


def test_extract_money_values_handles_thousands_and_decimals():
    assert memory._extract_money_values("$60,219.51") == [60219.51]
    assert memory._extract_money_values("$59,670") == [59670]
    assert memory._extract_money_values("$60,500.0") == [60500.0]


def _crypto_actions():
    return [
        {"id": "invalidation_confirmation", "label": "Find invalidation and confirmation", "resolved_query_template": "Identify confirmation, invalidation, and risk."},
        {"id": "timeframe_compare", "label": "Compare 5m/15m/1h", "resolved_query_template": "Compare timeframes."},
        {"id": "scenario_plan", "label": "Build a step by step plan", "resolved_query_template": "Build a plan."},
    ]


def _sports_actions():
    return [
        {"id": "calculate_value", "label": "calculate value", "resolved_query_template": "Calculate implied probability, edge, and minimum playable odds."},
        {"id": "compare_markets", "label": "compare markets", "resolved_query_template": "Compare moneyline, handicap, and total."},
    ]


def test_ru_generic_continuation_selects_first_action():
    memory.save_live_context(21, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", suggested_actions=_crypto_actions())
    result = memory.resolve_live_followup(21, "давай")

    assert result["is_followup"] is True
    assert result["selected_action_id"] == "invalidation_confirmation"
    assert "BTCUSDT" in result["resolved_query"]


def test_ru_ordinal_continuation_selects_second_action():
    memory.save_live_context(22, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", suggested_actions=_crypto_actions())
    result = memory.resolve_live_followup(22, "второй")

    assert result["selected_action_id"] == "timeframe_compare"


def test_ru_calculate_value_selects_sports_value_action():
    memory.save_live_context(23, mode="sports", original_user_text="Lakers — Celtics handicap", normalized_query="Lakers — Celtics handicap", teams_event="Lakers — Celtics", market="handicap", suggested_actions=_sports_actions())
    result = memory.resolve_live_followup(23, "посчитай")

    assert result["selected_action_id"] == "calculate_value"
    assert "implied probability" in result["resolved_query"]
    assert "edge" in result["resolved_query"]
    assert "odds" in result["resolved_query"]


def test_ru_stop_selects_invalidation_confirmation_action():
    memory.save_live_context(24, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", suggested_actions=_crypto_actions())
    result = memory.resolve_live_followup(24, "где стоп?")

    assert result["selected_action_id"] == "invalidation_confirmation"


def test_ru_timeframe_selects_timeframe_compare_action():
    memory.save_live_context(25, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", suggested_actions=_crypto_actions())
    result = memory.resolve_live_followup(25, "на 5m")

    assert result["selected_action_id"] == "timeframe_compare"


def test_en_generic_and_ordinal_continuations_select_actions():
    memory.save_live_context(26, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", suggested_actions=_crypto_actions())

    assert memory.resolve_live_followup(26, "yes")["selected_action_id"] == "invalidation_confirmation"
    assert memory.resolve_live_followup(26, "second")["selected_action_id"] == "timeframe_compare"


def test_en_value_and_stop_select_expected_actions():
    memory.save_live_context(27, mode="sports", original_user_text="Lakers — Celtics handicap", normalized_query="Lakers — Celtics handicap", teams_event="Lakers — Celtics", market="handicap", suggested_actions=_sports_actions())
    assert memory.resolve_live_followup(27, "calculate edge")["selected_action_id"] == "calculate_value"

    memory.save_live_context(28, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", suggested_actions=_crypto_actions())
    assert memory.resolve_live_followup(28, "where is stop")["selected_action_id"] == "invalidation_confirmation"


def test_generic_confirmation_without_suggested_actions_needs_context():
    memory.save_live_context(29, mode="crypto", original_user_text="ETHUSDT 1h", normalized_query="ETHUSDT 1h", asset_pair="ETHUSDT", timeframe="1h")
    result = memory.resolve_live_followup(29, "давай")

    assert result["need_context"] is True
    assert "что именно продолжить" in result["message"]
    assert "BTC" not in result.get("resolved_query", "")
    assert "ETH" not in result.get("resolved_query", "")


def test_continuation_resolved_queries_do_not_use_forbidden_terms():
    forbidden = ("покупай", "продавай", "ставь", "бери", "гарантирую", "железно", "buy now", "sell now", "bet now", "guaranteed", "lock")
    memory.save_live_context(30, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", suggested_actions=_crypto_actions())
    memory.save_live_context(31, mode="sports", original_user_text="Lakers — Celtics handicap", normalized_query="Lakers — Celtics handicap", teams_event="Lakers — Celtics", market="handicap", suggested_actions=_sports_actions())

    texts = [
        memory.resolve_live_followup(30, "давай")["resolved_query"],
        memory.resolve_live_followup(30, "where is stop")["resolved_query"],
        memory.resolve_live_followup(31, "calculate edge")["resolved_query"],
    ]
    assert all(term not in text.lower() for text in texts for term in forbidden)


def test_standalone_sports_short_request_is_not_live_continuation():
    assert memory.looks_like_new_standalone_live_request("Lakers Celtics тотал") is True
    assert memory.is_live_continuation("Lakers Celtics тотал") is False
    assert memory.is_live_followup("Lakers Celtics тотал") is False
    assert memory.is_live_followup("Brazil фора") is False
    assert memory.is_live_followup("UFC total") is False


def test_standalone_crypto_short_request_is_not_live_continuation():
    assert memory.looks_like_new_standalone_live_request("BTCUSDT 15m есть вход?") is True
    assert memory.is_live_continuation("BTCUSDT 15m есть вход?") is False
    assert memory.is_live_followup("BTCUSDT 15m есть вход?") is False
    assert memory.is_live_followup("ETHUSDT short 1h") is False


def test_pure_confirmation_without_context_still_requests_live_context():
    result = memory.resolve_live_followup(41, "давай")

    assert memory.is_live_continuation("давай") is True
    assert result["is_followup"] is True
    assert result["need_context"] is True
    assert "предыдущий Live-контекст" in result["message"]


def test_crypto_compare_ru_selects_timeframe_compare_and_strengthens_query():
    memory.save_live_context(
        51,
        mode="crypto",
        original_user_text="BTCUSDT 15m",
        normalized_query="BTCUSDT 15m",
        asset_pair="BTCUSDT",
        timeframe="15m",
        suggested_actions=_crypto_actions(),
    )

    result = memory.resolve_live_followup(51, "сравни")

    assert result["selected_action_id"] == "timeframe_compare"
    assert "BTCUSDT" in result["resolved_query"]
    assert "5m" in result["resolved_query"]
    assert "15m" in result["resolved_query"]
    assert "1h" in result["resolved_query"]
    assert "not a repeated single-timeframe answer" in result["resolved_query"]


def test_crypto_compare_en_selects_timeframe_compare():
    memory.save_live_context(
        52,
        mode="crypto",
        original_user_text="BTCUSDT 15m",
        normalized_query="BTCUSDT 15m",
        asset_pair="BTCUSDT",
        timeframe="15m",
        suggested_actions=_crypto_actions(),
    )

    result = memory.resolve_live_followup(52, "compare")

    assert result["selected_action_id"] == "timeframe_compare"
