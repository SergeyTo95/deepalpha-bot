import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


def _install_fake_db(monkeypatch, reserve_error=None):
    reservations = []
    finalizations = []
    blocks = []

    def reserve_gemini_attempt(**kwargs):
        if reserve_error:
            raise RuntimeError(reserve_error)
        reservations.append(kwargs)
        return len(reservations)

    fake = types.ModuleType("db.database")
    fake.reserve_gemini_attempt = reserve_gemini_attempt
    fake.finalize_gemini_attempt = lambda attempt_id, **kwargs: finalizations.append((attempt_id, kwargs))
    fake.record_gemini_blocked_request = lambda **kwargs: blocks.append(kwargs)
    monkeypatch.setitem(sys.modules, "db.database", fake)
    return reservations, finalizations, blocks


def _enable_kimi(monkeypatch):
    monkeypatch.setenv("KIMI_ENABLED", "true")
    monkeypatch.setenv("KIMI_API_KEY", "secret-kimi-key")
    monkeypatch.setenv("KIMI_MODEL", "kimi-k3")
    monkeypatch.setenv("KIMI_MAX_RETRIES", "0")
    monkeypatch.delenv("KIMI_MAX_COMPLETION_TOKENS", raising=False)
    monkeypatch.delenv("KIMI_MAX_COMPLETION_TOKENS_CAP", raising=False)


def _success_payload(content="Final answer", finish_reason="stop", completion_tokens=20):
    return {
        "choices": [{
            "finish_reason": finish_reason,
            "message": {
                "reasoning_content": "private chain of thought",
                "content": content,
            },
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": completion_tokens,
            "total_tokens": 100 + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 40},
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    }


def test_default_text_provider_remains_gemini(monkeypatch):
    from services import llm_service as llm

    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_POLYMARKET", raising=False)
    assert llm.resolve_text_provider("signal_generation") == "gemini"


def test_kimi_uses_max_completion_tokens_and_hides_reasoning(monkeypatch):
    from services import kimi_gateway as kimi

    _enable_kimi(monkeypatch)
    monkeypatch.setenv("KIMI_REASONING_EFFORT", "high")
    monkeypatch.setenv("KIMI_MAX_COMPLETION_TOKENS", "4096")
    reservations, finalizations, _ = _install_fake_db(monkeypatch)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(200, _success_payload(), {"x-request-id": "kimi-req-1"})

    monkeypatch.setattr(kimi.requests, "post", fake_post)
    result = kimi.call_kimi(prompt="Analyze this market", feature="signal_generation", max_tokens=300)

    assert result["ok"] is True
    assert result["text"] == "Final answer"
    assert "private chain of thought" not in result["text"]
    request_json = calls[0][1]["json"]
    assert request_json["model"] == "kimi-k3"
    assert request_json["reasoning_effort"] == "high"
    assert request_json["max_completion_tokens"] == 4096
    assert "max_tokens" not in request_json
    assert "temperature" not in request_json
    assert "top_p" not in request_json
    assert reservations[0]["model"] == "kimi:kimi-k3"
    assert finalizations[0][1]["prompt_tokens"] == 100
    assert finalizations[0][1]["completion_tokens"] == 20
    assert result["usage"]["cached_input_tokens"] == 40
    assert result["usage"]["reasoning_tokens"] == 12
    assert result["finish_reason"] == "stop"


def test_old_1200_output_limit_cannot_starve_reasoning_request(monkeypatch):
    from services import kimi_gateway as kimi

    _enable_kimi(monkeypatch)
    monkeypatch.setenv("KIMI_MAX_OUTPUT_TOKENS", "1200")
    _install_fake_db(monkeypatch)
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        return _FakeResponse(200, _success_payload())

    monkeypatch.setattr(kimi.requests, "post", fake_post)
    result = kimi.call_kimi(prompt="Analyze", feature="signal_generation", max_tokens=1024)

    assert result["ok"] is True
    assert calls[0]["max_completion_tokens"] == 8192


def test_finish_reason_length_is_failed_and_fallback_eligible(monkeypatch):
    from services import kimi_gateway as kimi

    _enable_kimi(monkeypatch)
    monkeypatch.setenv("KIMI_MAX_COMPLETION_TOKENS", "4096")
    _, finalizations, _ = _install_fake_db(monkeypatch)
    monkeypatch.setattr(
        kimi.requests,
        "post",
        lambda *a, **k: _FakeResponse(200, _success_payload(content="Partial", finish_reason="length", completion_tokens=4096)),
    )

    result = kimi.call_kimi(prompt="x", feature="signal_generation")

    assert result["ok"] is False
    assert result["reason"] == "completion_length"
    assert result["finish_reason"] == "length"
    assert result["fallback_allowed"] is True
    assert result["text"] == ""
    assert finalizations[0][1]["status"] == "failed"
    assert finalizations[0][1]["reason"] == "completion_length"


def test_truncation_retry_doubles_limit_then_succeeds(monkeypatch):
    from services import kimi_gateway as kimi

    _enable_kimi(monkeypatch)
    monkeypatch.setenv("KIMI_MAX_RETRIES", "1")
    monkeypatch.setenv("KIMI_MAX_COMPLETION_TOKENS", "4096")
    monkeypatch.setenv("KIMI_MAX_COMPLETION_TOKENS_CAP", "16384")
    reservations, finalizations, _ = _install_fake_db(monkeypatch)
    calls = []
    responses = [
        _FakeResponse(200, _success_payload(content="Partial", finish_reason="length", completion_tokens=4096)),
        _FakeResponse(200, _success_payload(content="Complete", finish_reason="stop", completion_tokens=5000)),
    ]

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["max_completion_tokens"])
        return responses.pop(0)

    monkeypatch.setattr(kimi.requests, "post", fake_post)
    monkeypatch.setattr(kimi.time, "sleep", lambda *a, **k: None)
    result = kimi.call_kimi(prompt="x", feature="signal_generation")

    assert result["ok"] is True
    assert result["text"] == "Complete"
    assert result["attempts_used"] == 2
    assert calls == [4096, 8192]
    assert len(reservations) == 2
    assert finalizations[0][1]["reason"] == "completion_length"
    assert finalizations[1][1]["status"] == "success"


