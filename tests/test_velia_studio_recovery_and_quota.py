import pytest

from services import velia_studio_recovery_service as recovery
from services import velia_studio_upload_quota as quota
from services.velia_studio_service import StudioError


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        return None


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)

    def cursor(self):
        return self.cursor_value

    def close(self):
        return None


def test_recovery_uses_exact_user_session_and_client_request(monkeypatch):
    connection = _Connection(("generation-1",))
    monkeypatch.setattr(recovery, "get_connection", lambda: connection)
    monkeypatch.setattr(
        recovery,
        "_generation",
        lambda user_id, generation_id=None: {
            "id": generation_id,
            "session_id": "session-1",
            "status": "completed",
        },
    )

    result = recovery.generation_for_client_request(
        42,
        "session-1",
        "client-request-1",
    )

    assert result == {
        "id": "generation-1",
        "session_id": "session-1",
        "status": "completed",
        "client_request_id": "client-request-1",
    }
    _, params = connection.cursor_value.executed[0]
    assert params == (42, "session-1", "client-request-1")


def test_reference_daily_count_limit_fails_closed(monkeypatch):
    monkeypatch.setenv("VELIA_STUDIO_DAILY_REFERENCE_LIMIT", "2")
    monkeypatch.setenv("VELIA_STUDIO_DAILY_REFERENCE_BYTES", str(100 * 1024 * 1024))
    monkeypatch.setattr(quota, "get_connection", lambda: _Connection((2, 1024)))

    with pytest.raises(StudioError) as exc:
        quota.assert_studio_upload_capacity(42, 1024)
    assert exc.value.code == "studio_reference_daily_limit"
    assert exc.value.status == 429


def test_reference_daily_byte_limit_fails_closed(monkeypatch):
    limit = 20 * 1024 * 1024
    monkeypatch.setenv("VELIA_STUDIO_DAILY_REFERENCE_LIMIT", "20")
    monkeypatch.setenv("VELIA_STUDIO_DAILY_REFERENCE_BYTES", str(limit))
    monkeypatch.setattr(
        quota,
        "get_connection",
        lambda: _Connection((1, 15 * 1024 * 1024)),
    )

    with pytest.raises(StudioError) as exc:
        quota.assert_studio_upload_capacity(42, 6 * 1024 * 1024)
    assert exc.value.code == "studio_reference_daily_bytes_limit"
    assert exc.value.status == 429
