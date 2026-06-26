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


def test_call_model_once_joins_multiple_gemini_text_parts(monkeypatch):
    monkeypatch.setattr(llm, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(llm.requests, "post", lambda *args, **kwargs: _FakeResponse())

    text, status = llm._call_model_once("prompt", "gemini-test", 123)

    assert status == 200
    assert text == "Hello\n world"


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


def test_404_on_live_analyst_primary_falls_back_to_flash(monkeypatch):
    models = []

    def fake_call_model_once(prompt, model, max_tokens):
        models.append(model)
        if model == "gemini-3.5-flash":
            return "", 404
        return "fallback ok", 200

    monkeypatch.setattr(llm, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(llm, "LIVE_ANALYST_GEMINI_MODEL", "gemini-3.5-flash")
    monkeypatch.setattr(llm, "GEMINI_FALLBACK_MODELS", ["gemini-2.5-flash", "gemini-2.5-flash-lite"])
    monkeypatch.setattr(llm, "_call_model_once", fake_call_model_once)
    monkeypatch.setattr(llm, "record_gemini_call", lambda **kwargs: None)

    assert llm.generate_live_analyst_text("prompt", budget_checked=True) == "fallback ok"
    assert models == ["gemini-3.5-flash", "gemini-2.5-flash"]
