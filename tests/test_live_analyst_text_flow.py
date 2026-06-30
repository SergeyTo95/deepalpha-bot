from services.live_analyst_service import finalize_live_answer


MARKET_WATCH = (
    "🧠 Коротко:\n"
    "WATCH: данных недостаточно для уверенного входа; лучше дождаться подтверждения.\n\n"
    "Данные:\n"
    "Качество evidence: medium. Не хватает: teams, event_time.\n\n"
    "Decision: WATCH"
)


def test_technical_debug_generated_market_like_answer_gets_replaced():
    composer = {
        "composer_mode": "technical_debug",
        "fallback_answer": (
            "LIKELY CAUSE: conflict getUpdates — две polling-инстанции с одним BOT_TOKEN "
            "одновременно читают updates в Railway после redeploy. FIX NEEDED: проверь "
            "active deployments, webhook/polling и old container."
        ),
    }

    final = finalize_live_answer(
        MARKET_WATCH,
        composer,
        evidence_pack={},
        ui_language="ru",
    )

    for expected in ("getUpdates", "polling", "BOT_TOKEN", "Railway"):
        assert expected in final
    assert "LIKELY CAUSE" in final or "FIX NEEDED" in final
    for forbidden in (
        "WATCH: данных недостаточно для уверенного входа",
        "teams, event_time",
        "уровней/коэффициентов",
        "Decision: WATCH",
        "Implied probability",
        "Edge",
        "moneyline",
        "american_football",
    ):
        assert forbidden not in final


def test_technical_debug_valid_llm_answer_is_preserved():
    answer = (
        "Похоже на conflict getUpdates: две polling-инстанции с одним BOT_TOKEN одновременно "
        "читают updates в Railway после redeploy. Проверь активные deployments, "
        "webhook/polling и старый контейнер. Итог: LIKELY CAUSE / FIX NEEDED."
    )
    composer = {"composer_mode": "technical_debug", "fallback_answer": "fallback"}

    assert finalize_live_answer(answer, composer, evidence_pack={}) == answer


def test_business_generated_market_like_answer_gets_replaced():
    composer = {
        "composer_mode": "business",
        "fallback_answer": (
            "Определи цель/goal, аудиторию/audience и бюджет/budget. Затем задай CAC, "
            "payback, метрики/metrics и запусти малый test/experiment."
        ),
    }

    final = finalize_live_answer(MARKET_WATCH, composer, evidence_pack={})

    lowered = final.lower()
    for expected in ("цель", "audience", "budget", "cac", "payback", "test"):
        assert expected in lowered
    for forbidden in ("teams", "event_time", "уверенного входа", "implied probability", "moneyline"):
        assert forbidden not in lowered


def test_existing_esports_odds_flow_unchanged_for_market_mode():
    answer = (
        "NAVI — Vitality\n"
        "Market: over / 2.5 maps at 1.85\n"
        "Implied probability: 54.1%\n"
        "Decision: DATA NEEDED / WATCH\n"
        "No invented rosters/form."
    )
    composer = {"composer_mode": "esports"}

    final = finalize_live_answer(answer, composer, evidence_pack={})

    assert "NAVI — Vitality" in final
    assert "over / 2.5" in final
    assert "1.85" in final
    assert "54.1%" in final
    assert "DATA NEEDED" in final or "NO EDGE" in final or "WATCH" in final
    assert "invented rosters/form" in final
