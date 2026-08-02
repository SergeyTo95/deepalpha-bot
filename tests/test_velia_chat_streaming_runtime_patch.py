from types import SimpleNamespace

from services import velia_chat_streaming_runtime_patch as runtime


def test_should_stream_only_substantive_text(monkeypatch):
    monkeypatch.setattr(runtime, "resolve_velia_provider", lambda: "kimi")

    assert runtime._should_stream_message("Проанализируй архитектуру VELIA") is True
    assert runtime._should_stream_message("Привет, как дела?") is False
    assert runtime._should_stream_message("Запомни: мой проект VELIA") is False
    assert runtime._should_stream_message("Создай изображение ночной Анталии") is False


def test_non_kimi_provider_keeps_existing_generation_path(monkeypatch):
    monkeypatch.setattr(runtime, "resolve_velia_provider", lambda: "gemini")
    assert runtime._should_stream_message("Проведи глубокий анализ") is False


def test_reasoning_effort_preserves_config_for_complex_and_can_reduce_casual(monkeypatch):
    monkeypatch.setattr(runtime.kimi_gateway, "kimi_reasoning_effort", lambda: "max")
    monkeypatch.setenv("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", "true")

    assert runtime._reasoning_effort_for_message("Проведи глубокий анализ") == "max"
    assert runtime._reasoning_effort_for_message("Да") == "low"

    monkeypatch.setenv("VELIA_CHAT_ADAPTIVE_REASONING_ENABLED", "false")
    assert runtime._reasoning_effort_for_message("Да") == "max"


def test_prompt_cache_key_honors_feature_flag(monkeypatch):
    monkeypatch.setattr(runtime, "_stable_prompt_cache_key", lambda value: "cache")
    monkeypatch.setenv("VELIA_CHAT_PROMPT_CACHE_KEY_ENABLED", "true")
    assert runtime._prompt_cache_key_for_conversation("conversation") == "cache"

    monkeypatch.setenv("VELIA_CHAT_PROMPT_CACHE_KEY_ENABLED", "false")
    assert runtime._prompt_cache_key_for_conversation("conversation") == ""


def test_run_streaming_send_scopes_callbacks_to_current_thread():
    events = []

    def fake_send(user_id, conversation_id, content, *, idempotency_key):
        runtime._STREAM_CONTEXT.on_delta("часть")
        runtime._STREAM_CONTEXT.on_reset()
        return {"ok": True, "content": content}

    result = runtime.run_streaming_send(
        fake_send,
        user_id=7,
        conversation_id="conversation",
        content="message",
        idempotency_key="request-123",
        on_delta=lambda text: events.append(("delta", text)),
        on_reset=lambda: events.append(("reset", "")),
    )

    assert result["ok"] is True
    assert events == [("delta", "часть"), ("reset", "")]
    assert not hasattr(runtime._STREAM_CONTEXT, "on_delta")
    assert not hasattr(runtime._STREAM_CONTEXT, "on_reset")


def test_installed_generator_streams_substantive_request(monkeypatch):
    monkeypatch.setattr(runtime, "_latest_request_user_message", lambda *args: "Проведи анализ")
    monkeypatch.setattr(runtime, "_should_stream_message", lambda message: True)
    monkeypatch.setattr(runtime, "_reasoning_effort_for_message", lambda message: "high")
    monkeypatch.setattr(runtime, "_prompt_cache_key_for_conversation", lambda value: "cache")
    monkeypatch.setattr(runtime, "resolve_velia_provider", lambda: "kimi")

    streamed = []

    def fake_stream(**kwargs):
        kwargs["on_delta"]("Готовый ")
        kwargs["on_delta"]("ответ")
        streamed.append(kwargs)
        return {
            "ok": True,
            "text": "Готовый ответ",
            "provider": "internal",
            "model": "model",
            "usage": {},
            "finish_reason": "stop",
            "estimated_cost_usd": 0.01,
            "first_delta_ms": 100,
        }

    monkeypatch.setattr(runtime, "call_kimi_stream", fake_stream)
    original_calls = []
    module = SimpleNamespace(
        generate_velia_chat_result=lambda *args, **kwargs: original_calls.append((args, kwargs))
        or {"ok": True, "text": "legacy"}
    )
    runtime.install(module)

    deltas = []
    result = runtime.run_streaming_send(
        lambda user_id, conversation_id, content, idempotency_key: module.generate_velia_chat_result(
            "prompt",
            user_id=user_id,
            conversation_id=conversation_id,
            request_id="request-1",
        ),
        user_id=7,
        conversation_id="conversation",
        content="Проведи анализ",
        idempotency_key="request-123",
        on_delta=deltas.append,
        on_reset=lambda: None,
    )

    assert result["text"] == "Готовый ответ"
    assert deltas == ["Готовый ", "ответ"]
    assert original_calls == []
    assert streamed[0]["prompt_cache_key"] == "cache"
    assert streamed[0]["reasoning_effort"] == "high"


