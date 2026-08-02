from types import SimpleNamespace

from services import velia_memory_shadow_runtime_patch as runtime_patch


def _success_result(*, duplicate=False):
    return {
        "ok": True,
        "duplicate": duplicate,
        "assistant_message": {
            "id": "assistant-1",
            "reply_to_message_id": "user-1",
            "content": "Готовый ответ Велии.",
            "status": "completed",
        },
    }


def test_runtime_patch_captures_only_new_completed_turns(monkeypatch):
    captured = []

    def original_send(user_id, conversation_id, content, *, idempotency_key):
        return _success_result()

    chat_module = SimpleNamespace(send_message=original_send)
    routes_module = SimpleNamespace(send_message=original_send)
    monkeypatch.setattr(
        runtime_patch,
        "enqueue_completed_turn",
        lambda **kwargs: captured.append(kwargs) or {"queued": True},
    )

    runtime_patch.install(chat_module, routes_module)
    result = routes_module.send_message(
        5811340792,
        "conversation-1",
        "Мой вопрос",
        idempotency_key="request-1",
    )

    assert result["ok"] is True
    assert captured == [
        {
            "user_id": 5811340792,
            "conversation_id": "conversation-1",
            "user_message_id": "user-1",
            "assistant_message_id": "assistant-1",
            "user_content": "Мой вопрос",
            "assistant_content": "Готовый ответ Велии.",
        }
    ]
    assert chat_module.send_message is routes_module.send_message


def test_runtime_patch_skips_duplicate_idempotent_result(monkeypatch):
    captured = []

    def original_send(user_id, conversation_id, content, *, idempotency_key):
        return _success_result(duplicate=True)

    chat_module = SimpleNamespace(send_message=original_send)
    routes_module = SimpleNamespace(send_message=original_send)
    monkeypatch.setattr(
        runtime_patch,
        "enqueue_completed_turn",
        lambda **kwargs: captured.append(kwargs),
    )

    runtime_patch.install(chat_module, routes_module)
    result = routes_module.send_message(
        5811340792,
        "conversation-1",
        "Мой вопрос",
        idempotency_key="request-1",
    )

    assert result["duplicate"] is True
    assert captured == []


def test_shadow_enqueue_failure_never_changes_successful_chat_result(monkeypatch):
    expected = _success_result()

    def original_send(user_id, conversation_id, content, *, idempotency_key):
        return expected

    def fail_enqueue(**kwargs):
        raise RuntimeError("database unavailable")

    chat_module = SimpleNamespace(send_message=original_send)
    routes_module = SimpleNamespace(send_message=original_send)
    monkeypatch.setattr(runtime_patch, "enqueue_completed_turn", fail_enqueue)

    runtime_patch.install(chat_module, routes_module)
    actual = routes_module.send_message(
        5811340792,
        "conversation-1",
        "Мой вопрос",
        idempotency_key="request-1",
    )

    assert actual is expected
    assert actual["ok"] is True


def test_runtime_patch_is_idempotent(monkeypatch):
    calls = []

    def original_send(user_id, conversation_id, content, *, idempotency_key):
        calls.append("send")
        return _success_result()

    chat_module = SimpleNamespace(send_message=original_send)
    routes_module = SimpleNamespace(send_message=original_send)
    monkeypatch.setattr(
        runtime_patch,
        "enqueue_completed_turn",
        lambda **kwargs: calls.append("capture"),
    )

    runtime_patch.install(chat_module, routes_module)
    first_wrapper = routes_module.send_message
    runtime_patch.install(chat_module, routes_module)

    assert routes_module.send_message is first_wrapper
    routes_module.send_message(
        1,
        "conversation-1",
        "question",
        idempotency_key="request-1",
    )
    assert calls == ["send", "capture"]
