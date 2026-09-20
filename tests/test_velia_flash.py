import json
import os
import uuid
from types import SimpleNamespace

import pytest
import requests

from services import velia_flash_service as flash
from services import velia_chat_service as chat
from services import velia_attachment_chat_runtime_patch as attachment
from services import velia_chat_streaming_runtime_patch as streaming
from services.velia_mobile_streaming_service import _stream_send_kwargs


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("VELIA_FLASH_ENABLED", "true")
    monkeypatch.setenv("VELIA_FLASH_API_KEY", "unit-test-credential")
    monkeypatch.setenv("VELIA_FLASH_BASE_URL", "http://bonsai.railway.internal:8080")
    monkeypatch.setenv("VELIA_CHAT_ENABLED", "true")
    monkeypatch.delenv("VELIA_CHAT_BETA_USER_IDS", raising=False)


def test_default_unavailable_and_invalid_mode_never_calls_paid(monkeypatch):
    monkeypatch.delenv("VELIA_FLASH_ENABLED", raising=False)
    def paid(*args, **kwargs):
        pytest.fail("paid provider reached from Flash")
    for mode in ["flash", "invalid", None, True]:
        assert not flash.dispatch_send(paid, 1, "c", "hello", chat_mode=mode)["ok"]


@pytest.mark.parametrize("url", ["http://public.example", "https://u:p@public.example",
                                "https://public.example/path", "https://public.example?key=x"])
def test_endpoint_rejects_unsafe_or_ambiguous_configuration(enabled, monkeypatch, url):
    monkeypatch.setenv("VELIA_FLASH_BASE_URL", url)
    assert not flash.available()


def test_pro_preserves_legacy_sender():
    calls = []
    result = flash.dispatch_send(lambda *a, **k: calls.append((a, k)) or {"ok": True},
                                 7, "conv", "hello", idempotency_key="request-123")
    assert result["ok"]
    assert calls == [((7, "conv", "hello"), {"idempotency_key": "request-123"})]


class Response:
    status_code = 200
    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload).encode()
    def json(self):
        return self.payload
    def close(self):
        pass


