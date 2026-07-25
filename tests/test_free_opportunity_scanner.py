from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents import opportunity_agent as opportunity_module
from agents.opportunity_agent import OpportunityAgent
from services import free_opportunity_scanner as scanner
from services.free_opportunity_runtime_patch import format_free_opportunity_card


def _market(
    *,
    market_id="m1",
    question="Will Bitcoin be above $120,000 on August 31, 2026?",
    yes=0.45,
    no=0.55,
    liquidity=25000,
    volume_24h=12000,
    volume=200000,
    move=0.035,
    days=20,
    event_slug="btc-august",
):
    return {
        "id": market_id,
        "slug": market_id,
        "eventSlug": event_slug,
        "question": question,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes}", "{no}"]',
        "liquidity": liquidity,
        "volume24hr": volume_24h,
        "volume": volume,
        "oneDayPriceChange": move,
        "endDate": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
        "active": True,
        "closed": False,
    }


def _event(slug="btc-august", markets=None):
    markets = markets or [_market(event_slug=slug)]
    return {
        "slug": slug,
        "title": "Bitcoin August markets",
        "liquidity": 50000,
        "volume24hr": 30000,
        "volume": 500000,
        "markets": markets,
    }


def test_liquid_objective_market_becomes_high_priority_candidate(monkeypatch):
    monkeypatch.setenv("FREE_OPPORTUNITY_MIN_LIQUIDITY", "500")
    candidate, rejected = scanner.score_market_candidate(_market(), event=_event())

    assert rejected == ""
    assert candidate is not None
    assert candidate.score >= 68
    assert candidate.tier == "DEEP_ANALYSIS_CANDIDATE"
    assert candidate.yes_price == 45.0
    assert candidate.no_price == 55.0
    assert candidate.category == "Crypto"
    assert candidate.event_market_count == 1
    assert "data_accessibility" in candidate.score_components


def test_illiquid_or_resolved_markets_are_rejected(monkeypatch):
    monkeypatch.setenv("FREE_OPPORTUNITY_MIN_LIQUIDITY", "500")
    monkeypatch.setenv("FREE_OPPORTUNITY_MIN_VOLUME_24H", "100")

    illiquid, reason = scanner.score_market_candidate(
        _market(liquidity=20, volume_24h=10),
        event={},
    )
    resolved, resolved_reason = scanner.score_market_candidate(
        _market(yes=0.999, no=0.001),
        event={},
    )

    assert illiquid is None
    assert reason == "illiquid"
    assert resolved is None
    assert resolved_reason == "near_resolved"


def test_diversity_prevents_one_event_from_filling_the_shortlist():
    candidates = []
    for index in range(5):
        candidate, _ = scanner.score_market_candidate(
            _market(market_id=f"a{index}", event_slug="same-event", liquidity=50000 + index),
            event=_event("same-event"),
        )
        candidates.append(candidate)
    for index in range(3):
        candidate, _ = scanner.score_market_candidate(
            _market(market_id=f"b{index}", event_slug=f"event-{index}", liquidity=30000 + index),
            event=_event(f"event-{index}"),
        )
        candidates.append(candidate)

    selected = scanner._select_diverse([item for item in candidates if item], result_limit=5)

    assert len(selected) == 5
    assert sum(1 for item in selected if item.event_key == "same-event") == 2
    assert len({item.event_key for item in selected}) >= 4


def test_public_scan_uses_only_market_api_and_reports_zero_provider_calls(monkeypatch):
    markets = [
        _market(market_id="m1", event_slug="event-1"),
        _market(
            market_id="m2",
            event_slug="event-2",
            question="Will the Fed cut rates by September 2026?",
            yes=0.38,
            no=0.62,
        ),
    ]
    events = [_event("event-1", [markets[0]]), _event("event-2", [markets[1]])]
    monkeypatch.setattr(scanner, "list_events", lambda limit, offset=0: events)
    monkeypatch.setattr(scanner, "list_markets", lambda **kwargs: [])
    scanner._CACHE["expires_at"] = 0
    scanner._CACHE["result"] = None

    result = scanner.scan_free_opportunities(
        scan_limit=20,
        result_limit=5,
        force_refresh=True,
    )

    assert result["provider_calls"] == 0
    assert result["paid_ai_used"] is False
    assert result["markets_received"] == 2
    assert len(result["candidates"]) == 2
    assert "No fair probability" in result["disclaimer"]


def test_opportunity_agent_defaults_to_free_mode_without_creating_paid_agents(monkeypatch):
    monkeypatch.delenv("OPPORTUNITY_PAID_ANALYSIS_ENABLED", raising=False)
    scan_result = {
        "candidates": [{"question": "Candidate", "score": 70}],
        "provider_calls": 0,
        "paid_ai_used": False,
    }
    formatted = {
        "mode": "free_opportunity_prescan",
        "question": "Candidate",
        "free_candidates": scan_result["candidates"],
    }
    monkeypatch.setattr(opportunity_module, "scan_free_opportunities", lambda **kwargs: scan_result)
    monkeypatch.setattr(opportunity_module, "format_free_opportunity_result", lambda scan, lang: dict(formatted))

    agent = OpportunityAgent()
    monkeypatch.setattr(agent, "_ensure_paid_agents", lambda: (_ for _ in ()).throw(AssertionError("paid path called")))
    result = agent.run(limit=3, lang="ru")

    assert result["opportunity_mode"] == "free_prescan"
    assert result["provider_calls"] == 0
    assert result["paid_ai_used"] is False
    assert agent.news_agent is None
    assert agent.decision_agent is None


def test_free_card_explicitly_says_not_buy_and_zero_ai_cost():
    result = {
        "mode": "free_opportunity_prescan",
        "free_candidates": [
            {
                "question": "Will Bitcoin be above $120,000?",
                "score": 76,
                "tier": "DEEP_ANALYSIS_CANDIDATE",
                "yes_price": 45,
                "no_price": 55,
                "volume_24h": 12000,
                "liquidity": 25000,
                "reasons": ["достаточная ликвидность", "активный объём за 24 часа"],
                "url": "https://polymarket.com/event/example",
            }
        ],
    }

    text = format_free_opportunity_card(result, lang="ru")

    assert "AI-расход: <b>0</b>" in text
    assert "публичная рыночная статистика" in text
    assert "не BUY" in text
    assert "Открыть рынок" in text
    assert "Kimi" not in text
    assert "Gemini" not in text


def test_scanner_source_has_no_llm_or_paid_provider_dependency():
    source = Path("services/free_opportunity_scanner.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "llm_service",
        "call_llm",
        "call_gemini",
        "call_kimi",
        "newsagent",
        "decisionagent",
        "gemini_gateway",
        "kimi_gateway",
    )

    for token in forbidden:
        assert token not in source
