from services.live_conversation_intelligence_service import resolve_live_conversation_intent


def politics_pending():
    return {"domain": "politics", "subject": "Trump", "intent": "probability_check", "missing_data": ["election_year", "market", "side"], "original_user_text": "Трамп победит на выборах?"}


def test_pending_politics_year_reconstructs():
    r = resolve_live_conversation_intent("2028", pending_clarification=politics_pending(), ui_language="ru")
    assert r["domain"] == "politics"
    assert r["filled"] == {"election_year": 2028}
    assert r["completed_text"] == "Трамп победит на выборах 2028?"
    assert r["answer_strategy"] == "market_lookup"


def test_pending_politics_yes_fills_side():
    r = resolve_live_conversation_intent("Yes", pending_clarification=politics_pending())
    assert r["filled"]["side"] == "Yes"
    assert r["answer_strategy"] != "generic_clarification"


def test_pending_politics_polymarket_url_fills_market_url():
    r = resolve_live_conversation_intent("вот ссылка https://polymarket.com/event/test", pending_clarification=politics_pending())
    assert r["filled"]["market_url"].startswith("https://polymarket.com/event/test")
    assert r["answer_strategy"] == "market_lookup"


def test_year_without_pending_does_not_invent_politics():
    r = resolve_live_conversation_intent("2028")
    assert r["domain"] == "unknown"


def test_pending_sports_odds_fills_odds():
    r = resolve_live_conversation_intent("победа кэф 1.85", pending_clarification={"domain": "sports", "subject": "Real Madrid", "missing_data": ["odds", "market"], "original_user_text": "Что по матчу Real Madrid?"})
    assert r["filled"]["odds"] == 1.85
    assert r["filled"]["market"] == "winner"
    assert r["domain"] == "sports"


def test_pending_crypto_timeframe_fills_timeframe():
    r = resolve_live_conversation_intent("15m", pending_clarification={"domain": "crypto", "subject": "BTC", "missing_data": ["timeframe"], "original_user_text": "BTC long сейчас?"})
    assert r["filled"]["timeframe"] == "15m"
    assert r["completed_text"] == "BTC long сейчас? Таймфрейм 15m"


def test_domain_context_never_generic():
    for domain in ["politics", "sports", "crypto"]:
        r = resolve_live_conversation_intent("что дальше", pending_clarification={"domain": domain, "original_user_text": "x", "missing_data": ["market"]})
        assert r["answer_strategy"] != "generic_clarification"


def test_weather_city_clarification_not_generic():
    r = resolve_live_conversation_intent("Какая погода?", ui_language="ru")
    assert r["domain"] == "weather"
    assert r["answer_strategy"] == "targeted_clarification"
    assert "город" in r["clarification_message"].lower()


def test_utility_intents_detected():
    assert resolve_live_conversation_intent("Какая погода в Анталии?")["domain"] == "weather"
    assert resolve_live_conversation_intent("Сколько будет 15% от 320?")["answer_strategy"] == "calculate"
    assert resolve_live_conversation_intent("Переведи на турецкий: хочу заказать креветки")["domain"] == "translation"
    assert resolve_live_conversation_intent("Что значит implied probability?")["domain"] == "explanation"
    assert resolve_live_conversation_intent("Привет")["domain"] == "casual"


def test_reconstructed_pending_storage_preserves_best_full_question():
    from services import live_context_memory as memory

    memory.clear_live_context_memory()
    completed = resolve_live_conversation_intent("2028", pending_clarification=politics_pending(), ui_language="ru")["completed_text"]
    memory.save_pending_clarification(5001, {
        "domain": "politics",
        "subject": "Trump",
        "intent": "probability_check",
        "missing_data": ["market", "side"],
        "original_user_text": completed,
        "latest_user_text": "2028",
        "raw_user_text": "2028",
    })
    pending = memory.get_pending_clarification(5001)
    assert pending["original_user_text"] == "Трамп победит на выборах 2028?"
    assert pending["latest_user_text"] == "2028"


