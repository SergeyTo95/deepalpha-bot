import re
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None, get=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services.live_understanding_service import understand_live_request
from services.live_analyst_service import format_live_final_answer, build_sports_betting_analysis


def _pack(understanding, estimated=None, sources=True):
    facts = {
        "understanding": understanding,
        "sports_context": {"sources": ([{"title": "preview", "snippet": "news"}] if sources else []), "teams": understanding.get("teams") or []},
    }
    if estimated is not None:
        facts["estimated_probability"] = estimated
    return {
        "mode": "sports",
        "intent": understanding.get("intent"),
        "derived_facts": facts,
        "missing_data": [] if sources else ["odds", "lineups/injuries"],
        "recommended_decision_labels": ["DATA NEEDED", "WATCH"],
    }


def test_ru_direct_betting_without_odds_data_needed_or_watch():
    u = understand_live_request("На кого ставить Реал — Барса?", {"mode": "unknown"}, {}, "ru")
    answer = build_sports_betting_analysis("На кого ставить Реал — Барса?", {}, _pack(u, sources=False), "ru")
    assert u["mode"] == "sports"
    assert "ставь железно" not in answer.lower()
    assert re.search(r"Decision: (DATA NEEDED|WATCH)", answer)
    assert "Без коэффициента нельзя понять value" in answer or "кэф" in answer.lower()


def test_ru_question_with_odds_edge_candidate():
    u = understand_live_request("Реал — Барса, Реал кэф 1.95, есть ставка?", {"mode": "sports"}, {}, "ru")
    answer = build_sports_betting_analysis("Реал — Барса, Реал кэф 1.95, есть ставка?", {"sources": [{"title": "odds"}], "teams": u["teams"]}, _pack(u, estimated=0.58, sources=True), "ru")
    assert "Implied probability" in answer
    assert "51.3%" in answer
    assert "Моя оценка" in answer and "58.0%" in answer
    assert "Edge" in answer
    assert "Minimum playable odds" in answer or "миним" in answer.lower()
    assert "Decision: EDGE CANDIDATE" in answer


def test_no_edge_odds_too_low():
    u = understand_live_request("Реал — Барса, Реал кэф 1.40, есть ставка?", {"mode": "sports"}, {}, "ru")
    answer = build_sports_betting_analysis("Реал — Барса, Реал кэф 1.40, есть ставка?", {"sources": [{"title": "odds"}], "teams": u["teams"]}, _pack(u, estimated=0.58, sources=True), "ru")
    assert re.search(r"Decision: (NO EDGE|NO BET)", answer)
    assert "не playable" in answer or "недостаточно" in answer


def test_unsafe_phrases_filtered():
    answer = format_live_final_answer("железно ставь, 100%, all-in, гарантия\nDecision: EDGE CANDIDATE", {"mode": "sports", "derived_facts": {"understanding": {"teams": ["A", "B"], "sport": "football", "odds": "2.10"}, "estimated_probability": 0.52, "sports_context": {"sources": [{"title": "x"}], "teams": ["A", "B"]}}, "missing_data": []}, "ru")
    low = answer.lower()
    assert "железно" not in low
    assert "100%" not in low
    assert "all-in" not in low
    assert "гарантия" not in low


def test_generic_ufc_recognized_data_needed_style_matchup():
    u = understand_live_request("UFC: Fighter A vs Fighter B, на кого ставить?", {"mode": "unknown"}, {}, "ru")
    answer = build_sports_betting_analysis("UFC: Fighter A vs Fighter B, на кого ставить?", {}, _pack(u, sources=False), "ru")
    assert u["sport"] == "mma"
    assert "style matchup" in answer or "ударк" in answer.lower() or "борьб" in answer.lower()
    assert "Decision: DATA NEEDED" in answer


def test_final_decision_one_line_sports_labels_only():
    answer = format_live_final_answer("Short\nDecision: NO TRADE", {"mode": "sports", "derived_facts": {"understanding": {"teams": ["A", "B"], "sport": "football"}, "sports_context": {"sources": [], "teams": ["A", "B"]}}}, "en")
    decisions = re.findall(r"(?m)^Decision: ([A-Z ]+)$", answer)
    assert len(decisions) == 1
    assert decisions[0] in {"NO BET", "NO EDGE", "WATCH", "DATA NEEDED", "EDGE CANDIDATE"}


def test_production_path_without_odds_uses_professional_betting_format():
    pack = {
        "mode": "sports",
        "intent": "betting_angle",
        "derived_facts": {
            "understanding": {
                "intent": "betting_angle",
                "teams": ["Реал", "Барса"],
                "sport": "football",
                "market": "moneyline",
                "odds": "",
            },
            "sports_context": {"sources": [], "teams": ["Реал", "Барса"]},
        },
        "missing_data": ["odds"],
    }
    answer = format_live_final_answer("На кого ставить Реал — Барса?\nDecision: DATA NEEDED", pack, "ru")
    low = answer.lower()
    assert "🏟 Коротко:" in answer
    assert "Данные:" in answer
    assert "Value:" in answer
    assert "Риск:" in answer
    assert re.search(r"Decision: (DATA NEEDED|WATCH)", answer)
    assert "Без коэффициента нельзя понять value" in answer
    assert "ставь железно" not in low
    assert "100%" not in low
    assert "гарантия" not in low


