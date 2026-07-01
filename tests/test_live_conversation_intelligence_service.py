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
