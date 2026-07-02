import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services.live_analyst_service import (
    compact_live_answer_if_needed,
    format_live_final_answer,
    normalize_ru_live_terms,
    remove_duplicate_decision_labels,
)


def _score(label="DATA NEEDED"):
    return {
        "overall_score": 43,
        "label": label,
        "confidence": 60,
        "risk_level": "medium",
        "data_quality": "missing",
        "edge_delta": None,
    }


def _sports_no_odds_pack(depth="normal"):
    return {
        "mode": "sports",
        "intent": "betting_angle",
        "deepalpha_score": _score(),
        "analyst_profile": {"answer_depth": depth},
        "derived_facts": {
            "understanding": {"intent": "betting_angle", "teams": ["Real Madrid"], "sport": "football", "odds": ""},
            "sports_context": {"sources": [], "teams": ["Real Madrid"]},
        },
        "missing_data": ["odds", "market", "event_time", "lineups"],
        "recommended_decision_labels": ["DATA NEEDED"],
    }


def test_duplicate_decision_removed_when_score_block_exists():
    answer = "📊 DeepAlpha Score: 43/100\nРешение: DATA NEEDED\n\nИтог: ждать\n\nDecision: DATA NEEDED"
    cleaned = remove_duplicate_decision_labels(answer)
    assert "Решение: DATA NEEDED" in cleaned
    assert "Decision: DATA NEEDED" not in cleaned


def test_ru_compact_answer_has_no_raw_bottom_decision():
    answer = format_live_final_answer("Что по матчу Real Madrid?\nDecision: DATA NEEDED", _sports_no_odds_pack(), "ru")
    assert "Решение: DATA NEEDED" in answer
    assert "Decision: DATA NEEDED" not in answer


def test_ru_compact_answer_replaces_value_heading():
    normalized = normalize_ru_live_terms("Value: unavailable\nMinimum playable odds: 2.05\ntravel/rest matters")
    assert "Преимущество:" in normalized
    assert "Value:" not in normalized
    assert "Минимальный рабочий кэф" in normalized
    assert "перелёты/отдых" in normalized


def test_short_normal_no_odds_sports_template_is_compact():
    answer = compact_live_answer_if_needed("Long body\nDecision: DATA NEEDED", _sports_no_odds_pack("short"), "ru")
    assert answer.startswith("📊 DeepAlpha Score: 43/100")
    assert "🏟 Коротко:" in answer
    assert "Нужны данные:" in answer
    assert "• коэффициент" in answer
    assert "Данные:" not in answer
    assert len(answer.splitlines()) <= 20


def test_deep_answer_mode_can_keep_detailed_sections():
    detailed = "📊 DeepAlpha Score: 43/100\nРешение: DATA NEEDED\n\nДанные:\n- много деталей\n\nРазбор:\n- фактор\n\nРиск:\n- фактор\n\nDecision: DATA NEEDED"
    answer = compact_live_answer_if_needed(detailed, _sports_no_odds_pack("deep"), "ru")
    assert "Данные:" in answer
    assert "Разбор:" in answer
    assert "Риск:" in answer
    assert "Decision: DATA NEEDED" not in answer


def test_no_forbidden_wording_in_compact_template():
    answer = compact_live_answer_if_needed("guaranteed profit guaranteed win ставь покупай 100% зайдёт", _sports_no_odds_pack("normal"), "ru")
    low = answer.lower()
    for phrase in ("guaranteed profit", "guaranteed win", "ставь", "покупай", "100% зайдёт"):
        assert phrase not in low

from services.live_analyst_service import _targeted_resolver_clarification, append_live_followup_suggestions


def _politics_pack():
    return {
        "mode": "polymarket",
        "intent": "probability_check",
        "deepalpha_score": _score("DATA NEEDED"),
        "market_resolution": {"domain": "politics", "notes": ["ambiguous_election_reference"]},
        "missing_data": ["election_year", "market", "side"],
    }


