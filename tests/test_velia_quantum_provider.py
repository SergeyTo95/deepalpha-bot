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


@pytest.mark.parametrize("code,reason,error", [
    ("1302","request_rate_limit","quantum_rate_limited"),
    ("1305","provider_overloaded","quantum_overloaded"),
    ("1113","balance_or_package_required","quantum_access_required"),
    ("1308","usage_limit","quantum_quota_exhausted"),
    ("1310","weekly_or_monthly_limit","quantum_quota_exhausted"),
])
@pytest.mark.parametrize("numeric", [False,True])
def test_zai_business_codes_distinguish_limits_without_logging_provider_prose(code,reason,error,numeric):
    raw=json.dumps({"error":{"code":int(code) if numeric else code,"message":"private account details"}}).encode()
    assert quantum.provider_error_details(raw)==(code,reason)
    assert quantum.response_error(429,provider_code=code,quota_reason=reason).code==error


@pytest.mark.parametrize("code,error",[("1302","quantum_rate_limited"),("1305","quantum_overloaded")])
def test_zai_stream_error_keeps_safe_business_code(code,error,caplog):
    raw=("data: "+json.dumps({"error":{"code":code,"message":"secret provider message"}})+"\n\n").encode()
    with pytest.raises(quantum.QuantumError,match=error):
        quantum.StreamDecoder().feed(raw)
    assert code in caplog.text and "secret provider message" not in caplog.text


def test_zai_uses_its_own_key_endpoint_and_free_model(enabled, monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "zai")
    config = quantum.configuration()
    assert config.base_url == quantum.ZAI_URL
    assert config.model == "glm-4.7-flash"
    assert config.source == "GLM-4.7-Flash через Z.ai"
    sent = quantum.payload(config, [{"role": "user", "content": "こんにちは"}],
        tools=[{"type": "function", "function": {"name": "read"}}], tool_choice="required")
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["tool_choice"] == "auto"
    assert "gateway" not in sent and "reasoning_effort" not in sent
    monkeypatch.delenv("VELIA_QUANTUM_API_KEY")
    monkeypatch.setenv("EXPLABS_API_KEY", "aggregator-key-must-not-be-used")
    monkeypatch.setenv("KIMI_API_KEY", "other-key-must-not-be-used")
    assert quantum.configuration() is None


@pytest.mark.parametrize("name,value", [
    ("VELIA_QUANTUM_MODEL", "glm-4.7-flashx"),
    ("VELIA_QUANTUM_MODEL", "glm-5.3"),
    ("VELIA_QUANTUM_MODEL", "glm-4.7-flash:free"),
    ("VELIA_QUANTUM_BASE_URL", quantum.EXPERIENTIAL_URL),
    ("VELIA_QUANTUM_BASE_URL", "https://api.z.ai/api/coding/paas/v4"),
    ("VELIA_QUANTUM_BASE_URL", "https://evil.example/api/paas/v4"),
    ("VELIA_QUANTUM_API_KEY", "${{missing.KEY}}"),
])
def test_zai_rejects_paid_models_wrong_hosts_and_unresolved_keys(enabled, monkeypatch, name, value):
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "zai")
    monkeypatch.setenv(name, value)
    assert quantum.configuration() is None


def test_zai_cannot_enable_billed_builtin_tools(enabled, monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "zai")
    with pytest.raises(quantum.QuantumError, match="quantum_free_guard_required"):
        quantum.payload(quantum.configuration(), [{"role": "user", "content": "search"}],
            tools=[{"type": "web_search", "web_search": {"enable": True}}])


