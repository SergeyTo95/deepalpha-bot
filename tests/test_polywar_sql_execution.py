import sqlite3

import pytest

from services import polywar_service as polywar


class _FakePostgresConnection:
    pass


class _FakePostgresCursor:
    def __init__(self, error=None):
        self.connection = _FakePostgresConnection()
        self.error = error
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.error is not None:
            raise self.error
        return self


class _LockedSqliteCursor:
    def __init__(self, connection, failures=2):
        self.connection = connection
        self.failures = failures
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if len(self.calls) <= self.failures:
            raise sqlite3.OperationalError("database is locked")
        return self


def test_execute_postgres_keeps_percent_s_and_executes_once():
    cursor = _FakePostgresCursor()

    result = polywar._execute(cursor, "SELECT * FROM demo WHERE id=%s", (7,))

    assert result is cursor
    assert cursor.calls == [("SELECT * FROM demo WHERE id=%s", (7,))]


def test_execute_postgres_propagates_first_error_without_placeholder_rewrite():
    cursor = _FakePostgresCursor(RuntimeError("original postgres failure"))

    with pytest.raises(RuntimeError, match="original postgres failure"):
        polywar._execute(cursor, "SELECT * FROM demo WHERE id=%s", (7,))

    assert cursor.calls == [("SELECT * FROM demo WHERE id=%s", (7,))]


def test_execute_sqlite_converts_percent_s_before_first_execute():
    connection = sqlite3.connect(":memory:")
    try:
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE demo (id INTEGER)")

        polywar._execute(cursor, "INSERT INTO demo (id) VALUES (%s)", (7,))

        assert cursor.execute("SELECT id FROM demo").fetchone() == (7,)
    finally:
        connection.close()


def test_execute_sqlite_locked_retry_reuses_same_converted_sql(monkeypatch):
    connection = sqlite3.connect(":memory:")
    try:
        cursor = _LockedSqliteCursor(connection, failures=2)
        sleeps = []
        monkeypatch.setattr(polywar.time, "sleep", sleeps.append)

        result = polywar._execute(cursor, "SELECT %s", (7,))

        assert result is cursor
        assert cursor.calls == [
            ("SELECT ?", (7,)),
            ("SELECT ?", (7,)),
            ("SELECT ?", (7,)),
        ]
        assert sleeps == [0.025, 0.05]
    finally:
        connection.close()
