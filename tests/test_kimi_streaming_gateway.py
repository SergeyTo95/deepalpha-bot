from services import kimi_streaming_gateway as streaming


class FakeResponse:
    def __init__(self, lines, status_code=200, headers=None):
        self._lines = list(lines)
        self.status_code = status_code
        self.headers = headers or {"x-request-id": "provider-request-1"}
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        self.closed = True


class FakeRequests:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


def _patch_gateway(monkeypatch, responses):
    finalized = []
    monkeypatch.setenv("KIMI_ENABLED", "true")
    monkeypatch.setenv("KIMI_API_KEY", "test-secret")
    monkeypatch.setenv("KIMI_MAX_RETRIES", "0")
    monkeypatch.setattr(streaming.kimi_gateway, "requests", FakeRequests(responses))

    import db.database as database

    monkeypatch.setattr(database, "reserve_gemini_attempt", lambda **kwargs: "attempt-1")
    monkeypatch.setattr(
        database,
        "finalize_gemini_attempt",
        lambda *args, **kwargs: finalized.append((args, kwargs)),
    )
    return finalized


def test_streaming_gateway_emits_only_final_content_and_records_usage(monkeypatch):
    response = FakeResponse(
        [
            'data: {"choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"reasoning_content":"private"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"Привет"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"!"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12,"prompt_tokens_details":{"cached_tokens":4},"completion_tokens_details":{"reasoning_tokens":3}}}',
            "data: [DONE]",
        ]
    )
    finalized = _patch_gateway(monkeypatch, [response])
    deltas = []

    result = streaming.call_kimi_stream(
        prompt="USER: hello",
        feature="velia_chat",
        on_delta=deltas.append,
        request_id="request-1",
        cycle_id="conversation-1",
        user_id=7,
        max_tokens=512,
        prompt_cache_key="cache-1",
        reasoning_effort="high",
    )

    assert result["ok"] is True
    assert result["text"] == "Привет!"
    assert deltas == ["Привет", "!"]
    assert result["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "cached_input_tokens": 4,
        "reasoning_tokens": 3,
    }
    assert finalized[-1][1]["status"] == "success"
    assert finalized[-1][1]["prompt_tokens"] == 10
    assert response.closed is True
    payload = streaming.kimi_gateway.requests.calls[0][1]["json"]
    assert payload["stream"] is True
    assert payload["prompt_cache_key"] == "cache-1"
    assert payload["reasoning_effort"] == "high"


def test_streaming_gateway_resets_partial_text_before_retry(monkeypatch):
    first = FakeResponse(
        [
            'data: {"choices":[{"delta":{"content":"Незавершённый"},"finish_reason":null}]}',
        ]
    )
    second = FakeResponse(
        [
            'data: {"choices":[{"delta":{"content":"Полный ответ"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}',
            "data: [DONE]",
        ]
    )
    finalized = _patch_gateway(monkeypatch, [first, second])
    monkeypatch.setenv("KIMI_MAX_RETRIES", "1")
    events = []

    result = streaming.call_kimi_stream(
        prompt="USER: analyze",
        feature="velia_chat",
        on_delta=lambda text: events.append(("delta", text)),
        on_reset=lambda: events.append(("reset", "")),
        request_id="request-2",
        cycle_id="conversation-2",
        user_id=7,
        max_tokens=512,
    )

    assert result["ok"] is True
    assert result["text"] == "Полный ответ"
    assert events == [
        ("delta", "Незавершённый"),
        ("reset", ""),
        ("delta", "Полный ответ"),
    ]
    assert finalized[0][1]["status"] == "failed"
    assert finalized[-1][1]["status"] == "success"


def test_streaming_gateway_accepts_usage_nested_in_final_choice(monkeypatch):
    response = FakeResponse(
        [
            'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop","usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}]}',
            "data: [DONE]",
        ]
    )
    _patch_gateway(monkeypatch, [response])

    result = streaming.call_kimi_stream(
        prompt="USER: test",
        feature="velia_chat",
        on_delta=lambda text: None,
        request_id="request-3",
        user_id=7,
        max_tokens=512,
    )

    assert result["ok"] is True
    assert result["usage"]["prompt_tokens"] == 2
    assert result["usage"]["completion_tokens"] == 1
    assert result["usage"]["total_tokens"] == 3
