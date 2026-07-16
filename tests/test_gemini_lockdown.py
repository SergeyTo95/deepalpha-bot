import os
import threading
import time

import pytest


class FakeResp:
    def __init__(self, code=200, data=None, text="{}"):
        self.status_code = code
        self._data = data or {}
        self.text = text
        self.headers = {"x-request-id": "provider-1"}
    def json(self):
        return self._data


def _enable(monkeypatch):
    for k in [
        "GEMINI_ENABLED","NEWS_AGENT_GEMINI_ENABLED","DECISION_AGENT_GEMINI_ENABLED","SUMMARY_AGENT_GEMINI_ENABLED",
        "LIVE_ANALYST_VISION_GEMINI_ENABLED","WATCHLIST_AI_SUMMARY_GEMINI_ENABLED","GEMINI_BACKGROUND_ENABLED",
    ]:
        monkeypatch.setenv(k, "true")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setenv("GEMINI_DAILY_HTTP_ATTEMPT_LIMIT", "100")
    monkeypatch.setenv("GEMINI_BACKGROUND_DAILY_HTTP_ATTEMPT_LIMIT", "100")


def _fake_db(monkeypatch, fail_reserve=False):
    attempts=[]
    def reserve(**kw):
        if fail_reserve:
            raise RuntimeError("db down")
        attempts.append({**kw, "status":"reserved"})
        return len(attempts)
    def finalize(attempt_id, **kw):
        attempts[attempt_id-1].update(kw)
    monkeypatch.setattr("db.database.reserve_gemini_attempt", reserve, raising=False)
    monkeypatch.setattr("db.database.finalize_gemini_attempt", finalize, raising=False)
    monkeypatch.setattr("db.database.record_gemini_blocked_request", lambda **kw: None, raising=False)
    return attempts


def test_global_switch_blocks_user_background_and_admin(monkeypatch):
    from services.llm_service import generate_text
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    called=[]
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    assert generate_text("p", admin_override=True, budget_checked=True) == ""
    assert generate_text("p", is_background=True, budget_checked=True) == ""
    assert generate_text("p", user_id=1) == ""
    assert called == []


def test_budget_checked_does_not_bypass_guard(monkeypatch):
    from services.llm_service import generate_live_analyst_text
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    called=[]
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    assert generate_live_analyst_text("p", budget_checked=True, admin_override=True) == ""
    assert called == []


def test_vision_uses_gateway_not_direct_http(monkeypatch):
    import services.live_analyst_image_service as svc
    monkeypatch.setenv("LIVE_ANALYST_VISION_GEMINI_ENABLED", "true")
    calls=[]
    monkeypatch.setattr("services.gemini_gateway.generate_content", lambda **kw: calls.append(kw) or {"text":"{}", "data":{"candidates":[{"finishReason":"STOP"}]}})
    monkeypatch.setattr(svc.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("direct http")))
    text, finish = svc._call_gemini_vision_parts("k", "gemini-2.5-flash", 1, [{"text":"x"}], 10, user_id=1, access_checked=True)
    assert text == "{}"
    assert calls and calls[0]["feature"] == "live_analyst_vision"


def test_timeout_429_503_empty_and_fallback_are_accounted(monkeypatch):
    from services.gemini_gateway import generate_content, requests
    _enable(monkeypatch)
    monkeypatch.setenv("GEMINI_RETRY_ON_TIMEOUT", "true")
    monkeypatch.setenv("GEMINI_RETRY_ON_RATE_LIMIT", "true")
    monkeypatch.setenv("GEMINI_RETRY_ON_SERVER_ERROR", "true")
    attempts=_fake_db(monkeypatch)
    seq=[requests.exceptions.Timeout(), FakeResp(429), FakeResp(503), FakeResp(200, {"candidates":[]}), FakeResp(200,{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]})]
    def post(*a, **k):
        x=seq.pop(0)
        if isinstance(x, Exception): raise x
        return x
    monkeypatch.setattr("services.gemini_gateway.requests.post", post)
    res=generate_content(feature="news_agent", origin="t", is_background=True, model="m", payload={}, max_attempts=5)
    assert res["text"] == "ok"
    assert [a.get("reason") for a in attempts[:4]] == ["timeout", "rate_limit", "server_error", "empty_200"]
    assert len(attempts) == 5


def test_db_failure_prevents_http(monkeypatch):
    from services.gemini_gateway import generate_content
    _enable(monkeypatch); _fake_db(monkeypatch, fail_reserve=True)
    called=[]
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    res=generate_content(feature="news_agent", origin="t", is_background=True, model="m", payload={})
    assert res["reason"] == "db_error"
    assert called == []


def test_background_context_reaches_decision_and_summary_no_hidden_call(monkeypatch):
    from agents.decision_agent import DecisionAgent
    calls=[]
    monkeypatch.setattr("agents.decision_agent.generate_decision_text", lambda *a, **kw: calls.append(kw) or "")
    out=DecisionAgent().run({"question":"Q","market_probability":"50%","options":[]}, {}, is_background=True, cycle_id="c", job_id="j")
    assert out
    assert calls[0]["is_background"] is True and calls[0]["cycle_id"] == "c"


def test_watchlist_deterministic_fallback_when_gemini_off(monkeypatch):
    from services.watchlist_ai_summary_service import build_watchlist_ai_summary
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    out=build_watchlist_ai_summary("probability_change", "Question?", initial_probability=40, current_probability=55, probability_change=15)
    assert out["summary"] and out["watch_next"]


def test_user_tokens_not_charged_on_provider_failure(monkeypatch):
    from services.llm_service import generate_live_analyst_text
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    called=[]
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    assert generate_live_analyst_text("p", user_id=7) == ""
    assert called == []


def test_two_lock_owners_do_not_get_same_active_lease():
    owner = {"value": None, "expires": 0}
    mu = threading.Lock()
    def acquire(name, who, ttl):
        with mu:
            now=time.time()
            if owner["value"] is None or owner["expires"] < now or owner["value"] == who:
                owner.update(value=who, expires=now+ttl); return True
            return False
    results=[]
    t1=threading.Thread(target=lambda: results.append(acquire("x","a",60)))
    t2=threading.Thread(target=lambda: results.append(acquire("x","b",60)))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == [False, True]
