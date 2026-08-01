from services import velia_llm_service


def test_velia_provider_prefers_dedicated_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_VELIA_CHAT", "kimi")
    monkeypatch.setenv("LLM_TEXT_PROVIDER", "gemini")
    assert velia_llm_service.resolve_velia_provider() == "kimi"


def test_velia_provider_fails_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_VELIA_CHAT", raising=False)
    monkeypatch.delenv("LLM_TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)
    assert velia_llm_service.resolve_velia_provider() == ""


def test_structured_generation_returns_usage_without_exposing_reasoning(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_VELIA_CHAT", "kimi")

    calls = []

    def fake_provider(provider, prompt, **kwargs):
        calls.append((provider, prompt, kwargs))
        return {
            "ok": True,
            "text": "Готовый ответ",
            "provider": "kimi",
            "model": "kimi-k3",
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "total_tokens": 160,
                "cached_input_tokens": 20,
                "reasoning_tokens": 15,
            },
            "estimated_cost_usd": 0.0012,
        }

    monkeypatch.setattr(velia_llm_service.llm_service, "_provider_result", fake_provider)

    result = velia_llm_service.generate_velia_chat_result(
        "USER: Привет",
        user_id=1,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["ok"] is True
    assert result["text"] == "Готовый ответ"
    assert calls[0][0] == "kimi"
    assert calls[0][2]["feature"] == "velia_chat"
    assert calls[0][2]["origin"] == "velia_mobile_chat"

    public = velia_llm_service.public_generation_metadata(result, debug_usage=True)
    assert public["request_id"] == "request-1"
    assert public["usage"]["total_tokens"] == 160
    assert "provider" not in public
    assert "model" not in public
    assert "reasoning" not in public


def test_provider_failure_uses_existing_fallback_policy(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_VELIA_CHAT", "kimi")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "gemini")
    providers = []

    def fake_provider(provider, prompt, **kwargs):
        providers.append(provider)
        if provider == "kimi":
            return {
                "ok": False,
                "text": "",
                "reason": "timeout",
                "fallback_allowed": True,
            }
        return {
            "ok": True,
            "text": "Fallback answer",
            "provider": "gemini",
            "model": "fallback",
            "usage": {},
        }

    monkeypatch.setattr(velia_llm_service.llm_service, "_provider_result", fake_provider)

    result = velia_llm_service.generate_velia_chat_result(
        "prompt",
        user_id=1,
        conversation_id="conversation-1",
    )

    assert providers == ["kimi", "gemini"]
    assert result["ok"] is True
    assert result["fallback_used"] is True
    assert result["text"] == "Fallback answer"
