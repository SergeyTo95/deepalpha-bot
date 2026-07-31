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


class _SqlStateError(RuntimeError):
    def __init__(self, sqlstate):
        super().__init__(f"database error {sqlstate}")
        self.pgcode = sqlstate


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


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
def test_retrying_bootstrap_retries_deadlock_and_serialization_failure(monkeypatch, sqlstate):
    connections = [_FakeConnection(), _FakeConnection()]
    pending = list(connections)
    sleeps = []
    calls = 0

    monkeypatch.setattr(bootstrap, "get_connection", lambda: pending.pop(0))
    monkeypatch.setattr(bootstrap.time, "sleep", sleeps.append)

    def migrate():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _SqlStateError(sqlstate)

    bootstrap.run_serialized_developer_api_schema_bootstrap(
        "retry-process",
        migrate,
        max_attempts=3,
        base_delay_seconds=0.25,
    )

    assert calls == 2
    assert sleeps == [0.25]
    assert not pending
    for connection in connections:
        assert connection.cursor_instance.calls[0][0].startswith("SELECT pg_advisory_lock")
        assert connection.cursor_instance.calls[-1][0].startswith("SELECT pg_advisory_unlock")
        assert connection.closed is True


def test_retrying_bootstrap_supports_psycopg_error_class_names(monkeypatch):
    class DeadlockDetected(RuntimeError):
        pass

    connections = [_FakeConnection(), _FakeConnection()]
    pending = list(connections)
    calls = 0
    monkeypatch.setattr(bootstrap, "get_connection", lambda: pending.pop(0))
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _delay: None)

    def migrate():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DeadlockDetected("deadlock")

    bootstrap.run_serialized_developer_api_schema_bootstrap("class-name", migrate)
    assert calls == 2


def test_retrying_bootstrap_raises_non_retryable_error_immediately(monkeypatch):
    connection = _FakeConnection()
    sleeps = []
    calls = 0
    monkeypatch.setattr(bootstrap, "get_connection", lambda: connection)
    monkeypatch.setattr(bootstrap.time, "sleep", sleeps.append)

    def migrate():
        nonlocal calls
        calls += 1
        raise RuntimeError("invalid migration")

    with pytest.raises(RuntimeError, match="invalid migration"):
        bootstrap.run_serialized_developer_api_schema_bootstrap(
            "invalid-process",
            migrate,
            max_attempts=3,
            base_delay_seconds=0.1,
        )

    assert calls == 1
    assert sleeps == []
    assert connection.closed is True


def test_retrying_bootstrap_stops_after_bounded_attempts(monkeypatch):
    connections = [_FakeConnection(), _FakeConnection(), _FakeConnection()]
    pending = list(connections)
    sleeps = []
    calls = 0
    monkeypatch.setattr(bootstrap, "get_connection", lambda: pending.pop(0))
    monkeypatch.setattr(bootstrap.time, "sleep", sleeps.append)

    def migrate():
        nonlocal calls
        calls += 1
        raise _SqlStateError("40P01")

    with pytest.raises(_SqlStateError):
        bootstrap.run_serialized_developer_api_schema_bootstrap(
            "exhausted-process",
            migrate,
            max_attempts=3,
            base_delay_seconds=0.1,
        )

    assert calls == 3
    assert sleeps == [0.1, 0.2]
    assert not pending
    assert all(connection.closed for connection in connections)


def test_all_developer_api_processes_use_retrying_serialized_bootstrap():
    expected = {
        "run_web_process.py": "webapp",
        "run_api_worker.py": "api-worker",
        "run_opportunity_worker.py": "opportunity-worker",
        "run_webhook_worker.py": "webhook-worker",
        "run_api_commercial_worker.py": "commercial-worker",
    }
    for path, process_name in expected.items():
        source = Path(path).read_text(encoding="utf-8")
        assert "run_serialized_developer_api_schema_bootstrap" in source
        assert f'"{process_name}"' in source
        assert f'with serialized_developer_api_schema_bootstrap("{process_name}")' not in source
