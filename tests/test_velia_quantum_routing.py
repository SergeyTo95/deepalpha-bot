import os

from services import velia_model_router as router
from services import velia_quantum_service as quantum
from services.velia_chat_service import _chat_mode_from_provider


def test_quantum_is_fail_closed_without_worker(monkeypatch):
    monkeypatch.delenv("VELIA_QUANTUM_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_QUANTUM_BASE_URL", raising=False)
    monkeypatch.delenv("VELIA_QUANTUM_API_KEY", raising=False)

    assert quantum.available() is False
    capability = quantum.public_capability()
    assert capability["chat_quantum"] is False
    assert capability["chat_quantum_status"] == "development"


def test_quantum_endpoint_allows_private_http_and_https(monkeypatch):
    monkeypatch.setenv(
        "VELIA_QUANTUM_BASE_URL",
        "http://velia-quantum.railway.internal:8080",
    )
    assert quantum.endpoint() == "http://velia-quantum.railway.internal:8080"

    monkeypatch.setenv(
        "VELIA_QUANTUM_BASE_URL",
        "https://quantum.example.test",
    )
    assert quantum.endpoint() == "https://quantum.example.test"

    monkeypatch.setenv(
        "VELIA_QUANTUM_BASE_URL",
        "http://quantum.example.test",
    )
    assert quantum.endpoint() == ""


def test_quantum_available_requires_flag_url_and_key(monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "true")
    monkeypatch.setenv(
        "VELIA_QUANTUM_BASE_URL",
        "http://velia-quantum.railway.internal:8080",
    )
    monkeypatch.setenv("VELIA_QUANTUM_API_KEY", "test-secret")
    assert quantum.available() is True

    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "false")
    assert quantum.available() is False


def test_model_router_keeps_modes_isolated(monkeypatch):
    calls = []

    def sender(user_id, conversation_id, content, **kwargs):
        calls.append(("pro", user_id, conversation_id, content, kwargs))
        return {"ok": True, "mode": "pro"}

    def flash_dispatch(sender_arg, user_id, conversation_id, content, **kwargs):
        assert sender_arg is sender
        calls.append(("flash", user_id, conversation_id, content, kwargs))
        return {"ok": True, "mode": "flash"}

    def quantum_dispatch(sender_arg, user_id, conversation_id, content, **kwargs):
        assert sender_arg is sender
        calls.append(("quantum", user_id, conversation_id, content, kwargs))
        return {"ok": True, "mode": "quantum"}

    monkeypatch.setattr(router.flash, "dispatch_send", flash_dispatch)
    monkeypatch.setattr(router.quantum, "dispatch_send", quantum_dispatch)

    assert router.dispatch_send(
        sender, 1, "c", "hello", chat_mode="pro", idempotency_key="abcdefgh"
    )["mode"] == "pro"
    assert router.dispatch_send(
        sender, 1, "c", "hello", chat_mode="flash", idempotency_key="abcdefgh"
    )["mode"] == "flash"
    assert router.dispatch_send(
        sender, 1, "c", "hello", chat_mode="quantum", idempotency_key="abcdefgh"
    )["mode"] == "quantum"
    assert router.dispatch_send(
        sender, 1, "c", "hello", chat_mode="unknown", idempotency_key="abcdefgh"
    ) == {"ok": False, "error": "invalid_chat_mode"}

    assert [call[0] for call in calls] == ["pro", "flash", "quantum"]


def test_message_chat_mode_round_trip_supports_quantum():
    assert _chat_mode_from_provider("bonsai", "velia-flash") == "flash"
    assert _chat_mode_from_provider("quantum", "velia-quantum") == "quantum"
    assert _chat_mode_from_provider("kimi", "kimi-k3") == "pro"
    assert _chat_mode_from_provider("", "velia-quantum") == "quantum"


def test_quantum_capability_merges_with_flash(monkeypatch):
    monkeypatch.setattr(
        router.flash,
        "public_capability",
        lambda: {"chat_flash": True},
    )
    monkeypatch.setattr(
        router.quantum,
        "public_capability",
        lambda user_id=None: {
            "chat_quantum": bool(user_id == 42),
            "chat_quantum_status": "ready" if user_id == 42 else "development",
        },
    )
    capability = router.public_capability()
    assert capability["chat_flash"] is True
    assert capability["chat_quantum"] is False
    assert capability["chat_quantum_status"] == "development"


def test_quantum_has_no_paid_fallback(monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "false")
    result = quantum.generate(
        [{"role": "user", "content": "hello"}],
        request_id="req-1",
    )
    assert result["ok"] is False
    assert result["error"] == "quantum_unavailable"
    assert result["provider"] == "quantum"
    assert result["fallback_used"] is False


def test_quantum_preview_allowlist_is_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "true")
    monkeypatch.setenv(
        "VELIA_QUANTUM_BASE_URL",
        "http://velia-quantum.railway.internal:8080",
    )
    monkeypatch.setenv("VELIA_QUANTUM_API_KEY", "x" * 40)
    monkeypatch.delenv("VELIA_QUANTUM_PUBLIC_ENABLED", raising=False)
    monkeypatch.setenv("VELIA_QUANTUM_PREVIEW_USER_IDS", "42, 77,invalid")

    assert quantum.user_allowed(42) is True
    assert quantum.user_allowed(77) is True
    assert quantum.user_allowed(99) is False
    assert quantum.user_allowed(None) is False

    allowed = quantum.public_capability(42)
    denied = quantum.public_capability(99)
    assert allowed["chat_quantum"] is True
    assert allowed["chat_quantum_status"] == "ready"
    assert denied["chat_quantum"] is False
    assert denied["chat_quantum_status"] == "development"


def test_quantum_public_flag_opens_access_after_acceptance(monkeypatch):
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "true")
    monkeypatch.setenv(
        "VELIA_QUANTUM_BASE_URL",
        "http://velia-quantum.railway.internal:8080",
    )
    monkeypatch.setenv("VELIA_QUANTUM_API_KEY", "x" * 40)
    monkeypatch.setenv("VELIA_QUANTUM_PUBLIC_ENABLED", "true")
    monkeypatch.delenv("VELIA_QUANTUM_PREVIEW_USER_IDS", raising=False)

    assert quantum.user_allowed(123456) is True
    assert quantum.public_capability(123456)["chat_quantum"] is True
