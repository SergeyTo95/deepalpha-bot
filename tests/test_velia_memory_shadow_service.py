from types import SimpleNamespace

import pytest

from services import velia_memory_shadow_service as memory


def _event():
    return {
        "event_id": "vmem_test",
        "user_id": 5811340792,
        "conversation_id": "conversation-1",
        "user_message_id": "user-message-1",
        "assistant_message_id": "assistant-message-1",
        "user_content": "Remember that my preferred language is Russian.",
        "assistant_content": "Поняла, буду отвечать по-русски.",
        "attempt_count": 1,
    }


def test_shadow_capture_is_fail_closed_without_explicit_user_allowlist(monkeypatch):
    monkeypatch.setenv("VELIA_MEMORY_SHADOW_ENABLED", "true")
    monkeypatch.delenv("VELIA_MEMORY_SHADOW_USER_IDS", raising=False)
    monkeypatch.delenv("VELIA_MEMORY_SHADOW_ALLOW_ALL", raising=False)

    assert memory.shadow_capture_enabled_for_user(5811340792) is False


def test_shadow_capture_accepts_only_allowlisted_users(monkeypatch):
    monkeypatch.setenv("VELIA_MEMORY_SHADOW_ENABLED", "true")
    monkeypatch.setenv(
        "VELIA_MEMORY_SHADOW_USER_IDS",
        "5811340792, invalid, 12345; 67890",
    )

    assert memory.shadow_capture_enabled_for_user(5811340792) is True
    assert memory.shadow_capture_enabled_for_user(12345) is True
    assert memory.shadow_capture_enabled_for_user(99999) is False


def test_allow_all_requires_a_separate_explicit_switch(monkeypatch):
    monkeypatch.setenv("VELIA_MEMORY_SHADOW_ENABLED", "true")
    monkeypatch.setenv("VELIA_MEMORY_SHADOW_ALLOW_ALL", "true")
    monkeypatch.delenv("VELIA_MEMORY_SHADOW_USER_IDS", raising=False)

    assert memory.shadow_capture_enabled_for_user(99999) is True


def test_build_shadow_payload_uses_strict_user_session_agent_isolation(monkeypatch):
    monkeypatch.setenv("VELIA_MEMORY_TEAM_ID", "velia-team")
    monkeypatch.setenv("VELIA_MEMORY_AGENT_ID", "velia-main")

    payload = memory.build_shadow_payload(_event())

    assert payload == {
        "team_id": "velia-team",
        "agent_id": "velia-main",
        "user_id": "5811340792",
        "session_id": "conversation-1",
        "messages": [
            {
                "role": "user",
                "content": "Remember that my preferred language is Russian.",
            },
            {
                "role": "assistant",
                "content": "Поняла, буду отвечать по-русски.",
            },
        ],
    }
    serialized = str(payload).lower()
    assert "provider" not in serialized
    assert "model" not in serialized


def test_send_shadow_event_uses_private_configuration_and_accepts_code_zero(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        headers = {"x-trace-id": "trace-1"}

        @staticmethod
        def json():
            return {"code": 0, "data": {"accepted_ids": ["1", "2"]}}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setenv("VELIA_MEMORY_ENDPOINT", "http://velyon-memory.railway.internal:8420/")
    monkeypatch.setenv("VELIA_MEMORY_API_KEY", "memory-secret-key-123456")
    monkeypatch.setenv("VELIA_MEMORY_SERVICE_ID", "velia-production")
    monkeypatch.setattr(memory.requests, "post", fake_post)

    result = memory.send_shadow_event(_event())

    assert result["success"] is True
    assert result["response_status"] == 200
    assert result["remote_trace_id"] == "trace-1"
    assert "memory-secret-key" not in str(result)
    assert calls[0][0] == (
        "http://velyon-memory.railway.internal:8420/v3/conversation/add"
    )
    assert calls[0][1]["headers"]["Authorization"] == (
        "Bearer memory-secret-key-123456"
    )
    assert calls[0][1]["headers"]["x-tdai-service-id"] == "velia-production"
    assert calls[0][1]["json"]["user_id"] == "5811340792"
    assert calls[0][1]["timeout"] == (2.0, 8.0)


def test_send_shadow_event_treats_remote_application_error_as_terminal(monkeypatch):
    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"code": 42201, "message": "invalid isolation"}

    monkeypatch.setenv("VELIA_MEMORY_ENDPOINT", "http://memory.internal:8420")
    monkeypatch.setenv("VELIA_MEMORY_API_KEY", "memory-secret-key-123456")
    monkeypatch.setattr(memory.requests, "post", lambda *args, **kwargs: Response())

    result = memory.send_shadow_event(_event())

    assert result["success"] is False
    assert result["retryable"] is False
    assert result["error"] == "memory_remote_code_42201"


def test_send_shadow_event_retries_transport_failures_without_exposing_details(monkeypatch):
    monkeypatch.setenv("VELIA_MEMORY_ENDPOINT", "http://memory.internal:8420")
    monkeypatch.setenv("VELIA_MEMORY_API_KEY", "memory-secret-key-123456")

    def fail(*args, **kwargs):
        raise memory.requests.Timeout("sensitive internal URL and token")

    monkeypatch.setattr(memory.requests, "post", fail)

    result = memory.send_shadow_event(_event())

    assert result["success"] is False
    assert result["retryable"] is True
    assert result["error"] == "memory_transport_Timeout"
    assert "sensitive" not in str(result)
    assert "token" not in str(result)


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "ftp://memory.internal",
        "http://user:pass@memory.internal",
        "http://memory.internal?token=secret",
        "http://memory.internal#fragment",
    ],
)
def test_memory_endpoint_rejects_unsafe_or_ambiguous_values(monkeypatch, endpoint):
    monkeypatch.setenv("VELIA_MEMORY_ENDPOINT", endpoint)

    with pytest.raises(ValueError):
        memory._memory_endpoint()


def test_retry_delay_is_bounded():
    assert memory.retry_delay_seconds(0) == 0
    assert memory.retry_delay_seconds(1) == 5
    assert memory.retry_delay_seconds(1000) == 43200