def test_compact_politics_ambiguity_answer_contains_targeted_fields():
    pack = _politics_pack()
    answer = format_live_final_answer(_targeted_resolver_clarification(pack["market_resolution"], "ru"), pack, "ru", user_text="Трамп победит на выборах?")
    assert "DeepAlpha Score" in answer
    assert "Решение: DATA NEEDED" in answer
    assert "политика / prediction market" in answer
    assert "какие выборы / год" in answer
    assert "Yes или No" in answer


def test_politics_followups_avoid_sports_betting_terms():
    answer = append_live_followup_suggestions("📊 DeepAlpha Score: 43/100\nРешение: DATA NEEDED\n\nКоротко.\nDecision: DATA NEEDED", _politics_pack(), "ru")
    low = answer.lower()
    assert "найти активный polymarket-рынок" in low
    for phrase in ("playable odds", "fair price", "ставка"):
        assert phrase not in low


def test_no_forbidden_wording_in_politics_ambiguity_template():
    answer = format_live_final_answer(_targeted_resolver_clarification(_politics_pack()["market_resolution"], "ru"), _politics_pack(), "ru")
    low = answer.lower()
    for phrase in ("guaranteed profit", "guaranteed win", "ставь", "покупай", "100%"):
        assert phrase not in low


def _trump_2028_pack(label="DATA NEEDED"):
    pack = _politics_pack()
    pack.update({
        "mode": "polymarket",
        "original_user_text": "Трамп победит на президентских выборах 2028?",
        "normalized_query": "Trump wins 2028 US presidential election 22nd Amendment cannot be elected again",
        "deepalpha_score": _score(label),
        "market_resolution": {"domain": "politics", "notes": ["22nd Amendment", "cannot be elected again"]},
    })
    return pack


def test_politics_final_followups_contain_polymarket_resolution_liquidity():
    pack = _politics_pack()
    answer = append_live_followup_suggestions(
        "📊 DeepAlpha Score: 43/100\nРешение: DATA NEEDED\n\nКоротко.\nDecision: DATA NEEDED\n\nХочешь продолжить разбор?\n\n- Посчитать value под твой коэффициент?\n- Найти минимальный playable odds / fair price?",
        pack,
        "ru",
    )
    low = answer.lower()
    assert "polymarket" in low
    assert "resolution" in low
    assert "ликвидность" in low or "liquidity" in low
    for phrase in ("playable odds", "fair price", "ставка", "поставить", "no bet"):
        assert phrase not in low


def test_ru_final_answer_removes_duplicate_english_decision_when_ru_decision_exists():
    answer = compact_live_answer_if_needed(
        "📊 DeepAlpha Score: 43/100\nРешение: DATA NEEDED\n\nИтог: ждать\n\nDecision: DATA NEEDED",
        _politics_pack(),
        "ru",
    )
    assert "Решение: DATA NEEDED" in answer
    assert "Decision: DATA NEEDED" not in answer


def test_trump_2028_legal_impossibility_direct_answer_and_no_no_bet():
    answer = format_live_final_answer(
        "По 22nd Amendment Trump cannot be elected again. Decision: NO BET",
        _trump_2028_pack(),
        "ru",
        user_text="2028",
    )
    low = answer.lower()
    assert "напрямую" in low
    assert "не может" in low
    assert "deepalpha score" in low
    assert "data needed" in low or "no edge" in low
    assert "no bet" not in low


def test_trump_2028_followups_are_specific_and_safe():
    answer = append_live_followup_suggestions("Коротко.\nDecision: DATA NEEDED", _trump_2028_pack(), "ru")
    low = answer.lower()
    assert "polymarket-рынок на выборы 2028" in low
    assert "resolution" in low
    assert "ликвидность" in low
    assert "преемник" in low
    for phrase in ("playable odds", "fair price", "ставка", "поставить", "no bet"):
        assert phrase not in low


