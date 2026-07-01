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
