from types import SimpleNamespace

from services import velia_attachment_privacy_service as privacy
from services import velia_attachment_upload_service as upload
from services import velia_conversation_quality_patch as quality
from services import velia_images_runtime_patch as images


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


def test_reservation_never_persists_original_upload_bytes(monkeypatch):
    secret = b"private-original-file-bytes"
    cursor = _Cursor(fetchone_values=[(1,), (0, 0)])
    connection = _Connection(cursor)
    monkeypatch.setattr(upload, "get_connection", lambda: connection)

    upload._reserve_attachment(
        attachment_id="attachment-1",
        user_id=7,
        conversation_id="conversation-1",
        filename="secret.txt",
        raw=secret,
        digest="digest",
        preflight={
            "mime_type": "text/plain",
            "kind": "document",
            "width": None,
            "height": None,
        },
        max_bytes=15 * 1024 * 1024,
    )

    insert_query, insert_params = cursor.calls[-1]
    assert insert_query.startswith("INSERT INTO velia_attachments")
    assert secret not in insert_params
    assert b"" in insert_params
    assert connection.commits == 1


def test_delete_scrubs_payload_and_metadata_atomically(monkeypatch):
    cursor = _Cursor(fetchone_values=[("attachment-1",), None])
    connection = _Connection(cursor)
    monkeypatch.setattr(privacy, "get_connection", lambda: connection)

    assert privacy.delete_attachment(7, "attachment-1") is True

    queries = [query for query, _params in cursor.calls]
    assert "FOR UPDATE" in queries[0]
    update_query, update_params = cursor.calls[-1]
    assert "content_bytes=%s" in update_query
    assert "extracted_text=''" in update_query
    assert "sha256=''" in update_query
    assert "original_name='deleted attachment'" in update_query
    assert update_params[0] == b""
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_attachment_backed_memory_note_uses_normal_generation(monkeypatch):
    monkeypatch.setattr(
        quality,
        "_latest_completed_user_message",
        lambda _user_id, _conversation_id: "remember this attachment",
    )
    monkeypatch.setattr(
        quality,
        "request_message_has_attachments",
        lambda _request_id, _user_id: True,
    )
    original_calls = []

    def original_generate(prompt, **kwargs):
        original_calls.append((prompt, kwargs))
        return {"ok": True, "text": "I analyzed the attachment.", "model": "chat"}

    module = SimpleNamespace(
        _build_prompt=lambda _user_id, _conversation_id: "SYSTEM\n\nConversation:\n",
        list_messages=lambda _user_id, _conversation_id, limit=100: [],
        generate_velia_chat_result=original_generate,
        _env_int=lambda _name, default, _minimum, _maximum: default,
        _dict_cursor=lambda conn: conn.cursor(),
        _row_value=lambda row, key, index, default=None: default,
    )
    quality.install(module)

    result = module.generate_velia_chat_result(
        "prompt with attachment context",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["model"] == "chat"
    assert len(original_calls) == 1


def test_attachment_backed_image_request_bypasses_paid_generation(monkeypatch):
    monkeypatch.setattr(images, "install_queue_runtime", lambda: None)
    monkeypatch.setattr(
        images,
        "request_message_has_attachments",
        lambda _request_id, _user_id: True,
    )
    monkeypatch.setattr(
        images,
        "generate_and_store_image",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("paid image generation must not run")
        ),
    )
    original_calls = []

    def original_generate(prompt, **kwargs):
        original_calls.append((prompt, kwargs))
        return {"ok": True, "text": "attachment-aware answer", "provider": "chat"}

    module = SimpleNamespace(
        generate_velia_chat_result=original_generate,
        _serialize_message=lambda row, debug_usage=False: dict(row),
        _row_value=lambda row, key, index, default=None: row.get(key, default),
    )
    images.install(module)

    result = module.generate_velia_chat_result(
        "create an image based on the attached screenshot",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["provider"] == "chat"
    assert len(original_calls) == 1
