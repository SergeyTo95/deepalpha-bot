import asyncio
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(get=lambda *args, **kwargs: None, post=lambda *args, **kwargs: None))

import services.polymarket_service as svc


WORLD_CUP_PAYLOAD = {
    "market_title_original": "Победитель Кубка мира",
    "market_title_canonical": "World Cup Winner",
    "outcomes_original": ["Испания", "Франция", "Португалия", "Англия"],
    "outcomes_canonical": ["Spain", "France", "Portugal", "England"],
    "category_canonical": "Sports · Football",
    "visible_prices": [
        {"outcome_original": "Испания", "outcome_canonical": "Spain", "probability": 16.7},
        {"outcome_original": "Франция", "outcome_canonical": "France", "probability": 16.4},
        {"outcome_original": "Португалия", "outcome_canonical": "Portugal", "probability": 11.8},
        {"outcome_original": "Англия", "outcome_canonical": "England", "probability": 9.7},
    ],
}


def _market(entity):
    return {
        "id": entity.lower(),
        "question": f"Will {entity} win the 2026 FIFA World Cup?",
        "outcomes": ["Yes", "No"],
        "outcomePrices": [0.15, 0.85],
        "active": True,
        "closed": False,
        "slug": f"will-{entity.lower()}-win-2026-fifa-world-cup",
        "eventSlug": "2026-fifa-world-cup-winner",
    }


def test_detect_world_cup_screenshot_as_outright_event():
    assert svc._is_outright_event_screenshot(WORLD_CUP_PAYLOAD) is True


def test_binary_candidate_title_matches_screenshot_entity():
    matched, entity = svc._candidate_matches_screenshot_entity(WORLD_CUP_PAYLOAD, _market("Spain"))
    assert matched is True
    assert entity == "Spain"


def test_rihanna_still_rejected():
    matched, entity = svc._candidate_matches_screenshot_entity(
        WORLD_CUP_PAYLOAD,
        {"question": "New Rihanna Album before GTA VI?", "outcomes": ["Yes", "No"]},
    )
    assert matched is False
    assert entity is None


def test_spain_snap_election_rejected():
    matched, entity = svc._candidate_matches_screenshot_entity(
        WORLD_CUP_PAYLOAD,
        {"question": "Spain snap election called by July 2026?", "outcomes": ["Yes", "No"]},
    )
    assert matched is False
    assert entity is None


def test_event_bundle_strong(monkeypatch):
    monkeypatch.setattr(svc, "_search_events_for_title", lambda *a, **k: [])
    markets = [_market(x) for x in ["Spain", "France", "Portugal", "England"]]
    monkeypatch.setattr(svc, "list_markets", lambda *a, **k: markets)
    result = asyncio.run(svc.resolve_outright_event_bundle_from_screenshot(WORLD_CUP_PAYLOAD))
    assert result["type"] == "event_bundle"
    assert result["confidence"] == "strong"
    assert result["matched_entities_count"] == 4
    assert [m["visible_probability"] for m in result["markets"]] == [16.7, 16.4, 11.8, 9.7]
    assert result["event_url"] == "https://polymarket.com/event/2026-fifa-world-cup-winner"
    assert result["market_url"] == "https://polymarket.com/event/2026-fifa-world-cup-winner"
    for market in result["markets"]:
        assert market["entity"]
        assert market["outcome_name"]
        assert market["candidate_title"].startswith("Will ")
        assert market["candidate_slug"].startswith("will-")
        assert market["market_url"].startswith("https://polymarket.com/market/")
        assert market["current_probability"] == 15.0
        assert market["event_slug"] == "2026-fifa-world-cup-winner"
        assert market["event_url"] == "https://polymarket.com/event/2026-fifa-world-cup-winner"


def test_event_bundle_medium(monkeypatch):
    monkeypatch.setattr(svc, "_search_events_for_title", lambda *a, **k: [])
    markets = [_market(x) for x in ["Spain", "France"]]
    monkeypatch.setattr(svc, "list_markets", lambda *a, **k: markets)
    result = asyncio.run(svc.resolve_outright_event_bundle_from_screenshot(WORLD_CUP_PAYLOAD))
    assert result["confidence"] == "medium"
    assert result["matched_entities_count"] == 2


def test_event_bundle_no_match(monkeypatch):
    monkeypatch.setattr(svc, "_search_events_for_title", lambda *a, **k: [])
    monkeypatch.setattr(svc, "list_markets", lambda *a, **k: [{"question": "New Rihanna Album before GTA VI?", "outcomes": ["Yes", "No"]}])
    assert asyncio.run(svc.resolve_outright_event_bundle_from_screenshot(WORLD_CUP_PAYLOAD)) is None


def test_single_market_behavior_unchanged(monkeypatch):
    candidate = {
        "id": "world-cup-winner",
        "question": "2026 FIFA World Cup Winner",
        "outcomes": ["Spain", "France", "Portugal", "England"],
        "active": True,
        "closed": False,
        "slug": "2026-fifa-world-cup-winner",
    }
    monkeypatch.setattr(svc, "_search_events_for_title", lambda *a, **k: [])
    monkeypatch.setattr(svc, "list_markets", lambda *a, **k: [candidate])
    result = asyncio.run(svc.resolve_polymarket_market_from_screenshot(WORLD_CUP_PAYLOAD))
    assert result["type"] if "type" in result else "single" == "single"
    assert result["match_strength"] == "strong"


def test_no_invented_event_url_without_shared_event_slug(monkeypatch):
    a = _market("Spain"); a.pop("eventSlug"); a.pop("slug")
    b = _market("France"); b.pop("eventSlug"); b.pop("slug")
    monkeypatch.setattr(svc, "_search_events_for_title", lambda *a, **k: [])
    monkeypatch.setattr(svc, "list_markets", lambda *args, **k: [a, b])
    result = asyncio.run(svc.resolve_outright_event_bundle_from_screenshot(WORLD_CUP_PAYLOAD))
    assert result["market_url"] is None
    assert result["url"] == ""
