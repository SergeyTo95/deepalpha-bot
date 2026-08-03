from services import velia_attachment_service as attachment_service
from services import velia_chat_latency_runtime_patch as latency
from services import velia_mobile_streaming_service as mobile_streaming


def test_attachment_prompt_disables_instant_casual_response():
    assert not latency._prompt_has_attachment_context("USER: hello")
    assert latency._prompt_has_attachment_context(
        "USER: hello\nATTACHMENT_DATA_UNTRUSTED:\n[BEGIN_ATTACHMENT]"
    )


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
    explicit_values = mobile_streaming._stream_send_kwargs(
        {"attachment_ids": ["attachment-a"]},
        **base,
    )

    assert "attachment_ids" not in omitted
    assert "attachment_ids" in explicit_null
    assert explicit_null["attachment_ids"] is None
    assert explicit_values["attachment_ids"] == ["attachment-a"]


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


def test_delete_locks_attachment_before_checking_message_links(monkeypatch):
    cursor = _DeleteCursor(
        [
            ("attachment-a",),
            None,
        ]
    )
    connection = _DeleteConnection(cursor)
    monkeypatch.setattr(
        attachment_service,
        "get_connection",
        lambda: connection,
    )

    assert attachment_service.delete_attachment(7, "attachment-a") is True

    queries = [query for query, _params in cursor.calls]
    assert "FROM velia_attachments" in queries[0]
    assert "FOR UPDATE" in queries[0]
    assert "FROM velia_message_attachments" in queries[1]
    assert queries[2].startswith("UPDATE velia_attachments")
    assert connection.commits == 1
