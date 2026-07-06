from pathlib import Path

TELEGRAM_BOT_SOURCE = Path(__file__).resolve().parents[1] / "telegram_bot.py"


def test_live_success_keyboard_preserves_live_keyboard_with_share_helper():
    source = TELEGRAM_BOT_SOURCE.read_text()
    helper_start = source.index("def get_live_analyst_keyboard_with_share")
    helper_end = source.index("def get_help_keyboard", helper_start)
    helper = source[helper_start:helper_end]

    assert "get_live_analyst_keyboard(user_id)" in helper
    assert "📤 Поделиться инсайтом" in helper
    assert "📤 Share insight" in helper

    live_handler_start = source.index("async def live_text_handler")
    live_handler_end = source.index("logger.info(\n        \"live_text_handled_stop_propagation", live_handler_start)
    live_handler = source[live_handler_start:live_handler_end]

    assert "get_live_analyst_keyboard_with_share(uid)" in live_handler
    assert "get_airdrop_share_keyboard(uid)" not in live_handler