def test_yes_after_reconstructed_pending_uses_full_question_not_short_year():
    pending = {
        "domain": "politics",
        "subject": "Trump",
        "intent": "probability_check",
        "missing_data": ["market", "side"],
        "original_user_text": "Трамп победит на выборах 2028?",
        "latest_user_text": "2028",
    }
    r = resolve_live_conversation_intent("Yes", pending_clarification=pending, ui_language="ru")
    assert r["domain"] == "politics"
    assert r["filled"]["side"] == "Yes"
    assert r["completed_text"] == "Трамп победит на выборах 2028? Yes"
    assert not r["completed_text"].startswith("2028 Yes")
    assert r["answer_strategy"] != "generic_clarification"


def test_pending_macron_year_reconstructs_full_context():
    pending = {"domain": "politics", "subject": "Макрон", "intent": "probability_check", "missing_data": ["election_year", "market", "side"], "original_user_text": "Макрон победит на выборах?"}
    r = resolve_live_conversation_intent("2027", pending_clarification=pending, ui_language="ru")
    assert r["domain"] == "politics"
    assert r["subject"] == "Макрон"
    assert r["filled"]["election_year"] == 2027
    assert r["completed_text"] == "Макрон победит на выборах 2027?"


def test_pending_macron_yes_keeps_candidate_year_and_fills_side():
    pending = {"domain": "politics", "subject": "Макрон", "intent": "probability_check", "missing_data": ["market", "side"], "original_user_text": "Макрон победит на выборах 2027?", "election_context": {"is_election_question": True, "candidate": "Макрон", "country": "France", "election_year": 2027}}
    r = resolve_live_conversation_intent("Yes", pending_clarification=pending, ui_language="ru")
    assert r["domain"] == "politics"
    assert r["subject"] == "Макрон"
    assert r["filled"]["side"] == "Yes"
    assert r["election_context"]["candidate"] == "Макрон"
    assert r["election_context"]["election_year"] == 2027
    assert r["completed_text"] == "Макрон победит на выборах 2027? Yes"

from services.live_conversation_intelligence_service import resolve_short_live_followup
from services.live_conversation_intelligence_service import cleanup_final_politics_election_answer
from services import live_context_memory as memory


def macron_context():
    return {
        "domain": "politics",
        "mode": "polymarket",
        "original_user_text": "Макрон победит на выборах 2027?",
        "normalized_query": "Макрон победит на выборах 2027?",
        "candidate": "Макрон",
        "country": "France",
        "election_year": 2027,
        "election_context": {"is_election_question": True, "candidate": "Макрон", "country": "France", "election_year": 2027},
    }


def test_short_followup_macron_year_reconstructs_context_first():
    prev = {"domain": "politics", "original_user_text": "Макрон победит на выборах?", "candidate": "Макрон", "country": "France", "election_context": {"is_election_question": True, "candidate": "Макрон", "country": "France"}}
    r = resolve_short_live_followup("2027", prev, None, ui_language="ru")
    assert r["effective_text"] == "Макрон победит на выборах 2027?"
    assert r["filled"]["election_year"] == 2027


def test_successful_context_stores_effective_election_fields_not_short_reply():
    memory.clear_live_context_memory()
    ctx = memory.save_live_context(
        7101,
        mode="polymarket",
        original_user_text="Макрон победит на выборах 2027?",
        normalized_query="Макрон победит на выборах 2027?",
        latest_user_text="2027",
        raw_user_text="2027",
        last_effective_user_text="Макрон победит на выборах 2027?",
        market_domain="politics",
        election_context={"is_election_question": True, "candidate": "Макрон", "country": "France", "election_year": 2027},
        candidate="Макрон",
        country="France",
        election_year=2027,
    )
    assert ctx["original_user_text"] == "Макрон победит на выборах 2027?"
    assert ctx["latest_user_text"] == "2027"
    assert ctx["candidate"] == "Макрон"
    assert ctx["country"] == "France"
    assert ctx["election_year"] == 2027
    assert ctx["domain"] == "politics"


