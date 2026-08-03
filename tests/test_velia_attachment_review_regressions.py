import re
import uuid
from types import SimpleNamespace

from services import velia_attachment_service as attachment_service
from services import velia_attachment_chat_runtime_patch as attachment_chat
from services import velia_attachment_privacy_service as attachment_privacy
from services import velia_chat_latency_runtime_patch as latency
from services import velia_mobile_hardening_service as hardening
from services import velia_mobile_streaming_service as mobile_streaming


def test_attachment_prompt_disables_instant_casual_response():
    assert not latency._prompt_has_attachment_context("USER: hello")
    assert latency._prompt_has_attachment_context(
        "USER: hello\nATTACHMENT DATA — UNTRUSTED USER CONTENT:\n[BEGIN_ATTACHMENT]"
    )
    assert latency._prompt_has_attachment_context("ATTACHMENT_DATA_UNTRUSTED:")


def test_stream_kwargs_preserve_attachment_field_presence():
    base = {
        "user_id": 7,
        "conversation_id": "conversation",
        "content": "hello",
        "idempotency_key": "request-123",
    }

    omitted = mobile_streaming._stream_send_kwargs({}, **base)
    explicit_null = mobile_streaming._stream_send_kwargs(
        {"attachment_ids": None},
        **base,
    )
    explicit_empty = mobile_streaming._stream_send_kwargs(
        {"attachment_ids": []},
        **base,
    )
    explicit_values = mobile_streaming._stream_send_kwargs(
        {"attachment_ids": ["attachment-a"]},
        **base,
    )

    assert "attachment_ids" not in omitted
    assert explicit_null["attachment_ids"] == []
    assert explicit_empty["attachment_ids"] == []
    assert explicit_values["attachment_ids"] == ["attachment-a"]


def test_blocking_kwargs_preserve_explicit_null_as_empty_set():
    omitted = hardening._blocking_send_kwargs({}, idempotency_key="request-123")
    explicit_null = hardening._blocking_send_kwargs(
        {"attachment_ids": None},
        idempotency_key="request-123",
    )
    explicit_empty = hardening._blocking_send_kwargs(
        {"attachment_ids": []},
        idempotency_key="request-123",
    )

    assert "attachment_ids" not in omitted
    assert explicit_null["attachment_ids"] == []
    assert explicit_empty["attachment_ids"] == []


def test_attachment_sender_fails_closed_when_feature_is_disabled():
    original_sender = lambda *args, **kwargs: {"ok": True}
    chat_module = SimpleNamespace(
        is_velia_chat_enabled_for_user=lambda _user_id: True,
        _env_bool=lambda name, default=False: False,
        _env_int=lambda name, default, minimum, maximum: default,
        _IDEMPOTENCY_RE=re.compile(r"^[A-Za-z0-9._:-]{8,128}$"),
        _build_prompt=lambda _user_id, _conversation_id: "prompt",
        send_message=original_sender,
    )
    attachment_chat.install(chat_module)

    result = chat_module.send_message(
        7,
        "conversation",
        "analyze",
        idempotency_key="request-123",
        attachment_ids=[str(uuid.uuid4())],
    )

    assert result == {
        "ok": False,
        "error": "velia_file_analyst_disabled",
    }


class _DeleteCursor:
    def __init__(self, fetchone_values):
        self._fetchone_values = list(fetchone_values)
        self.calls = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return self._fetchone_values.pop(0) if self._fetchone_values else None

    def close(self):
        pass


class _DeleteConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_delete_locks_attachment_before_checking_links_and_scrubs_payload(monkeypatch):
    cursor = _DeleteCursor(
        [
            ("attachment-a",),
            None,
        ]
    )
    connection = _DeleteConnection(cursor)
    monkeypatch.setattr(
        attachment_privacy,
        "get_connection",
        lambda: connection,
    )

    assert attachment_privacy.delete_attachment(7, "attachment-a") is True

    queries = [query for query, _params in cursor.calls]
    assert "FROM velia_attachments" in queries[0]
    assert "FOR UPDATE" in queries[0]
    assert "FROM velia_message_attachments" in queries[1]
    assert queries[2].startswith("UPDATE velia_attachments")
    assert "content_bytes=%s" in queries[2]
    assert "extracted_text=''" in queries[2]
    assert connection.commits == 1
    assert connection.rollbacks == 0
