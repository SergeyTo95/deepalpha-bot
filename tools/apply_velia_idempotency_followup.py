from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services/velia_attachment_idempotency_service.py"
TEST = ROOT / "tests/test_velia_attachment_idempotency_followup.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


text = SERVICE.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''            existing_created_at = existing[10]
            existing_deleted_at = existing[11]
            if (
''',
    '''            existing_created_at = existing[10]
            existing_deleted_at = existing[11]
            if existing_status == "ready" and existing_deleted_at is not None:
                # A privacy deletion is terminal for this deterministic upload
                # key. Never resurrect scrubbed data through a delayed retry.
                raise attachment_service.AttachmentError(
                    "attachment_not_found",
                    status=404,
                )
            if (
''',
    "terminal deleted ready tombstone",
)

start = text.index("def _reconcile_completion_error(")
end = text.index("\n\ndef create_attachment_idempotently(", start)
section = text[start:end]
section = replace_once(
    section,
    '''    for attempt in range(1, attempts + 1):
        conn = get_connection()
        cursor = conn.cursor()
        try:
''',
    '''    for attempt in range(1, attempts + 1):
        conn = None
        cursor = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
''',
    "retry connection acquisition",
)
section = replace_once(
    section,
    '''        except Exception as exc:
            conn.rollback()
            if attempt >= attempts:
                logger.error(
                    "VELIA_ATTACHMENT_COMPLETION_RECONCILE_FAILED attachment_id=%s user_id=%s attempts=%s error=%s",
                    str(attachment_id),
                    int(user_id),
                    attempts,
                    exc.__class__.__name__,
                )
        finally:
            cursor.close()
            conn.close()
''',
    '''        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if attempt >= attempts:
                logger.error(
                    "VELIA_ATTACHMENT_COMPLETION_RECONCILE_FAILED attachment_id=%s user_id=%s attempts=%s error=%s",
                    str(attachment_id),
                    int(user_id),
                    attempts,
                    exc.__class__.__name__,
                )
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
''',
    "safe reconcile cleanup",
)
text = text[:start] + section + text[end:]
SERVICE.write_text(text, encoding="utf-8")

TEST.write_text(
    '''from datetime import datetime, timezone

import pytest

from services import velia_attachment_idempotency_service as idempotency
from services import velia_attachment_service as attachment_service
from services import velia_attachment_upload_service as upload_service


ATTACHMENT_ID = "8b07392d-7d71-4acf-a077-13e50aa0dcb5"


class _Cursor:
    def __init__(self, fetchone_values=None):
        self.fetchone_values = list(fetchone_values or [])
        self.calls = []
        self.closed = False

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _existing_row(*, status="ready", deleted_at=None):
    return (
        ATTACHMENT_ID,
        "conversation-1",
        "report.txt",
        "text/plain",
        "document",
        5,
        "digest",
        None,
        None,
        status,
        datetime.now(timezone.utc),
        deleted_at,
    )


def _reserve_kwargs():
    return {
        "attachment_id": ATTACHMENT_ID,
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


def test_deleted_ready_upload_key_is_terminal(monkeypatch):
    cursor = _Cursor(
        fetchone_values=[
            (1,),
            _existing_row(deleted_at=datetime.now(timezone.utc)),
        ]
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(idempotency, "get_connection", lambda: connection)

    with pytest.raises(attachment_service.AttachmentError) as error:
        idempotency._existing_or_reserve(**_reserve_kwargs())

    assert error.value.code == "attachment_not_found"
    assert error.value.status == 404
    assert connection.rollbacks == 1
    assert not any(
        query.startswith("UPDATE velia_attachments")
        for query, _ in cursor.calls
    )


def test_reconcile_retries_connection_acquisition(monkeypatch):
    cursor = _Cursor(fetchone_values=[_existing_row()])
    connection = _Connection(cursor)
    attempts = []

    def flaky_connection():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("postgres temporarily unavailable")
        return connection

    monkeypatch.setattr(idempotency, "get_connection", flaky_connection)
    monkeypatch.setattr(upload_service, "_env_int", lambda *_args: 3)
    monkeypatch.setattr(
        attachment_service,
        "_serialize_attachment",
        lambda row: {"id": row[0], "status": row[9]},
    )

    result = idempotency._reconcile_completion_error(
        attachment_id=ATTACHMENT_ID,
        user_id=7,
        conversation_id="conversation-1",
        expected_size=5,
        expected_digest="digest",
    )

    assert len(attempts) == 2
    assert result == {"id": ATTACHMENT_ID, "status": "ready"}
    assert connection.commits == 1
    assert connection.closed is True
    assert cursor.closed is True
''',
    encoding="utf-8",
)