def test_installed_generator_preserves_original_for_special_request(monkeypatch):
    monkeypatch.setattr(runtime, "_latest_request_user_message", lambda *args: "Привет")
    monkeypatch.setattr(runtime, "_should_stream_message", lambda message: False)
    original_calls = []

    def original(prompt, *, user_id, conversation_id, request_id=None):
        original_calls.append(prompt)
        return {"ok": True, "text": "Привет!"}

    module = SimpleNamespace(generate_velia_chat_result=original)
    runtime.install(module)

    result = runtime.run_streaming_send(
        lambda user_id, conversation_id, content, idempotency_key: module.generate_velia_chat_result(
            "prompt",
            user_id=user_id,
            conversation_id=conversation_id,
            request_id="request-2",
        ),
        user_id=7,
        conversation_id="conversation",
        content="Привет",
        idempotency_key="request-123",
        on_delta=lambda text: None,
        on_reset=lambda: None,
    )

    assert result["text"] == "Привет!"
    assert original_calls == ["prompt"]


def test_stream_failure_calls_fallback_directly_without_replaying_primary(monkeypatch):
    monkeypatch.setattr(runtime, "_latest_request_user_message", lambda *args: "Проведи анализ")
    monkeypatch.setattr(runtime, "_should_stream_message", lambda message: True)
    monkeypatch.setattr(runtime, "_reasoning_effort_for_message", lambda message: "high")
    monkeypatch.setattr(runtime, "_prompt_cache_key_for_conversation", lambda value: "cache")
    monkeypatch.setattr(runtime, "resolve_velia_provider", lambda: "kimi")
    monkeypatch.setattr(
        runtime,
        "call_kimi_stream",
        lambda **kwargs: {
            "ok": False,
            "text": "",
            "reason": "connection_error",
            "fallback_allowed": True,
        },
    )
    monkeypatch.setattr(
        runtime.llm_service,
        "resolve_fallback_provider",
        lambda primary: "gemini",
    )
    fallback_calls = []

    def fake_fallback(provider, prompt, **kwargs):
        fallback_calls.append((provider, prompt, kwargs))
        return {"ok": True, "text": "Fallback answer"}

    monkeypatch.setattr(runtime, "_call_provider", fake_fallback)
    original_calls = []
    module = SimpleNamespace(
        generate_velia_chat_result=lambda *args, **kwargs: original_calls.append((args, kwargs))
        or {"ok": True, "text": "Primary replay"}
    )
    runtime.install(module)

    result = runtime.run_streaming_send(
        lambda user_id, conversation_id, content, idempotency_key: module.generate_velia_chat_result(
            "prompt",
            user_id=user_id,
            conversation_id=conversation_id,
            request_id="request-3",
        ),
        user_id=7,
        conversation_id="conversation",
        content="Проведи анализ",
        idempotency_key="request-123",
        on_delta=lambda text: None,
        on_reset=lambda: None,
    )

    assert result["text"] == "Fallback answer"
    assert result["stream_fallback_used"] is True
    assert result["stream_failure_reason"] == "connection_error"
    assert result["fallback_used"] is True
    assert original_calls == []
    assert len(fallback_calls) == 1
    assert fallback_calls[0][0] == "gemini"


def test_direct_fallback_returns_primary_failure_when_not_configured(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_service,
        "resolve_fallback_provider",
        lambda primary: "",
    )
    primary = {
        "ok": False,
        "reason": "connection_error",
        "fallback_allowed": True,
    }

    result = runtime._direct_fallback_result(
        "kimi",
        primary,
        "prompt",
        user_id=7,
        conversation_id="conversation",
        request_id="request-4",
    )

    assert result is primary
