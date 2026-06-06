import asyncio

from services import polymarket_service as svc


def test_market_title_similarity_exact_match():
    title = "What will Dr. Oz say during the next White House press briefing?"

    assert svc._market_title_similarity(title, title) == 1.0


def test_market_title_similarity_extracted_contained_in_candidate():
    extracted = "Dr. Oz say during the next White House press briefing"
    candidate = "What will Dr. Oz say during the next White House press briefing?"

    assert svc._market_title_similarity(extracted, candidate) >= 0.78


def test_market_title_similarity_low_confidence():
    assert svc._market_title_similarity("Will it rain in Paris tomorrow?", "NBA Finals winner") < 0.78


def test_expand_multilingual_title_terms_dr_oz_white_house():
    expanded = svc._expand_multilingual_title_terms("Что скажет доктор Оз во время следующего брифинга Белого дома?")

    assert "dr oz" in expanded
    assert "white house" in expanded
    assert "press briefing" in expanded


def test_market_title_similarity_strong_for_translated_dr_oz_title():
    extracted = "Что скажет доктор Оз во время следующего брифинга Белого дома?"
    candidate = "What will Dr. Oz say during the next White House press briefing?"

    assert svc._market_title_similarity(extracted, candidate) >= 0.82


def test_resolve_polymarket_market_from_title_confident_match(monkeypatch):
    def fake_list_markets(search="", limit=10, offset=0):
        return [
            {
                "id": "123",
                "question": "What will Dr. Oz say during the next White House press briefing?",
                "slug": "what-will-dr-oz-say-during-the-next-white-house-press-briefing",
                "eventSlug": "what-will-dr-oz-say-during-the-next-white-house-press-briefing",
                "active": True,
                "closed": False,
            }
        ]

    monkeypatch.setattr(svc, "list_markets", fake_list_markets)
    monkeypatch.setattr(svc, "_search_events_for_title", lambda query, limit=20: [])

    result = asyncio.run(svc.resolve_polymarket_market_from_title("What will Dr. Oz say during the next White House press briefing?"))

    assert result is not None
    assert result["market_id"] == "123"
    assert result["confidence"] >= 0.82
    assert result["url"].startswith("https://polymarket.com/event/")


def test_resolve_polymarket_market_from_title_low_confidence_returns_none(monkeypatch):
    def fake_list_markets(search="", limit=10, offset=0):
        return [
            {
                "id": "123",
                "question": "NBA Finals winner",
                "slug": "nba-finals-winner",
                "active": True,
                "closed": False,
            }
        ]

    monkeypatch.setattr(svc, "list_markets", fake_list_markets)
    monkeypatch.setattr(svc, "_search_events_for_title", lambda query, limit=20: [])

    result = asyncio.run(svc.resolve_polymarket_market_from_title("Will it rain in Paris tomorrow?"))

    assert result is None
