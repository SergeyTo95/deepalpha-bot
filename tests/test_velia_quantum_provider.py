import json

import pytest


@pytest.mark.parametrize('code,message,expected', [
    ('insufficient_quota', 'key_daily_cap: private account data', ('insufficient_quota', 'key_daily_cap')),
    ('insufficient_quota', 'insufficient_credits private account data', ('insufficient_quota', 'insufficient_credits')),
    ('insufficient_quota', 'free_limit_reached: private account data', ('insufficient_quota', 'free_limit_reached')),
    ('rate_limit_exceeded', 'org_token_rate_limit_day: private account data', ('rate_limit_exceeded', 'org_token_rate_limit_day')),
    ('free_tier_requires_payment', 'private account data', ('free_tier_requires_payment', 'none')),
    ('card_required', 'private account data', ('card_required', 'none')),
    ('email_unverified', 'private account data', ('email_unverified', 'none')),
    ('org_under_review', 'private account data', ('org_under_review', 'none')),
    ('insufficient_quota', 'key_daily_cap_private_secret', ('insufficient_quota', 'none')),
    ('insufficient_quota', 'private text mentions key_daily_cap', ('insufficient_quota', 'none')),
    ('private-code', 'key_daily_cap: private account data', ('unknown_error', 'none')),
    (['insufficient_quota'], 'key_daily_cap', ('unknown_error', 'none')),
])
def test_provider_diagnostic_returns_only_fixed_codes_and_quota_prefixes(code, message, expected):
    raw = json.dumps({'error': {'code': code, 'message': message}}).encode()
    assert quantum.provider_error_details(raw) == expected


@pytest.mark.parametrize('raw,expected', [
    (b'private HTML', ('unreadable_error', 'none')),
    (b'{"error":{"code":"card_required","code":"invalid_key"}}', ('unreadable_error', 'none')),
    (b'{"error":{"code":"card_required"},"number":NaN}', ('unreadable_error', 'none')),
    (b'\xff', ('unreadable_error', 'none')),
    (b'x' * 8193, ('oversized_error', 'none')),
    (b'[]', ('unknown_error', 'none')),
    (b'{"error":null}', ('unknown_error', 'none')),
])
def test_provider_diagnostic_rejects_malformed_or_oversized_bodies(raw, expected):
    assert quantum.provider_error_details(raw) == expected

import velia_quantum_provider as quantum


