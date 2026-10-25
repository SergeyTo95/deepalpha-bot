import json
from types import SimpleNamespace

import pytest
import requests

from services import velia_chat_service as chat, velia_flash_service as flash
from services import velia_quantum_service as quantum
from test_velia_quantum_provider import enabled, wire
from test_velia_flash import postgres_chat


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.body = body if body is not None else wire()
        self.headers = {"Content-Type": "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        for index in range(0, len(self.body), 7):
            yield self.body[index:index + 7]


class Client:
    def __init__(self, response):
        self.response, self.calls = response, []
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_account_generation_free_route_preserves_user_text_and_streams(enabled, monkeypatch):
    client = Client(Response())
    monkeypatch.setattr(quantum.requests, "Session", lambda: client)
    deltas = []
    question = "Не меняй 0,5 TON и порт 8080."
    result = quantum.generate(question, request_id="request-123", on_delta=deltas.append)
    assert result["ok"] and result["text"] == "".join(deltas) == "Поняла"
    assert not result["fallback_used"] and result["estimated_cost_usd"] == 0
    assert result["provider"] == "velia-quantum" and result["model"] == "velia-quantum"
    assert result["usage"] == {"total_tokens": 19}
    assert len(client.calls) == 1 and not client.trust_env
    url, kwargs = client.calls[0]
    assert url == "https://api.experientiallabs.ai/v1/chat/completions"
    assert kwargs["json"]["model"] == "glm-5.3-uncensored:free"
    assert kwargs["json"]["messages"][-1]["content"] == question
    assert kwargs["json"]["reasoning_effort"] == "low"
    assert not kwargs["allow_redirects"]


@pytest.mark.parametrize("response,code", [(Response(429), "quantum_quota_exhausted"),
    (Response(402), "quantum_access_required"), (Response(307), "quantum_provider_error"),
    (Response(401), "quantum_unavailable"), (requests.Timeout(), "quantum_timeout"),
    (Response(body=wire(done=False)), "quantum_invalid_response"),
    (Response(body=wire(reason="length")), "quantum_invalid_response"),
    (Response(body=wire(content="")), "quantum_invalid_response")])
def test_failures_never_retry_or_persist_partial_reasoning(enabled, monkeypatch, response, code):
    client = Client(response)
    monkeypatch.setattr(quantum.requests, "Session", lambda: client)
    result = quantum.generate("hello")
    assert not result["ok"] and result["text"] == "" and result["reason"] == code
    assert len(client.calls) == 1 and not result["fallback_used"]


def test_disabled_quantum_and_unsupported_files_do_not_touch_database(enabled, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled or unsupported request reached a database/provider")
    monkeypatch.setattr(chat, "get_connection", forbidden)
    assert quantum.dispatch_send(7, "conv", "hi", attachment_ids=["file"])["error"] == "quantum_attachments_unsupported"
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "false")
    assert flash.dispatch_send(forbidden, 7, "conv", "hi", chat_mode="quantum")["error"] == "quantum_unavailable"


def test_quantum_voice_uses_quantum_only_with_a_bounded_reasoning_budget(enabled, monkeypatch):
    client = Client(Response())
    monkeypatch.setattr(quantum.requests, "Session", lambda: client)
    result = quantum.generate("Привет, Велия", voice_turn=True)
    assert result["ok"] and result["model"]=="velia-quantum" and len(client.calls)==1
    body=client.calls[0][1]["json"]
    assert body["model"]=="glm-5.3-uncensored:free" and body["max_tokens"]==1024
    assert "one or two complete short sentences" in body["messages"][0]["content"]
    assert "рада тебя слышать" in body["messages"][0]["content"]
    assert "Род собеседника не меняй" in body["messages"][0]["content"]
    assert "распознавания речи" in body["messages"][0]["content"]
    assert body["messages"][-1]["content"]=="Привет, Велия"
    assert body["reasoning_effort"]=="low"


def test_quantum_file_capability_tracks_local_file_processing(enabled, monkeypatch):
    monkeypatch.setenv("VELIA_FILE_ANALYST_ENABLED","true")
    assert quantum.public_capability()["chat_quantum_attachments"] is True
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED","false")
    assert quantum.public_capability()["chat_quantum_attachments"] is False


@pytest.mark.parametrize("provider,mode", [("velia-quantum", "quantum"), ("bonsai", "flash"), ("kimi", "pro"), (None, "pro")])
def test_saved_mode_uses_stable_quantum_identity(provider, mode):
    row = {"provider": provider, "role": "assistant", "content": "answer"}
    assert chat._serialize_message(row)["chat_mode"] == mode


def test_quantum_budget_accepts_real_dict_cursor_rows(monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_USER_DAILY_LIMIT", "1")
    seen = []
    cursor = SimpleNamespace(execute=lambda sql, params: seen.append((sql, params)),
        fetchone=lambda: {"user_count": 1, "total_count": 2})
    assert quantum.budget_error(cursor, 7) == "quantum_daily_user_limit_exceeded"
    assert "AS user_count" in seen[0][0] and "AS total_count" in seen[0][0]


def test_postgres_quantum_history_idempotency_quotas_and_owner(enabled, monkeypatch, postgres_chat):
    proxy, connect = postgres_chat
    from services import velia_attachment_service
    monkeypatch.setattr(velia_attachment_service, "get_connection", connect)
    velia_attachment_service.ensure_velia_attachment_tables()
    monkeypatch.setenv("VELIA_CHAT_ENABLED", "true")
    monkeypatch.delenv("VELIA_CHAT_BETA_USER_IDS", raising=False)
    monkeypatch.setenv("VELIA_QUANTUM_USER_DAILY_LIMIT", "1")
    def paid(*args, **kwargs):
        pytest.fail("Quantum reached the Pro generation or billing path")
    proxy._budget_error = paid
    proxy.generate_velia_chat_result = paid
    calls = []
    def generate(prompt, **kwargs):
        calls.append(prompt)
        if kwargs.get("on_delta"):
            kwargs["on_delta"]("Quantum answer")
        return {"ok": True, "text": "Quantum answer", "provider": "velia-quantum", "model": "velia-quantum",
            "estimated_cost_usd": 0.0, "usage": {}, "request_id": kwargs["request_id"]}
    monkeypatch.setattr(quantum, "generate", generate)
    cid = chat.create_conversation(77)["id"]
    deltas = []
    first = flash.dispatch_send(paid, 77, cid, "Analyze architecture", chat_mode="quantum",
        idempotency_key="quantum-turn-1", on_delta=deltas.append)
    assert first["ok"] and first["assistant_message"]["chat_mode"] == "quantum"
    assert deltas == ["Quantum answer"]
    again = flash.dispatch_send(paid, 77, cid, "Analyze architecture", chat_mode="quantum", idempotency_key="quantum-turn-1")
    assert again["ok"] and again["duplicate"] and len(calls) == 1
    assert chat._daily_usage_snapshot(77)["user_messages"] == 0
    limited = flash.dispatch_send(paid, 77, cid, "second", chat_mode="quantum", idempotency_key="quantum-turn-2")
    assert limited["error"] == "quantum_daily_user_limit_exceeded"
    denied = flash.dispatch_send(paid, 78, cid, "other owner", chat_mode="quantum", idempotency_key="quantum-turn-3")
    assert denied["error"] == "conversation_not_found"
    with connect() as conn, conn.cursor() as cursor:
        cross = chat._existing_request_result(cursor, user_id=77, conversation_id=cid, idempotency_key="quantum-turn-1", chat_mode="flash")
        assert cross["error"] == "idempotency_mode_mismatch"
        cursor.execute("SELECT provider, model, estimated_cost_usd FROM velia_messages WHERE role='assistant'")
        provider, model, cost = cursor.fetchone()
        assert provider == model == "velia-quantum" and float(cost) == 0.0
