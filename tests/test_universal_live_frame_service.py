from services.universal_live_frame_service import build_universal_live_frame


def test_esports_odds_request_frame():
    frame = build_universal_live_frame("NAVI Vitality тб 2.5 карт кэф 1.85", {"mode": "esports"}, {"mode": "esports", "teams": ["NAVI", "Vitality"]})
    assert frame["domain"] == "esports"
    assert frame["user_intent"] in ("calculate_value", "analyze_probability")
    assert frame["subject"] == "NAVI — Vitality"
    assert frame["question_type"] == "total"
    assert frame["side"] == "over"
    assert frame["line"] == "2.5"
    assert frame["odds"] == "1.85"
    assert frame["safety_domain"] == "betting_advice"
    assert frame["answer_style"] == "probability_vs_price"
    for label in ("DATA NEEDED", "NO BET", "WATCH"):
        assert label in frame["allowed_decision_labels"]


def test_followup_preserves_previous_market_fields():
    previous = {"universal_live_frame": {"domain": "esports", "user_intent": "calculate_value", "subject": "NAVI — Vitality", "question_type": "total", "side": "over", "line": "2.5", "odds": "1.85", "followup_state": {"domain": "esports", "subject": "NAVI — Vitality", "question_type": "total", "side": "over", "line": "2.5", "odds": "1.85"}}}
    frame = build_universal_live_frame("посчитай", {"mode": "esports"}, {"mode": "esports"}, previous_context=previous)
    assert frame["domain"] == "esports"
    assert frame["subject"] == "NAVI — Vitality"
    assert frame["side"] == "over"
    assert frame["line"] == "2.5"
    assert frame["odds"] == "1.85"


def test_technical_debug_frame():
    frame = build_universal_live_frame("Railway aiogram traceback: Terminated by other getUpdates request", {}, {})
    assert frame["domain"] == "technical_debug"
    assert frame["user_intent"] == "debug_problem"
    assert frame["answer_style"] == "debug_report"
    for item in ("logs", "environment variables", "recent deployment/commit"):
        assert item in frame["evidence_needs"]
    for label in ("LIKELY CAUSE", "FIX NEEDED"):
        assert label in frame["allowed_decision_labels"]


def test_politics_frame():
    frame = build_universal_live_frame("Trump win 2028 odds 2.4", {}, {})
    assert frame["domain"] == "politics"
    assert frame["question_type"] == "binary_event"
    assert frame["odds"] == "2.4"
    assert frame["safety_domain"] == "political_prediction"
    for item in ("polls", "election calendar", "legal/institutional constraints"):
        assert item in frame["evidence_needs"]


def test_business_frame():
    frame = build_universal_live_frame("Стоит ли запускать рекламу для DeepAlpha сейчас?", {}, {})
    assert frame["domain"] == "business"
    assert frame["user_intent"] in ("make_decision", "compare_options")
    assert frame["safety_domain"] == "business_advice"
    assert frame["answer_style"] in ("decision_tree", "pros_cons")
    for item in ("goal", "audience", "budget", "current metrics"):
        assert item in frame["evidence_needs"]


def test_health_and_legal_safety_labels():
    health = build_universal_live_frame("болит голова нужен диагноз?", {}, {})
    legal = build_universal_live_frame("legal contract regulation question", {}, {})
    assert health["safety_domain"] == "medical_info"
    assert legal["safety_domain"] == "legal_info"
    for frame in (health, legal):
        assert "INFORMATIONAL" in frame["allowed_decision_labels"]
        assert "ASK PROFESSIONAL" in frame["allowed_decision_labels"]
        assert "RECOMMENDED" not in frame["allowed_decision_labels"]
