import pytest

from services import edge_watch_service as service
from services.edge_watch_market_resolver import market_matches_question, select_best_market
from services.edge_watch_runtime_patch import install as install_edge_watch


def _analysis(system="No — 79.0%", market="Yes: 22.5% | No: 77.5%", confidence="Low", analysis_id=7):
    return {
        "id": analysis_id,
        "system_probability": system,
        "market_probability": market,
        "confidence": confidence,
    }


def _market(yes=0.225, no=0.775):
    return {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes}", "{no}"]',
        "active": True,
        "closed": False,
    }


def _row(*, watchlist_id=11, user_id=22, question="Will the event happen?", language="ru"):
    return {
        "watchlist_id": watchlist_id,
        "user_id": user_id,
        "market_slug": "example-event",
        "market_url": "https://polymarket.com/event/example-event",
        "question": question,
        "language": language,
    }


def test_probability_and_side_price_parsing():
    assert service.parse_probability_text("No — 79.0%") == ("NO", 79.0)
    assert service.parse_probability_text("YES: 21,5%") == ("YES", 21.5)
    assert service.extract_side_price(_market(), "YES") == 22.5
    assert service.extract_side_price(_market(), "NO") == 77.5


def test_decision_policy_matches_product_contract():
    assert service.classify_decision(1.5, "low", True) == "NO_TRADE"
    assert service.classify_decision(5.0, "low", True) == "WATCH"
    assert service.classify_decision(9.0, "low", True) == "WATCH"
    assert service.classify_decision(8.0, "medium", True) == "WATCH"
    assert service.classify_decision(8.01, "medium", True) == "BUY"
    assert service.classify_decision(20.0, "high", False) == "NO_TRADE"


def test_exact_market_fallback_is_not_independent():
    snapshot = service.snapshot_from_analysis(
        _analysis(system="No — 77.5%"),
        _market(),
    )

    assert snapshot is not None
    assert snapshot.independent is False
    assert snapshot.edge_pp == 0.0
    assert snapshot.decision == "NO_TRADE"


def test_price_crossing_changes_no_trade_to_watch():
    first = service.snapshot_from_analysis(_analysis(), _market())
    crossed = service.snapshot_from_analysis(_analysis(), _market(yes=0.26, no=0.74))

    assert first is not None and first.decision == "NO_TRADE"
    assert crossed is not None and crossed.decision == "WATCH"
    assert crossed.edge_pp == 5.0
    assert service.should_notify_transition(
        {"decision": first.decision, "side": first.side}, crossed
    ) is True


def test_medium_confidence_price_crossing_can_become_buy():
    snapshot = service.snapshot_from_analysis(
        _analysis(confidence="Medium"),
        _market(yes=0.30, no=0.70),
    )

    assert snapshot is not None
    assert snapshot.edge_pp == 9.0
    assert snapshot.decision == "BUY"


def test_event_resolver_rejects_wrong_first_range_and_selects_exact_submarket():
    question = "Will Donald Trump post 120-139 Truth Social posts from July 28 to August 4, 2026?"
    markets = [
        {"question": "Will Donald Trump post 100-119 Truth Social posts from July 28 to August 4, 2026?", "id": "a"},
        {"question": question, "id": "b"},
        {"question": "Will Donald Trump post 140-159 Truth Social posts from July 28 to August 4, 2026?", "id": "c"},
    ]

    assert market_matches_question(question, markets[0], minimum_score=0.90) is False
    assert market_matches_question(question, markets[1], minimum_score=0.90) is True

    selected = select_best_market(question, markets)
    assert selected is not None
    assert selected["id"] == "b"


def test_alert_explains_real_trade_transition_in_both_languages():
    snapshot = service.build_snapshot(
        side="NO",
        fair_probability=79.0,
        market_probability=74.0,
        confidence="low",
        independent=True,
        analysis_id=7,
    )
    previous = {"decision": "NO_TRADE", "market_probability": 77.5}

    ru = service.format_edge_alert(
        question="Will the event happen?",
        market_url="https://polymarket.com/event/example",
        previous=previous,
        snapshot=snapshot,
        lang="ru",
    )
    en = service.format_edge_alert(
        question="Will the event happen?",
        market_url="https://polymarket.com/event/example",
        previous=previous,
        snapshot=snapshot,
        lang="en",
    )

    assert "NO_TRADE → WATCH" in ru
    assert "Edge: +5.0 п.п." in ru
    assert "Цена рынка: 74.0%" in ru
    assert "BUY пока недоступен" in ru
    assert "Decision changed: NO_TRADE → WATCH" in en
    assert "Market price: 74.0%" in en
    assert "BUY remains blocked" in en