def test_kimi_auth_error_is_not_retried_or_fallback_eligible(monkeypatch):
    from services import kimi_gateway as kimi

    _enable_kimi(monkeypatch)
    monkeypatch.setenv("KIMI_MAX_RETRIES", "2")
    reservations, _, _ = _install_fake_db(monkeypatch)
    calls = []
    monkeypatch.setattr(kimi.requests, "post", lambda *a, **k: calls.append((a, k)) or _FakeResponse(401, {}))

    result = kimi.call_kimi(prompt="x", feature="signal_generation")

    assert result["reason"] == "auth_error"
    assert result["fallback_allowed"] is False
    assert len(calls) == 1
    assert len(reservations) == 1


def test_budget_block_prevents_http_call(monkeypatch):
    from services import kimi_gateway as kimi

    _enable_kimi(monkeypatch)
    _, _, blocks = _install_fake_db(monkeypatch, reserve_error="daily_limit_exceeded")
    calls = []
    monkeypatch.setattr(kimi.requests, "post", lambda *a, **k: calls.append((a, k)))

    result = kimi.call_kimi(prompt="x", feature="signal_generation")

    assert result["blocked"] is True
    assert result["reason"] == "daily_limit_exceeded"
    assert result["fallback_allowed"] is False
    assert calls == []
    assert blocks[0]["model"] == "kimi:kimi-k3"


def test_retryable_kimi_failure_falls_back_to_gemini_once(monkeypatch):
    from services import llm_service as llm

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "gemini")
    calls = []

    def fake_provider(provider, *args, **kwargs):
        calls.append(provider)
        if provider == "kimi":
            return {"text": "", "reason": "completion_length", "fallback_allowed": True}
        return {"text": "Gemini fallback"}

    monkeypatch.setattr(llm, "_provider_result", fake_provider)
    assert llm.generate_text("prompt") == "Gemini fallback"
    assert calls == ["kimi", "gemini"]


def test_blocked_kimi_does_not_fallback(monkeypatch):
    from services import llm_service as llm

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "gemini")
    calls = []

    def fake_provider(provider, *args, **kwargs):
        calls.append(provider)
        return {"text": "", "blocked": True, "reason": "api_key_missing", "fallback_allowed": False}

    monkeypatch.setattr(llm, "_provider_result", fake_provider)
    assert llm.generate_text("prompt") == ""
    assert calls == ["kimi"]


def test_identical_primary_and_fallback_are_not_called_twice(monkeypatch):
    from services import llm_service as llm

    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "kimi")
    calls = []
    monkeypatch.setattr(
        llm,
        "_provider_result",
        lambda provider, *args, **kwargs: calls.append(provider) or {"text": "", "reason": "timeout", "fallback_allowed": True},
    )
    assert llm.generate_text("prompt") == ""
    assert calls == ["kimi"]


def test_api_key_and_prompt_are_not_logged(monkeypatch, caplog):
    from services import kimi_gateway as kimi

    _enable_kimi(monkeypatch)
    _install_fake_db(monkeypatch)
    monkeypatch.setattr(kimi.requests, "post", lambda *a, **k: _FakeResponse(503, {}))

    with caplog.at_level(logging.INFO):
        kimi.call_kimi(prompt="private user market text", feature="signal_generation")

    assert "secret-kimi-key" not in caplog.text
    assert "private user market text" not in caplog.text
    assert "KIMI_REQUEST_START" in caplog.text
    assert "KIMI_REQUEST_FAILED" in caplog.text


def test_moonshot_endpoint_is_isolated_to_kimi_gateway():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in (root / "services").glob("*.py"):
        if path.name == "kimi_gateway.py":
            continue
        if "api.moonshot.ai" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []
