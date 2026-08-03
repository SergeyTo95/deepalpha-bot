from datetime import datetime, timezone
from pathlib import Path

import pytest

from services import velia_attachment_idempotency_service as idempotency
from services import velia_attachment_service as attachment_service
from services import velia_attachment_upload_service as upload_service


class _Cursor:
    def __init__(self, fetchone_values=None):
        self.fetchone_values = list(fetchone_values or [])
        self.calls = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None

    def close(self):
        pass


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _existing_row(*, status="ready", deleted_at=None, digest="digest"):
    return (
        "attachment-1",
        "conversation-1",
        "report.txt",
        "text/plain",
        "document",
        5,
        digest,
        None,
        None,
        status,
        datetime.now(timezone.utc),
        deleted_at,
    )


def _reserve_kwargs():
    return {
        "attachment_id": "8b07392d-7d71-4acf-a077-13e50aa0dcb5",
        "user_id": 7,
        "conversation_id": "conversation-1",
        "filename": "report.txt",
        "raw": b"hello",
        "digest": "digest",
        "preflight": {
            "mime_type": "text/plain",
            "kind": "document",
            "width": None,
            "height": None,
        },
        "max_bytes": 15 * 1024 * 1024,
    }


def test_upload_idempotency_key_is_required_and_validated():
    with pytest.raises(attachment_service.AttachmentError) as missing:
        idempotency.normalize_upload_idempotency_key("")
    assert missing.value.code == "attachment_idempotency_key_required"
    assert missing.value.status == 400

    with pytest.raises(attachment_service.AttachmentError) as invalid:
        idempotency.normalize_upload_idempotency_key("bad key")
    assert invalid.value.code == "invalid_attachment_idempotency_key"

    assert idempotency.normalize_upload_idempotency_key("draft-12345678") == "draft-12345678"


def test_same_key_returns_existing_ready_attachment(monkeypatch):
    cursor = _Cursor(fetchone_values=[_existing_row()])
    connection = _Connection(cursor)
    monkeypatch.setattr(idempotency, "get_connection", lambda: connection)
    monkeypatch.setattr(
        attachment_service,
        "_serialize_attachment",
        lambda row: {"id": row[0], "status": row[9]},
    )

    result = idempotency._existing_or_reserve(**_reserve_kwargs())

    assert result == {"id": "attachment-1", "status": "ready"}
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert not any(query.startswith("INSERT INTO") for query, _ in cursor.calls)


def test_parallel_same_key_reports_upload_in_progress(monkeypatch):
    cursor = _Cursor(fetchone_values=[_existing_row(status="failed")])
    connection = _Connection(cursor)
    monkeypatch.setattr(idempotency, "get_connection", lambda: connection)

    with pytest.raises(attachment_service.AttachmentError) as error:
        idempotency._existing_or_reserve(**_reserve_kwargs())

    assert error.value.code == "attachment_upload_in_progress"
    assert error.value.status == 409
    assert connection.rollbacks == 1


def test_same_key_with_different_content_is_rejected(monkeypatch):
    cursor = _Cursor(fetchone_values=[_existing_row(digest="other")])
    connection = _Connection(cursor)
    monkeypatch.setattr(idempotency, "get_connection", lambda: connection)

    with pytest.raises(attachment_service.AttachmentError) as error:
        idempotency._existing_or_reserve(**_reserve_kwargs())

    assert error.value.code == "idempotency_attachment_mismatch"
    assert error.value.status == 409


def test_known_ready_row_is_scrubbed_when_unlinked(monkeypatch):
    cursor = _Cursor(fetchone_values=[("attachment-1",), None])
    connection = _Connection(cursor)
    monkeypatch.setattr(idempotency, "get_connection", lambda: connection)

    assert idempotency.scrub_unlinked_known_attachment("attachment-1", 7) is True

    queries = [query for query, _ in cursor.calls]
    assert "FOR UPDATE" in queries[0]
    assert queries[1].startswith("SELECT 1 FROM velia_message_attachments")
    assert "extraction_status='failed'" in queries[2]
    assert "deleted_at=COALESCE" in queries[2]
    assert connection.commits == 1


def test_ambiguous_completion_failure_scrubs_known_id(monkeypatch):
    monkeypatch.setattr(idempotency, "_existing_or_reserve", lambda **_kwargs: None)
    monkeypatch.setattr(
        upload_service,
        "_preflight_attachment",
        lambda _raw, _mime: {
            "mime_type": "text/plain",
            "kind": "document",
            "width": None,
            "height": None,
        },
    )
    monkeypatch.setattr(
        attachment_service,
        "inspect_attachment",
        lambda *_args, **_kwargs: {
            "mime_type": "text/plain",
            "kind": "document",
            "width": None,
            "height": None,
            "extracted_text": "hello",
        },
    )
    monkeypatch.setattr(
        upload_service,
        "_complete_attachment",
        lambda **_kwargs: (_ for _ in ()).throw(ConnectionError("commit ack lost")),
    )
    scrubbed = []
    monkeypatch.setattr(
        idempotency,
        "scrub_unlinked_known_attachment",
        lambda attachment_id, user_id: scrubbed.append((attachment_id, user_id)) or True,
    )

    with pytest.raises(ConnectionError):
        idempotency.create_attachment_idempotently(
            7,
            "conversation-1",
            idempotency_key="draft-12345678",
            filename="report.txt",
            mime_type="text/plain",
            content=b"hello",
        )

    assert len(scrubbed) == 1
    assert scrubbed[0][1] == 7


def test_mobile_route_requires_upload_idempotency_header():
    source = Path("velia_mobile_attachment_routes.py").read_text(encoding="utf-8")

    assert 'request.headers.get("Idempotency-Key")' in source
    assert "create_attachment_idempotently" in source
    assert "upload_task.add_done_callback(_consume_detached_upload_result)" in source
    assert "delete_attachment, int(user_id), attachment_id" not in source