@pytest.mark.parametrize("failure", [None, "denied", "plain_text", "truncated"])
def test_zai_migration_proves_tools_and_stream_without_claiming_metered_cost(enabled, monkeypatch, capsys, failure):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    seen = []
    async def run():
        async def completion(request):
            body = await request.json()
            seen.append(body)
            assert body["model"] == "glm-4.7-flash"
            assert body["thinking"] == {"type": "disabled"} and "gateway" not in body
            assert request.headers["Authorization"] == "Bearer quantum-test-secret"
            if failure == "denied":
                return web.json_response({"error": {"code": "private", "message": "private"}}, status=403)
            if not body["stream"]:
                assert body["tool_choice"] == "auto"
                if failure == "plain_text":
                    return web.json_response({"choices": [{"finish_reason": "stop", "message": {"content": "15"}}]})
                return web.json_response({"usage": {"total_tokens": 20}, "choices": [{"finish_reason": "tool_calls",
                    "message": {"tool_calls": [{"function": {"name": "report_sum", "arguments": {"sum": 15}}}]}}]})
            return web.Response(body=wire(done=failure != "truncated"), content_type="text/event-stream")
        app = web.Application()
        app.router.add_post("/api/paas/v4/chat/completions", completion)
        async with TestServer(app) as server:
            monkeypatch.setattr(quantum, "ZAI_URL", str(server.make_url("/api/paas/v4")))
            monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "zai")
            monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", quantum.ZAI_URL)
            if failure:
                with pytest.raises((quantum.QuantumError, KeyError)):
                    await quantum.probe_free_core()
            else:
                await quantum.probe_free_core()
    asyncio.run(run())
    assert len(seen) == (1 if failure in {"denied", "plain_text"} else 2)
    output = capsys.readouterr().out
    assert "quantum-test-secret" not in output and "private" not in output
    if not failure:
        report = json.loads(output.removeprefix("VELIA_QUANTUM_FREE_ACCEPTED "))
        assert report["ok"] and report["tool_call"] and report["stream"]
        assert report["pricing_basis"] == "zai_published_free_model"
        assert "reported_cost_usd" not in report


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


@pytest.mark.parametrize("model", [quantum.FREE_CORE_MODEL, quantum.FREE_CORE_ROUTE])
def test_quantum_free_core_is_an_explicit_model_switch(enabled, monkeypatch, model):
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", model)
    config = quantum.configuration()
    assert config.model == model
    assert config.source == "DeepSeek V4.1 Flash через Experiential"
    sent = quantum.payload(config, [{"role": "user", "content": "Merhaba"}])
    assert sent["model"] == config.model
    assert sent["reasoning_effort"] == "low"
    assert sent["gateway"]["routing"]["allow_fallbacks"] is False
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", "another-paid-model")
    assert quantum.configuration() is None


@pytest.mark.parametrize("usage", [None, {}, {"cost": 0, "total_tokens": 10},
    {"cost": 0, "is_byok": True, "total_tokens": 10},
    {"cost": 0.001, "is_byok": False, "total_tokens": 10},
    {"cost": False, "is_byok": False, "total_tokens": 10},
    {"cost": "0", "is_byok": False, "total_tokens": 10},
    {"cost": 0, "is_byok": False, "total_tokens": 0}])
def test_free_acceptance_needs_explicit_zero_cost_and_real_metering(usage):
    with pytest.raises(quantum.QuantumError):
        quantum.require_free_usage({"usage": usage})


@pytest.mark.parametrize("model", [quantum.FREE_CORE_MODEL, quantum.FREE_CORE_ROUTE])
@pytest.mark.parametrize("failure", [None, "paid", "quota", "bad_tool"])
def test_free_migration_checks_exact_key_tools_and_stream_without_fallback(enabled, monkeypatch, capsys, failure, model):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    seen = []
    async def run():
        async def key(request):
            assert request.headers["Authorization"] == "Bearer quantum-test-secret"
            seen.append("guard")
            return web.json_response({"data": {"limit": 0}})
        async def completion(request):
            body = await request.json()
            assert request.headers["Authorization"] == "Bearer quantum-test-secret"
            assert body["model"] == model
            assert body["gateway"]["routing"]["allow_fallbacks"] is False
            assert body["gateway"]["retry"]["max_total_attempts"] == 1
            seen.append("stream" if body["stream"] else "tool")
            usage = {"cost": .001 if failure == "paid" else 0, "is_byok": False, "total_tokens": 20}
            if failure == "quota":
                return web.json_response({"error": {"code": "insufficient_quota", "message": "key_daily_cap: private account"}}, status=429)
            if not body["stream"]:
                return web.json_response({"usage": usage, "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{"function": {
                    "name": "report_sum", "arguments": json.dumps({"sum": 99 if failure == "bad_tool" else 15})}}]}}]})
            raw = "data: " + json.dumps({"choices": [{"delta": {"content": "Готова. Ready. Hazırım."}, "finish_reason": "stop"}]})
            raw += "\n\ndata: " + json.dumps({"choices": [], "usage": usage}) + "\n\ndata: [DONE]\n\n"
            return web.Response(text=raw, content_type="text/event-stream")
        app = web.Application()
        app.router.add_get("/api/v1/key", key)
        app.router.add_post("/v1/chat/completions", completion)
        async with TestServer(app) as server:
            monkeypatch.setattr(quantum, "EXPERIENTIAL_URL", str(server.make_url("/v1")))
            monkeypatch.setattr(quantum, "KEY_INFO_URL", str(server.make_url("/api/v1/key")))
            monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", quantum.EXPERIENTIAL_URL)
            monkeypatch.setenv("VELIA_QUANTUM_MODEL", model)
            if failure:
                with pytest.raises(quantum.QuantumError):
                    await quantum.probe_free_core()
            else:
                await quantum.probe_free_core()
    asyncio.run(run())
    assert seen == (["guard", "tool"] if failure else ["guard", "tool", "guard", "stream"])
    output = capsys.readouterr().out
    assert "quantum-test-secret" not in output and "private account" not in output
    if not failure:
        report = json.loads(output.removeprefix("VELIA_QUANTUM_FREE_ACCEPTED "))
        assert report["ok"] and report["tool_call"] and report["stream"]
        assert report["reported_cost_usd"] == 0 and report["calls"] == 2


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


