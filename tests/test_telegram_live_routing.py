from pathlib import Path


TELEGRAM_BOT = Path("telegram_bot.py")
POLYMARKET_FALLBACK_RU = "Отправь ссылку Polymarket"


def _source() -> str:
    return TELEGRAM_BOT.read_text()


def _function_block(source: str, function_name: str) -> str:
    marker = f"async def {function_name}"
    start = source.index(marker)
    next_decorator = source.find("\n@dp.", start + len(marker))
    if next_decorator == -1:
        return source[start:]
    return source[start:next_decorator]


def test_live_mode_btc_text_stops_before_polymarket_fallback():
    src = _source()
    live_block = _function_block(src, "live_text_handler")

    assert "process_live_text(uid, text" in live_block
    assert "_send_live_final_chunks" in live_block
    assert "live_text_handled_stop_propagation user_id=%s ok=%s charged=%s" in live_block
    assert live_block.rstrip().endswith("return")
    assert POLYMARKET_FALLBACK_RU not in live_block


def test_live_mode_unavailable_still_stops_before_polymarket_fallback():
    src = _source()
    live_block = _function_block(src, "live_text_handler")

    unavailable_pos = live_block.index("LIVE_UNAVAILABLE_MESSAGE")
    send_pos = live_block.index("_send_live_final_chunks")
    log_pos = live_block.index("live_text_handled_stop_propagation")
    return_pos = live_block.rindex("return")

    assert unavailable_pos < send_pos < log_pos < return_pos
    assert POLYMARKET_FALLBACK_RU not in live_block


def test_non_live_ordinary_text_still_gets_polymarket_prompt():
    src = _source()
    fallback_block = _function_block(src, "fallback_handler")

    assert "is_live_session_active(message.from_user.id)" in fallback_block
    assert "return" in fallback_block.split("is_live_session_active(message.from_user.id)", 1)[1]
    assert 't(message.from_user.id, "fallback")' in fallback_block

    # Ensure the localized generic prompt remains configured for non-Live fallback users.
    assert '"fallback": "Отправь ссылку Polymarket или используй кнопки 👇"' in src
