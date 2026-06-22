import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services.ai_control_center import (
    build_ai_control_context,
    choose_ai_provider,
    score_ai_response_quality,
)
from services.ai_provider_gateway import get_provider_status
from services import live_analyst_service as live_svc


def _pack(score=0.8, mode="crypto", confidence="high"):
    return {"data_quality_score": score, "mode": mode, "intent": "analysis", "confidence_label": confidence}


def test_provider_gateway_defaults_to_gemini_only(monkeypatch):
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_ENABLED", raising=False)
    status = get_provider_status()
    assert status["gemini"]["enabled"] is True
    assert status["openai"]["enabled"] is False
    assert status["anthropic"]["enabled"] is False
    assert choose_ai_provider("live_analyst", "crypto")["provider"] == "gemini"


def test_control_context_has_trust_adjusted_token_revenue_objective():
    ctx = build_ai_control_context(1, "BTCUSDT 15m?", "crypto", "entry", evidence_pack=_pack())
    assert ctx["objective"]["name"] == "trust_adjusted_token_revenue"
    assert ctx["quality_constraints"]["must_use_evidence"] is True
    assert ctx["economics"]["should_refund_if_bad"] is True


def test_major_validation_issue_penalizes_and_recommends_refund():
    result = score_ai_response_quality("Decision: BUY now at exactly $70000", _pack(), {"ok": False, "severity": "major"})
    penalty_types = {p["type"] for p in result["penalties"]}
    assert "hallucination" in penalty_types
    assert "direct_command" in penalty_types
    assert result["should_refund"] is True


def test_low_evidence_overconfident_exact_answer_penalized():
    result = score_ai_response_quality("Decision: EDGE CANDIDATE. Entry is 68420 USDT with 82% certainty.", _pack(score=0.1, confidence="low"), {"ok": True, "severity": "none"})
    penalty_types = {p["type"] for p in result["penalties"]}
    assert "low_evidence" in penalty_types
    assert "overconfidence" in penalty_types


def test_evidence_grounded_decision_answer_gets_bonus():
    result = score_ai_response_quality("Short take: evidence supports WATCH. Decision: WATCH. Uncertainty remains limited.", _pack(score=0.9, confidence="medium"), {"ok": True, "severity": "none"})
    bonus_types = {b["type"] for b in result["bonuses"]}
    assert "evidence_grounded" in bonus_types
    assert "useful_decision" in bonus_types
    assert result["quality_score"] > 0.8


def test_direct_command_major_penalty():
    result = score_ai_response_quality("Decision: EDGE. Buy YES now.", _pack(), {"ok": True, "severity": "none"})
    assert any(p["type"] == "direct_command" and p["points"] >= 0.35 for p in result["penalties"])
    assert result["should_refund"] is True


def test_billing_service_not_changed_by_quality_scoring():
    result = score_ai_response_quality("Decision: BUY now", _pack(), {"ok": False, "severity": "major"})
    assert "should_refund" in result
    assert "should_discount_next_answer" in result
    assert "should_not_charge_future_equivalent" in result
    # Recommendations only: no balance mutation API is returned or invoked by the scorer.
    assert "charged_tokens" not in result


def test_live_analyst_integration_scores_and_logs_without_extra_charge(monkeypatch):
    session = {"id": 321, "current_market_url": "", "current_market_title": ""}
    charges = []
    events = []
    qualities = []
    monkeypatch.setattr(live_svc, "is_live_enabled", lambda: True)
    monkeypatch.setattr(live_svc, "get_live_request_cost", lambda message_type: 1)
    monkeypatch.setattr(live_svc, "can_user_afford_live_request", lambda user_id, cost: True)
    monkeypatch.setattr(live_svc, "get_max_daily_live_messages", lambda: 0)
    monkeypatch.setattr(live_svc, "count_live_analyst_messages_today", lambda *args, **kwargs: 0)
    monkeypatch.setattr(live_svc, "get_or_create_active_session", lambda user_id: dict(session))
    monkeypatch.setattr(live_svc, "get_memory_message_limit", lambda: 5)
    monkeypatch.setattr(live_svc, "get_recent_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(live_svc, "update_context_from_user_text", lambda current, text: current)
    monkeypatch.setattr(live_svc, "save_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_svc, "charge_live_request", lambda user_id, cost, reason: charges.append((user_id, cost, reason)) or True)
    monkeypatch.setattr(live_svc, "generate_decision_text", lambda prompt, **kwargs: "Short take: WATCH\nDecision: WATCH")
    monkeypatch.setattr(live_svc, "record_ai_control_event", lambda **kwargs: events.append(kwargs))
    original = live_svc.score_ai_response_quality
    monkeypatch.setattr(live_svc, "score_ai_response_quality", lambda answer, evidence_pack, validation: qualities.append((answer, evidence_pack, validation)) or original(answer, evidence_pack, validation))

    result = live_svc.process_live_text(55, "BTCUSDT 15m?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT"}}, ui_language="en")

    assert result["ok"] is True
    assert len(charges) == 1
    assert len(qualities) == 1
    assert len(events) == 1
    assert events[0]["charged_tokens"] == 1
