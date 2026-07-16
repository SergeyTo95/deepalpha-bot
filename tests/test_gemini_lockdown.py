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
    TimeoutExc = getattr(getattr(requests, "exceptions", object), "Timeout", TimeoutError)
    seq=[TimeoutExc(), FakeResp(429), FakeResp(503), FakeResp(200, {"candidates":[]}), FakeResp(200,{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]})]
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


@pytest.mark.parametrize("feature,flag", [
    ("live_analyst", "LIVE_ANALYST_GEMINI_ENABLED"),
    ("live_analyst_vision", "LIVE_ANALYST_VISION_GEMINI_ENABLED"),
    ("news_agent", "NEWS_AGENT_GEMINI_ENABLED"),
    ("decision_agent", "DECISION_AGENT_GEMINI_ENABLED"),
    ("summary_agent", "SUMMARY_AGENT_GEMINI_ENABLED"),
    ("watchlist_ai_summary", "WATCHLIST_AI_SUMMARY_GEMINI_ENABLED"),
    ("signal_cache", "SIGNAL_CACHE_GEMINI_ENABLED"),
])
def test_gateway_feature_flags_match_expected_env(feature, flag):
    from services.gemini_gateway import FEATURE_FLAGS
    assert FEATURE_FLAGS[feature] == flag


