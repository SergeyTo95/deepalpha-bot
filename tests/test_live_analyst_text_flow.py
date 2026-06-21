import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))

from services import live_analyst_memory_service as memory_svc
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
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short conclusion: WATCH\nWhat I see: BTCUSDT 15m\nRisk: high\nDecision: WATCH")

    result = svc.process_live_text(7, "BTCUSDT 15m есть вход?", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m"}})

    assert result["ok"] is True
    assert result["charged"] is True
    assert len(charges) == 1
    assert len(saved) == 2
    assert saved[0][0][2] == "user"
    assert saved[1][0][2] == "assistant"
    assert "crypto consultant" in prompts[0]
    assert "BTCUSDT" in prompts[0]


def test_sports_text_useful_answer_path(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: "Short conclusion: DATA NEEDED\nWhat I see: odds 1.85\nRisk: missing live stats\nDecision: DATA NEEDED")

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
    monkeypatch.setattr(svc, "generate_decision_text", fake_llm)

    result = svc.process_live_text(9, "BTCUSDT 15m", router_result={"mode": "crypto"})

    assert result["ok"] is False
    assert called is False
    assert charges == []


def test_failed_answer_does_not_charge_or_save(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "generate_decision_text", lambda *args, **kwargs: "")

    result = svc.process_live_text(10, "Team A vs Team B odds 1.85", router_result={"mode": "sports"})

    assert result["ok"] is False
    assert charges == []
    assert saved == []


def test_polymarket_empty_entities_do_not_overwrite_market_title(monkeypatch):
    _saved, _charges = _patch_common(monkeypatch)
    context_updates = []
    monkeypatch.setattr(memory_svc, "update_current_market_context", lambda session, **kwargs: context_updates.append(kwargs) or session)
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: "Short conclusion: WATCH\nDecision: WATCH")

    result = svc.process_live_text(11, "дай премиум", router_result={"mode": "polymarket", "entities": {}})

    assert result["ok"] is True
    assert context_updates == []


def test_crypto_entities_update_useful_context_title(monkeypatch):
    _saved, _charges = _patch_common(monkeypatch)
    context_updates = []
    monkeypatch.setattr(memory_svc, "update_current_market_context", lambda session, **kwargs: context_updates.append(kwargs) or session)
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: "Short conclusion: WATCH\nDecision: WATCH")

    result = svc.process_live_text(12, "BTCUSDT 15m Binance", router_result={"mode": "crypto", "entities": {"pair": "BTCUSDT", "asset": "BTC", "timeframe": "15m", "exchange": "Binance"}})

    assert result["ok"] is True
    assert len(context_updates) == 1
    assert context_updates[0]["market_title"] == "pair=BTCUSDT; asset=BTC; timeframe=15m; exchange=Binance"


def test_sports_entities_update_useful_context_title(monkeypatch):
    _saved, _charges = _patch_common(monkeypatch)
    context_updates = []
    monkeypatch.setattr(memory_svc, "update_current_market_context", lambda session, **kwargs: context_updates.append(kwargs) or session)
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: "Short conclusion: DATA NEEDED\nDecision: DATA NEEDED")

    result = svc.process_live_text(13, "Team A vs Team B odds 1.85", router_result={"mode": "sports", "entities": {"teams": ["Team A", "Team B"], "odds": 1.85}})

    assert result["ok"] is True
    assert len(context_updates) == 1
    assert context_updates[0]["market_title"] == "teams=['Team A', 'Team B']; odds=1.85"


def test_crypto_asset_without_pair_still_uses_paid_consultant_path(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short conclusion: DATA NEEDED/WATCH\nDecision: DATA NEEDED\nNext step: пришли таймфрейм")

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
    assert "DATA NEEDED/WATCH" in result["message"]
    assert "asset': 'BTC" in prompts[0]


def test_unknown_mode_asks_clarification_and_does_not_charge(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    called = False

    def fake_llm(*args, **kwargs):
        nonlocal called
        called = True
        return "answer"

    monkeypatch.setattr(svc, "generate_decision_text", fake_llm)

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
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: "Short take: limited\nDecision: DATA NEEDED\nNext step: send chart")
    result = svc.process_live_text(16, "should I buy bitcoin now?", router_result={"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en")
    assert result["ok"] is True
    assert len(charges) == 1
    assert len(saved) == 2


def test_unknown_mode_english_clarification_and_does_not_charge(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    monkeypatch.setattr(svc, "generate_decision_text", lambda *args, **kwargs: "answer")
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


def test_live_research_mocked_provider_success(monkeypatch):
    from services import live_research_service as research
    research._CACHE.clear()
    monkeypatch.setenv("LIVE_WEB_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("LIVE_WEB_RESEARCH_PROVIDER", "gemini")
    monkeypatch.setattr(research, "_gemini_grounded_research", lambda *args, **kwargs: {"ok": True, "summary": "BTC fresh summary", "sources": [{"title": "Market", "url": "https://example.com", "source": "example", "published_at": "today"}], "freshness": "fresh", "error": ""})
    result = research.get_live_research_context("BTC now", "crypto", {"asset": "BTC"}, "en")
    assert result["ok"] is True
    assert result["summary"] == "BTC fresh summary"
    assert result["sources"][0]["url"] == "https://example.com"


def test_process_live_text_includes_research_summary_and_charges_once(monkeypatch):
    saved, charges = _patch_common(monkeypatch)
    prompts = []
    monkeypatch.setattr(svc, "fresh_context_needed", lambda *args, **kwargs: True)
    monkeypatch.setattr(svc, "get_live_research_context", lambda *args, **kwargs: {"ok": True, "summary": "BTC fresh summary", "sources": [{"title": "Market", "url": "https://example.com", "source": "example", "published_at": "today"}], "freshness": "fresh", "error": ""})
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short take: WATCH\nFresh context: BTC fresh summary\nDecision: WATCH")
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
    monkeypatch.setattr(svc, "generate_decision_text", lambda prompt, **kwargs: prompts.append(prompt) or "Short take: DATA NEEDED\nDecision: DATA NEEDED")
    result = svc.process_live_text(19, "BTC now buy?", router_result={"mode": "crypto", "entities": {"asset": "BTC"}}, ui_language="en")
    assert result["ok"] is True
    assert len(charges) == 1
    assert "provider returned no sources" in prompts[0]
    assert len(saved) == 2
