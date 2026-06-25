import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace())
psycopg2 = types.ModuleType("psycopg2")
class Error(Exception): pass
psycopg2.Error = Error
psycopg2.connect = lambda *a, **k: (_ for _ in ()).throw(Error("stub"))
psycopg2.errors = types.SimpleNamespace()
sys.modules.setdefault("psycopg2", psycopg2)
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services.universal_market_intelligence_service import build_market_intelligence_plan
from services.live_analyst_service import format_live_final_answer


def test_universal_planner_esports_total():
    plan = build_market_intelligence_plan(
        "NAVI Vitality тб 2.5 карт кэф 1.85",
        {"mode": "esports", "teams": ["NAVI", "Vitality"]},
        {},
    )
    assert plan["market_domain"] == "esports"
    assert plan["market_type"] == "total"
    assert plan["odds"] == "1.85"
    assert abs(plan["implied_probability"] - 54.1) < 0.2
    assert "recent form" in plan["needed_factors"]
    assert "line movement" in plan["needed_factors"]
    assert any("map" in x or "draft" in x for x in plan["needed_factors"])
    assert plan["research_queries"]
    assert any("recent form" in x for x in plan["must_not_invent"])
    assert any("roster" in x for x in plan["must_not_invent"])
    assert plan["side"] == "over"
    assert plan["line"] == "2.5"


def test_universal_planner_crypto():
    plan = build_market_intelligence_plan("BTCUSDT лонг от 61500 15m", {"mode": "crypto", "pair": "BTCUSDT"}, {})
    assert plan["market_domain"] == "crypto"
    assert plan["market_type"] == "price_direction"
    assert plan["timeframe"] == "15m"
    for factor in ("current price", "support/resistance", "confirmation trigger", "invalidation level"):
        assert factor in plan["needed_factors"]


def test_universal_planner_politics():
    plan = build_market_intelligence_plan("Trump win 2028 odds 2.4", {}, {})
    assert plan["market_domain"] in ("politics", "event")
    assert "polling" in plan["needed_factors"]
    assert any("news" in x for x in plan["needed_factors"])
    assert any("calendar" in x for x in plan["needed_factors"])
    assert any("market rules" in x for x in plan["needed_factors"])
    assert plan["research_queries"]


def test_universal_planner_generic_event():
    plan = build_market_intelligence_plan("ивент X over 3.5 odds 1.9", {}, {})
    assert plan["market_domain"] == "event"
    assert plan["market_type"] == "total"
    for factor in ("event rules", "participants", "timeline", "current odds"):
        assert factor in plan["needed_factors"]


def test_universal_calculate_value_no_independent_probability():
    plan = build_market_intelligence_plan("NAVI Vitality тб 2.5 карт кэф 1.85", {"mode": "esports", "teams": ["NAVI", "Vitality"]}, {})
    answer = format_live_final_answer("", {"mode": "esports", "selected_action_id": "calculate_value", "market_intelligence_plan": plan, "derived_facts": {}, "missing_data": plan["missing_data"], "answer_policy": {"must_not_invent": plan["must_not_invent"]}, "recommended_decision_labels": ["DATA NEEDED", "WATCH"]}, ui_language="ru")
    assert "54.1%" in answer
    assert "edge честно не считается" in answer
    assert "Независимой оценки вероятности пока нет" in answer
    assert "evidence pack" not in answer
    assert "Decision: DATA NEEDED" in answer
    forbidden = ("ставь", "бери", "грузи", "железно", "100%", "buy now", "bet now", "lock", "guaranteed")
    assert not any(word in answer.lower() for word in forbidden)


def test_universal_ru_localization_avoids_raw_internal_labels():
    plan = build_market_intelligence_plan("NAVI Vitality тб 2.5 карт кэф 1.85", {"mode": "esports", "teams": ["NAVI", "Vitality"]}, {})
    answer = format_live_final_answer("", {"mode": "esports", "selected_action_id": "calculate_value", "market_intelligence_plan": plan, "derived_facts": {"data_freshness": "partial"}, "missing_data": plan["missing_data"], "answer_policy": {}, "recommended_decision_labels": ["DATA NEEDED"]}, ui_language="ru")
    for forbidden in ("partial", "recent form", "participant/team strength", "roster/stand-in changes", "evidence pack", "derived_facts", "market_intelligence_plan"):
        assert forbidden not in answer
    assert "Свежесть данных: частичная" in answer
    assert "свежая форма" in answer
    assert "сила участников / команд" in answer
    assert "движение линии" in answer
    assert "Независимой оценки вероятности пока нет" in answer
    assert "Сторона / линия: over / 2.5" in answer
    assert answer.count("Событие / рынок:") == 1
    assert answer.count("Коэффициент / цена:") == 1
    assert "- Событие:" not in answer
    assert "- Коэффициент:" not in answer