class Session:
    def __init__(self, failure=None):
        self.headers = {}
        self.calls = []
        self.failure = failure
        self.trust_env = True
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/apply-template"):
            return Response({"prompt": "rendered chat"})
        if url.endswith("/tokenize"):
            return Response({"tokens": [1, 2, 3]})
        if self.failure:
            raise self.failure
        return Response({"choices": [{"message": {"content": "391"}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 30, "completion_tokens": 3}})
    def close(self):
        pass


def test_real_template_budget_and_free_result(enabled, monkeypatch):
    session = Session()
    monkeypatch.setattr(flash.requests, "Session", lambda: session)
    result = flash.generate([{"role": "user", "content": "17*23"}])
    assert result["text"] == "391"
    assert result["estimated_cost_usd"] == 0
    assert result["provider"] == "bonsai" and not result["fallback_used"]
    assert session.trust_env is False
    assert all(not kwargs["allow_redirects"] for _, kwargs in session.calls)
    assert session.calls[-1][1]["json"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_provider_failure_does_not_retry_or_fallback(enabled, monkeypatch):
    session = Session(requests.Timeout())
    monkeypatch.setattr(flash.requests, "Session", lambda: session)
    result = flash.generate([{"role": "user", "content": "hi"}])
    assert result["reason"] == "flash_timeout"
    assert len(session.calls) == 3
    assert not result["fallback_used"]


def test_context_overflow_never_generates(enabled, monkeypatch):
    session = Session()
    original = session.post
    def post(url, **kwargs):
        if url.endswith("/tokenize"):
            return Response({"tokens": list(range(5000))})
        return original(url, **kwargs)
    monkeypatch.setattr(session, "post", post)
    monkeypatch.setattr(flash.requests, "Session", lambda: session)
    result = flash.generate([{"role": "user", "content": "very large input"}])
    assert result["reason"] == "flash_context_too_long"
    assert not any(url.endswith("completions") for url, _ in session.calls)


def test_stream_keeps_flash_selection_before_provider_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(flash, "dispatch_send", lambda *a, **k: calls.append(k) or {"ok": True})
    kwargs = _stream_send_kwargs({"chat_mode": "flash"}, user_id=1, conversation_id="c",
                                content="hi", idempotency_key="request-123")
    assert streaming.run_streaming_send(lambda: pytest.fail("paid sender"),
        **kwargs, on_delta=lambda _: None, on_reset=lambda: None)["ok"]
    assert calls[0]["chat_mode"] == "flash"


@pytest.fixture
def postgres_chat(monkeypatch):
    dsn = os.getenv("VELIA_FLASH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Dedicated PostgreSQL required; exercised in Flash CI")
    import psycopg2
    schema = "flash_test_" + uuid.uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute('CREATE SCHEMA "' + schema + '"')
    def connect():
        return psycopg2.connect(dsn, options="-c search_path=" + schema)
    monkeypatch.setattr(chat, "get_connection", connect)
    monkeypatch.setattr(attachment, "get_connection", connect)
    chat.ensure_velia_chat_tables()
    proxy = SimpleNamespace(**vars(chat))
    proxy._velia_attachment_chat_patch_installed = False
    attachment.install(proxy)
    monkeypatch.setattr(chat, "_attachment_persistence_send", proxy._attachment_persistence_send,
                        raising=False)
    try:
        yield proxy, connect
    finally:
        with admin.cursor() as cursor:
            cursor.execute('DROP SCHEMA "' + schema + '" CASCADE')
        admin.close()


def test_postgres_flash_owner_quota_idempotency_and_zero_paid_path(enabled, monkeypatch, postgres_chat):
    proxy, connect = postgres_chat
    monkeypatch.setenv("VELIA_FLASH_USER_DAILY_LIMIT", "1")
    def paid(*args, **kwargs):
        pytest.fail("paid route reached")
    proxy._budget_error = paid
    proxy._build_prompt = paid
    proxy.generate_velia_chat_result = paid
    calls = []
    def generate(messages, **kwargs):
        calls.append(messages)
        return {"ok": True, "text": "Flash answer", "provider": "bonsai", "model": "velia-flash",
                "estimated_cost_usd": 0.0, "usage": {}, "request_id": kwargs["request_id"]}
    monkeypatch.setattr(flash, "generate", generate)
    conversation = chat.create_conversation(77)
    cid = conversation["id"]
    first = flash.dispatch_send(paid, 77, cid, "Write a Python function", chat_mode="flash",
                                idempotency_key="flash-turn-1")
    assert first["ok"], first
    assert first["assistant_message"]["chat_mode"] == "flash"
    again = flash.dispatch_send(paid, 77, cid, "Write a Python function", chat_mode="flash",
                                idempotency_key="flash-turn-1")
    assert again["ok"] and again["duplicate"] and len(calls) == 1
    assert chat._daily_usage_snapshot(77)["user_messages"] == 0
    limited = flash.dispatch_send(paid, 77, cid, "second", chat_mode="flash",
                                  idempotency_key="flash-turn-2")
    assert limited["error"] == "flash_daily_user_limit_exceeded"
    denied = flash.dispatch_send(paid, 78, cid, "other owner", chat_mode="flash",
                                 idempotency_key="flash-turn-3")
    assert denied["error"] == "conversation_not_found"
    with connect() as conn, conn.cursor() as cursor:
        cross_mode = chat._existing_request_result(cursor, user_id=77, conversation_id=cid,
                                                   idempotency_key="flash-turn-1")
        assert cross_mode["error"] == "idempotency_mode_mismatch"
        cursor.execute("SELECT estimated_cost_usd FROM velia_messages WHERE role='assistant'")
        assert float(cursor.fetchone()[0]) == 0.0
