import pytest

from services import velia_developer_chat_runtime_patch as patch


def test_read_only_repository_question_skips_coding_job_lookup(monkeypatch):
    monkeypatch.setattr(patch.coding_service, "is_coding_request", lambda message: False)
    monkeypatch.setattr(patch.coding_service, "is_approval", lambda message: False)
    monkeypatch.setattr(patch.coding_service, "is_cancel", lambda message: False)
    monkeypatch.setattr(patch.coding_service, "is_status_request", lambda message: False)
    monkeypatch.setattr(
        patch.coding_service,
        "active_job",
        lambda *args, **kwargs: pytest.fail("read-only routing must not query coding jobs"),
    )

    result = patch._coding_chat_result(
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
        message="Проверь в нашем репозитории, где создаётся mobile API route",
        project={"id": "project-1"},
    )

    assert result is None
