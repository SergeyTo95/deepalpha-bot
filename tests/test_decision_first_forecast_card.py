from types import SimpleNamespace

from agents.decision_first_trading_plan_agent import build_decision_summary
from run_bot_process import polling_disabled_reason
from services.decision_first_renderer_patch import (
    build_decision_first_block,
    install,
)
from services.webapp_report_formatter import build_webapp_analysis_report


def _card(
    *,
    fair_yes=20.0,
    fair_no=80.0,
    market_yes=22.5,
    market_no=77.5,
    edge_yes=-2.5,
    edge_no=2.5,
    confidence="low",
    decision="WAIT",
    best_side="NO",
    independent=True,
):
    return {
        "version": "1.0",
        "market": {
            "market_price": {"YES": market_yes, "NO": market_no},
        },
        "model": {
            "model_level": 1,
            "point_estimate": {"YES": fair_yes, "NO": fair_no},
            "confidence": confidence,
            "independent_probability": independent,
        },
        "value": {
            "market_price": {"YES": market_yes, "NO": market_no},
            "edge": {"YES": edge_yes, "NO": edge_no},
            "decision": decision,
            "best_side": best_side,
            "entry_price": {},
        },
        "what_would_change": ["Verified Truth Social posting-rate data appears."],
        "data_requirements": [],
        "evidence": {"missing_data": []},
    }


def test_small_low_confidence_edge_becomes_watch_without_fake_buy_price():
    summary = build_decision_summary(
        forecast_card=_card(),
        analysis_quality={"quality_score": 0.2},
        lang="ru",
    )

    assert summary["verdict"] == "WATCH"
    assert summary["entry_now"] is False
    assert summary["side"] == "NO"
    assert summary["fair_probability"] == 80.0
    assert summary["market_probability"] == 77.5
    assert summary["edge_pp"] == 2.5
    assert summary["watch_edge_required_pp"] == 5.0
    assert summary["watch_price_max"] == 75.0
    assert summary["minimum_edge_required_pp"] is None
    assert summary["entry_price_max"] is None
    assert summary["buy_available"] is False
    assert summary["buy_blocked_reason"] == "confidence_below_medium"
    assert summary["data_quality_score"] == 2
    assert "buy недоступен" in summary["reason"].lower()


def test_strong_medium_confidence_consider_becomes_buy_at_actual_policy_threshold():
    summary = build_decision_summary(
        forecast_card=_card(
            fair_yes=34.0,
            fair_no=66.0,
            market_yes=22.5,
            market_no=77.5,
            edge_yes=11.5,
            edge_no=-11.5,
            confidence="medium",
            decision="CONSIDER",
            best_side="YES",
        ),
        analysis_quality={"quality_score": 0.82},
        lang="ru",
    )

    assert summary["verdict"] == "BUY"
    assert summary["entry_now"] is True
    assert summary["side"] == "YES"
    assert summary["watch_edge_required_pp"] == 5.0
    assert summary["minimum_edge_required_pp"] == 8.1
    assert summary["watch_price_max"] == 29.0
    assert summary["entry_price_max"] == 25.9
    assert summary["buy_available"] is True
    assert summary["data_quality_score"] == 8


def test_medium_confidence_watch_uses_buy_threshold_not_three_point_margin():
    summary = build_decision_summary(
        forecast_card=_card(
            fair_yes=34.0,
            fair_no=66.0,
            market_yes=28.0,
            market_no=72.0,
            edge_yes=6.0,
            edge_no=-6.0,
            confidence="medium",
            decision="WATCH",
            best_side="YES",
        ),
        analysis_quality={"quality_score": 0.6},
        lang="ru",
    )

    assert summary["verdict"] == "WATCH"
    assert summary["edge_pp"] == 6.0
    assert summary["minimum_edge_required_pp"] == 8.1
    assert summary["entry_price_max"] == 25.9
    assert summary["market_probability"] == 28.0
    assert summary["market_probability"] > summary["entry_price_max"]
    assert "более +8.0" in summary["reason"]


def test_market_fallback_is_always_no_trade():
    summary = build_decision_summary(
        forecast_card=_card(
            fair_yes=22.5,
            fair_no=77.5,
            edge_yes=0.0,
            edge_no=0.0,
            decision="WATCH",
            independent=False,
        ),
        analysis_quality={"quality_score": 0.1},
        lang="ru",
    )

    assert summary["verdict"] == "NO_TRADE"
    assert summary["entry_now"] is False
    assert summary["independent_probability"] is False
    assert summary["watch_price_max"] is None
    assert summary["entry_price_max"] is None
    assert "отдельная ai-оценка" in summary["reason"].lower()


