import sys
import types

if "requests" not in sys.modules:
    req = types.ModuleType("requests")
    class Timeout(Exception):
        pass
    req.exceptions = types.SimpleNamespace(Timeout=Timeout)
    req.post = lambda *a, **k: None
    sys.modules["requests"] = req

if "psycopg2" not in sys.modules:
    psy = types.ModuleType("psycopg2")
    psy.extras = types.ModuleType("psycopg2.extras")
    psy.errors = types.ModuleType("psycopg2.errors")
    sys.modules["psycopg2"] = psy
    sys.modules["psycopg2.extras"] = psy.extras
    sys.modules["psycopg2.errors"] = psy.errors


def _allow_env(monkeypatch, feature_flag="NEWS_AGENT_GEMINI_ENABLED"):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_DAILY_CALL_LIMIT", "10")
    monkeypatch.setenv("GEMINI_BACKGROUND_DAILY_CALL_LIMIT", "10")
    monkeypatch.setenv(feature_flag, "true")


def test_gemini_disabled_denies(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    from services.gemini_budget_guard import can_call_gemini
    assert can_call_gemini("news_agent")["reason"] == "gemini_disabled"


def test_hot_news_disabled_blocks_http(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("HOT_NEWS_GEMINI_ENABLED", "false")
    import services.llm_service as llm
    called = {"n": 0}
    monkeypatch.setattr(llm, "_call_model_once", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ("x", 200))
    assert llm.generate_news_text("p", feature="hot_news", is_background=True) == ""
    assert called["n"] == 0


def test_channel_news_disabled_denied(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("CHANNEL_NEWS_GEMINI_ENABLED", "false")
    from services.gemini_budget_guard import can_call_gemini
    assert can_call_gemini("channel_news", is_background=True)["allowed"] is False


def test_background_limit_zero_denied_unless_admin(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("NEWS_AGENT_GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_DAILY_CALL_LIMIT", "10")
    monkeypatch.setenv("GEMINI_BACKGROUND_DAILY_CALL_LIMIT", "0")
    import services.gemini_budget_guard as guard
    monkeypatch.setattr("db.database.count_gemini_usage_today", lambda *a, **k: 0)
    assert guard.can_call_gemini("news_agent", is_background=True)["reason"] == "background_budget_exceeded"
    assert guard.can_call_gemini("news_agent", is_background=True, admin_override=True)["reason"] == "admin"


def test_unknown_feature_denied(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    from services.gemini_budget_guard import can_call_gemini
    assert can_call_gemini("unknown")["reason"] == "invalid_feature"


def test_dynamic_driver_disabled_returns_empty_no_http(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("DYNAMIC_DRIVERS_GEMINI_ENABLED", "false")
    import services.llm_service as llm
    called = {"n": 0}
    monkeypatch.setattr(llm, "_call_model_once", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ('{}', 200))
    from agents.dynamic_driver_agent import DynamicDriverAgent
    res = DynamicDriverAgent().build({}, "Q", ["Yes", "No"])
    assert res["yes_drivers"] == []
    assert called["n"] == 0


def test_news_agent_disabled_no_http(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("NEWS_AGENT_GEMINI_ENABLED", "false")
    import services.llm_service as llm
    called = {"n": 0}
    monkeypatch.setattr(llm, "_call_model_once", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ("x", 200))
    assert llm.generate_news_text("p", feature="news_agent", is_background=True) == ""
    assert called["n"] == 0


def test_usage_recording_increments(monkeypatch):
    counts = {"total": 0}
    monkeypatch.setattr("db.database.record_gemini_usage", lambda *a, **k: counts.__setitem__("total", counts["total"] + 1) or counts["total"])
    from services.gemini_budget_guard import record_gemini_call
    assert record_gemini_call("news_agent", is_background=True) == 1
    assert counts["total"] == 1
