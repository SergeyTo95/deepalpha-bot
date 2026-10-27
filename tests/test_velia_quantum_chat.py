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
    def __init__(self, response, guard_response=None):
        self.response, self.calls = response, []
        self.guard_response = guard_response if guard_response is not None else Response(body=b'{"data":{"limit":0}}')
        self.guard_calls = []
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

    def get(self, url, **kwargs):
        self.guard_calls.append((url, kwargs))
        if isinstance(self.guard_response, Exception):
            raise self.guard_response
        return self.guard_response


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
    assert kwargs["json"]["model"] == "glm-5.3-uncensored"
    assert kwargs["json"]["messages"][-1]["content"] == question
    assert kwargs["json"]["reasoning_effort"] == "low"
    assert not kwargs["allow_redirects"]
    assert len(client.guard_calls)==1
    guard_url,guard=client.guard_calls[0]
    assert guard_url=='https://api.experientiallabs.ai/api/v1/key'
    assert guard['headers']['Authorization']==kwargs['headers']['Authorization'] and guard['allow_redirects'] is False
    assert guard['headers']['Cache-Control']=='no-cache'
    assert kwargs['json']['gateway']['retry']['max_total_attempts']==1
    assert kwargs['json']['gateway']['routing']['allow_fallbacks'] is False


@pytest.mark.parametrize("voice", [False, True])
def test_zai_account_chat_and_voice_use_dedicated_endpoint_without_aggregator(enabled, monkeypatch, voice):
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "zai")
    client = Client(Response())
    monkeypatch.setattr(quantum.requests, "Session", lambda: client)
    result = quantum.generate("こんにちは", voice_turn=voice)
    assert result["ok"] and result["text"] == "Поняла" and result["fallback_used"] is False
    assert client.guard_calls == [] and len(client.calls) == 1
    url, request = client.calls[0]
    assert url == "https://api.z.ai/api/paas/v4/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer quantum-test-secret"
    assert request["json"]["model"] == "glm-4.7-flash"
    assert request["json"]["thinking"] == {"type": "disabled"}
    assert request["json"]["max_tokens"] == (1024 if voice else 8192)
    assert "gateway" not in request["json"] and request["allow_redirects"] is False


def test_small_sse_delta_reaches_client_before_upstream_finishes(enabled, monkeypatch, caplog):
    """Exercise a real unchunked HTTP stream, not a mocked chunk-size assertion."""
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    first_delta, finish = threading.Event(), threading.Event()
    parts = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            early = {"choices": [{"delta": {"content": "Поняла"}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(early) + "\n\n").encode())
            self.wfile.flush()
            finish.wait(5)
            self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            self.wfile.flush()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
    monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", "local-test")
    caplog.set_level("INFO", logger="services.velia_quantum_service")
    def delta(value):
        parts.append(value)
        first_delta.set()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(quantum.generate, "synthetic private prompt", on_delta=delta)
            try:
                assert first_delta.wait(2), "first SSE delta was buffered until generation finished"
                assert parts == ["Поняла"] and not result.done()
            finally:
                finish.set()
            assert result.result(timeout=3)["ok"]
    finally:
        finish.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    timing = [r.message for r in caplog.records if "VELIA_QUANTUM_RESPONSE_TIMING" in r.message]
    assert len(timing) == 1 and "outcome=complete" in timing[0] and "first_byte_ms=" in timing[0]
    assert "synthetic private prompt" not in timing[0] and "quantum-test-secret" not in timing[0]


def test_quantum_reuses_http_connection_without_cookies_or_cached_answers_and_rotates_key(enabled, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    seen = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            prompt = body["messages"][-1]["content"]
            seen.append((self.client_address[1], self.headers.get("Cookie"), self.headers["Authorization"], prompt))
            result = wire(content=prompt)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(result)))
            self.send_header("Set-Cookie", "provider_tracker=must-not-be-forwarded; Path=/")
            self.end_headers()
            self.wfile.write(result)
            self.wfile.flush()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
    monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", "owned-test")
    try:
        for prompt in ("first isolated prompt", "second isolated prompt"):
            answer = quantum.generate(prompt)
            assert answer["ok"] and answer["text"] == prompt
        assert len(seen) == 2 and seen[0][0] == seen[1][0]
        assert all(row[1] is None for row in seen)
        monkeypatch.setenv("VELIA_QUANTUM_API_KEY", "rotated-test-secret")
        assert quantum.generate("third isolated prompt")["ok"]
        assert len(seen) == 3 and seen[2][0] != seen[1][0]
        assert seen[2][1] is None and seen[2][2] == "Bearer rotated-test-secret"
    finally:
        quantum._close_transport()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reused_transport_rechecks_aggregator_zero_spend_cap_before_every_completion(enabled, monkeypatch):
    client = Client(Response())
    monkeypatch.setattr(quantum.requests, "Session", lambda: client)
    assert quantum.generate("first synthetic turn")["ok"]
    client.guard_response = Response(body=b'{"data":{"limit":1}}')
    second = quantum.generate("second synthetic turn")
    assert not second["ok"] and second["reason"] == "quantum_free_guard_required"
    assert len(client.guard_calls) == 2 and len(client.calls) == 1


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


