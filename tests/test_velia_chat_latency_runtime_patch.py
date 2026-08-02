from services import velia_chat_latency_runtime_patch as latency_patch


def test_prompt_cache_key_is_stable_and_conversation_scoped():
    first = latency_patch._stable_prompt_cache_key("conversation-1")
    repeated = latency_patch._stable_prompt_cache_key("conversation-1")
    other = latency_patch._stable_prompt_cache_key("conversation-2")

    assert first == repeated
    assert first != other
    assert len(first) == 64


def test_adaptive_reasoning_is_conservative_for_casual_messages():
    assert latency_patch._is_casual_message("Привет!") is True
    assert latency_patch._is_casual_message("ку ку 🙂") is True
    assert latency_patch._is_casual_message("Спасибо") is True
    assert latency_patch._is_casual_message("How are you?") is True
    assert latency_patch._is_casual_message("Merhaba") is True

    assert latency_patch._is_casual_message(
        "Проанализируй архитектуру приложения и предложи безопасный план миграции"
    ) is False
    assert latency_patch._is_casual_message("Что такое квантовая механика?") is False
    assert latency_patch._is_casual_message("Напиши код авторизации") is False


def test_selected_reasoning_keeps_high_for_complex_and_uses_low_for_casual(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", "true")
    monkeypatch.setattr(
        latency_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Привет",
    )
    assert latency_patch._selected_reasoning_effort(
        feature="velia_chat",
        request_id="request-1",
        user_id=1,
        default_effort="high",
    ) == "low"

    monkeypatch.setattr(
        latency_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Проведи глубокий анализ бизнес-модели",
    )
    assert latency_patch._selected_reasoning_effort(
        feature="velia_chat",
        request_id="request-2",
        user_id=1,
        default_effort="high",
    ) == "high"

    assert latency_patch._selected_reasoning_effort(
        feature="decision_agent",
        request_id="request-3",
        user_id=1,
        default_effort="max",
    ) == "max"


def test_adaptive_reasoning_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", "false")
    monkeypatch.setattr(
        latency_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Привет",
    )

    assert latency_patch._selected_reasoning_effort(
        feature="velia_chat",
        request_id="request-1",
        user_id=1,
        default_effort="high",
    ) == "high"


def test_payload_adds_cache_key_and_selected_reasoning_without_mutating_source():
    source = {
        "model": "internal-model",
        "messages": [{"role": "user", "content": "hello"}],
        "reasoning_effort": "high",
    }
    latency_patch._CONTEXT.prompt_cache_key = "cache-key"
    latency_patch._CONTEXT.reasoning_effort = "low"
    try:
        prepared = latency_patch._prepare_payload(source)
    finally:
        del latency_patch._CONTEXT.prompt_cache_key
        del latency_patch._CONTEXT.reasoning_effort

    assert source["reasoning_effort"] == "high"
    assert "prompt_cache_key" not in source
    assert prepared["prompt_cache_key"] == "cache-key"
    assert prepared["reasoning_effort"] == "low"


def test_thread_local_transport_reuses_session_and_prepares_payload(monkeypatch):
    created_sessions = []

    class FakeResponse:
        status_code = 200

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.mounts = []

        def mount(self, prefix, adapter):
            self.mounts.append(prefix)

        def post(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return FakeResponse()

    class FakeRequests:
        def Session(self):
            session = FakeSession()
            created_sessions.append(session)
            return session

        def post(self, *args, **kwargs):
            raise AssertionError("fallback transport should not be used")

    transport = latency_patch._ThreadLocalPooledRequests(FakeRequests())
    latency_patch._CONTEXT.prompt_cache_key = "cache-key"
    latency_patch._CONTEXT.reasoning_effort = "high"
    try:
        transport.post("https://example.test", json={"model": "x"}, timeout=1)
        transport.post("https://example.test", json={"model": "x"}, timeout=1)
    finally:
        del latency_patch._CONTEXT.prompt_cache_key
        del latency_patch._CONTEXT.reasoning_effort

    assert len(created_sessions) == 1
    assert len(created_sessions[0].calls) == 2
    assert created_sessions[0].calls[0][1]["json"]["prompt_cache_key"] == "cache-key"
    assert created_sessions[0].calls[0][1]["json"]["reasoning_effort"] == "high"
