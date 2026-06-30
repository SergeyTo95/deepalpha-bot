import re
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services import live_analyst_memory_service as memory_svc
from services import live_context_memory as context_memory
from services import live_analyst_service as svc


def _patch_common(monkeypatch, balance=True):
    session = {"id": 123, "current_market_url": "", "current_market_title": ""}
    saved = []
    charges = []
    monkeypatch.setattr(svc, "is_live_enabled", lambda: True)
    monkeypatch.setattr(svc, "get_live_request_cost", lambda message_type: 1)
    monkeypatch.setattr(svc, "can_user_afford_live_request", lambda user_id, cost: balance)
    monkeypatch.setattr(svc, "get_max_daily_live_messages", lambda: 0)
    monkeypatch.setattr(svc, "get_or_create_active_session", lambda user_id: dict(session))
    monkeypatch.setattr(svc, "get_memory_message_limit", lambda: 12)
    monkeypatch.setattr(svc, "get_recent_context", lambda session_id, limit: [])
    monkeypatch.setattr(svc, "update_context_from_user_text", lambda current, text: current)
    monkeypatch.setattr(svc, "save_message", lambda *args, **kwargs: saved.append((args, kwargs)))
    monkeypatch.setattr(svc, "charge_live_request", lambda user_id, cost, reason: charges.append((user_id, cost, reason)) or True)
    return saved, charges


def test_crypto_text_useful_answer_charges_once_and_saves_memory(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short conclusion: WATCH\nWhat I see: BTCUSDT 15m\nRisk: high\nDecision: WATCH")

    result = svc.process_live_text(7, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}})

    assert result["ok"] is True
    assert result["charged"] is True
    assert len(charges) == 1
    assert len(saved) == 2
    assert saved[0][0][2] == "user"
    assert saved[1][0][2] == "assistant"
    assert "crypto consultant" in prompts[0]
    assert "BTCUSDT" in prompts[0]