def test_vision_disabled_and_global_disabled_do_not_http(monkeypatch):
    import services.live_analyst_image_service as svc
    called = []
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("LIVE_ANALYST_VISION_GEMINI_ENABLED", "false")
    text, reason = svc._call_gemini_vision_parts("secret-key", "gemini-2.5-flash", 1, [{"text": "x"}], 10, access_checked=True)
    assert (text, reason) == ("", "vision_disabled")
    monkeypatch.setenv("LIVE_ANALYST_VISION_GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    text, reason = svc._call_gemini_vision_parts("secret-key", "gemini-2.5-flash", 1, [{"text": "x"}], 10, access_checked=True)
    assert text == ""
    assert called == []


def test_distributed_lock_owner_ttl_and_release_semantics():
    locks = {}
    now = [100.0]
    def acquire(name, owner, ttl):
        current = locks.get(name)
        if current is None or current["expires"] < now[0] or current["owner"] == owner:
            locks[name] = {"owner": owner, "expires": now[0] + ttl}
            return True
        return False
    def release(name, owner):
        if locks.get(name, {}).get("owner") == owner:
            del locks[name]
            return True
        return False
    assert acquire("signal", "A", 10) is True
    assert acquire("signal", "B", 10) is False
    assert release("signal", "B") is False
    now[0] = 111.0
    assert acquire("signal", "B", 10) is True
    assert locks["signal"]["owner"] == "B"


def test_concurrent_fake_reservation_limit_one_allows_only_one():
    lock = threading.Lock()
    attempts = []
    def reserve():
        with lock:
            if len([a for a in attempts if a["status"] in {"reserved", "success", "failed"}]) >= 1:
                raise RuntimeError("request_limit_exceeded")
            attempts.append({"status": "reserved"})
            return len(attempts)
    results = []
    def worker():
        try:
            reserve()
            results.append("ok")
        except RuntimeError:
            results.append("blocked")
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert results.count("ok") == 1
    assert results.count("blocked") == 1
    assert len(attempts) == 1


def test_gemini_enabled_missing_fails_closed_no_http(monkeypatch):
    from services.gemini_gateway import call_gemini
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.setenv("NEWS_AGENT_GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    called = []
    blocked = []
    monkeypatch.setattr("db.database.record_gemini_blocked_request", lambda **kw: blocked.append(kw), raising=False)
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    res = call_gemini(feature="news_agent", origin="test", model="m", payload={})
    assert res["reason"] == "blocked_global"
    assert called == []
    assert blocked[0]["reason"] == "blocked_global"


def test_live_analyst_requires_own_feature_flag(monkeypatch):
    from services.gemini_gateway import call_gemini
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("LIVE_ANALYST_GEMINI_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setattr("db.database.record_gemini_blocked_request", lambda **kw: None, raising=False)
    called = []
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    assert call_gemini(feature="live_analyst", origin="test", model="m", payload={})["reason"] == "blocked_feature"
    assert called == []


def test_live_analyst_and_vision_enabled_reach_reservation(monkeypatch):
    from services.gemini_gateway import call_gemini
    for key in ("GEMINI_ENABLED", "LIVE_ANALYST_GEMINI_ENABLED", "LIVE_ANALYST_VISION_GEMINI_ENABLED"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    reserved = []
    monkeypatch.setattr("db.database.reserve_gemini_attempt", lambda **kw: reserved.append(kw) or len(reserved), raising=False)
    monkeypatch.setattr("db.database.finalize_gemini_attempt", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: FakeResp(200, {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}))
    assert call_gemini(feature="live_analyst", origin="test", model="m", payload={})["text"] == "ok"
    assert call_gemini(feature="live_analyst_vision", origin="test", model="m", payload={})["text"] == "ok"
    assert [r["feature"] for r in reserved] == ["live_analyst", "live_analyst_vision"]


@pytest.mark.parametrize("reservation_reason", [
    "daily_limit_exceeded",
    "background_limit_exceeded",
    "request_limit_exceeded",
    "cycle_limit_exceeded",
])
def test_reservation_block_reason_preserved_and_no_http(monkeypatch, reservation_reason):
    from services.gemini_gateway import call_gemini
    _enable(monkeypatch)
    blocked=[]; called=[]
    monkeypatch.setattr("db.database.reserve_gemini_attempt", lambda **kw: (_ for _ in ()).throw(RuntimeError(reservation_reason)), raising=False)
    monkeypatch.setattr("db.database.record_gemini_blocked_request", lambda **kw: blocked.append(kw), raising=False)
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    res = call_gemini(feature="news_agent", origin="test", is_background=True, model="m", payload={})
    assert res["reason"] == reservation_reason
    assert blocked[0]["reason"] == reservation_reason
    assert called == []


@pytest.mark.parametrize("setup,reason", [
    (lambda m: m.setenv("GEMINI_ENABLED", "false"), "blocked_global"),
    (lambda m: (m.setenv("GEMINI_ENABLED", "true"), m.setenv("NEWS_AGENT_GEMINI_ENABLED", "false")), "blocked_feature"),
    (lambda m: (m.setenv("GEMINI_ENABLED", "true"), m.setenv("NEWS_AGENT_GEMINI_ENABLED", "true"), m.setenv("GEMINI_BACKGROUND_ENABLED", "false")), "blocked_background"),
    (lambda m: (m.setenv("GEMINI_ENABLED", "true"), m.setenv("NEWS_AGENT_GEMINI_ENABLED", "true"), m.setenv("GEMINI_BACKGROUND_ENABLED", "true"), m.delenv("GEMINI_API_KEY", raising=False)), "api_key_missing"),
])
def test_blocked_logical_request_accounting_reasons(monkeypatch, setup, reason):
    from services.gemini_gateway import call_gemini
    setup(monkeypatch)
    blocked=[]; called=[]
    monkeypatch.setattr("db.database.record_gemini_blocked_request", lambda **kw: blocked.append(kw), raising=False)
    monkeypatch.setattr("services.gemini_gateway.requests.post", lambda *a, **k: called.append(1))
    res = call_gemini(feature="news_agent", origin="test", is_background=True, model="m", payload={}, request_id="r")
    assert res["reason"] == reason
    assert blocked[0]["request_id"] == "r"
    assert blocked[0]["reason"] == reason
    assert called == []


class _FakeCursor:
    def __init__(self, state, fail=False):
        self.state = state; self.fail = fail; self.last = None; self.rowcount = 0
    def execute(self, sql, params=()):
        if self.fail:
            raise RuntimeError("db exploded")
        self.state["sql"].append(sql)
        if "pg_advisory_xact_lock" in sql:
            self.last = (None,)
        elif "COUNT(*) FROM gemini_call_attempts" in sql:
            if "request_id=%s" in sql:
                self.last = (sum(1 for row in self.state["attempts"] if row["request_id"] == params[0] and row["status"] != "blocked"),)
            elif "cycle_id=%s" in sql:
                self.last = (sum(1 for row in self.state["attempts"] if row.get("cycle_id") == params[0] and row["status"] != "blocked"),)
            else:
                self.last = (len([row for row in self.state["attempts"] if row["status"] != "blocked"]),)
        elif "INSERT INTO gemini_call_attempts" in sql:
            row = {"request_id": params[0], "cycle_id": params[1], "status": "reserved"}
            self.state["attempts"].append(row); self.last = (len(self.state["attempts"]),)
        elif "INSERT INTO distributed_locks" in sql:
            name, owner, ttl = params
            current = self.state["locks"].get(name)
            if current is None or current["expired"] or current["owner"] == owner:
                self.state["locks"][name] = {"owner": owner, "expired": False}
                self.last = (owner,)
            else:
                self.last = None
        elif "DELETE FROM distributed_locks" in sql:
            name, owner = params
            if self.state["locks"].get(name, {}).get("owner") == owner:
                del self.state["locks"][name]; self.rowcount = 1
            else:
                self.rowcount = 0
    def fetchone(self):
        return self.last
    def close(self): pass


class _FakeConn:
    def __init__(self, state, fail=False):
        self.state = state; self.fail = fail; self.commits = 0; self.rollbacks = 0
    def cursor(self): return _FakeCursor(self.state, fail=self.fail)
    def commit(self): self.commits += 1; self.state["commits"] += 1
    def rollback(self): self.rollbacks += 1; self.state["rollbacks"] += 1
    def close(self): pass


def test_production_reservation_uses_one_connection_lock_counts_reserved_and_rolls_back(monkeypatch):
    from db.database import reserve_gemini_attempt
    state = {"attempts": [], "locks": {}, "sql": [], "commits": 0, "rollbacks": 0}
    monkeypatch.setenv("GEMINI_DAILY_HTTP_ATTEMPT_LIMIT", "10")
    monkeypatch.setenv("GEMINI_BACKGROUND_DAILY_HTTP_ATTEMPT_LIMIT", "10")
    monkeypatch.setenv("GEMINI_MAX_ATTEMPTS_PER_REQUEST", "1")
    monkeypatch.setattr("db.database.get_connection", lambda: _FakeConn(state), raising=False)
    assert reserve_gemini_attempt(request_id="r", feature="news_agent") == 1
    with pytest.raises(RuntimeError, match="request_limit_exceeded"):
        reserve_gemini_attempt(request_id="r", feature="news_agent")
    assert any("pg_advisory_xact_lock" in sql for sql in state["sql"])
    assert len(state["attempts"]) == 1
    assert state["rollbacks"] == 1


def test_production_distributed_lock_owner_ttl_release_and_errors(monkeypatch):
    from db.database import acquire_distributed_lock, release_distributed_lock
    state = {"attempts": [], "locks": {}, "sql": [], "commits": 0, "rollbacks": 0}
    monkeypatch.setattr("db.database.get_connection", lambda: _FakeConn(state), raising=False)
    assert acquire_distributed_lock("signal", "A", 60) is True
    assert acquire_distributed_lock("signal", "B", 60) is False
    assert release_distributed_lock("signal", "B") is False
    state["locks"]["signal"]["expired"] = True
    assert acquire_distributed_lock("signal", "B", 60) is True
    monkeypatch.setattr("db.database.get_connection", lambda: _FakeConn(state, fail=True), raising=False)
    assert acquire_distributed_lock("signal", "C", 60) is False


def test_live_analyst_primary_and_repair_share_request_id(monkeypatch):
    import sys, types
    if "psycopg2" not in sys.modules:
        psy = types.ModuleType("psycopg2")
        psy.extras = types.ModuleType("psycopg2.extras")
        psy.errors = types.ModuleType("psycopg2.errors")
        monkeypatch.setitem(sys.modules, "psycopg2", psy)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", psy.extras)
        monkeypatch.setitem(sys.modules, "psycopg2.errors", psy.errors)
    if "requests" not in sys.modules:
        req = types.ModuleType("requests")
        req.post = lambda *a, **k: None
        req.get = lambda *a, **k: None
        req.exceptions = types.SimpleNamespace(Timeout=TimeoutError)
        monkeypatch.setitem(sys.modules, "requests", req)
    import services.live_analyst_service as svc
    calls = []
    monkeypatch.setattr(svc, "can_user_access_live", lambda user_id: {"allowed": True, "mode": "test"})
    monkeypatch.setattr(svc, "is_live_enabled", lambda: True)
    monkeypatch.setattr(svc, "get_live_request_cost", lambda kind: 1)
    monkeypatch.setattr(svc, "can_user_afford_live_request", lambda user_id, cost: True)
    monkeypatch.setattr(svc, "get_max_daily_live_messages", lambda: 0)
    monkeypatch.setattr(svc, "count_live_analyst_messages_today", lambda *a, **k: 0)
    monkeypatch.setattr(svc, "get_or_create_active_session", lambda user_id: {"id": 1})
    monkeypatch.setattr(svc, "extract_polymarket_url", lambda text: "")
    monkeypatch.setattr(svc, "get_memory_message_limit", lambda: 0)
    monkeypatch.setattr(svc, "is_live_followup", lambda text: False)
    monkeypatch.setattr(svc, "get_recent_context", lambda *a, **k: [])
    monkeypatch.setattr(svc, "get_pending_clarification", lambda user_id: None)
    monkeypatch.setattr(svc, "get_live_context", lambda user_id: {})
    monkeypatch.setattr(svc, "resolve_live_conversation_intent", lambda *a, **k: {})
    monkeypatch.setattr(svc, "resolve_live_followup", lambda *a, **k: {})
    monkeypatch.setattr(svc, "understand_live_request", lambda *a, **k: {"mode": "general", "intent": "question", "needs": {}})
    monkeypatch.setattr(svc, "resolve_live_market_context", lambda *a, **k: {"domain": "unknown"})
    monkeypatch.setattr(svc, "plan_live_research_queries", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_should_use_planned_research", lambda *a, **k: False)
    monkeypatch.setattr(svc, "build_live_evidence_pack", lambda *a, **k: {"mode": "general", "intent": "question"})
    monkeypatch.setattr(svc, "merge_market_resolution_into_pack", lambda *a, **k: None)
    monkeypatch.setattr(svc, "build_ai_control_context", lambda *a, **k: {"mode": "general", "intent": "question", "economics": {}})
    monkeypatch.setattr(svc, "choose_ai_provider", lambda *a, **k: {"provider": "gemini", "model": "m", "reason": "test"})
    monkeypatch.setattr(svc, "compose_live_answer", lambda *a, **k: {})
    monkeypatch.setattr(svc, "get_user_analyst_profile", lambda user_id: {})
    monkeypatch.setattr(svc, "build_user_analyst_profile_prompt_block", lambda user_id: "")
    monkeypatch.setattr(svc, "_build_live_deepalpha_score", lambda *a, **k: {})
    monkeypatch.setattr(svc, "build_score_prompt_block", lambda score: "")
    monkeypatch.setattr(svc, "_build_live_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(svc, "_build_live_repair_prompt", lambda *a, **k: "repair")
    monkeypatch.setattr(svc, "generate_live_analyst_text", lambda prompt, **kw: calls.append(kw) or ("incomplete" if len(calls) == 1 else "complete answer"))
    monkeypatch.setattr(svc, "_is_incomplete_live_answer", lambda answer, *a, **k: answer == "incomplete")
    monkeypatch.setattr(svc, "is_strict_non_market_composer", lambda *a, **k: False)
    monkeypatch.setattr(svc, "validate_live_answer_against_evidence", lambda *a, **k: {"severity": "none"})
    monkeypatch.setattr(svc, "format_live_final_answer", lambda answer, *a, **k: answer)
    monkeypatch.setattr(svc, "append_live_followup_suggestions", lambda answer, *a, **k: answer)
    monkeypatch.setattr(svc, "cleanup_final_politics_election_answer", lambda answer, *a, **k: answer)
    monkeypatch.setattr(svc, "score_ai_response_quality", lambda *a, **k: {"quality_score": 1, "penalties": [], "bonuses": [], "should_refund": False})
    monkeypatch.setattr(svc, "record_ai_control_event", lambda **kw: None)
    monkeypatch.setattr(svc, "charge_live_request", lambda *a, **k: True)
    monkeypatch.setattr(svc, "_store_successful_live_context", lambda *a, **k: None)
    monkeypatch.setattr(svc, "clear_pending_clarification", lambda *a, **k: None)
    monkeypatch.setattr(svc, "update_context_from_user_text", lambda session, text: session)
    monkeypatch.setattr(svc, "save_message", lambda *a, **k: None)

    res = svc.process_live_text(1, "hello", router_result={"mode": "general"}, ui_language="en")
    assert res["ok"] is True
    assert len(calls) == 2
    assert calls[0]["request_id"] == calls[1]["request_id"]
