import sys
import types
sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None, get=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services.live_understanding_service import understand_live_request
from services.live_evidence_engine import build_live_evidence_pack
from services.live_analyst_service import format_live_final_answer, build_live_followup_suggestions, build_live_suggested_actions
from services import live_context_memory as memory


def test_ru_cs2_understanding():
    u = understand_live_request("NAVI Vitality тб 2.5 карт кэф 1.85", {"mode": "unknown"}, {}, "ru")
    assert u["mode"] == "esports"
    assert u["game"] in ("cs2", "counter_strike")
    assert "NAVI" in u["teams"] and "Vitality" in u["teams"]
    assert u["market_type"] in ("map_total", "total")
    assert u["side"] == "over"
    assert u["line"] == "2.5"
    assert u["odds"] == "1.85"


def test_en_cs2_understanding():
    u = understand_live_request("CS2 NAVI vs Vitality over 2.5 maps odds 1.85", {"mode": "unknown"}, {}, "en")
    assert u["mode"] == "esports"
    assert u["game"] in ("cs2", "counter_strike")
    assert "NAVI" in u["teams"] and "Vitality" in u["teams"]
    assert u["market_type"] in ("map_total", "total")
    assert u["side"] == "over"
    assert u["line"] == "2.5"
    assert u["odds"] == "1.85"


def test_dota_understanding():
    u = understand_live_request("Dota Spirit Liquid тб 2.5 1.9", {"mode": "unknown"}, {}, "ru")
    assert u["mode"] == "esports"
    assert u["game"] == "dota2"
    assert "Spirit" in u["teams"] and "Liquid" in u["teams"]
    assert u["odds"] == "1.9"


def test_esports_formatted_answer_with_odds_ru():
    u = understand_live_request("NAVI Vitality тб 2.5 карт кэф 1.85", {"mode": "unknown"}, {}, "ru")
    pack = build_live_evidence_pack("NAVI Vitality тб 2.5 карт кэф 1.85", u, {"mode": "unknown"}, ui_language="ru")
    answer = format_live_final_answer("Decision: DATA NEEDED", pack, "ru")
    assert "Домен: esports" in answer
    assert "Игра: CS2" in answer
    assert "Событие / рынок: NAVI — Vitality" in answer
    assert "Коэффициент / цена: 1.85" in answer
    assert "Implied probability" in answer
    assert "54" in answer
    assert "DATA NEEDED" in answer
    assert "ставь" not in answer.lower()
    assert "бери" not in answer.lower()


def test_missing_odds_needs_data():
    u = understand_live_request("NAVI Vitality тотал карт больше 2.5", {"mode": "unknown"}, {}, "ru")
    pack = build_live_evidence_pack("NAVI Vitality тотал карт больше 2.5", u, {"mode": "unknown"}, ui_language="ru")
    answer = format_live_final_answer("Decision: WATCH", pack, "ru")
    assert "DATA NEEDED" in answer
    assert "Коэффициент нужен" in answer or "коэффициент" in answer.lower()
    assert "value" in answer or "edge" in answer


def test_followup_suggestions_esports_ru():
    pack = {"mode": "esports"}
    text = build_live_followup_suggestions(pack, "ru")
    assert "Посчитать value под твой коэффициент?" in text
    assert "Разобрать форму, карту/драфт и риск?" in text
    assert "Найти минимальный playable odds" in text


def test_followup_suggestions_esports_plan_avoids_injuries_ru():
    pack = {
        "mode": "esports",
        "market_intelligence_plan": {
            "market_domain": "esports",
            "needed_factors": ["recent form", "map/draft/pick-ban context", "line movement"],
        },
    }
    text = build_live_followup_suggestions(pack, "ru")
    assert "карта/драфт" in text or "ключевые факторы" in text
    assert "травмы" not in text


def test_esports_continuation_calculate_value():
    memory.clear_live_context(777)
    actions = build_live_suggested_actions({"mode": "esports"}, "ru")
    memory.save_live_context(777, mode="esports", original_user_text="NAVI — Vitality total maps", normalized_query="NAVI — Vitality total maps", teams_event="NAVI — Vitality", market="total maps over 2.5", odds="1.85", suggested_actions=actions)
    result = memory.resolve_live_followup(777, "посчитай")
    assert result["selected_action_id"] == "calculate_value"
    assert "implied probability" in result["resolved_query"].lower()
    assert "edge" in result["resolved_query"].lower()
    assert "minimum playable odds" in result["resolved_query"].lower()


def test_lakers_celtics_total_stays_sports():
    u = understand_live_request("Lakers Celtics тотал 218.5 кэф 1.9", {"mode": "unknown"}, {}, "ru")
    assert u["mode"] == "sports"
    assert u["sport"] == "basketball"
    assert u["market"] == "total"
    assert u["odds"] in ("1.9", "1.90")


def test_nba_lakers_celtics_over_stays_sports():
    u = understand_live_request("NBA Lakers vs Celtics over 218.5 odds 1.90", {"mode": "unknown"}, {}, "en")
    assert u["mode"] == "sports"
    assert u["sport"] == "basketball"


def test_ufc_total_rounds_stays_sports():
    u = understand_live_request("UFC total rounds 2.5 odds 1.85", {"mode": "unknown"}, {}, "en")
    assert u["mode"] == "sports"
    assert u["sport"] == "mma"


def test_esports_still_wins_before_sports():
    u = understand_live_request("NAVI Vitality тб 2.5 карт кэф 1.85", {"mode": "unknown"}, {}, "ru")
    assert u["mode"] == "esports"
    assert u["game"] == "cs2"


def test_generic_event_betting_fallback_still_works():
    u = understand_live_request("ивент X over 3.5 odds 1.9", {"mode": "unknown"}, {}, "ru")
    assert u["mode"] == "event_betting"
