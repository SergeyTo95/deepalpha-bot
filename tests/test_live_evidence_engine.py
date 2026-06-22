import os
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None, get=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

sys.path.insert(0, os.getcwd())

from services.live_evidence_engine import build_live_evidence_pack, validate_live_answer_against_evidence, plan_live_research_queries
from services import live_analyst_service as svc


def test_crypto_market_context_levels_allowed_and_better_zone():
    pack = build_live_evidence_pack(
        "BTCUSDT 1h есть вход?",
        {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "timeframe": "1h", "missing": [], "needs": {"ohlcv": True}},
        {"mode": "crypto", "entities": {"pair": "BTCUSDT"}},
        crypto_market_context={"ok": True, "pair": "BTCUSDT", "price": 64000, "price_source": "Binance", "ohlcv": [[1, 2, 3, 1, 2, 10]], "support_levels": [63500], "resistance_levels": [65000], "entry_context": {"better_zone": 63500, "invalidation": "below 63500", "confirmation": "reclaim 65000"}},
    )
    assert pack["answer_policy"]["can_give_levels"] is True
    assert pack["answer_policy"]["can_give_entry_zone"] is True
    assert pack["derived_facts"]["better_zone"] == 63500
    assert pack["confidence_label"] in ("medium", "high")


def test_crypto_no_ohlcv_disallows_levels_and_validator_flags_level():
    pack = build_live_evidence_pack(
        "BTC купить?",
        {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "missing": ["timeframe"], "needs": {"ohlcv": True}},
        {"mode": "crypto", "entities": {"asset": "BTC"}},
        crypto_market_context={"ok": False, "price": None, "support_levels": [], "resistance_levels": [], "ohlcv": []},
    )
    assert pack["answer_policy"]["can_give_levels"] is False
    assert "ohlcv" in pack["missing_data"]
    result = validate_live_answer_against_evidence("Decision: WATCH. Вход от $63,500 выглядит хорошо.", pack)
    assert result["severity"] == "major"
    assert "answer_contains_price_levels_but_levels_not_allowed" in result["issues"]


def test_sports_betting_without_odds_requires_data_needed_watch():
    pack = build_live_evidence_pack(
        "На кого ставить Team A vs Team B?",
        {"mode": "sports", "intent": "betting_angle", "teams": ["Team A", "Team B"], "missing": [], "needs": {"odds": True}},
        {"mode": "sports", "entities": {"teams": ["Team A", "Team B"]}},
        sports_context={"ok": True, "sources": [{"title": "Preview", "url": "https://example.com"}], "odds": [], "lineups": [], "injuries": []},
    )
    assert pack["answer_policy"]["can_comment_on_odds"] is False
    assert "odds" in pack["missing_data"]
    assert "DATA NEEDED" in pack["recommended_decision_labels"] or "WATCH" in pack["recommended_decision_labels"]


def test_schedule_no_event_time_validator_flags_exact_time():
    pack = build_live_evidence_pack(
        "Когда матч Team A vs Team B?",
        {"mode": "sports", "intent": "schedule_check", "teams": ["Team A", "Team B"], "missing": [], "needs": {"sports_schedule": True}},
        {"mode": "sports", "entities": {"teams": ["Team A", "Team B"]}},
        sports_context={"ok": True, "sources": [{"title": "Schedule", "url": "https://example.com"}], "event_time": ""},
    )
    assert "event_time" in pack["missing_data"]
    result = validate_live_answer_against_evidence("Точное время матча: 19:30. Decision: WATCH", pack)
    assert result["severity"] == "major"


def test_polymarket_probability_in_derived_facts():
    pack = build_live_evidence_pack(
        "Polymarket election market",
        {"mode": "polymarket", "intent": "market_check", "missing": [], "needs": {}},
        {"mode": "polymarket", "entities": {"probability": 0.57, "url": "https://polymarket.com/event/x", "outcomes": ["Yes", "No"], "market_rules": "rules", "end_date": "2026-12-31"}},
    )
    assert pack["derived_facts"]["polymarket_probability"] == 0.57


def test_validator_catches_direct_commands():
    pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {}, "answer_policy": {"can_give_levels": True}}
    for word in ["покупай", "ставь", "лонгуй", "шорти"]:
        assert validate_live_answer_against_evidence("Decision: WATCH. " + word, pack)["severity"] == "major"


def test_prompt_includes_live_evidence_pack_and_forbidden_claims():
    pack = build_live_evidence_pack("BTC?", {"mode": "crypto", "intent": "price_check", "missing": []}, {"mode": "crypto"})
    prompt = svc._build_live_prompt({"id": 1}, [], "BTC?", {"mode": "crypto"}, ui_language="ru", evidence_pack=pack)
    assert "Live Evidence Pack" in prompt
    assert "Forbidden claims" in prompt


def test_query_planner_crypto_limits_queries():
    queries = plan_live_research_queries("BTC купить?", {"mode": "crypto", "pair": "BTCUSDT"})
    assert 1 <= len(queries) <= 5
    assert all("purpose" in q and "query" in q and "priority" in q for q in queries)
