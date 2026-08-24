from services import velia_code_archive_runtime_patch as runtime


class FakeChatModule:
    def __init__(self):
        self.generated = 0
        self.generate_velia_chat_result = self._generate
        self._serialize_message = self._serialize

    def _generate(self, prompt, *, user_id, conversation_id, request_id=None):
        self.generated += 1
        return {
            "ok": True,
            "text": "delegated",
            "provider": "original",
            "request_id": str(request_id or ""),
        }

    @staticmethod
    def _serialize(row, *, debug_usage=False):
        return {
            "id": "m1",
            "role": row.get("role", "assistant"),
            "status": row.get("status", "completed"),
            "request_id": row.get("request_id", "r1"),
            "type": "text",
        }

    @staticmethod
    def _row_value(row, key, index, default=None):
        return row.get(key, default)


def test_archive_request_is_deterministic_and_does_not_call_llm(monkeypatch):
    module = FakeChatModule()
    monkeypatch.setattr(runtime, "_persisted_request_user_message", lambda *_args, **_kwargs: "Отправь файлы архивом")
    metadata = {
        "id": "a1",
        "filename": "VELIA-project-job.zip",
        "content_url": "/api/mobile/code-archives/a1/content?x=1",
        "mime_type": "application/zip",
        "size_bytes": 123,
        "sha256": "a" * 64,
        "file_count": 3,
    }
    monkeypatch.setattr(
        runtime.archive_service,
        "create_archive_for_latest_coding_job",
        lambda **_kwargs: metadata,
    )
    monkeypatch.setattr(
        runtime.archive_service,
        "archive_metadata_for_request",
        lambda *_args, **_kwargs: metadata,
    )

    runtime.install(module)
    result = module.generate_velia_chat_result(
        "untrusted built prompt",
        user_id=7,
        conversation_id="c1",
        request_id="r1",
    )
    assert module.generated == 0
    assert result["provider"] == "velia_code_archive"
    assert result["finish_reason"] == "archive_created"

    serialized = module._serialize_message(
        {
            "role": "assistant",
            "status": "completed",
            "provider": "velia_code_archive",
            "request_id": "r1",
            "user_id": 7,
        }
    )
    assert serialized["type"] == "archive"
    assert serialized["archive"] == metadata


def test_non_archive_request_delegates(monkeypatch):
    module = FakeChatModule()
    monkeypatch.setattr(runtime, "_persisted_request_user_message", lambda *_args, **_kwargs: "Проверь этот файл")
    runtime.install(module)
    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="c1",
        request_id="r2",
    )
    assert module.generated == 1
    assert result["provider"] == "original"


def test_missing_completed_job_returns_text_without_archive(monkeypatch):
    module = FakeChatModule()
    monkeypatch.setattr(runtime, "_persisted_request_user_message", lambda *_args, **_kwargs: "Собери код в zip")

    def fail(**_kwargs):
        raise runtime.archive_service.VeliaCodeArchiveError(
            "code_archive_completed_job_missing",
            status=404,
        )

    monkeypatch.setattr(runtime.archive_service, "create_archive_for_latest_coding_job", fail)
    monkeypatch.setattr(runtime.archive_service, "archive_metadata_for_request", lambda *_args, **_kwargs: None)
    runtime.install(module)
    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="c1",
        request_id="r3",
    )
    assert result["provider"] == "velia_code_archive"
    assert result["reason"] == "code_archive_completed_job_missing"
    serialized = module._serialize_message(
        {
            "role": "assistant",
            "status": "completed",
            "provider": "velia_code_archive",
            "request_id": "r3",
            "user_id": 7,
        }
    )
    assert serialized["type"] == "text"
    assert "archive" not in serialized