def test_process_live_text_injects_crypto_followup_fields_into_evidence_pack(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    context_memory.clear_live_context_memory()
    context_memory.save_live_context(101, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", last_final_answer="Decision: WATCH")
    evidence_pack = {
        "mode": "crypto",
        "intent": "entry_now",
        "derived_facts": {"current_price": 64000, "support_levels": [63800], "resistance_levels": [64500]},
        "answer_policy": {"can_give_levels": True, "can_give_entry_zone": True, "can_comment_on_odds": False, "must_not_invent": []},
        "recommended_decision_labels": ["WATCH"],
    }
    prompts = []
    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Коротко: WATCH\nDecision: WATCH")

    result = svc.process_live_text(101, "а если лонг от 64500?", router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert len(charges) == 1
    assert saved
    assert evidence_pack["followup_type"] == "long_position"
    assert evidence_pack["followup_level"] == "64500"
    assert evidence_pack["followup_timeframe"] == "15m"
    assert "Follow-up type: long_position" in prompts[0]
    assert "Follow-up level: 64500" in prompts[0]


def test_sports_text_useful_answer_path(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Short conclusion: DATA NEEDED\nWhat I see: odds 1.85\nRisk: missing live stats\nDecision: DATA NEEDED")

    result = svc.process_live_text(8, "Team A vs Team B odds 1.85", router_result={"mode": "sports", "entities": {"teams": ["Team A", "Team B"], "odds": 1.85}})

    assert result["ok"] is True
    assert len(charges) == 1
    assert len(saved) == 2


def test_insufficient_balance_blocks_before_llm_and_charge(monkeypatch):
    _saved, charges = _patch_common(monkeypatch, balance=False)
    called = False
    def fake_llm(*args, **kwargs):
        nonlocal called
        called = True
        return "answer"
    monkeypatch.setattr(svc, "generate_live_analyst_text", fake_llm)

    result = svc.process_live_text(9, "BTCUSDT 15m", router_result={"mode": "crypto"})

    assert result["ok"] is False
    assert called is False
    assert charges == []


def test_empty_model_failure_does_not_charge_or_save_assistant(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    calls = []
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: calls.append(args) or "")

    result = svc.process_live_text(10, "Team A vs Team B odds 1.85", router_result={"mode": "sports"})

    assert result["ok"] is False
    assert result["charged"] is False
    assert len(calls) == 1
    assert charges == []
    assert saved == []


def test_polymarket_empty_entities_do_not_overwrite_market_title(monkeypatch):
    _saved, _charges = _patch_common(monkeypatch)
    context_updates = []
    monkeypatch.setattr(memory_svc, "update_current_market_context", lambda session, **kwargs: context_updates.append(kwargs) or session)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Short conclusion: WATCH\nDecision: WATCH")

    result = svc.process_live_text(11, "дай премиум", router_result={"mode": "polymarket", "entities": {}})

    assert result["ok"] is True
    assert context_updates == []


def test_crypto_entities_update_useful_context_title(monkeypatch):
    _saved, _charges = _patch_common(monkeypatch)
    context_updates = []
    monkeypatch.setattr(memory_svc, "update_current_market_context", lambda session, **kwargs: context_updates.append(kwargs) or session)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Short conclusion: WATCH\nDecision: WATCH")

    result = svc.process_live_text(12, "BTCUSDT 15m Binance", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "exchange": "Binance"}})

    assert result["ok"] is True
    assert len(context_updates) == 1
    assert context_updates[0]["market_title"] == "pair=BTCUSDT; asset=BTC; timeframe=15m; exchange=Binance"


def test_sports_entities_update_useful_context_title(monkeypatch):
    _saved, _charges = _patch_common(monkeypatch)
    context_updates = []
    monkeypatch.setattr(memory_svc, "update_current_market_context", lambda session, **kwargs: context_updates.append(kwargs) or session)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Short conclusion: DATA NEEDED\nDecision: DATA NEEDED")

    result = svc.process_live_text(13, "Team A vs Team B odds 1.85", router_result={"mode": "sports", "entities": {"teams": ["Team A", "Team B"], "odds": 1.85}})

    assert result["ok"] is True
    assert len(context_updates) == 1
    assert context_updates[0]["market_title"] == "teams=['Team A', 'Team B']; odds=1.85"


def test_crypto_asset_without_pair_still_uses_paid_consultant_path(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short conclusion: DATA NEEDED/WATCH\nDecision: DATA NEEDED\nNext step: пришли таймфрейм")

    result = svc.process_live_text(
        14,
        "биткоин сейчас покупать или не нужно?",
        router_result={"mode": "crypto", "entities": {"asset": "BTC"}, "missing_data": ["timeframe"]},
    )

    assert result["ok"] is True
    assert result["charged"] is True
    assert len(charges) == 1
    assert len(saved) == 2
    assert "crypto consultant" in prompts[0]
    assert "Decision:" in result["message"]
    assert "asset': 'BTC" in prompts[0]


def test_unknown_mode_asks_clarification_and_does_not_charge(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    called = False

    def fake_llm(*args, **kwargs):
        nonlocal called
        called = True
        return "answer"

    monkeypatch.setattr(svc, "generate_live_analyst_text", fake_llm)

    result = svc.process_live_text(15, "что думаешь?", router_result={"mode": "unknown"})

    assert result["ok"] is False
    assert result["charged"] is False
    assert result["needs_clarification"] is True
    assert called is False
    assert charges == []
    assert saved == []


def test_ru_prompt_contains_russian_language_instruction():
    prompt = svc._build_live_prompt({"id": 1}, [], "BTC", {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="ru")
    assert "Отвечай на русском." in prompt


def test_en_prompt_contains_english_language_instruction():
    prompt = svc._build_live_prompt({"id": 1}, [], "BTC", {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en")
    assert "Reply in English." in prompt


def test_research_context_included_in_prompt():
    prompt = svc._build_live_prompt(
        {"id": 1}, [], "BTC now", {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en",
        research_context={"ok": True, "summary": "BTC spot ETF flow summary", "freshness": "fresh", "sources": [{"title": "Source", "url": "https://example.com", "published_at": "today"}]},
    )
    assert "BTC spot ETF flow summary" in prompt
    assert "https://example.com" in prompt


def test_research_failure_does_not_crash_answer_path(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: True)
    monkeypatch.setattr(svc, "get_live_research_context", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Short take: limited\nDecision: DATA NEEDED\nNext step: send chart")
    result = svc.process_live_text(16, "should I buy bitcoin now?", router_result={"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en")
    assert result["ok"] is True
    assert len(charges) == 1
    assert len(saved) == 2


def test_unknown_mode_english_clarification_and_does_not_charge(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "answer")
    result = svc.process_live_text(17, "what?", router_result={"mode": "unknown"}, ui_language="en")
    assert result["ok"] is False
    assert result["charged"] is False
    assert "Please clarify" in result["message"]
    assert charges == []
    assert saved == []


def test_ru_thinking_message_helper_returns_russian_text():
    from services.live_language_service import get_live_thinking_message
    assert get_live_thinking_message("ru") == "🧠 Думаю… проверяю свежий контекст, риск и возможные сценарии."


def test_en_thinking_message_helper_returns_english_text():
    from services.live_language_service import get_live_thinking_message
    assert get_live_thinking_message("en") == "🧠 Thinking… checking fresh context, risk, and possible scenarios."


def test_live_research_disabled_provider_returns_not_ok(monkeypatch):
    from services import live_research_service as research
    research._CACHE.clear()
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "false")
    result = research.get_live_research_context("BTC now", "crypto", {"asset": "BTC"}, "en")
    assert result["ok"] is False
    assert result["sources"] == []


def test_fresh_context_needed_crypto_asset_true():
    from services.live_research_service import fresh_context_needed
    assert fresh_context_needed("биткоин сейчас покупать или не нужно?", "crypto", {"asset": "BTC"}) is True


def test_live_research_mocked_existing_search_success(monkeypatch):
    from services import live_research_service as research
    research._CACHE.clear()
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(research, "_run_existing_search", lambda *args, **kwargs: [{"title": "BTC Market", "url": "https://example.com", "source": "example", "published": "today", "snippet": "BTC fresh summary"}])
    result = research.get_live_research_context("BTC now", "crypto", {"asset": "BTC"}, "en")
    assert result["ok"] is True
    assert "BTC fresh summary" in result["summary"]
    assert result["sources"][0]["url"] == "https://example.com"


def test_process_live_text_includes_research_summary_and_charges_once(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: True)
    monkeypatch.setattr(svc, "get_live_research_context", lambda *args, **kwargs: {"ok": True, "summary": "BTC fresh summary", "sources": [{"title": "Market", "url": "https://example.com", "source": "example", "published_at": "today"}], "freshness": "fresh", "error": ""})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short take: WATCH\nFresh context: BTC fresh summary\nDecision: WATCH")
    result = svc.process_live_text(18, "BTC now buy?", router_result={"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en")
    assert result["ok"] is True
    assert len(charges) == 1
    assert "BTC fresh summary" in prompts[0]
    assert len(saved) == 2


def test_process_live_text_research_failure_still_charges_once(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: True)
    monkeypatch.setattr(svc, "get_live_research_context", lambda *args, **kwargs: {"ok": False, "summary": "", "sources": [], "freshness": "fresh context unavailable", "error": "provider returned no sources"})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short take: DATA NEEDED\nDecision: DATA NEEDED")
    result = svc.process_live_text(19, "BTC now buy?", router_result={"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en")
    assert result["ok"] is True
    assert len(charges) == 1
    assert "provider returned no sources" in prompts[0]
    assert len(saved) == 2


def test_live_research_enabled_auto_with_existing_web_config(monkeypatch):
    from services import live_research_service as research
    monkeypatch.delenv("LIVE_WEB_RESEARCH_ENABLED", raising=False)
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-key")
    assert research.live_research_enabled() is True


def test_live_research_enabled_explicit_false_overrides_web_config(monkeypatch):
    from services import live_research_service as research
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "false")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-key")
    assert research.live_research_enabled() is False


def test_research_ok_prompt_forbids_no_current_data_claim():
    prompt = svc._build_live_prompt(
        {"id": 1}, [], "BTC now", {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en",
        research_context={"ok": True, "summary": "BTC fresh summary", "freshness": "fresh", "sources": [{"title": "Market Source", "url": "https://example.com", "source": "example", "published_at": "today"}]},
    )
    assert "You DO have fresh web context" in prompt
    assert "Do not claim" in prompt
    assert "no current data" in prompt


def test_btc_research_queries_are_targeted():
    from services.live_research_service import _build_research_queries
    queries = _build_research_queries("биткоин сейчас покупать?", {"asset": "BTC"})
    assert queries == [
        "BTC price today crypto market",
        "Bitcoin BTC latest market news today",
        "BTC USDT price trend today",
        "Bitcoin ETF flows crypto market today",
    ]


def test_crypto_ru_prompt_enforces_consultant_decision_first_with_research_ok():
    prompt = svc._build_live_prompt(
        {"id": 1}, [], "биткоин сейчас покупать или не нужно?",
        {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="ru",
        research_context={"ok": True, "summary": "BTC trades near a range", "freshness": "fresh", "sources": [{"title": "CoinDesk", "url": "https://example.com", "published_at": "today"}]},
    )
    assert "не говори «невозможно принять решение» первым" in prompt
    assert "Сначала короткий вывод" in prompt
    assert "Decision-first" in prompt
    assert "WATCH / DATA NEEDED / NO TRADE / EDGE CANDIDATE" in prompt
    assert "Дальше" in prompt


def test_crypto_en_prompt_enforces_consultant_decision_first_with_research_ok():
    prompt = svc._build_live_prompt(
        {"id": 1}, [], "should I buy bitcoin now?",
        {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en",
        research_context={"ok": True, "summary": "BTC fresh summary", "freshness": "fresh", "sources": [{"title": "Coinbase", "url": "https://example.com", "published_at": "today"}]},
    )
    assert "Decision-first" in prompt
    assert "Fresh context" in prompt
    assert "Next" in prompt
    assert "WATCH / DATA NEEDED / NO TRADE / EDGE CANDIDATE" in prompt


def test_crypto_prompt_research_failure_instructs_cautious_fallback_without_pretending_current_data():
    prompt = svc._build_live_prompt(
        {"id": 1}, [], "BTC now buy?",
        {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en",
        research_context={"ok": False, "summary": "", "freshness": "fresh context unavailable", "sources": [], "error": "disabled"},
    )
    assert "fresh search returned no sources/is disabled" in prompt
    assert "answer cautiously with DATA NEEDED/WATCH" in prompt
    assert "do not pretend" in prompt.lower() or "не притворяйся" in prompt
    assert "Fresh search did not return sources / is disabled, so this is limited." in prompt


def test_process_live_text_market_context_better_zone_in_prompt(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "get_crypto_market_context", lambda *args, **kwargs: {"ok": True, "pair": "BTCUSDT", "timeframe": "1h", "price": 64050, "price_source": "mock", "support_levels": [63500], "resistance_levels": [64800], "local_high": 64800, "local_low": 63500, "volatility_note": "mock", "entry_context": {"better_zone": 63500, "current_entry_quality": "risky", "confirmation": "reaction", "invalidation": "below 63500"}, "sources": ["mock"], "error": ""})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short take: WATCH\nDecision: WATCH")
    result = svc.process_live_text(20, "биткоин сейчас покупать или не нужно?", router_result={"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="ru")
    assert result["ok"] is True
    assert len(charges) == 1
    assert "63500" in prompts[0]
    assert "use the derived price, support/resistance, better_zone" in prompts[0]


def test_crypto_prompt_market_context_false_forbids_inventing_levels():
    prompt = svc._build_live_prompt({"id": 1}, [], "BTC now buy?", {"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en", crypto_market_context={"ok": False, "error": "no data", "support_levels": [], "resistance_levels": []})
    assert "do not invent entry levels" in prompt
    assert "DATA NEEDED/WATCH" in prompt


def test_sports_understanding_calls_context_and_prompt_includes_rules(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    called = []
    monkeypatch.setattr(svc, "get_sports_context", lambda understanding, ui_language="ru": called.append(understanding) or {"ok": True, "partial": True, "sport": "football", "teams": understanding.get("teams"), "sources": [{"title": "source", "url": "https://example.com"}], "news_summary": "mock sports news"})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "🧠 Коротко:\nWATCH\nDecision:\nWATCH")
    result = svc.process_live_text(21, "Реал — Барса тотал 2.5, есть value?", router_result={"mode": "sports", "entities": {"teams": ["Real", "Barcelona"]}}, ui_language="ru")
    assert result["ok"] is True
    assert len(charges) == 1
    assert called
    assert "Sports data context" in prompts[0]
    assert "Do not invent kickoff time" in prompts[0]
    assert "NO BET / WATCH / DATA NEEDED / EDGE CANDIDATE" in prompts[0]
    assert "mock sports news" in prompts[0]


def test_incomplete_live_answer_detection():
    assert svc._is_incomplete_live_answer("🧠 Коротко...\n\nС", mode="crypto", ui_language="ru") is True
    complete = "🧠 Коротко:\n" + ("WATCH. " * 45) + "\n\nРиск:\nHigh.\n\nDecision: WATCH"
    assert svc._is_incomplete_live_answer(complete, mode="crypto", ui_language="ru") is False
    assert svc._is_incomplete_live_answer("Коротко:", mode="crypto", ui_language="ru") is True


def test_process_live_text_repairs_truncated_answer_and_charges_once(monkeypatch):
    _patch_common(monkeypatch)
    charges = []
    calls = []
    research_calls = []
    answers = iter([
        "🧠 Коротко:\nТекущая позиция нейтральна.\n\nС",
        "🧠 Коротко:\nWATCH: сейчас лучше ждать подтверждения, а не входить по рынку.\n\nДанные:\nЕсть только ограниченный контекст по BTCUSDT 15m; подтверждённых уровней для входа недостаточно. " + ("x" * 220) + "\n\nСценарий:\nЖдать реакции на уровень и прислать график.\n\nРиск:\nБез графика сигнал может быть ложным.\n\nDecision: WATCH",
    ])
    monkeypatch.setattr(svc, "charge_live_request", lambda user_id, cost, reason: charges.append((user_id, cost, reason)) or True)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: calls.append(prompt) or next(answers))
    monkeypatch.setattr(svc, "get_live_research_context", lambda *args, **kwargs: research_calls.append(args) or {"ok": False, "summary": "", "sources": []})

    result = svc.process_live_text(77, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")

    assert result["ok"] is True
    assert "Decision: WATCH" in result["message"]
    assert len(calls) == 2
    assert len(charges) == 1
    assert len(research_calls) <= 1


def test_process_live_text_uses_safe_fallback_when_repair_still_incomplete(monkeypatch):
    _patch_common(monkeypatch)
    charges = []
    calls = []
    answers = iter([
        "🧠 Коротко:\nBTCUSDT 15m сейчас выглядит как WATCH, но ответ был обрезан до сценария.\n\nС",
        "",
    ])
    monkeypatch.setattr(svc, "charge_live_request", lambda user_id, cost, reason: charges.append((user_id, cost, reason)) or True)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: calls.append(prompt) or next(answers))

    result = svc.process_live_text(78, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")

    assert result["ok"] is True
    assert result["charged"] is True
    assert "Decision:" in result["message"]
    assert len(calls) == 2
    assert len(charges) == 1


def test_format_live_final_answer_normalizes_money_levels():
    result = svc.format_live_final_answer(
        "поддержки $64000.0 и сопротивления $64500.0\nDecision: WATCH",
        {"mode": "polymarket", "recommended_decision_labels": ["WATCH"]},
        ui_language="ru",
    )
    assert "$64,000" in result
    assert "$64,500" in result
    assert "$64000.0" not in result


def test_format_live_final_answer_normalizes_decision_newline():
    result = svc.format_live_final_answer(
        "Коротко: ждать\nDecision:\nWATCH",
        {"mode": "polymarket", "recommended_decision_labels": ["WATCH"]},
        ui_language="ru",
    )
    assert "Decision: WATCH" in result
    assert "Decision:\nWATCH" not in result


def test_crypto_formatter_includes_final_decision_line():
    result = svc.format_live_final_answer(
        "Коротко: сейчас скорее ждать подтверждения.",
        {"mode": "crypto", "recommended_decision_labels": ["WATCH"], "derived_facts": {}, "answer_policy": {"can_give_levels": False}},
        ui_language="ru",
    )
    assert result.endswith("Decision: WATCH")
    assert "Данные:" in result
    assert "Сценарий:" in result
    assert "Риск:" in result


def test_crypto_formatter_does_not_invent_levels_without_evidence():
    result = svc.format_live_final_answer(
        "Коротко: BTC лучше наблюдать.\nDecision: WATCH",
        {"mode": "crypto", "recommended_decision_labels": ["WATCH"], "derived_facts": {}, "answer_policy": {"can_give_levels": False}},
        ui_language="ru",
    )
    assert "$64,000" not in result
    assert "Поддержка:" not in result
    assert "Сопротивление:" not in result
    assert "подтверждённых уровней" in result


def test_crypto_formatter_normalizes_raw_evidence_levels():
    result = svc.format_live_final_answer(
        "Коротко: поддержки $64000.0 и сопротивления 64500.0 USDT.\nDecision:\nWATCH",
        {
            "mode": "crypto",
            "recommended_decision_labels": ["WATCH"],
            "derived_facts": {"current_price": 64200.0, "support_levels": [64000.0], "resistance_levels": [64500.0]},
            "answer_policy": {"can_give_levels": True},
        },
        ui_language="ru",
    )
    assert "$64,000" in result
    assert "$64,500" in result
    assert "$64000.0" not in result


def test_process_live_text_applies_final_formatter_before_saving(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "get_crypto_market_context", lambda *args, **kwargs: {
        "ok": True,
        "pair": "BTCUSDT",
        "timeframe": "15m",
        "price": 64200.0,
        "price_source": "mock",
        "support_levels": [64000.0],
        "resistance_levels": [64500.0],
        "entry_context": {"better_zone": 64000.0, "confirmation": "reclaim", "invalidation": "below 64000"},
        "sources": ["mock"],
    })
    monkeypatch.setattr(svc, "validate_live_answer_against_evidence", lambda answer, evidence_pack: {"ok": True, "severity": "none", "issues": []})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Коротко: сейчас скорее WATCH, поддержки $64000.0 и сопротивления $64500.0, вход только после подтверждения реакции от уровня. Риск — ложный пробой и быстрый возврат в диапазон, поэтому без подтверждения лучше не форсировать сделку. Сценарий — дождаться реакции, затем оценить риск.\nDecision:\nWATCH")

    result = svc.process_live_text(22, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")

    assert result["ok"] is True
    assert "$64,000" in result["message"]
    assert "$64,500" in result["message"]
    assert "Decision: WATCH" in result["message"]
    assert saved[1][0][4] == result["message"]
    assert len(charges) == 1


def test_crypto_formatter_evidence_levels_remove_missing_level_contradictions_and_stale_price():
    result = svc.format_live_final_answer(
        "Данные: текущая цена по CoinMarketCap $64,100.\nСценарий: отсутствуют уровни поддержки и сопротивления, нет оснований для входа.\nDecision: DATA NEEDED",
        {
            "mode": "crypto",
            "recommended_decision_labels": ["WATCH"],
            "derived_facts": {
                "current_price": 65536,
                "support_levels": [65500, 63868],
                "resistance_levels": [65590, 66000],
                "better_zone": 65500,
            },
            "answer_policy": {"can_give_levels": True},
        },
        ui_language="ru",
    )
    assert "$65,536" in result
    assert "Поддержка: $65,500 / $63,868" in result
    assert "Сопротивление: $65,590 / $66,000" in result
    assert "отсутствуют уровни" not in result.lower()
    assert "$64,100" not in result
    assert result.endswith("Decision: WATCH")


def test_crypto_formatter_allows_missing_levels_phrase_when_evidence_lacks_levels():
    result = svc.format_live_final_answer(
        "Коротко: подтверждённых уровней нет.\nDecision: DATA NEEDED",
        {
            "mode": "crypto",
            "recommended_decision_labels": ["DATA NEEDED"],
            "derived_facts": {"current_price": 65536, "support_levels": [], "resistance_levels": []},
            "answer_policy": {"can_give_levels": False},
        },
        ui_language="ru",
    )
    assert "$65,536" in result
    assert "Поддержка:" not in result
    assert "Сопротивление:" not in result
    assert "подтверждённых уровней нет" in result.lower()


def test_fact_list_joins_multiple_money_levels_with_slash():
    assert svc._fact_list([65500, 63868]) == "$65,500 / $63,868"


def test_process_live_text_saves_contradiction_free_crypto_answer(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "get_crypto_market_context", lambda *args, **kwargs: {
        "ok": True,
        "pair": "BTCUSDT",
        "timeframe": "15m",
        "price": 65536,
        "price_source": "mock",
        "ohlcv": [1],
        "support_levels": [65500, 63868],
        "resistance_levels": [65590, 66000],
        "entry_context": {"better_zone": 65500, "confirmation": "reaction", "invalidation": "below 63868"},
        "sources": ["mock"],
    })
    monkeypatch.setattr(svc, "validate_live_answer_against_evidence", lambda answer, evidence_pack: {"ok": True, "severity": "none", "issues": []})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Данные: текущая цена по CoinMarketCap $64,100.\nСценарий: отсутствуют уровни поддержки и сопротивления, нет оснований для входа.\nDecision: DATA NEEDED")

    result = svc.process_live_text(23, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")

    assert result["ok"] is True
    assert "$65,536" in result["message"]
    assert "Поддержка: $65,500 / $63,868" in result["message"]
    assert "Сопротивление: $65,590 / $66,000" in result["message"]
    assert "отсутствуют уровни" not in result["message"].lower()
    assert "$64,100" not in result["message"]
    assert saved[1][0][4] == result["message"]
    assert len(charges) == 1


def test_crypto_default_short_ru_uses_key_levels_not_evidence():
    result = svc._crypto_default_short("ru", True)
    assert "ключевых уровней" in result
    assert "evidence" not in result


def test_format_live_final_answer_localizes_ru_confirmation_and_invalidation():
    result = svc.format_live_final_answer(
        "Коротко: WATCH\nDecision: WATCH",
        {
            "mode": "crypto",
            "recommended_decision_labels": ["WATCH"],
            "derived_facts": {
                "support_levels": [65000],
                "resistance_levels": [65500],
                "confirmation": "Wait for reaction/reclaim from support or breakout retest on the selected timeframe.",
                "invalidation": "Scenario weakens below the nearest derived support.",
            },
            "answer_policy": {"can_give_levels": True},
        },
        ui_language="ru",
    )
    assert "Ждать реакции/возврата от поддержки" in result
    assert "Сценарий слабеет ниже ближайшей поддержки" in result
    assert "Wait for" not in result
    assert "Scenario weakens" not in result


def test_format_live_final_answer_formats_raw_evidence_levels_inside_risk():
    result = svc.format_live_final_answer(
        "Коротко: WATCH\nРиск: вход без подтверждения отскока от поддержки 65000 или сопротивления 65500\nDecision: WATCH",
        {
            "mode": "crypto",
            "recommended_decision_labels": ["WATCH"],
            "derived_facts": {"support_levels": [65000], "resistance_levels": [65500]},
            "answer_policy": {"can_give_levels": True},
        },
        ui_language="ru",
    )
    assert "$65,000" in result
    assert "$65,500" in result
    assert "поддержки 65000" not in result
    assert "сопротивления 65500" not in result


def test_format_live_final_answer_does_not_replace_timeframe_as_level():
    result = svc.format_live_final_answer(
        "Коротко: BTCUSDT 15m остается WATCH\nDecision: WATCH",
        {
            "mode": "crypto",
            "recommended_decision_labels": ["WATCH"],
            "derived_facts": {"support_levels": [65000], "resistance_levels": [65500]},
            "answer_policy": {"can_give_levels": True},
        },
        ui_language="ru",
    )
    assert "BTCUSDT 15m" in result
    assert "$15" not in result


def test_process_live_text_saves_fully_localized_ru_crypto_answer(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "get_crypto_market_context", lambda *args, **kwargs: {
        "ok": True,
        "pair": "BTCUSDT",
        "timeframe": "15m",
        "price": 65062,
        "price_source": "mock",
        "support_levels": [65000],
        "resistance_levels": [65500],
        "entry_context": {
            "better_zone": 65000,
            "confirmation": "Wait for reaction/reclaim from support or breakout retest on the selected timeframe.",
            "invalidation": "Scenario weakens below the nearest derived support.",
        },
        "sources": ["mock"],
    })
    monkeypatch.setattr(svc, "validate_live_answer_against_evidence", lambda answer, evidence_pack: {"ok": True, "severity": "none", "issues": []})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Коротко: WATCH: вход не подтверждён сейчас; лучше ждать реакции от evidence-уровней.\nДанные: BTCUSDT 15m, уровни есть в контексте.\nСценарий: ждать реакции от поддержки и не входить по рынку без подтверждения; это сохраняет риск контролируемым и не превращает идею в догон цены.\nРиск: вход без подтверждения отскока от поддержки 65000 или пробоя/ретеста сопротивления 65500.\nDecision: WATCH")

    result = svc.process_live_text(24, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")

    assert result["ok"] is True
    assert "Ждать реакции/возврата от поддержки" in result["message"]
    assert "Сценарий слабеет ниже ближайшей поддержки" in result["message"]
    assert "$65,000" in result["message"]
    assert "$65,500" in result["message"]
    assert "Wait for" not in result["message"]
    assert "Scenario weakens" not in result["message"]
    assert "evidence-уровней" not in result["message"]
    assert saved[1][0][4] == result["message"]
    assert len(charges) == 1


def _section_body(text: str, heading: str) -> str:
    pattern = rf"(?s){re.escape(heading)}:\n(.*?)(?=\n\n(?:🧠 Коротко|Данные|Сценарий|Риск|Decision|Data|Scenario|Risk):|\Z)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _crypto_evidence_with_levels(extra_facts=None):
    facts = {
        "support_levels": [62500],
        "resistance_levels": [63500],
        "better_zone": 62500,
        "confirmation": "ждать реакции от поддержки",
        "invalidation": "Сценарий слабеет ниже ближайшей поддержки.",
    }
    facts.update(extra_facts or {})
    return {
        "mode": "crypto",
        "recommended_decision_labels": ["WATCH"],
        "derived_facts": facts,
        "answer_policy": {"can_give_levels": True},
    }


def test_format_live_final_answer_removes_duplicate_short_heading():
    result = svc.format_live_final_answer(
        "🧠 Коротко: BTCUSDT на 15m находится у поддержки $62,500.\nDecision: WATCH",
        _crypto_evidence_with_levels(),
        ui_language="ru",
    )
    assert result.startswith("🧠 Коротко:\nBTCUSDT на 15m находится у поддержки $62,500.")
    assert result.count("🧠 Коротко:") == 1
    assert _section_body(result, "🧠 Коротко") == "BTCUSDT на 15m находится у поддержки $62,500."
    assert "Коротко:" not in result.replace("🧠 Коротко:", "")


def test_format_live_final_answer_removes_markdown_section_markers():
    result = svc.format_live_final_answer(
        "**Коротко:** BTCUSDT ждет подтверждения.\n**Риск:** вход без подтверждения рискован.\nDecision: WATCH",
        _crypto_evidence_with_levels(),
        ui_language="ru",
    )
    assert "**" not in result
    assert "**Коротко:**" not in result


def test_format_live_final_answer_rebuilds_risk_that_duplicates_invalidation():
    result = svc.format_live_final_answer(
        "Коротко: BTCUSDT ждет подтверждения.\nРиск: Сценарий слабеет ниже ближайшей поддержки.\nDecision: WATCH",
        _crypto_evidence_with_levels(),
        ui_language="ru",
    )
    risk = _section_body(result, "Риск")
    invalidation = _section_body(result, "Данные").split("- Инвалидация: ", 1)[1].split("\n", 1)[0]
    assert risk != invalidation
    assert "$62,500" in risk
    assert "$63,500" in risk
    assert "Вход без подтверждения" in risk


def test_format_live_final_answer_keeps_clean_ru_answer_body():
    result = svc.format_live_final_answer(
        "Коротко: BTCUSDT ждет подтверждения у поддержки.\nСценарий: ждать реакции цены.\nРиск: вход без подтверждения повышает риск ложного движения.\nDecision: WATCH",
        {"mode": "crypto", "recommended_decision_labels": ["WATCH"], "derived_facts": {}, "answer_policy": {"can_give_levels": False}},
        ui_language="ru",
    )
    assert _section_body(result, "🧠 Коротко") == "BTCUSDT ждет подтверждения у поддержки."
    assert _section_body(result, "Сценарий") == "ждать реакции цены."
    assert _section_body(result, "Риск") == "вход без подтверждения повышает риск ложного движения."


def test_format_live_final_answer_keeps_single_decision_label():
    result = svc.format_live_final_answer(
        "Коротко: Decision: WATCH\nСценарий: Scenario: ждать подтверждения.\nРиск: Risk: вход без подтверждения рискован.\nDecision:\nWATCH",
        _crypto_evidence_with_levels(),
        ui_language="ru",
    )
    assert result.count("Decision: WATCH") == 1
    assert "\nDecision:\nWATCH" not in result
    assert "Scenario:" not in result
    assert "Risk:" not in result


def test_format_live_final_answer_strips_emoji_bold_ru_short_heading():
    result = svc.format_live_final_answer(
        "🧠 **Коротко:** BTCUSDT на 15m таймфрейме находится вблизи ключевой поддержки $62,500.\nDecision: WATCH",
        _crypto_evidence_with_levels(),
        ui_language="ru",
    )
    short_body = _section_body(result, "🧠 Коротко")
    assert result.startswith("🧠 Коротко:\nBTCUSDT на 15m таймфрейме")
    assert result.count("🧠 Коротко:") == 1
    assert "🧠 Коротко:" not in result.replace("🧠 Коротко:", "", 1)
    assert "**" not in result
    assert not short_body.startswith("🧠")
    assert not short_body.startswith("Коротко:")


def test_format_live_final_answer_strips_nested_emoji_bold_english_heading():
    result = svc.format_live_final_answer(
        "🧠 **Short take:** BTCUSDT waits for confirmation.\nDecision: WATCH",
        _crypto_evidence_with_levels(),
        ui_language="en",
    )
    short_body = _section_body(result, "🧠 Short take")
    assert result.startswith("🧠 Short take:\nBTCUSDT waits for confirmation.")
    assert result.count("🧠 Short take:") == 1
    assert "Short take:" not in result.replace("🧠 Short take:", "", 1)
    assert "**" not in result
    assert not short_body.startswith("🧠")
    assert not short_body.startswith("Short take:")


def test_crypto_short_summary_strips_watch_prefix_and_keeps_final_decision():
    result = svc.format_live_final_answer(
        "WATCH: вход не подтверждён сейчас; лучше ждать реакции от ключевых уровней.\nDecision: WATCH",
        _crypto_evidence_with_levels(),
        ui_language="ru",
    )
    short_body = _section_body(result, "🧠 Коротко")
    assert not short_body.startswith("WATCH:")
    assert result.endswith("Decision: WATCH")


def test_crypto_evidence_based_short_includes_support_and_resistance_range():
    result = svc.format_live_final_answer(
        "WATCH: вход не подтверждён сейчас; лучше ждать реакции от ключевых уровней.\nDecision: WATCH",
        _crypto_evidence_with_levels({
            "current_price": 62272,
            "support_levels": [62000, 61938],
            "resistance_levels": [62500, 63095],
            "better_zone": 62000,
            "timeframe": "15m",
            "symbol": "BTCUSDT",
        }),
        ui_language="ru",
    )
    short_body = _section_body(result, "🧠 Коротко")
    assert "$62,000" in short_body
    assert "$62,500–$63,095" in short_body
    assert not short_body.startswith("WATCH:")


def test_crypto_data_needed_short_strips_duplicate_decision_label():
    result = svc.format_live_final_answer(
        "DATA NEEDED: подтверждённых уровней недостаточно.\nDecision: DATA NEEDED",
        {"mode": "crypto", "recommended_decision_labels": ["DATA NEEDED"], "derived_facts": {"symbol": "BTCUSDT", "timeframe": "15m", "support_levels": [], "resistance_levels": []}, "answer_policy": {"can_give_levels": False}},
        ui_language="ru",
    )
    short_body = _section_body(result, "🧠 Коротко")
    assert not short_body.startswith("DATA NEEDED:")
    assert result.endswith("Decision: DATA NEEDED")


def test_crypto_existing_clean_short_remains_clean():
    result = svc.format_live_final_answer(
        "Коротко: BTCUSDT находится рядом с поддержкой $62,000.\nDecision: WATCH",
        _crypto_evidence_with_levels({"support_levels": [62000], "resistance_levels": [62500], "better_zone": 62000}),
        ui_language="ru",
    )
    assert _section_body(result, "🧠 Коротко") == "BTCUSDT находится рядом с поддержкой $62,000."


def _crypto_deterministic_evidence(**overrides):
    pack = {
        "mode": "crypto",
        "intent": "entry_now",
        "derived_facts": {
            "symbol": "BTCUSDT",
            "current_price": 60370,
            "support_levels": [60219.51, 60000],
            "resistance_levels": [60500, 63239.06],
            "better_zone": 60219.51,
            "confirmation": "Wait for reaction/reclaim from support or breakout retest on the selected timeframe.",
            "invalidation": "Scenario weakens below the nearest derived support.",
        },
        "answer_policy": {"can_give_levels": True, "can_give_entry_zone": True, "can_comment_on_odds": False, "must_not_invent": []},
        "recommended_decision_labels": ["WATCH"],
    }
    pack.update(overrides)
    return pack


def test_process_live_text_uses_deterministic_crypto_fallback_when_llm_empty(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    calls = []
    evidence_pack = _crypto_deterministic_evidence()
    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: calls.append(args) or "")

    result = svc.process_live_text(201, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")

    assert result["ok"] is True
    assert result["charged"] is False
    assert result["cost"] == 0
    assert "Ответ собран без LLM" in result["message"]
    assert "Цена" in result["message"]
    assert "Поддержка" in result["message"]
    assert "Сопротивление" in result["message"]
    assert "Decision: WATCH" in result["message"]
    assert charges == []
    assert len(calls) == 1
    assert saved and saved[-1][0][2] == "assistant"


def test_process_live_text_deterministic_followup_long_position(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    context_memory.clear_live_context_memory()
    context_memory.save_live_context(202, mode="crypto", original_user_text="BTCUSDT 15m", normalized_query="BTCUSDT 15m", asset_pair="BTCUSDT", timeframe="15m", last_final_answer="Decision: WATCH")
    evidence_pack = _crypto_deterministic_evidence()
    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "")

    result = svc.process_live_text(202, "а если лонг от 64500?", router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert result["charged"] is False
    assert "Условие follow-up: лонг от $64,500" in result["message"]
    assert "Это не текущий вход" in result["message"]
    assert "долгосрочный" not in result["message"].lower()
    assert "Decision: WATCH" in result["message"]
    assert charges == []
    assert saved and saved[-1][0][2] == "assistant"


def test_process_live_text_keeps_unavailable_when_deterministic_facts_missing(monkeypatch):
    _saved, charges = _patch_common(monkeypatch)
    calls = []
    evidence_pack = _crypto_deterministic_evidence(derived_facts={"current_price": 60370})
    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: calls.append(args) or "")

    result = svc.process_live_text(203, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")

    assert result == {"ok": False, "message": svc.LIVE_UNAVAILABLE_MESSAGE, "charged": False}
    assert charges == []
    assert len(calls) == 1


def test_process_live_text_reconstructs_followup_context_from_recent_messages(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    context_memory.clear_live_context_memory()
    recent = [
        {"role": "user", "content": "BTCUSDT 15m есть вход?"},
        {"role": "assistant", "content": "Данные:\n\n- Цена: $59,670\n- Поддержка: $59,500 / $59,339\n- Сопротивление: $60,000 / $63,239\n- Зона лучше: $59,500\nDecision: WATCH"},
    ]
    limits = []
    monkeypatch.setattr(svc, "get_recent_context", lambda session_id, limit: limits.append(limit) or recent)
    evidence_pack = {
        "mode": "crypto",
        "intent": "entry_now",
        "derived_facts": {},
        "answer_policy": {"can_give_levels": True, "can_give_entry_zone": True, "can_comment_on_odds": False, "must_not_invent": []},
        "recommended_decision_labels": ["WATCH"],
    }
    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "Коротко: WATCH\nDecision: WATCH")

    result = svc.process_live_text(303, "а если лонг от 64500?", router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert result.get("needs_clarification") is not True
    assert limits == [60]
    assert len(charges) == 1
    ctx = context_memory.get_live_context(303)
    assert ctx["mode"] == "crypto"
    assert ctx["asset_pair"] == "BTCUSDT"
    assert ctx["timeframe"] == "15m"
    assert evidence_pack["previous_live_context"]["key_levels"]["support"] == [59500, 59339]
    assert evidence_pack["previous_live_context"]["key_levels"]["resistance"] == [60000, 63239]
    assert evidence_pack["followup_type"] == "long_position"
    assert evidence_pack["followup_level"] == "64500"


def test_live_followup_suggestions_crypto_successful_answer():
    answer = svc.append_live_followup_suggestions(
        "🧠 Коротко:\nBTCUSDT лучше наблюдать.\n\nDecision: WATCH",
        {"mode": "crypto", "intent": "entry_now"},
        ui_language="ru",
    )

    assert "Decision: WATCH" in answer
    assert "Хочешь продолжить разбор?" in answer
    suggestion_lines = [line for line in answer.splitlines() if line.startswith("- ")]
    assert suggestion_lines
    assert all(line.endswith("?") for line in suggestion_lines)
    assert "где лучше ждать вход" in answer
    forbidden = ("покупай", "продавай", "ставь", "гарантирую")
    assert not any(word in answer.lower() for word in forbidden)


def test_live_followup_suggestions_crypto_long_position():
    answer = svc.append_live_followup_suggestions(
        "🧠 Коротко:\nЛонг пока только сценарий.\n\nDecision: WATCH",
        {"mode": "crypto", "intent": "entry_now", "followup_type": "long_position"},
        ui_language="ru",
    )
    lower = answer.lower()

    assert "лонг" in lower
    assert "подтверждение" in lower
    assert "отмена" in lower
    assert "риск" in lower
    assert "долгосрочный" not in lower


def test_live_followup_suggestions_sports_betting_answer():
    answer = svc.append_live_followup_suggestions(
        "🏟 Коротко:\nПо линии пока WATCH.\n\nDecision: WATCH",
        {"mode": "sports", "intent": "betting_analysis"},
        ui_language="ru",
    )
    lower = answer.lower()

    assert "Посчитать value под твой коэффициент?" in answer
    assert "Сравнить рынки" in answer
    assert "ставь" not in lower
    assert "бери" not in lower



def test_live_followup_suggestions_crypto_english_questions():
    answer = svc.append_live_followup_suggestions(
        "🧠 Short take:\nBTCUSDT is a watch setup.\n\nDecision: WATCH",
        {"mode": "crypto", "intent": "entry_now"},
        ui_language="en",
    )

    assert "Want to continue the analysis?" in answer
    suggestion_lines = [line for line in answer.splitlines() if line.startswith("- ")]
    assert suggestion_lines
    assert all(line.endswith("?") for line in suggestion_lines)


def test_live_followup_suggestions_does_not_duplicate_old_or_new_titles():
    old_answer = svc.append_live_followup_suggestions(
        "Decision: WATCH\n\nМожно продолжить:\n\n- Старый вариант.",
        {"mode": "crypto"},
        ui_language="ru",
    )
    new_answer = svc.append_live_followup_suggestions(
        "Decision: WATCH\n\nХочешь продолжить разбор?\n\n- Старый вариант?",
        {"mode": "crypto"},
        ui_language="ru",
    )

    assert old_answer.count("Можно продолжить:") == 1
    assert "Хочешь продолжить разбор?" not in old_answer
    assert new_answer.count("Хочешь продолжить разбор?") == 1
    assert "Можно продолжить:" not in new_answer

def test_live_followup_suggestions_not_added_to_clarification_response():
    answer = svc.append_live_followup_suggestions(
        "Уточни, пожалуйста, что разбираем: Polymarket-рынок, crypto-актив/пару или sports-матч/линию?",
        {"mode": "unknown"},
        ui_language="ru",
    )

    assert "Хочешь продолжить разбор?" not in answer
    assert "Можно продолжить:" not in answer


def test_live_followup_suggestions_not_added_to_live_unavailable_message():
    answer = svc.append_live_followup_suggestions(
        svc.LIVE_UNAVAILABLE_MESSAGE,
        {"mode": "crypto"},
        ui_language="ru",
    )

    assert answer == svc.LIVE_UNAVAILABLE_MESSAGE
    assert "Хочешь продолжить разбор?" not in answer
    assert "Можно продолжить:" not in answer


def test_process_live_text_stores_suggested_actions_and_resolves_short_confirmation(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    context_memory.clear_live_context_memory()
    evidence_packs = []
    prompts = []

    def fake_understand(text, *args, **kwargs):
        assert "BTCUSDT" in text
        return {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}}

    def fake_build(text, understanding, router_result, **kwargs):
        pack = {
            "mode": "crypto",
            "intent": "entry_now",
            "derived_facts": {"current_price": 64000, "support_levels": [63800], "resistance_levels": [64500], "confirmation": "reclaim", "invalidation": "below support"},
            "answer_policy": {"can_give_levels": True, "can_give_entry_zone": True, "can_comment_on_odds": False, "must_not_invent": []},
            "recommended_decision_labels": ["WATCH"],
        }
        evidence_packs.append(pack)
        return pack

    monkeypatch.setattr(svc, "understand_live_request", fake_understand)
    monkeypatch.setattr(svc, "build_live_evidence_pack", fake_build)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Коротко: WATCH\nDecision: WATCH")

    first = svc.process_live_text(301, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}}, ui_language="ru")
    ctx = context_memory.get_live_context(301)
    assert first["ok"] is True
    assert ctx and ctx["suggested_actions"]

    second = svc.process_live_text(301, "давай", router_result={"mode": "unknown"}, ui_language="ru")

    assert second["ok"] is True
    assert second.get("needs_clarification") is not True
    assert evidence_packs[-1]["selected_action_id"] == ctx["suggested_actions"][0]["id"]
    assert "Selected action:" in prompts[-1]
    assert len(charges) == 2


def test_process_live_text_reconstructs_suggested_actions_for_short_confirmation(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    context_memory.clear_live_context_memory()
    recent = [
        {"role": "user", "content": "BTCUSDT 15m есть вход?"},
        {"role": "assistant", "content": "Данные:\n\n- Цена: $59,670\n- Поддержка: $59,500 / $59,339\n- Сопротивление: $60,000 / $63,239\n- Зона лучше: $59,500\nDecision: WATCH\n\nХочешь продолжить разбор?\n\n- Разобрать, где лучше ждать вход и где сценарий ломается?"},
    ]
    evidence_packs = []
    prompts = []
    monkeypatch.setattr(svc, "get_recent_context", lambda session_id, limit: recent)
    monkeypatch.setattr(svc, "understand_live_request", lambda text, *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})

    def fake_build(text, understanding, router_result, **kwargs):
        pack = {
            "mode": "crypto",
            "intent": "entry_now",
            "derived_facts": {"current_price": 59670, "support_levels": [59500], "resistance_levels": [60000], "confirmation": "reclaim", "invalidation": "below support"},
            "answer_policy": {"can_give_levels": True, "can_give_entry_zone": True, "can_comment_on_odds": False, "must_not_invent": []},
            "recommended_decision_labels": ["WATCH"],
        }
        evidence_packs.append(pack)
        return pack

    monkeypatch.setattr(svc, "build_live_evidence_pack", fake_build)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: prompts.append(prompt) or "Коротко: WATCH\nDecision: WATCH")

    result = svc.process_live_text(401, "давай", router_result={"mode": "unknown"}, ui_language="ru")
    ctx = context_memory.get_live_context(401)

    assert result["ok"] is True
    assert result.get("needs_clarification") is not True
    assert ctx and ctx["suggested_actions"]
    assert evidence_packs[-1].get("selected_action_id")
    assert "BTCUSDT" in prompts[-1]
    assert len(charges) == 1
    assert saved


def test_format_live_final_answer_removes_technical_followup_metadata():
    result = svc.format_live_final_answer(
        "Коротко: жду.\n\nДанные:\n\n- Тип follow-up: generic\n- Таймфрейм follow-up: 15m\n\nDecision: WATCH",
        {"mode": "crypto", "recommended_decision_labels": ["WATCH"], "derived_facts": {}, "answer_policy": {"can_give_levels": False}},
        ui_language="ru",
    )

    assert "Тип follow-up" not in result
    assert "Таймфрейм follow-up" not in result


def test_format_live_final_answer_keeps_long_condition_without_technical_metadata():
    result = svc.format_live_final_answer(
        "Коротко: это сценарий.\nDecision: WATCH",
        {
            "mode": "crypto",
            "recommended_decision_labels": ["WATCH"],
            "derived_facts": {"current_price": 64000},
            "answer_policy": {"can_give_levels": True},
            "followup_type": "long_position",
            "followup_level": 64500,
            "followup_timeframe": "15m",
        },
        ui_language="ru",
    )

    assert "Условие follow-up: лонг от $64,500" in result
    assert "Тип follow-up" not in result
    assert "Таймфрейм follow-up" not in result


def _timeframe_compare_evidence():
    return {
        "mode": "crypto",
        "selected_action_id": "timeframe_compare",
        "derived_facts": {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "current_price": 61768,
            "support_levels": [61522, 61500],
            "resistance_levels": [61962, 62000],
            "better_zone": 61522,
            "confirmation": "...",
            "invalidation": "...",
        },
        "recommended_decision_labels": ["WATCH"],
        "answer_policy": {"can_give_levels": True, "can_give_entry_zone": True, "can_comment_on_odds": False},
    }


def test_crypto_timeframe_compare_final_formatting_ru():
    result = svc.format_live_final_answer("Коротко: WATCH\nDecision: WATCH", _timeframe_compare_evidence(), ui_language="ru")

    assert "Сравнение таймфреймов" in result
    assert "- 5m:" in result
    assert "- 15m:" in result
    assert "- 1h:" in result
    assert "Ключевые уровни" in result
    assert "Decision: WATCH" in result
    assert "Тип follow-up" not in result
    assert "Таймфрейм follow-up" not in result


def test_crypto_timeframe_compare_suggestions_ru():
    answer = svc.append_live_followup_suggestions("🧠 Коротко:\nWATCH\n\nDecision: WATCH", _timeframe_compare_evidence(), ui_language="ru")

    assert "Хочешь продолжить разбор?" in answer
    assert "Собрать итоговый план" in answer
    assert "какой таймфрейм сейчас главный" in answer
    assert "лонга или шорта" in answer
    assert "Сравнить этот сценарий на 5m / 15m / 1h?" not in answer


def test_crypto_normal_first_answer_suggestions_unchanged():
    answer = svc.append_live_followup_suggestions("🧠 Коротко:\nWATCH\n\nDecision: WATCH", {"mode": "crypto"}, ui_language="ru")

    assert "Сравнить этот сценарий на 5m / 15m / 1h?" in answer


def test_crypto_timeframe_compare_en_format_and_suggestions():
    formatted = svc.format_live_final_answer("Short take: WATCH\nDecision: WATCH", _timeframe_compare_evidence(), ui_language="en")
    answer = svc.append_live_followup_suggestions(formatted, _timeframe_compare_evidence(), ui_language="en")

    assert "Timeframe comparison" in answer
    assert "Build a final plan" in answer
    assert "Compare this setup on 5m / 15m / 1h?" not in answer


def test_process_live_text_crypto_compare_flow_is_contextual(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    context_memory.clear_live_context_memory()
    context_memory.save_live_context(
        909,
        mode="crypto",
        original_user_text="BTCUSDT 15m",
        normalized_query="BTCUSDT 15m",
        asset_pair="BTCUSDT",
        timeframe="15m",
        last_final_answer="Decision: WATCH",
        suggested_actions=svc.build_live_suggested_actions({"mode": "crypto"}, ui_language="ru"),
    )
    evidence_pack = _timeframe_compare_evidence()
    evidence_packs = []

    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_packs.append(dict(evidence_pack)) or evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "validate_live_answer_against_evidence", lambda answer, evidence_pack: {"ok": True, "severity": "none", "issues": []})
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Коротко: WATCH\nDecision: WATCH")

    result = svc.process_live_text(909, "сравни", router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert evidence_pack["selected_action_id"] == "timeframe_compare"
    assert "Сравнение таймфреймов" in result["message"]
    assert "Сравнить этот сценарий на 5m / 15m / 1h?" not in result["message"]
    assert len(charges) == 1
    assert saved[1][0][4] == result["message"]


def test_process_live_text_timeframe_compare_empty_llm_uses_contextual_deterministic_fallback(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    context_memory.clear_live_context_memory()
    context_memory.save_live_context(
        910,
        mode="crypto",
        original_user_text="BTCUSDT 15m",
        normalized_query="BTCUSDT 15m",
        asset_pair="BTCUSDT",
        timeframe="15m",
        last_final_answer="Decision: WATCH",
        suggested_actions=svc.build_live_suggested_actions({"mode": "crypto"}, ui_language="ru"),
    )
    evidence_pack = _timeframe_compare_evidence()

    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "crypto", "intent": "entry_now", "pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "needs": {}})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "")

    result = svc.process_live_text(910, "сравни", router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert result["charged"] is False
    assert result["cost"] == 0
    assert "Сравнение таймфреймов" in result["message"]
    assert "- 5m:" in result["message"]
    assert "- 15m:" in result["message"]
    assert "- 1h:" in result["message"]
    assert "Сравнить этот сценарий на 5m / 15m / 1h?" not in result["message"]
    assert "Собрать итоговый план" in result["message"]
    assert charges == []
    assert saved and saved[0][0][4] == result["message"]


def test_unknown_router_does_not_block_esports_live_understanding(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "Short conclusion: DATA NEEDED\nWhat I see: NAVI vs Vitality over 2.5 maps at 1.85\nRisk: need fresh map pool/form\nDecision: DATA NEEDED")

    result = svc.process_live_text(
        1501,
        "NAVI Vitality тб 2.5 карт кэф 1.85",
        router_result={"mode": "unknown"},
        ui_language="ru",
    )

    assert result["ok"] is True
    assert len(charges) == 1
    assert "Домен: esports" in result["message"]
    assert "Игра: CS2" in result["message"]
    assert "NAVI — Vitality" in result["message"]
    assert "Коэффициент / цена: 1.85" in result["message"]
    assert "Уточни, пожалуйста" not in result["message"]


def test_unknown_router_does_not_block_sports_live_understanding(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "get_sports_context", lambda *args, **kwargs: {"ok": True, "sources": []})
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "Short conclusion: DATA NEEDED\nWhat I see: Lakers vs Celtics total 218.5 at 1.9\nRisk: need fresh injury/news context\nDecision: DATA NEEDED")

    result = svc.process_live_text(
        1502,
        "Lakers Celtics тотал 218.5 кэф 1.9",
        router_result={"mode": "unknown"},
        ui_language="ru",
    )

    assert result["ok"] is True
    assert len(charges) == 1
    assert "Уточни, пожалуйста" not in result["message"]


def test_unknown_router_truly_unknown_clarifies_with_esports_event_wording(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda *args, **kwargs: "should not be called")

    result = svc.process_live_text(1503, "что думаешь?", router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is False
    assert result["charged"] is False
    assert result["needs_clarification"] is True
    assert "sports/esports матч или линию/коэффициент" in result["message"]
    assert charges == []
    assert saved == []


def test_followup_without_previous_context_still_clarifies(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "get_recent_context", lambda session_id, limit: [])

    result = svc.process_live_text(1504, "давай", router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is False
    assert result["charged"] is False
    assert result["needs_clarification"] is True
    assert result["is_followup"] is True
    assert charges == []
    assert saved == []


def test_technical_debug_final_answer_must_not_use_sports_formatter(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    text = "Railway aiogram traceback: Terminated by other getUpdates request; make sure that only one bot instance is running. Бот на aiogram polling, Railway production, после redeploy иногда появляется conflict getUpdates."
    frame = {
        "domain": "technical_debug",
        "user_intent": "debug_problem",
        "answer_style": "debug_report",
        "safety_domain": "technical_debug",
        "subject": text,
        "missing_data": ["logs", "deployments", "BOT_TOKEN"],
    }
    evidence_pack = {"mode": "sports", "intent": "betting_angle", "universal_live_frame": frame, "derived_facts": {}, "missing_data": frame["missing_data"], "answer_policy": {}, "recommended_decision_labels": ["DATA NEEDED"]}
    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "sports", "intent": "betting_angle"})
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "Вероятная причина: conflict getUpdates — две polling-инстанции с одним BOT_TOKEN в Railway после redeploy. Проверь deployments/replicas и логи старого процесса. Итог: LIKELY CAUSE / FIX NEEDED")

    result = svc.process_live_text(808, text, router_result={"mode": "sports"}, ui_language="ru")

    assert result["ok"] is True
    output = result["message"]
    assert "getUpdates" in output
    assert "polling" in output
    assert "BOT_TOKEN" in output or "bot token" in output.lower()
    assert "Railway" in output
    assert any(x in output for x in ["Вероятная причина", "LIKELY CAUSE", "FIX NEEDED"])
    forbidden = ["🏟", "sports", "спортив", "american_football", "moneyline", "форма/составы", "травмы", "travel/rest", "Implied probability", "Edge", "Minimum playable odds"]
    for item in forbidden:
        assert item not in output


def test_no_duplicate_decision_ending_for_market_answer():
    answer = svc.format_live_final_answer("Итог: DATA NEEDED\nDecision: DATA NEEDED", {"mode": "esports", "derived_facts": {}, "missing_data": ["recent form"], "recommended_decision_labels": ["DATA NEEDED"]}, "ru")
    assert not ("Итог: DATA NEEDED" in answer and "Decision: DATA NEEDED" in answer)


def _patch_non_market_flow(monkeypatch, text, mode):
    monkeypatch.setattr(svc, "understand_live_request", lambda *args, **kwargs: {"mode": "unknown", "intent": "incident_response" if mode == "technical_debug" else "business_decision", "needs": {}})
    evidence_pack = {
        "mode": "unknown",
        "intent": "incident_response" if mode == "technical_debug" else "business_decision",
        "missing_data": [],
        "derived_facts": {},
        "universal_live_frame": {"domain": mode if mode != "technical_debug" else "technical_debug", "user_intent": "incident_response" if mode == "technical_debug" else "business_decision"},
    }
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *args, **kwargs: evidence_pack)
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *args, **kwargs: False)
    return evidence_pack


def test_technical_debug_market_like_answer_gets_replaced(monkeypatch):
    _saved, charges = _patch_common(monkeypatch)
    text = "Railway aiogram traceback: Terminated by other getUpdates request; make sure that only one bot instance is running."
    _patch_non_market_flow(monkeypatch, text, "technical_debug")
    market_answer = "🧠 Коротко:\nWATCH: данных недостаточно для уверенного входа; лучше дождаться подтверждения.\n\nДанные:\nКачество evidence: medium. Не хватает: teams, event_time.\n\nDecision: WATCH"
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: market_answer)

    result = svc.process_live_text(24201, text, router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert len(charges) == 1
    out = result["message"]
    assert "getUpdates" in out
    assert "polling" in out
    assert "BOT_TOKEN" in out or "bot token" in out
    assert "Railway" in out
    assert "LIKELY CAUSE" in out or "FIX NEEDED" in out
    forbidden = ["WATCH: данных недостаточно для уверенного входа", "teams, event_time", "уровней/коэффициентов", "Decision: WATCH", "Implied probability", "Edge", "moneyline", "american_football"]
    assert not any(x in out for x in forbidden)


def test_technical_debug_valid_llm_answer_is_preserved(monkeypatch):
    _saved, charges = _patch_common(monkeypatch)
    text = "Railway aiogram traceback: Terminated by other getUpdates request; make sure that only one bot instance is running."
    _patch_non_market_flow(monkeypatch, text, "technical_debug")
    llm_answer = "Похоже на conflict getUpdates: две polling-инстанции с одним BOT_TOKEN одновременно читают updates в Railway после redeploy. Проверь активные deployments, webhook/polling и старый контейнер. Итог: LIKELY CAUSE / FIX NEEDED."
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: llm_answer)

    result = svc.process_live_text(24202, text, router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert len(charges) == 1
    assert llm_answer in result["message"]


def test_business_market_like_answer_gets_replaced(monkeypatch):
    _saved, charges = _patch_common(monkeypatch)
    text = "Стоит ли запускать рекламу для DeepAlpha сейчас?"
    _patch_non_market_flow(monkeypatch, text, "business")
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kwargs: "WATCH: данных недостаточно для уверенного входа; Не хватает: teams, event_time. Implied probability и moneyline неизвестны. Decision: WATCH")

    result = svc.process_live_text(24203, text, router_result={"mode": "unknown"}, ui_language="ru")

    assert result["ok"] is True
    assert len(charges) == 1
    out = result["message"]
    low = out.lower()
    for expected in ("цель", "audience", "бюдж", "cac", "payback", "тест"):
        assert expected in low
    for forbidden in ("teams", "event_time", "уверенного входа", "implied probability", "moneyline"):
        assert forbidden not in low