@pytest.mark.parametrize('status,code,reason,expected', [
    (429, 'insufficient_quota', 'key_daily_cap', 'quantum_spend_cap_reached'),
    (429, 'insufficient_quota', 'insufficient_credits', 'quantum_quota_exhausted'),
    (429, 'rate_limit_exceeded', 'key_daily_cap', 'quantum_quota_exhausted'),
    (401, 'insufficient_quota', 'key_daily_cap', 'quantum_unavailable'),
])
def test_spending_cap_classification_requires_exact_provider_rejection(status, code, reason, expected):
    error = quantum.response_error(status, provider_code=code, quota_reason=reason)
    assert error.code == expected
    if expected == 'quantum_spend_cap_reached':
        assert error.status == 503


@pytest.mark.parametrize("voice", [False, True])
def test_free_pro_thinking_search_continuation_keeps_reasoning_private_and_turn_local(monkeypatch, voice):
    import velia_free_models as free
    from velia_quantum_turn import Turn
    monkeypatch.setenv("VELIA_FREE_API_TIERS_ENABLED", "true")
    monkeypatch.setenv("VELIA_ZAI_API_KEY", "fixture-zai")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config = free.configuration("pro", voice=voice)
    turn = Turn(config, [{"role":"user", "content":"Check Python"}], search_enabled=True)
    body = turn.body()
    assert body["model"] == "glm-4.7-flash" and body["thinking"] == {"type":"enabled"}
    assert body["max_tokens"] == (4096 if voice else 8192)
    tool = {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function",
        "function":{"name":"web_search","arguments":json.dumps({"query":"Python version","scope":"general"})}}]},"finish_reason":"tool_calls"}]}
    reasoning = {"choices":[{"delta":{"reasoning_content":"PRIVATE-THINKING"}}]}
    stream = ("data: "+json.dumps(reasoning)+"\n\ndata: "+json.dumps(tool)+"\n\ndata: [DONE]\n\n").encode()
    public = []
    for byte in stream:
        public.extend(turn.feed(bytes([byte])))
    assert turn.finish() == {"query":"Python version","scope":"general"}
    turn.supply({"results":[{"url":"https://python.org","title":"Python","snippet":"Evidence"}]})
    continuation = turn.body()
    assert continuation["messages"][-2]["reasoning_content"] == "PRIVATE-THINKING"
    assert continuation["thinking"] == {"type":"enabled"} and "tools" not in continuation
    public.extend(turn.feed(wire("Verified answer")))
    assert turn.finish() is None and "".join(public) == "Verified answer"
    other = Turn(config, [{"role":"user","content":"Independent turn"}])
    assert "PRIVATE-THINKING" not in json.dumps(other.body())


def test_private_reasoning_capture_is_bounded_and_never_public():
    import velia_quantum_provider as quantum
    decoder = quantum.StreamDecoder(capture_reasoning=True)
    events = decoder.feed(wire())
    decoder.finish()
    assert decoder.reasoning_content == "PRIVATE" and "PRIVATE" not in json.dumps(events)
    oversized = {"choices":[{"delta":{"reasoning_content":"x"*64001}}]}
    with pytest.raises(quantum.QuantumError, match="quantum_invalid_response"):
        quantum.StreamDecoder(capture_reasoning=True).feed(("data: "+json.dumps(oversized)+"\n\n").encode())
