from services.live_answer_composer_service import compose_live_answer, is_strict_non_market_composer
from services.universal_live_frame_service import build_universal_live_frame


def _pack(text):
    frame = build_universal_live_frame(text, {}, {})
    return {"mode": frame["domain"], "intent": frame["user_intent"], "universal_live_frame": frame, "missing_data": frame.get("missing_data") or []}


def test_technical_debug_composer_selects_incident_responder():
    text = "Railway aiogram traceback: Terminated by other getUpdates request; make sure that only one bot instance is running."
    result = compose_live_answer(text, _pack(text), ui_language="ru")
    assert result["should_use_adaptive_answer"] is True
    assert result["composer_mode"] in {"technical_debug", "debug_report"}
    assert "incident responder" in result["system_role"]
    assert "moneyline" in result["forbidden_phrases"]
    assert "american_football" in result["forbidden_phrases"]
    assert "implied probability" in result["forbidden_phrases"]
    fallback = result["fallback_answer"]
    assert "getUpdates" in fallback
    assert "polling" in fallback
    assert "BOT_TOKEN" in fallback
    assert "Railway" in fallback
    low = fallback.lower()
    assert "moneyline" not in low
    assert "american_football" not in low
    assert "implied probability" not in low


def test_business_question_uses_advisor_composer():
    text = "Стоит ли запускать рекламу для DeepAlpha сейчас?"
    result = compose_live_answer(text, _pack(text), ui_language="ru")
    assert result["composer_mode"] == "business"
    assert "product/growth/business advisor" in result["system_role"]
    combined = " ".join(result["style_instructions"] + [result["fallback_answer"]]).lower()
    assert any(x in combined for x in ["goal", "цели", "audience", "аудитории"])
    assert any(x in combined for x in ["budget", "бюджет"])
    assert "moneyline" in result["forbidden_phrases"]
    assert "odds" not in result["fallback_answer"].lower()


def test_health_request_uses_informational_composer():
    text = "болит голова нужен диагноз?"
    result = compose_live_answer(text, _pack(text), ui_language="ru")
    assert result["composer_mode"] == "health_info"
    assert "health information assistant" in result["system_role"]
    assert "direct diagnosis" in result["forbidden_phrases"]
    fallback = result["fallback_answer"].lower()
    assert "не могу поставить диагноз" in fallback
    assert "врач" in fallback or "doctor" in fallback
    assert "moneyline" not in fallback
    assert "betting" not in fallback


def test_strict_non_market_composer_helper_matches_modes_and_roles():
    assert is_strict_non_market_composer({"composer_mode": "technical_debug"}) is True
    assert is_strict_non_market_composer({"composer_mode": "business"}) is True
    assert is_strict_non_market_composer({"composer_mode": "health_info"}) is True
    assert is_strict_non_market_composer({"composer_mode": "legal_info"}) is True
    assert is_strict_non_market_composer({"composer_mode": "research"}) is True
    assert is_strict_non_market_composer({"composer_mode": "other", "system_role": "senior production incident responder"}) is True
    assert is_strict_non_market_composer({"composer_mode": "betting", "system_role": "betting market analyst"}) is False
