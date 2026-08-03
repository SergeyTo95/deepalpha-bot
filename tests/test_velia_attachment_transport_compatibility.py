from types import SimpleNamespace

from services import velia_chat_streaming_runtime_patch as streaming
from services import velia_memory_shadow_runtime_patch as memory_shadow
from services import velia_mobile_hardening_service as hardening


class _Cursor:
    def __init__(self, fetchone_values):
        self._values = list(fetchone_values)
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return self._values.pop(0) if self._values else None

    def close(self):
        pass


class _Connection:
    def __init__(self, cursor):
        self.cursor = cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_hardening_forwards_explicit_attachment_set_to_final_sender():
    cursor = _Cursor([(True,), (True,)])
    connection = _Connection(cursor)
    calls = []
    chat_module = SimpleNamespace(
        get_connection=lambda: connection,
        _dict_cursor=lambda conn: conn.cursor,
        _IDEMPOTENCY_RE=__import__("re").compile(r"^[A-Za-z0-9._:-]{8,128}$"),
        _existing_request_result=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("explicit attachment sets must reach the final sender")
        ),
    )

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    hardened = hardening.build_hardened_send_message(chat_module, original)
    result = hardened(
        7,
        "conversation",
        "analyze this",
        idempotency_key="request-123",
        attachment_ids=["attachment-a"],
    )

    assert result == {"ok": True}
    assert calls == [
        (
            (7, "conversation", "analyze this"),
            {
                "idempotency_key": "request-123",
                "attachment_ids": ["attachment-a"],
            },
        )
    ]


def test_memory_shadow_omits_attachment_keyword_for_legacy_sender(monkeypatch):
    calls = []

    def legacy_sender(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": False, "error": "expected"}

    chat_module = SimpleNamespace()
    routes_module = SimpleNamespace(send_message=legacy_sender)
    memory_shadow.install(chat_module, routes_module)

    result = routes_module.send_message(
        7,
        "conversation",
        "hello",
        idempotency_key="request-123",
    )

    assert result == {"ok": False, "error": "expected"}
    assert calls == [
        (
            (7, "conversation", "hello"),
            {"idempotency_key": "request-123"},
        )
    ]


def test_streaming_sender_forwards_exact_attachment_set():
    calls = []

    def sender(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    result = streaming.run_streaming_send(
        sender,
        user_id=7,
        conversation_id="conversation",
        content="analyze",
        idempotency_key="request-123",
        attachment_ids=["attachment-a", "attachment-b"],
        on_delta=lambda _text: None,
        on_reset=lambda: None,
    )

    assert result == {"ok": True}
    assert calls == [
        (
            (7, "conversation", "analyze"),
            {
                "idempotency_key": "request-123",
                "attachment_ids": ["attachment-a", "attachment-b"],
            },
        )
    ]


def test_streaming_sender_forwards_explicit_empty_attachment_set():
    calls = []

    def sender(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    result = streaming.run_streaming_send(
        sender,
        user_id=7,
        conversation_id="conversation",
        content="analyze",
        idempotency_key="request-123",
        attachment_ids=[],
        on_delta=lambda _text: None,
        on_reset=lambda: None,
    )

    assert result == {"ok": True}
    assert calls == [
        (
            (7, "conversation", "analyze"),
            {
                "idempotency_key": "request-123",
                "attachment_ids": [],
            },
        )
    ]