@pytest.mark.asyncio
async def test_worker_initializes_silently_then_notifies_on_transition(monkeypatch):
    row = _row()
    previous = {
        "watchlist_id": 11,
        "side": "NO",
        "decision": "NO_TRADE",
        "market_probability": 77.5,
        "last_notification_fingerprint": None,
    }
    updates = []
    messages = []

    class FakeBot:
        async def send_message(self, user_id, text, **kwargs):
            messages.append((user_id, text, kwargs))

    monkeypatch.setattr(service, "init_edge_watch_schema", lambda: None)
    monkeypatch.setattr(service, "get_active_edge_watch_rows", lambda limit=500: [row])
    monkeypatch.setattr(
        service,
        "resolve_watch_market",
        lambda market_slug, market_url, question: _market(yes=0.26, no=0.74),
    )
    monkeypatch.setattr(service, "is_market_resolved", lambda market: False)
    monkeypatch.setattr(service, "get_latest_analysis", lambda *args: _analysis())
    monkeypatch.setattr(service, "get_edge_state", lambda watchlist_id: previous)
    monkeypatch.setattr(service, "charge_edge_transition", lambda *args: {"charged": False, "reason": "edge_alert_included"})
    monkeypatch.setattr(service, "upsert_edge_state", lambda **kwargs: updates.append(kwargs))
    monkeypatch.setattr(service.asyncio, "sleep", lambda *_args, **_kwargs: _completed_sleep())

    stats = await service.check_edge_watch_once(FakeBot())

    assert stats == {"rows": 1, "initialized": 0, "notified": 1, "errors": 0}
    assert len(messages) == 1
    assert messages[0][0] == 22
    assert "NO_TRADE → WATCH" in messages[0][1]
    assert updates[-1]["last_notified_decision"] == "WATCH"
    assert updates[-1]["last_notification_fingerprint"].startswith("edge:11:NO_TRADE->WATCH")


@pytest.mark.asyncio
async def test_same_event_slug_rows_are_processed_as_distinct_contracts(monkeypatch):
    rows = [
        _row(watchlist_id=11, user_id=22, question="Range 100-119", language="ru"),
        _row(watchlist_id=12, user_id=23, question="Range 120-139", language="en"),
    ]
    resolved_questions = []
    analysis_questions = []
    messages = []

    class FakeBot:
        async def send_message(self, user_id, text, **kwargs):
            messages.append((user_id, text))

    monkeypatch.setattr(service, "init_edge_watch_schema", lambda: None)
    monkeypatch.setattr(service, "get_active_edge_watch_rows", lambda limit=500: rows)

    def resolve(market_slug, market_url, question):
        resolved_questions.append(question)
        return _market(yes=0.26, no=0.74)

    def latest(user_id, market_slug, question):
        analysis_questions.append((user_id, question))
        return _analysis()

    monkeypatch.setattr(service, "resolve_watch_market", resolve)
    monkeypatch.setattr(service, "is_market_resolved", lambda market: False)
    monkeypatch.setattr(service, "get_latest_analysis", latest)
    monkeypatch.setattr(
        service,
        "get_edge_state",
        lambda watchlist_id: {
            "watchlist_id": watchlist_id,
            "side": "NO",
            "decision": "NO_TRADE",
            "market_probability": 77.5,
            "last_notification_fingerprint": None,
        },
    )
    monkeypatch.setattr(service, "charge_edge_transition", lambda *args: {"charged": False})
    monkeypatch.setattr(service, "upsert_edge_state", lambda **kwargs: None)
    monkeypatch.setattr(service.asyncio, "sleep", lambda *_args, **_kwargs: _completed_sleep())

    stats = await service.check_edge_watch_once(FakeBot())

    assert stats["rows"] == 2
    assert stats["notified"] == 2
    assert resolved_questions == ["Range 100-119", "Range 120-139"]
    assert analysis_questions == [(22, "Range 100-119"), (23, "Range 120-139")]
    assert "Решение изменилось" in messages[0][1]
    assert "Decision changed" in messages[1][1]


def test_edge_alerts_are_included_without_second_charge(monkeypatch):
    monkeypatch.delenv("EDGE_WATCH_BILLING_ENABLED", raising=False)
    assert service.charge_edge_transition(1, 2, "slug", "fingerprint") == {
        "charged": False,
        "reason": "edge_alert_included",
        "cost": 0,
    }


def test_runtime_patch_wraps_existing_watchlist_worker():
    class FakeBot:
        pass

    async def legacy_worker():
        return None

    class FakeTelegram:
        bot = FakeBot()

    class FakeApp:
        watchlist_worker = staticmethod(legacy_worker)
        telegram_bot = FakeTelegram()

    install_edge_watch(FakeApp)

    assert getattr(FakeApp.watchlist_worker, "_deepalpha_edge_watch", False) is True


async def _completed_sleep():
    return None