def test_general_utility_answer_still_has_no_deepalpha_score():
    pack = {"mode": "general", "deepalpha_score": _score(), "universal_live_frame": {"domain": "utility"}}
    answer = format_live_final_answer("Коротко: можно составить список задач. Decision: DATA NEEDED", pack, "ru", user_text="как составить список задач")
    assert "DeepAlpha Score" not in answer


def _macron_2027_pack():
    pack = _politics_pack()
    ctx = {
        "is_election_question": True,
        "candidate": "Макрон",
        "country": "France",
        "office": "president",
        "election_year": 2027,
        "eligibility_status": "unknown",
    }
    pack.update({
        "mode": "polymarket",
        "original_user_text": "Макрон победит на выборах 2027?",
        "election_context": ctx,
        "market_resolution": {"domain": "politics", "election_context": ctx, "missing_data": ["market", "side"]},
        "missing_data": ["market", "side"],
    })
    return pack


def test_macron_2027_final_followups_are_election_safe_and_generic():
    answer = append_live_followup_suggestions("Коротко.\nDecision: DATA NEEDED", _macron_2027_pack(), "ru")
    low = answer.lower()
    assert "polymarket-рынок на выборы 2027" in low
    assert "eligibility" in low
    assert "кандидат, номинация, партия, преемник" in low
    assert "trump" not in low and "трамп" not in low


def test_macron_2027_final_answer_has_no_betting_terms():
    answer = format_live_final_answer(
        "Коротко: надо проверить market. Minimum playable odds / fair price неизвестны. Decision: NO BET",
        _macron_2027_pack(),
        "ru",
        user_text="Макрон победит на выборах 2027?",
    )
    low = answer.lower()
    for phrase in ("playable odds", "fair price", "ставка", "поставить", "no bet"):
        assert phrase not in low
    assert "data needed" in low


def test_biden_2028_legal_evidence_gets_generic_direct_answer():
    ctx = {
        "is_election_question": True,
        "candidate": "Biden",
        "country": "United States",
        "office": "president",
        "election_year": 2028,
        "eligibility_status": "unknown",
    }
    pack = _politics_pack()
    pack.update({
        "mode": "polymarket",
        "original_user_text": "Will Biden win in 2028?",
        "election_context": ctx,
        "market_resolution": {"domain": "politics", "election_context": ctx},
        "deepalpha_score": _score("DATA NEEDED"),
    })
    answer = format_live_final_answer(
        "Eligibility evidence mentions term limit and constitution constraints. Decision: DATA NEEDED",
        pack,
        "ru",
        user_text="Will Biden win in 2028?",
    )
    low = answer.lower()
    assert low.startswith("📊 deepalpha score") or "коротко: если речь именно" in low
    assert "у biden может быть юридическое ограничение" in low
    assert "трамп" not in low and "trump" not in low


def test_trump_2028_still_uses_generic_election_context():
    pack = _trump_2028_pack()
    pack["market_resolution"]["election_context"] = {
        "is_election_question": True,
        "candidate": "Trump",
        "country": "United States",
        "office": "president",
        "election_year": 2028,
        "eligibility_status": "ineligible",
    }
    answer = format_live_final_answer(
        "22nd Amendment says Trump cannot be elected again. Decision: NO BET",
        pack,
        "ru",
        user_text="Трамп победит на президентских выборах 2028?",
    )
    low = answer.lower()
    assert "напрямую участвовать/победить" in low
    assert "юридических ограничений" in low
    assert "no bet" not in low


def test_putin_election_followups_are_kept():
    pack = _politics_pack()
    ctx = {"is_election_question": True, "candidate": "Путин", "country": "Russia", "office": "president", "election_year": None}
    pack.update({"original_user_text": "Путин победит на выборах?", "election_context": ctx, "market_resolution": {"domain": "politics", "election_context": ctx}})
    answer = append_live_followup_suggestions("Коротко.\nDecision: DATA NEEDED", pack, "ru")
    low = answer.lower()
    assert "найти активный polymarket-рынок?" in low
    assert "eligibility" in low
    assert "resolution" in low