def test_total_line_is_not_parsed_as_odds_in_production_path():
    u = understand_live_request("Реал — Барса, тотал 2.5 больше?", {"mode": "sports"}, {}, "ru")
    pack = _pack(u, sources=False)
    answer = format_live_final_answer("Реал — Барса, тотал 2.5 больше?\nDecision: DATA NEEDED", pack, "ru")
    assert "- Рынок: total" in answer
    assert "- Коэффициент: не указан" in answer
    assert "- Implied probability: —" in answer
    assert "40.0%" not in answer
    assert re.search(r"Decision: (DATA NEEDED|WATCH)", answer)


def test_handicap_line_is_not_parsed_as_odds_in_production_path():
    u = understand_live_request("Lakers vs Celtics фора -3.5 есть ставка?", {"mode": "sports"}, {}, "ru")
    pack = _pack(u, sources=False)
    answer = format_live_final_answer("Lakers vs Celtics фора -3.5 есть ставка?\nDecision: DATA NEEDED", pack, "ru")
    assert "- Рынок: handicap" in answer
    assert "- Коэффициент: не указан" in answer
    assert "- Implied probability: —" in answer
    assert "28.6%" not in answer
    assert re.search(r"Decision: (DATA NEEDED|WATCH)", answer)


def test_explicit_odds_still_parsed_in_production_path():
    u = understand_live_request("Реал — Барса, Реал кэф 1.95, есть ставка?", {"mode": "sports"}, {}, "ru")
    pack = _pack(u, estimated=0.58, sources=True)
    answer = format_live_final_answer("Реал — Барса, Реал кэф 1.95, есть ставка?\nDecision: DATA NEEDED", pack, "ru")
    assert "- Коэффициент: 1.95" in answer
    assert "- Implied probability: 51.3%" in answer
    assert "- Edge: +6.7 pp" in answer
    assert "Decision: EDGE CANDIDATE" in answer


def test_lakers_celtics_overrides_bad_mma_context_odds_without_estimate():
    pack = {
        "mode": "sports",
        "intent": "betting_angle",
        "derived_facts": {
            "understanding": {
                "intent": "betting_angle",
                "teams": ["Lakers", "Celtics"],
                "sport": "mma",
                "league": "",
                "market": "total",
                "odds": "1.95",
            },
            "sports_context": {"sources": [], "teams": ["Lakers", "Celtics"], "sport": "mma"},
        },
        "missing_data": ["lineups/injuries"],
    }
    answer = format_live_final_answer("Lakers — Celtics total кэф 1.95\nDecision: DATA NEEDED", pack, "ru")
    low = answer.lower()
    assert "Спорт/лига: basketball / NBA" in answer
    assert "style matchup: ударка/борьба" not in answer
    assert "pace" in low or "rotation" in low
    assert "Коэффициент: 1.95" in answer
    assert "Implied probability: 51.3%" in answer
    assert "Без коэффициента нельзя понять value" not in answer
    assert "Коэффициент есть, implied probability посчитана" in answer
    assert "Decision: DATA NEEDED" in answer


def test_ufc_event_keeps_mma_key_factors_with_odds_no_estimate():
    u = understand_live_request("UFC: Fighter A vs Fighter B, кэф 1.95, есть ставка?", {"mode": "sports"}, {}, "ru")
    answer = format_live_final_answer("UFC: Fighter A vs Fighter B, кэф 1.95, есть ставка?\nDecision: DATA NEEDED", _pack(u, sources=False), "ru")
    assert "Спорт/лига: mma / UFC" in answer
    assert "style matchup" in answer or "ударка/борьба" in answer
    assert "Спорт/лига: basketball" not in answer


def test_lakers_celtics_missing_odds_keeps_no_odds_value_copy():
    u = understand_live_request("На кого ставить Lakers — Celtics?", {"mode": "sports"}, {}, "ru")
    answer = format_live_final_answer("На кого ставить Lakers — Celtics?\nDecision: DATA NEEDED", _pack(u, sources=False), "ru")
    assert "Спорт/лига: basketball / NBA" in answer
    assert "Коэффициент: не указан" in answer
    assert "Без коэффициента нельзя понять value" in answer
    assert re.search(r"Decision: (DATA NEEDED|WATCH)", answer)


def test_lakers_celtics_edge_candidate_with_estimated_probability():
    u = understand_live_request("Lakers — Celtics, Lakers кэф 1.95, есть ставка?", {"mode": "sports"}, {}, "ru")
    pack = _pack(u, estimated=0.58, sources=True)
    answer = format_live_final_answer("Lakers — Celtics, Lakers кэф 1.95, есть ставка?\nDecision: DATA NEEDED", pack, "ru")
    assert "Спорт/лига: basketball / NBA" in answer
    assert "Implied probability: 51.3%" in answer
    assert "Моя оценка: 58.0%" in answer
    assert "Edge: +6.7 pp" in answer
    assert "Decision: EDGE CANDIDATE" in answer