@pytest.mark.parametrize('status,code,message,reason', [
    (402, 'card_required', 'private provider body', 'none'),
    (402, 'email_unverified', 'private provider body', 'none'),
    (402, 'free_tier_requires_payment', 'private provider body', 'none'),
    (429, 'insufficient_quota', 'key_daily_cap: private provider body', 'key_daily_cap'),
    (429, 'insufficient_quota', 'insufficient_credits: private provider body', 'insufficient_credits'),
    (429, 'insufficient_quota', 'free_limit_reached: private provider body', 'free_limit_reached'),
])
def test_native_rejection_logs_safe_reason_without_retry(enabled, monkeypatch, caplog, status, code, message, reason):
    raw = json.dumps({'error': {'code': code, 'message': message}}).encode()
    client = Client(Response(status, raw))
    monkeypatch.setattr(quantum.requests, 'Session', lambda: client)
    result = quantum.generate('private user prompt', voice_turn=True)
    expected = ('quantum_access_required' if status == 402 else
                'quantum_spend_cap_reached' if reason == 'key_daily_cap' else 'quantum_quota_exhausted')
    assert not result['ok'] and result['reason'] == expected
    assert len(client.guard_calls) == len(client.calls) == 1 and not result['fallback_used']
    assert f'status={status} provider_code={code} quota_reason={reason}' in caplog.text
    assert 'private' not in caplog.text and 'quantum-test-secret' not in caplog.text


@pytest.mark.parametrize('raw,expected', [
    (b'private error body', 'unreadable_error'),
    (b'x' * 8193, 'oversized_error'),
])
def test_native_unreadable_rejection_keeps_original_http_error(enabled, monkeypatch, caplog, raw, expected):
    client = Client(Response(402, raw))
    monkeypatch.setattr(quantum.requests, 'Session', lambda: client)
    result = quantum.generate('private user prompt')
    assert not result['ok'] and result['reason'] == 'quantum_access_required'
    assert len(client.calls) == 1 and f'provider_code={expected}' in caplog.text
    assert 'private' not in caplog.text


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
    assert body["model"]=="glm-5.3-uncensored" and body["max_tokens"]==1024
    assert "one or two complete short sentences" in body["messages"][0]["content"]
    assert "рада тебя слышать" in body["messages"][0]["content"]
    assert "Род собеседника не меняй" in body["messages"][0]["content"]
    assert "распознавания речи" in body["messages"][0]["content"]
    assert body["messages"][-1]["content"]=="Привет, Велия"
    assert body["reasoning_effort"]=="low"


@pytest.mark.parametrize('guard',[
    Response(body=b'{"data":{"limit":null}}'),
    Response(body=b'{"data":{"limit":1,"label":"private label"}}'),
    Response(body=b'{"data":{"limit":false}}'),
    Response(body=b'{"data":{"limit_remaining":0}}'),
    Response(body=b'{"data":{"limit":0,"limit":10}}'),
    Response(body=b'x'*8193),Response(body=b'invalid private body'),
    Response(401),Response(403),Response(307),requests.Timeout(),
])
def test_native_never_posts_without_a_verified_zero_spend_key(enabled,monkeypatch,caplog,guard):
    client=Client(Response(),guard_response=guard)
    monkeypatch.setattr(quantum.requests,'Session',lambda:client)
    result=quantum.generate('private user prompt')
    assert not result['ok'] and result['reason']=='quantum_free_guard_required'
    assert len(client.guard_calls)==1 and client.calls==[]
    assert 'private' not in caplog.text and 'quantum-test-secret' not in caplog.text
    assert not result['fallback_used']


def test_own_model_does_not_depend_on_experiential_budget_reads(enabled,monkeypatch):
    client=Client(Response(),guard_response=AssertionError('own model contacted Experiential'))
    monkeypatch.setattr(quantum.requests,'Session',lambda:client)
    monkeypatch.setenv('VELIA_QUANTUM_PROVIDER','self_hosted')
    monkeypatch.setenv('VELIA_QUANTUM_MODEL','owned-model')
    monkeypatch.setenv('VELIA_QUANTUM_BASE_URL','http://worker.railway.internal:8080/v1')
    assert quantum.generate('hello')['ok']
    assert client.guard_calls==[] and len(client.calls)==1


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
