import importlib
import sys
import types

requests_stub = types.SimpleNamespace(
    post=lambda *args, **kwargs: None,
    exceptions=types.SimpleNamespace(Timeout=TimeoutError),
)
sys.modules.setdefault("requests", requests_stub)

import services.llm_service as llm


class _FakeResponse:
    status_code = 200
    text = "ok"

    def json(self):
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "Hello"}, {"text": " world"}]},
                }
            ]
        }


def test_llm_service_routes_text_through_gateway(monkeypatch):
    calls = []
    monkeypatch.setattr("services.gemini_gateway.generate_content", lambda **kw: calls.append(kw) or {"text": "Hello\n world"})

    assert llm.generate_text("prompt", feature="signal_generation", request_id="req-1") == "Hello\n world"

    assert calls[0]["feature"] == "signal_generation"
    assert calls[0]["origin"] == "llm_service"
    assert calls[0]["request_id"] == "req-1"
    assert calls[0]["payload"]["contents"][0]["parts"][0]["text"] == "prompt"


def test_generate_live_analyst_text_uses_default_env_max_tokens(monkeypatch):
    calls = []
    monkeypatch.delenv("LIVE_ANALYST_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setattr(llm, "_call_gemini", lambda prompt, **kwargs: calls.append(kwargs) or "ok")

    assert llm.generate_live_analyst_text("prompt", user_id=1, budget_checked=True) == "ok"

    assert calls[0]["max_tokens"] == 2200
    assert calls[0]["feature"] == "live_analyst"
    assert calls[0]["user_id"] == 1
    assert calls[0]["budget_checked"] is True


def test_live_analyst_model_overrides_only_live_analyst(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "DEFAULT_GEMINI_MODEL", "gemini-default")
    monkeypatch.setattr(llm, "LIVE_ANALYST_GEMINI_MODEL", "gemini-live")
    monkeypatch.setattr(llm, "GEMINI_FALLBACK_MODELS", ["gemini-fallback"])
    monkeypatch.setattr(llm, "_call_gemini", lambda prompt, **kwargs: calls.append(kwargs) or "ok")

    assert llm.generate_live_analyst_text("prompt", budget_checked=True) == "ok"
    assert llm.generate_text("prompt", budget_checked=True) == "ok"
    assert llm.generate_decision_text("prompt", budget_checked=True) == "ok"
    assert llm.generate_news_text("prompt", budget_checked=True) == "ok"

    assert calls[0]["primary_model"] == "gemini-live"
    assert [call["primary_model"] for call in calls[1:]] == [
        "gemini-default",
        "gemini-default",
        "gemini-default",
    ]
    assert all(call["fallback_models"] == ["gemini-fallback"] for call in calls)


def test_gemini_model_env_controls_default_model(monkeypatch):
    calls = []
    monkeypatch.setenv("GEMINI_MODEL", "gemini-env-default")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "gemini-fallback")
    reloaded = importlib.reload(llm)
    monkeypatch.setattr(reloaded, "_call_gemini", lambda prompt, **kwargs: calls.append(kwargs) or "ok")

    assert reloaded.generate_text("prompt", budget_checked=True) == "ok"

    assert reloaded.DEFAULT_GEMINI_MODEL == "gemini-env-default"
    assert calls[0]["primary_model"] == "gemini-env-default"
    assert calls[0]["fallback_models"] == ["gemini-fallback"]


def test_fallback_list_parses_comma_separated_env():
    assert llm._parse_fallback_models(" gemini-a,gemini-b, , gemini-c ") == [
        "gemini-a",
        "gemini-b",
        "gemini-c",
    ]



def _gateway_sequence(monkeypatch, sequence):
    calls = []
    seq = list(sequence)
    def fake_gateway(**kwargs):
        calls.append(kwargs)
        return seq.pop(0)
    monkeypatch.setattr("services.gemini_gateway.generate_content", fake_gateway)
    return calls


def test_live_analyst_uses_gateway_fallback_models(monkeypatch):
    monkeypatch.setattr(llm, "LIVE_ANALYST_GEMINI_MODEL", "gemini-3.5-flash")
    monkeypatch.setattr(llm, "GEMINI_FALLBACK_MODELS", ["gemini-2.5-flash", "gemini-2.5-flash-lite"])
    calls = _gateway_sequence(monkeypatch, [{"text": "fallback ok"}])

    assert llm.generate_live_analyst_text("prompt", budget_checked=True) == "fallback ok"
    assert calls[0]["model"] == "gemini-3.5-flash"
    assert calls[0]["fallback_models"] == ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    assert calls[0]["max_attempts"] == 2


def test_live_analyst_gateway_receives_timeout_retry_disabled_by_default(monkeypatch):
    calls = _gateway_sequence(monkeypatch, [{"text": ""}])
    assert llm.generate_live_analyst_text("prompt", budget_checked=True) == ""
    assert calls[0]["feature"] == "live_analyst"
    assert calls[0]["is_background"] is False


def test_non_live_generate_text_uses_gateway_single_attempt_default(monkeypatch):
    monkeypatch.setattr(llm, "DEFAULT_GEMINI_MODEL", "gemini-default")
    monkeypatch.setattr(llm, "GEMINI_FALLBACK_MODELS", ["gemini-fallback"])
    calls = _gateway_sequence(monkeypatch, [{"text": "ok"}])

    assert llm.generate_text("prompt", budget_checked=True) == "ok"
    assert calls[0]["model"] == "gemini-default"
    assert calls[0]["max_attempts"] == 1
    assert calls[0]["fallback_models"] == ["gemini-fallback"]
