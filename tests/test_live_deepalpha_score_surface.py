import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services.live_analyst_service import prepend_deepalpha_score_if_needed, should_surface_deepalpha_score


def _score(label="DATA NEEDED", edge_delta=None):
    return {
        "overall_score": 42,
        "label": label,
        "confidence": 50,
        "risk_level": "medium",
        "data_quality": "mixed",
        "edge_delta": edge_delta,
    }


def test_market_like_answer_without_score_gets_score_prepended():
    pack = {"mode": "crypto", "deepalpha_score": _score(edge_delta=7.5)}
    answer = prepend_deepalpha_score_if_needed("BTC выглядит как WATCH\nDecision: WATCH", pack, "en")
    assert answer.startswith("📊 DeepAlpha Score: 42/100")
    assert "Edge: +7.50 pp" in answer


def test_casual_answer_does_not_get_score():
    pack = {"mode": "general", "deepalpha_score": _score()}
    answer = prepend_deepalpha_score_if_needed("Привет! Чем помочь?", pack, "ru", user_text="привет")
    assert "DeepAlpha Score" not in answer


def test_no_duplicate_if_answer_already_includes_score():
    pack = {"mode": "sports", "deepalpha_score": _score()}
    existing = "📊 DeepAlpha Score: 42/100\nDecision: DATA NEEDED\n\nBody"
    answer = prepend_deepalpha_score_if_needed(existing, pack, "en")
    assert answer.count("DeepAlpha Score") == 1


def test_sports_no_odds_data_needed_still_surfaces_score():
    pack = {"mode": "sports", "missing_data": ["odds"], "deepalpha_score": _score("DATA NEEDED")}
    assert should_surface_deepalpha_score(pack, user_text="Что по матчу Real Madrid?")
    answer = prepend_deepalpha_score_if_needed("No odds were provided, so implied probability and edge cannot be calculated.", pack, "en")
    assert "Decision: DATA NEEDED" in answer
    assert "Edge: unavailable" in answer
