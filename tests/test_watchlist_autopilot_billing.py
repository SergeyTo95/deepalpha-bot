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


def test_watchlist_ai_settings_defaults_exist():
    assert '("watchlist_ai_summary_enabled", "on")' in DB_SOURCE
    assert '("watchlist_ai_summary_max_bullets", "3")' in DB_SOURCE


def test_watchlist_ai_called_after_charge_in_source():
    body = _function_body(APP_SOURCE, "_check_subscriber_notifications")
    charge_pos = body.index('charge = charge_watchlist_event(user_id, watchlist_id')
    insufficient_pos = body.index('if charge.get("reason") == "insufficient_tokens":', charge_pos)
    ai_pos = body.index('build_watchlist_ai_summary(', insufficient_pos)
    assert charge_pos < insufficient_pos < ai_pos


def test_insufficient_tokens_block_does_not_call_ai_summary_in_source():
    body = _function_body(APP_SOURCE, "_check_subscriber_notifications")
    insufficient_block = body.split('if charge.get("reason") == "insufficient_tokens":', 1)[1].split("return", 1)[0]
    assert "build_watchlist_ai_summary" not in insufficient_block


def test_watchlist_alerts_append_deepalpha_view_in_source():
    body = _function_body(APP_SOURCE, "_check_subscriber_notifications")
    resolved_body = _function_body(APP_SOURCE, "_handle_resolved_market")
    assert body.count('format_watchlist_ai_summary(ai_summary)') >= 2
    assert 'event_type="probability_change"' in body
    assert 'event_type="closing_soon"' in body
    assert 'event_type="resolved_recap"' in resolved_body


def test_provider_failure_returns_fallback_summary(monkeypatch):
    import services.watchlist_ai_summary_service as svc

    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(svc, "get_setting", lambda key, default=None: default)

    result = svc.build_watchlist_ai_summary(
        "probability_change",
        "Will event happen?",
        initial_probability=40,
        current_probability=56,
        probability_change=16,
        lang="en",
    )
    assert result["fallback"] is True
    assert result["summary"]
    assert result["label"] in svc.SAFE_LABELS


def test_forbidden_words_not_present_in_fallback(monkeypatch):
    import services.watchlist_ai_summary_service as svc

    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(svc, "get_setting", lambda key, default=None: default)

    result = svc.build_watchlist_ai_summary("closing_soon", "Question", current_probability=51, closing_hours=6, lang="en")
    text = " ".join([result["summary"], result["label"], " ".join(result["watch_next"])])
    lowered = text.lower()
    assert "100%" not in lowered
    for word in ("bet", "buy", "guaranteed"):
        assert word not in lowered