def test_short_followup_macron_yes_reconstructs_yes_not_crypto():
    r = resolve_live_conversation_intent("Да", previous_context=macron_context(), ui_language="ru")
    assert r["domain"] == "politics"
    assert r["completed_text"] == "Макрон победит на выборах 2027? Yes"
    assert r["filled"]["side"] == "Yes"


def test_explicit_btc_2027_yes_overrides_previous_election_context():
    r = resolve_live_conversation_intent("BTC 2027 yes", previous_context=macron_context(), ui_language="ru")
    assert r["domain"] == "crypto"


def test_country_and_office_short_followups_fill_election_context():
    prev = {"domain": "politics", "original_user_text": "Кто победит на выборах?", "election_context": {"is_election_question": True}}
    assert resolve_short_live_followup("Франция", prev, None)["filled"]["country"] == "France"
    assert resolve_short_live_followup("президентские", prev, None)["filled"]["office"] == "president"


def test_yes_after_election_fills_side_and_continue_when_side_exists():
    no_side = macron_context(); no_side.pop("side", None)
    assert resolve_short_live_followup("Yes", no_side, None)["filled"]["side"] == "Yes"
    with_side = macron_context(); with_side["side"] = "Yes"; with_side["election_context"]["side"] = "Yes"
    r = resolve_short_live_followup("Да", with_side, None)
    assert r["should_continue_previous_analysis"] is True
    assert r["filled"] == {}


def test_yes_after_continue_prompt_keeps_election_context():
    pending = macron_context() | {"bot_clarification_message": "Хочешь продолжить разбор?"}
    r = resolve_live_conversation_intent("Да", pending_clarification=pending, ui_language="ru")
    assert r["domain"] == "politics"
    assert r["completed_text"] == "Макрон победит на выборах 2027?"
    assert r["answer_strategy"] == "continue_previous_analysis"


def test_final_politics_cleanup_removes_betting_terms_and_adds_safe_followups():
    answer = "Решение: DATA NEEDED\nDecision: DATA NEEDED\nplayable odds 55%\nfair price 0.42\nvalue под твой коэффициент\nNO BET\nМожно поставить позже"
    cleaned = cleanup_final_politics_election_answer(answer, {"mode": "polymarket", "election_context": {"election_year": 2027}}, "ru")
    low = cleaned.lower()
    for banned in ["playable odds", "fair price", "value под твой коэффициент", "no bet"]:
        assert banned not in low
    assert "Polymarket" in cleaned
    assert "eligibility" in cleaned
    assert "resolution" in cleaned
    assert "ликвидность" in cleaned


def ambiguous_france_context_without_candidate():
    return {
        "domain": "politics",
        "original_user_text": "Кто победит на выборах во Франции?",
        "country": "France",
        "election_context": {"is_election_question": True, "country": "France"},
    }


def test_russian_yes_after_candidate_missing_context_never_becomes_candidate():
    r = resolve_live_conversation_intent("Да", previous_context=ambiguous_france_context_without_candidate(), ui_language="ru")
    assert r["domain"] == "politics"
    assert (r.get("filled") or {}).get("side") == "Yes" or r.get("answer_strategy") == "continue_previous_analysis"
    assert (r.get("filled") or {}).get("candidate") != "Да"
    assert (r.get("election_context") or {}).get("candidate") != "Да"


def test_russian_no_after_candidate_missing_context_never_becomes_candidate():
    r = resolve_short_live_followup("Нет", ambiguous_france_context_without_candidate(), None, ui_language="ru")
    assert (r.get("filled") or {}).get("candidate") != "Нет"
    assert (r.get("election_context") or {}).get("candidate") != "Нет"


def test_english_yes_after_candidate_missing_context_never_becomes_candidate():
    r = resolve_short_live_followup("Yes", ambiguous_france_context_without_candidate(), None, ui_language="ru")
    assert (r.get("filled") or {}).get("candidate") != "Yes"
    assert (r.get("election_context") or {}).get("candidate") != "Yes"
