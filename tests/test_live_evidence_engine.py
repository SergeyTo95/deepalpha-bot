import os
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None, get=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

sys.path.insert(0, os.getcwd())

from services.live_evidence_engine import apply_validation_safety, build_live_evidence_pack, validate_live_answer_against_evidence, plan_live_research_queries
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
    assert "answer_contains_unsupported_trading_level" in result["issues"]


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
    for text in ["Decision: WATCH. покупай BTC", "Decision: WATCH. ставь на Team A", "Decision: WATCH. лонгуй BTC", "Decision: WATCH. шорти BTC"]:
        assert validate_live_answer_against_evidence(text, pack)["severity"] == "major"


def test_validator_negated_commands_are_not_major():
    pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {}, "answer_policy": {"can_give_levels": True}}
    result = validate_live_answer_against_evidence("Decision: NO TRADE. Не покупай сейчас без подтверждения.", pack)
    assert result["severity"] in ("minor", "none")


def test_prompt_includes_live_evidence_pack_and_forbidden_claims():
    pack = build_live_evidence_pack("BTC?", {"mode": "crypto", "intent": "price_check", "missing": []}, {"mode": "crypto"})
    prompt = svc._build_live_prompt({"id": 1}, [], "BTC?", {"mode": "crypto"}, ui_language="ru", evidence_pack=pack)
    assert "Live Evidence Pack" in prompt
    assert "Forbidden claims" in prompt


def test_query_planner_crypto_limits_queries():
    queries = plan_live_research_queries("BTC купить?", {"mode": "crypto", "pair": "BTCUSDT"})
    assert 1 <= len(queries) <= 5
    assert all("purpose" in q and "query" in q and "priority" in q for q in queries)


def test_query_planner_returns_queries_for_all_live_modes():
    assert plan_live_research_queries("BTC?", {"mode": "crypto", "pair": "BTCUSDT"})
    assert plan_live_research_queries("Team A vs Team B", {"mode": "sports", "teams": ["Team A", "Team B"]})
    assert plan_live_research_queries("Election market", {"mode": "polymarket"})
    assert len(plan_live_research_queries("BTC?", {"mode": "crypto", "pair": "BTCUSDT"})) <= 5


def test_evidence_pack_includes_planned_queries():
    pack = build_live_evidence_pack("BTC?", {"mode": "crypto", "intent": "price_check", "pair": "BTCUSDT", "missing": []}, {"mode": "crypto"})
    assert pack["planned_queries"]
    assert len(pack["planned_queries"]) <= 5


def test_prompt_includes_planned_queries():
    pack = build_live_evidence_pack("BTC?", {"mode": "crypto", "intent": "price_check", "pair": "BTCUSDT", "missing": []}, {"mode": "crypto"})
    prompt = svc._build_live_prompt({"id": 1}, [], "BTC?", {"mode": "crypto"}, ui_language="ru", evidence_pack=pack)
    assert "Planned research queries:" in prompt
    assert "price:" in prompt


def test_current_price_mention_allowed_when_current_price_exists():
    pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {"current_price": 64000}, "answer_policy": {"can_give_levels": False, "can_give_entry_zone": False}}
    result = validate_live_answer_against_evidence("Decision: WATCH. BTC сейчас около $64,000, но вход не подтверждён.", pack)
    assert result["severity"] != "major"


def test_entry_zone_flagged_when_entry_zone_not_allowed():
    pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {"current_price": 64000}, "answer_policy": {"can_give_levels": True, "can_give_entry_zone": False}}
    result = validate_live_answer_against_evidence("Decision: WATCH. Вход от $63,500.", pack)
    assert result["severity"] == "major"
    assert "answer_contains_unsupported_entry_zone" in result["issues"]


def test_support_flagged_when_levels_not_allowed():
    pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {"current_price": 64000}, "answer_policy": {"can_give_levels": False, "can_give_entry_zone": False}}
    result = validate_live_answer_against_evidence("Decision: WATCH. Поддержка $63,500.", pack)
    assert result["severity"] == "major"


