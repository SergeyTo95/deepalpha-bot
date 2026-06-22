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
