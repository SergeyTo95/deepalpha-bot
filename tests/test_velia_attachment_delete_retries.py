import pytest

from services import velia_attachment_privacy_service as privacy
from services import velia_attachment_service as attachment_service


def test_delete_retries_transient_storage_failures(monkeypatch):
    calls = []

    def flaky_delete(user_id, attachment_id):
        calls.append((user_id, attachment_id))
        if len(calls) < 3:
            raise RuntimeError("temporary database interruption")
        return True

    monkeypatch.setenv("VELIA_ATTACHMENTS_DELETE_ATTEMPTS", "3")
    monkeypatch.setenv("VELIA_ATTACHMENTS_DELETE_RETRY_DELAY_MS", "0")
    monkeypatch.setattr(privacy, "_delete_attachment_once", flaky_delete)

    assert privacy.delete_attachment(7, "attachment-1") is True
    assert calls == [
        (7, "attachment-1"),
        (7, "attachment-1"),
        (7, "attachment-1"),
    ]


def test_delete_does_not_retry_domain_errors(monkeypatch):
    calls = []

    def in_use(user_id, attachment_id):
        calls.append((user_id, attachment_id))
        raise attachment_service.AttachmentError("attachment_in_use", status=409)

    monkeypatch.setenv("VELIA_ATTACHMENTS_DELETE_ATTEMPTS", "5")
    monkeypatch.setenv("VELIA_ATTACHMENTS_DELETE_RETRY_DELAY_MS", "0")
    monkeypatch.setattr(privacy, "_delete_attachment_once", in_use)

    with pytest.raises(attachment_service.AttachmentError) as error:
        privacy.delete_attachment(7, "attachment-1")

    assert error.value.code == "attachment_in_use"
    assert calls == [(7, "attachment-1")]


def test_delete_raises_after_retry_budget_is_exhausted(monkeypatch):
    calls = []

    def always_fails(user_id, attachment_id):
        calls.append((user_id, attachment_id))
        raise RuntimeError("database unavailable")

    monkeypatch.setenv("VELIA_ATTACHMENTS_DELETE_ATTEMPTS", "2")
    monkeypatch.setenv("VELIA_ATTACHMENTS_DELETE_RETRY_DELAY_MS", "0")
    monkeypatch.setattr(privacy, "_delete_attachment_once", always_fails)

    with pytest.raises(RuntimeError, match="database unavailable"):
        privacy.delete_attachment(7, "attachment-1")

    assert len(calls) == 2