def test_current_price_english_allowed_when_current_price_exists():
    pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {"current_price": 64000}, "answer_policy": {"can_give_levels": False, "can_give_entry_zone": False}}
    result = validate_live_answer_against_evidence("Decision: WATCH. $64,000 current price; no entry levels confirmed.", pack)
    assert result["severity"] != "major"


def test_better_zone_normalized_formats_do_not_trigger_minor_issue():
    for answer in ["Decision: WATCH. Better zone $63,500.", "Decision: WATCH. Better zone 63 500.", "Decision: WATCH. Better zone 63500."]:
        pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {"better_zone": 63500}, "answer_policy": {"can_give_levels": True, "can_give_entry_zone": True}}
        assert "answer_ignores_better_zone" not in validate_live_answer_against_evidence(answer, pack)["issues"]


def test_major_validation_safety_replaces_bad_answer_without_level_or_command():
    pack = {"mode": "crypto", "confidence_label": "low", "missing_data": ["ohlcv"], "derived_facts": {}, "answer_policy": {"can_give_levels": False, "can_give_entry_zone": False}}
    validation = validate_live_answer_against_evidence("Decision: WATCH. покупай BTC, вход от $63,500", pack)
    safe = apply_validation_safety("Decision: WATCH. покупай BTC, вход от $63,500", pack, validation, ui_language="ru")
    assert "покупай" not in safe.lower()
    assert "63500" not in safe and "63,500" not in safe and "63 500" not in safe
    assert "Decision: DATA NEEDED" in safe


def test_minor_validation_does_not_replace_answer():
    pack = {"mode": "crypto", "confidence_label": "medium", "missing_data": [], "derived_facts": {}, "answer_policy": {"can_give_levels": True}}
    answer = "Decision: NO TRADE. Не покупай сейчас без подтверждения."
    validation = validate_live_answer_against_evidence(answer, pack)
    assert apply_validation_safety(answer, pack, validation, ui_language="ru") == answer


def test_process_live_text_passes_enriched_research_seed_and_charges_once(monkeypatch):
    saved = []
    charges = []
    research_calls = []
    monkeypatch.setattr(svc, "is_live_enabled", lambda: True)
    monkeypatch.setattr(svc, "get_live_request_cost", lambda message_type: 1)
    monkeypatch.setattr(svc, "can_user_afford_live_request", lambda user_id, cost: True)
    monkeypatch.setattr(svc, "get_max_daily_live_messages", lambda: 0)
    monkeypatch.setattr(svc, "get_or_create_active_session", lambda user_id: {"id": 555})
    monkeypatch.setattr(svc, "get_memory_message_limit", lambda: 12)
    monkeypatch.setattr(svc, "get_recent_context", lambda session_id, limit: [])
    monkeypatch.setattr(svc, "get_crypto_market_context", lambda *args, **kwargs: {"ok": False, "ohlcv": [], "support_levels": [], "resistance_levels": [], "entry_context": {}})
    monkeypatch.setattr(svc, "get_live_research_context", lambda query, *args, **kwargs: research_calls.append(query) or {"ok": False, "sources": [], "summary": "", "freshness": "unknown"})
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: "Decision: DATA NEEDED")
    monkeypatch.setattr(svc, "charge_live_request", lambda user_id, cost, reason: charges.append((user_id, cost, reason)) or True)
    monkeypatch.setattr(svc, "update_context_from_user_text", lambda current, text: current)
    monkeypatch.setattr(svc, "save_message", lambda *args, **kwargs: saved.append((args, kwargs)))

    result = svc.process_live_text(99, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}})

    assert result["ok"] is True
    assert len(charges) == 1
    assert len(saved) == 2
    assert research_calls and "Planned research queries:" in research_calls[0]
    assert "BTCUSDT current price OHLC support resistance today" in research_calls[0]
