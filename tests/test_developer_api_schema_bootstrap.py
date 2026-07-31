from pathlib import Path

import pytest

from services import developer_api_schema_bootstrap as bootstrap


class _FakeCursor:
    def __init__(self):
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), params))

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self):
        self.cursor_instance = _FakeCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_schema_bootstrap_lock_is_acquired_and_released(monkeypatch):
    connection = _FakeConnection()
    monkeypatch.setattr(bootstrap, "get_connection", lambda: connection)

    with bootstrap.serialized_developer_api_schema_bootstrap("test-process"):
        assert connection.cursor_instance.calls[0][0].startswith("SELECT pg_advisory_lock")

    assert connection.cursor_instance.calls[-1][0].startswith("SELECT pg_advisory_unlock")
    assert connection.commits == 1
    assert connection.cursor_instance.closed is True
    assert connection.closed is True


def test_schema_bootstrap_lock_releases_after_startup_failure(monkeypatch):
    connection = _FakeConnection()
    monkeypatch.setattr(bootstrap, "get_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="migration failed"):
        with bootstrap.serialized_developer_api_schema_bootstrap("failing-process"):
            raise RuntimeError("migration failed")

    assert connection.cursor_instance.calls[-1][0].startswith("SELECT pg_advisory_unlock")
    assert connection.closed is True


def test_all_developer_api_processes_serialize_schema_bootstrap():
    expected = {
        "run_web_process.py": "webapp",
        "run_api_worker.py": "api-worker",
        "run_opportunity_worker.py": "opportunity-worker",
        "run_webhook_worker.py": "webhook-worker",
        "run_api_commercial_worker.py": "commercial-worker",
    }
    for path, process_name in expected.items():
        source = Path(path).read_text(encoding="utf-8")
        assert "serialized_developer_api_schema_bootstrap" in source
        assert f'serialized_developer_api_schema_bootstrap("{process_name}")' in source
