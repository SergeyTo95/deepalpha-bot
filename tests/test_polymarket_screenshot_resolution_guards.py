import asyncio
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(get=lambda *args, **kwargs: None, post=lambda *args, **kwargs: None))

from services import polymarket_service as svc

WORLD_CUP_PAYLOAD = {
    "market_title_canonical": "2026 FIFA World Cup Winner",
    "market_title_original": "Победитель Кубка мира",
    "outcomes_canonical": ["Spain", "France", "Portugal", "England"],
    "category_canonical": "Sports · Football",
}

RIHANNA = {
    "id": "rihanna",
    "question": "New Rihanna Album before GTA VI?",
    "slug": "new-rihanna-album-before-gta-vi",
    "eventSlug": "new-rihanna-album-before-gta-vi",
    "outcomes": ["Yes", "No"],
    "active": True,
    "closed": False,
}


def test_reject_zero_outcome_overlap_binary_candidate():
    valid, reason = svc._validate_screenshot_candidate_consistency(WORLD_CUP_PAYLOAD, RIHANNA, 0.94, "url")

    assert valid is False
    assert "outcome_overlap" in reason


def test_title_category_contradiction_for_rihanna_world_cup():
    assert svc._screenshot_title_category_contradiction(WORLD_CUP_PAYLOAD, RIHANNA) is True


def test_strong_world_cup_candidate_allowed():
    candidate = {
        "id": "wc",
        "question": "2026 FIFA World Cup Winner",
        "outcomes": ["Spain", "France", "Portugal", "England", "Brazil"],
        "active": True,
        "closed": False,
    }
    score, overlap = svc._screenshot_candidate_score(candidate, WORLD_CUP_PAYLOAD["market_title_canonical"], WORLD_CUP_PAYLOAD["outcomes_canonical"], WORLD_CUP_PAYLOAD["category_canonical"])
    valid, reason = svc._validate_screenshot_candidate_consistency(WORLD_CUP_PAYLOAD, candidate, score, "search")

    assert overlap == 4
    assert valid is True
    assert reason == "ok"
    assert score >= 0.82


def test_medium_with_partial_overlap_not_strong():
    candidate = {
        "id": "partial",
        "question": "2026 FIFA World Cup Winner",
        "outcomes": ["Spain", "Brazil", "Argentina"],
        "active": True,
        "closed": False,
    }
    score, _ = svc._screenshot_candidate_score(candidate, WORLD_CUP_PAYLOAD["market_title_canonical"], WORLD_CUP_PAYLOAD["outcomes_canonical"], WORLD_CUP_PAYLOAD["category_canonical"])
    valid, reason = svc._validate_screenshot_candidate_consistency(WORLD_CUP_PAYLOAD, candidate, score, "search")

    assert valid is True
    assert reason.startswith("downgrade_")


def test_partial_visible_url_cannot_override_contradiction(monkeypatch):
    monkeypatch.setattr(svc, "get_primary_market_from_url", lambda url: RIHANNA)
    monkeypatch.setattr(svc, "search_markets_by_slug", lambda slug, limit=10: [RIHANNA])
    monkeypatch.setattr(svc, "list_markets", lambda search="", limit=10, offset=0: [RIHANNA])
    monkeypatch.setattr(svc, "_search_events_for_title", lambda query, limit=20: [])

    result = asyncio.run(svc.resolve_polymarket_market_from_screenshot({**WORLD_CUP_PAYLOAD, "visible_url_hint": "https://polymarket.com/ru/event/new-rihanna-album-before-gta-vi"}))

    assert result is None


def test_resolver_rejects_unrelated_rihanna_candidate(monkeypatch):
    monkeypatch.setattr(svc, "list_markets", lambda search="", limit=10, offset=0: [RIHANNA])
    monkeypatch.setattr(svc, "_search_events_for_title", lambda query, limit=20: [])

    result = asyncio.run(svc.resolve_polymarket_market_from_screenshot(WORLD_CUP_PAYLOAD))

    assert result is None
