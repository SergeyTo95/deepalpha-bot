from types import SimpleNamespace

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
    assert latency_patch._is_casual_message("Привет, как дела?") is True
    assert latency_patch._is_casual_message("Как дела, привет!") is True
    assert latency_patch._is_casual_message("ку ку 🙂") is True
    assert latency_patch._is_casual_message("Спасибо") is True
    assert latency_patch._is_casual_message("Hello, how are you?") is True
    assert latency_patch._is_casual_message("Merhaba, nasılsın?") is True

    assert latency_patch._is_casual_message(
        "Проанализируй архитектуру приложения и предложи безопасный план миграции"
    ) is False
    assert latency_patch._is_casual_message("Что такое квантовая механика?") is False
    assert latency_patch._is_casual_message("Напиши код авторизации") is False


def test_casual_intent_recognizes_combined_greeting():
    assert latency_patch._casual_intent("Привет, как дела?") == (
        "ru",
        "greeting_how",
    )
    assert latency_patch._casual_intent("Hello! How are you?") == (
        "en",
        "greeting_how",
    )
    assert latency_patch._casual_intent("Merhaba, nasılsın?") == (
        "tr",
        "greeting_how",
    )


def test_instant_response_is_short_personalized_and_context_safe(monkeypatch):
    monkeypatch.setattr(latency_patch, "_preferred_name", lambda user_id: "Сергей")

    response = latency_patch._instant_response_for_message(
        "Привет, как дела?",
        7,
    )

    assert response == (
        "Привет, Сергей! Всё отлично, я на связи 🙂 Чем займёмся?",
        "ru",
        "greeting_how",
    )
    assert latency_patch._instant_response_for_message("Да", 7) is None
    assert latency_patch._instant_response_for_message("Что ты умеешь?", 7) is None
    assert latency_patch._instant_response_for_message(
        "Проанализируй бизнес-модель",
        7,
    ) is None


def test_instant_response_supports_english_and_turkish(monkeypatch):
    monkeypatch.setattr(latency_patch, "_preferred_name", lambda user_id: "")

    assert latency_patch._instant_response_for_message("Thank you!", 1) == (
        "You’re welcome 🙂",
        "en",
        "thanks",
    )
    assert latency_patch._instant_response_for_message("Teşekkür ederim", 1) == (
        "Rica ederim 🙂",
        "tr",
        "thanks",
    )


def test_selected_reasoning_keeps_high_for_complex_and_uses_low_for_casual(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", "true")
    monkeypatch.setattr(
        latency_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Привет, как дела?",
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


def test_selected_reasoning_reuses_request_message_from_thread_context(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", "true")
    monkeypatch.setattr(
        latency_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: (_ for _ in ()).throw(
            AssertionError("database lookup should not repeat")
        ),
    )
    latency_patch._CONTEXT.user_message = "Привет, как дела?"
    try:
        effort = latency_patch._selected_reasoning_effort(
            feature="velia_chat",
            request_id="request-1",
            user_id=1,
            default_effort="high",
        )
    finally:
        del latency_patch._CONTEXT.user_message

    assert effort == "low"


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


def test_installed_generator_skips_core_for_instant_message(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_INSTANT_CASUAL_ENABLED", "true")
    monkeypatch.setattr(latency_patch, "_install_kimi_transport_patch", lambda: None)
    monkeypatch.setattr(
        latency_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Привет, как дела?",
    )
    monkeypatch.setattr(latency_patch, "_preferred_name", lambda user_id: "Сергей")

    core_calls = []

    def original_generate(*args, **kwargs):
        core_calls.append((args, kwargs))
        return {"ok": True, "text": "slow"}

    chat_module = SimpleNamespace(
        _build_prompt=lambda user_id, conversation_id: "prompt",
        generate_velia_chat_result=original_generate,
        send_message=lambda *args, **kwargs: {"ok": True},
    )
    routes_module = SimpleNamespace(send_message=chat_module.send_message)

    latency_patch.install(chat_module, routes_module)
    result = chat_module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert core_calls == []
    assert result["instant_response"] is True
    assert result["estimated_cost_usd"] == 0.0
    assert result["text"].startswith("Привет, Сергей!")


def test_installed_generator_keeps_core_for_substantive_message(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_INSTANT_CASUAL_ENABLED", "true")
    monkeypatch.setattr(latency_patch, "_install_kimi_transport_patch", lambda: None)
    monkeypatch.setattr(
        latency_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Проведи глубокий анализ архитектуры",
    )

    core_calls = []

    def original_generate(*args, **kwargs):
        core_calls.append((args, kwargs))
        return {"ok": True, "text": "deep answer"}

    chat_module = SimpleNamespace(
        _build_prompt=lambda user_id, conversation_id: "prompt",
        generate_velia_chat_result=original_generate,
        send_message=lambda *args, **kwargs: {"ok": True},
    )

    latency_patch.install(chat_module)
    result = chat_module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert len(core_calls) == 1
    assert result["text"] == "deep answer"
    assert result.get("instant_response") is None