def test_renderer_places_actionable_summary_before_long_analysis():
    summary = build_decision_summary(
        forecast_card=_card(),
        analysis_quality={"quality_score": 0.2},
        lang="ru",
    )
    forecast_card = _card()
    forecast_card["decision_summary"] = summary

    module = SimpleNamespace()
    module.get_user_lang = lambda uid: "ru"
    module._format_forecast_card_signal = lambda result, uid: (
        "🔎 DeepAlpha Signal\n\n"
        "📌 Рынок: Test\n\n"
        "🎯 Прогноз исхода:\n"
        "👉 Наиболее вероятный исход: исход NO\n"
        "📌 Оценка DeepAlpha:\n"
        "— исход YES: 20.0%\n"
        "— исход NO: 80.0%"
    )

    install(module)
    text = module._format_forecast_card_signal(
        {"forecast_card": forecast_card, "lang": "ru"},
        1,
    )

    assert text.index("🎯 РЕШЕНИЕ:") < text.index("📌 Рынок:")
    assert "WATCH — НАБЛЮДАТЬ, НЕ ВХОДИТЬ" in text
    assert "Цена для усиления WATCH NO: 75% или ниже" in text
    assert "Порог BUY: сначала нужна уверенность не ниже средней" in text
    assert "Интересная цена" not in text
    assert "Качество данных: 2/10" in text
    assert "исход NO" not in text
    assert "— YES: 20.0%" in text


def test_block_exposes_fair_price_edge_and_separate_watch_policy():
    summary = build_decision_summary(
        forecast_card=_card(),
        analysis_quality={"quality_score": 0.2},
        lang="ru",
    )
    block = build_decision_first_block(summary, lang="ru")

    assert "Справедливая вероятность: 80%" in block
    assert "Цена рынка: 77.5%" in block
    assert "Edge: +2.5 п.п." in block
    assert "Порог усиления WATCH: +5.0 п.п." in block
    assert "Порог BUY: сначала нужна уверенность не ниже средней" in block
    assert "Минимум для входа: +5.0" not in block


def test_webapp_report_receives_same_decision_first_canonical_text():
    summary = build_decision_summary(
        forecast_card=_card(),
        analysis_quality={"quality_score": 0.2},
        lang="ru",
    )
    forecast_card = _card()
    forecast_card["decision_summary"] = summary
    raw = {
        "question": "Test market",
        "lang": "ru",
        "full_analysis": "🔎 DeepAlpha Signal\n\n📌 Рынок: Test market\n\n— исход NO: 80.0%",
        "forecast_card": forecast_card,
        "decision_summary": summary,
    }

    report = build_webapp_analysis_report(raw, market_url="https://polymarket.com/event/test", lang="ru")

    assert report["canonical_text"].index("🎯 РЕШЕНИЕ:") < report["canonical_text"].index("📌 Рынок:")
    assert report["telegram_text"] == report["canonical_text"]
    assert report["copy_text"] == report["canonical_text"]
    assert "исход NO" not in report["canonical_text"]
    assert report["decision_summary"]["verdict"] == "WATCH"
    assert report["sections"]["decision_summary"]["side"] == "NO"
    assert report["sections"]["forecast_card"]["version"] == "1.0"


def test_preview_and_wrong_branch_never_poll_telegram():
    assert polling_disabled_reason({"BOT_POLLING_ENABLED": "false"}) == "BOT_POLLING_ENABLED=false"
    assert polling_disabled_reason({
        "BOT_POLLING_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "preview",
    }) == "non_production_environment:preview"
    assert polling_disabled_reason({
        "BOT_POLLING_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/decision-first-forecast-card",
        "BOT_PRODUCTION_BRANCH": "feature/turbo-short-term-btc",
    }) == "non_production_branch:feature/decision-first-forecast-card"


def test_production_branch_can_poll_and_preview_override_is_explicit():
    assert polling_disabled_reason({
        "BOT_POLLING_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }) is None
    assert polling_disabled_reason({
        "BOT_POLLING_ENABLED": "true",
        "BOT_POLLING_ALLOW_PREVIEW": "true",
        "RAILWAY_ENVIRONMENT_NAME": "preview",
    }) is None