@pytest.fixture
def enabled(monkeypatch):
    for name in ("VELIA_QUANTUM_PROVIDER", "VELIA_QUANTUM_MODEL", "VELIA_QUANTUM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "true")
    monkeypatch.setenv("VELIA_QUANTUM_API_KEY", "quantum-test-secret")


def test_external_free_only_and_no_other_model_credentials(enabled, monkeypatch):
    config = quantum.configuration()
    assert config.model == quantum.FREE_MODEL
    assert config.base_url == quantum.EXPERIENTIAL_URL
    assert config.api_key not in repr(config)
    sent = quantum.payload(config, [{"role": "user", "content": "Привет"}])
    assert sent["model"] == quantum.FREE_MODEL and sent["max_tokens"] == 8192
    assert sent["reasoning_effort"] == "low" and "max_completion_tokens" not in sent
    monkeypatch.delenv("VELIA_QUANTUM_API_KEY")
    monkeypatch.delenv("EXPLABS_API_KEY", raising=False)
    monkeypatch.setenv("KIMI_API_KEY", "must-not-be-used")
    assert not quantum.available()
    monkeypatch.setenv("EXPLABS_API_KEY", "dedicated-key")
    assert quantum.configuration().api_key == "dedicated-key"


@pytest.mark.parametrize("name,value", [
    ("VELIA_QUANTUM_ENABLED", "false"), ("VELIA_QUANTUM_PROVIDER", "unknown"),
    ("VELIA_QUANTUM_MODEL", "glm-5.3-uncensored:free"),
    ("VELIA_QUANTUM_MODEL", "glm-5.3-uncensored:free-other"),
    ("VELIA_QUANTUM_BASE_URL", "https://evil.example/v1"),
    ("VELIA_QUANTUM_BASE_URL", "http://api.experientiallabs.ai/v1"),
    ("VELIA_QUANTUM_API_KEY", "bad\nkey"),
])
def test_external_configuration_fails_closed(enabled, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert quantum.configuration() is None


@pytest.mark.parametrize("base", ["http://worker.railway.internal:8080/v1", "http://127.0.0.1:8020/v1", "https://inference.velia.example/v1"])
def test_self_hosted_switch_needs_only_configuration(enabled, monkeypatch, base):
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", "velia-owned-checkpoint")
    monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", base)
    config = quantum.configuration()
    assert config.base_url == base and config.model == "velia-owned-checkpoint"
    body = quantum.payload(config, [{"role": "user", "content": "hello"}])
    assert body["model"] == config.model and "reasoning_effort" not in body


@pytest.mark.parametrize("base", ["http://public.example/v1", "https://user:pass@worker/v1", "https://worker/v1?key=a",
    "https://worker/v1#fragment", "https://worker/not-v1", "https://worker:invalid/v1", quantum.EXPERIENTIAL_URL])
def test_self_hosted_invalid_addresses_are_rejected(enabled, monkeypatch, base):
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", "local-model")
    monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", base)
    assert not quantum.available()


def wire(content="Поняла", *, done=True, reason="stop"):
    events = [{"choices": [{"delta": {"reasoning": "PRIVATE", "reasoning_content": "PRIVATE"}}]},
        {"choices": [{"delta": {"content": content}, "finish_reason": reason}]},
        {"choices": [], "usage": {"total_tokens": 19, "private": "SECRET"}}]
    return ("".join("data: " + json.dumps(e, ensure_ascii=False) + "\r\n\r\n" for e in events)
        + ("data: [DONE]\r\n\r\n" if done else "")).encode()


def test_byte_split_sse_hides_reasoning_and_requires_complete_answer():
    decoder = quantum.StreamDecoder()
    events = []
    for byte in wire():
        events.extend(decoder.feed(bytes([byte])))
    decoder.finish()
    assert "Поняла" in json.dumps(events, ensure_ascii=False)
    assert "PRIVATE" not in json.dumps(events)
    assert decoder.usage == {"total_tokens": 19}
    assert all(e["model"] == quantum.MODEL_ID for e in events)


@pytest.mark.parametrize("data", [wire(done=False), wire(content=""), wire(reason=None), b'data: {bad}\n\n', b'data: []\n\n',
    b'data: {"error":{"message":"private detail"}}\n\n', b"data: " + b"x" * (256 * 1024 + 1)])
def test_invalid_or_reasoning_only_stream_is_not_success(data):
    decoder = quantum.StreamDecoder()
    with pytest.raises(quantum.QuantumError):
        decoder.feed(data)
        decoder.finish()


def test_context_limit_rejects_without_truncating_latest_question(enabled):
    with pytest.raises(quantum.QuantumError, match="quantum_context_too_long"):
        quantum.payload(quantum.configuration(), [{"role": "user", "content": "x" * 100000}])


def test_nonstream_result_hides_reasoning_and_rejects_empty_answers():
    result = quantum.public_result({"model": "private-model", "usage": {"total_tokens": 19},
        "choices": [{"message": {"content": "answer", "reasoning_content": "private reasoning"}, "finish_reason": "stop"}]})
    assert result["model"] == quantum.MODEL_ID
    assert "private" not in json.dumps(result)
    with pytest.raises(quantum.QuantumError):
        quantum.public_result({"choices": [{"message": {"content": "", "reasoning_content": "PRIVATE"}}]})


@pytest.mark.parametrize('raw', [b'{"data":{"limit":0}}', b'{"data":{"limit":0.0}}'])
def test_explicit_zero_daily_spend_cap_is_required(raw):
    quantum.require_zero_spend_key(raw)


@pytest.mark.parametrize('raw', [
    b'{"data":{"limit":null}}', b'{"data":{"limit":1}}', b'{"data":{"limit":-1}}',
    b'{"data":{"limit":false}}', b'{"data":{"limit":"0"}}', b'{"data":{"limit":1e-4000}}',
    b'{"data":{"limit":0,"limit":null}}', b'{"data":{"limit":NaN}}',
    b'{"data":{"limit_remaining":0}}', b'{"limit":0}', b'[]', b'not json',
    b'{"data":{"limit":0,"label":"'+b'x'*8192+b'"}}',
])
def test_nonzero_unknown_or_ambiguous_caps_never_allow_inference(raw):
    with pytest.raises(quantum.QuantumError,match='quantum_free_guard_required'):
        quantum.require_zero_spend_key(raw)
