from pathlib import Path

DB_SOURCE = Path("db/database.py").read_text()
APP_SOURCE = Path("app.py").read_text()


def _function_body(source: str, name: str) -> str:
    marker = f"def {name}"
    start = source.index(marker)
    next_start = source.find("\ndef ", start + len(marker))
    if next_start == -1:
        next_start = len(source)
    return source[start:next_start]


def test_get_active_watchlist_items_returns_all_open_markets_for_resolution_checks():
    body = _function_body(DB_SOURCE, "get_active_watchlist_items")

    assert "WHERE is_closed = 0" in body
    assert "notify_enabled" not in body
    assert "autopilot_enabled" not in body
    assert "billing_status" not in body


def test_get_watchlist_subscribers_filters_paid_alert_delivery_only():
    body = _function_body(DB_SOURCE, "get_watchlist_subscribers")

    assert "WHERE market_slug = %s AND is_closed = 0" in body
    assert "COALESCE(notify_enabled, 1) = 1" in body
    assert "COALESCE(autopilot_enabled, 1) = 1" in body
    assert "billing_status IS NULL OR billing_status = 'active'" in body


def test_resolved_insufficient_tokens_uses_terminal_message_without_resume_pause():
    body = _function_body(APP_SOURCE, "_handle_resolved_market")
    insufficient_block = body.split('if charge.get("reason") == "insufficient_tokens":', 1)[1].split("continue", 1)[0]

    assert "send_watchlist_resolved_insufficient_tokens_message" in insufficient_block
    assert "mark_watchlist_notified" in insufficient_block
    assert "send_watchlist_pause_message" not in insufficient_block


def test_resolved_insufficient_message_has_buy_tokens_but_no_resume_watcher():
    body = _function_body(APP_SOURCE, "send_watchlist_resolved_insufficient_tokens_message")

    assert "Buy tokens / cashier" in APP_SOURCE
    assert "Resume watcher" not in body
    assert "Рынок закрыт и удалён из watchlist" in body
    assert "The market was closed and removed from your watchlist" in body


def test_probability_change_insufficient_tokens_keeps_generic_pause_resume_message():
    body = _function_body(APP_SOURCE, "_check_subscriber_notifications")
    insufficient_block = body.split('if charge.get("reason") == "insufficient_tokens":', 1)[1].split("return", 1)[0]

    assert "send_watchlist_pause_message" in insufficient_block
    assert "send_watchlist_resolved_insufficient_tokens_message" not in insufficient_block
