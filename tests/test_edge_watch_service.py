import os

import pytest

from services import edge_watch_service as service
from services.edge_watch_market_resolver import select_best_market
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


def test_event_resolver_selects_exact_truth_social_submarket():
    markets = [
        {"question": "Will Donald Trump post 100-119 Truth Social posts from July 28 to August 4, 2026?", "id": "a"},
        {"question": "Will Donald Trump post 120-139 Truth Social posts from July 28 to August 4, 2026?", "id": "b"},
        {"question": "Will Donald Trump post 140-159 Truth Social posts from July 28 to August 4, 2026?", "id": "c"},
    ]

    selected = select_best_market(
        "Will Donald Trump post 120-139 Truth Social posts from July 28 to August 4, 2026?",
        markets,
    )

    assert selected is not None
    assert selected["id"] == "b"


def test_alert_explains_real_trade_transition():
    snapshot = service.build_snapshot(
        side="NO",
        fair_probability=79.0,
        market_probability=74.0,
        confidence="low",
        independent=True,
        analysis_id=7,
    )
    text = service.format_edge_alert(
        question="Will the event happen?",
        market_url="https://polymarket.com/event/example",
        previous={"decision": "NO_TRADE", "market_probability": 77.5},
        snapshot=snapshot,
        lang="ru",
    )

    assert "NO_TRADE → WATCH" in text
    assert "Edge: +5.0 п.п." in text
    assert "Цена рынка: 74.0%" in text
    assert "BUY пока недоступен" in text


@pytest.mark.asyncio
async def test_worker_initializes_silently_then_notifies_on_transition(monkeypatch):
    item = {
        "market_slug": "example",
        "question": "Will the event happen?",
        "market_url": "https://polymarket.com/event/example",
    }
    sub = {"id": 11, "user_id": 22, "notify_enabled": True, "lang": "ru"}
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
    monkeypatch.setattr(service, "get_active_watchlist_items", lambda limit=500: [item])
    monkeypatch.setattr(service, "get_watchlist_subscribers", lambda slug: [sub])
    monkeypatch.setattr(service, "fetch_market_by_slug", lambda slug: _market(yes=0.26, no=0.74))
    monkeypatch.setattr(service, "is_market_resolved", lambda market: False)
    monkeypatch.setattr(service, "get_latest_analysis", lambda *args: _analysis())
    monkeypatch.setattr(service, "get_edge_state", lambda watchlist_id: previous)
    monkeypatch.setattr(service, "charge_watchlist_event", lambda *args: {"charged": True, "cost": 1})
    monkeypatch.setattr(service, "upsert_edge_state", lambda **kwargs: updates.append(kwargs))
    monkeypatch.setattr(service.asyncio, "sleep", lambda *_args, **_kwargs: _completed_sleep())

    stats = await service.check_edge_watch_once(FakeBot())

    assert stats["notified"] == 1
    assert len(messages) == 1
    assert messages[0][0] == 22
    assert "NO_TRADE → WATCH" in messages[0][1]
    assert updates[-1]["last_notified_decision"] == "WATCH"
    assert updates[-1]["last_notification_fingerprint"].startswith("edge:11:NO_TRADE->WATCH")


def test_runtime_patch_includes_edge_alerts_without_second_charge(monkeypatch):
    class FakeBot:
        pass

    async def legacy_worker():
        return None

    class FakeTelegram:
        bot = FakeBot()

    class FakeApp:
        watchlist_worker = staticmethod(legacy_worker)
        telegram_bot = FakeTelegram()

    monkeypatch.delenv("EDGE_WATCH_BILLING_ENABLED", raising=False)
    original_charge = service.charge_watchlist_event
    original_fetch = service.fetch_market_by_slug
    try:
        install_edge_watch(FakeApp)
        charge = service.charge_watchlist_event(1, 2, "slug", "edge_transition", "fp")
        assert charge == {"charged": False, "reason": "edge_alert_included", "cost": 0}
        assert getattr(FakeApp.watchlist_worker, "_deepalpha_edge_watch", False) is True
    finally:
        service.charge_watchlist_event = original_charge
        service.fetch_market_by_slug = original_fetch


async def _completed_sleep():
    return None
