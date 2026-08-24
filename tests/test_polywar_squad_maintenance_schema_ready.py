import sqlite3

import pytest

from services import polywar_service as polywar
from services import polywar_squad_service as squad


class _SQLiteConnectionWrapper:
    def __init__(self):
        self.inner = sqlite3.connect(":memory:")

    def cursor(self):
        return self.inner.cursor()

    def commit(self):
        return self.inner.commit()

    def rollback(self):
        return self.inner.rollback()

    def close(self):
        # The production connection is closed by maintenance. Keep the in-memory
        # database inspectable for assertions in these focused tests.
        return None


def test_maintenance_skips_cleanly_before_polywar_schema_bootstrap(monkeypatch):
    conn = _SQLiteConnectionWrapper()
    monkeypatch.setattr(polywar, "get_connection", lambda: conn)

    result = squad.run_squad_maintenance_once()

    assert result == {"ok": True, "processed": False, "reason": "no_active_season"}
    assert conn.inner.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='polywar_seasons'"
    ).fetchone() is None


def test_maintenance_keeps_no_active_season_contract_after_schema_exists(monkeypatch):
    conn = _SQLiteConnectionWrapper()
    conn.inner.execute(
        "CREATE TABLE polywar_seasons (id INTEGER PRIMARY KEY, status TEXT, starts_at TIMESTAMP)"
    )
    conn.inner.commit()
    monkeypatch.setattr(polywar, "get_connection", lambda: conn)

    result = squad.run_squad_maintenance_once()

    assert result == {"ok": True, "processed": False, "reason": "no_active_season"}


def test_maintenance_does_not_hide_errors_after_schema_is_ready(monkeypatch):
    conn = _SQLiteConnectionWrapper()
    conn.inner.execute("CREATE TABLE polywar_seasons (status TEXT)")
    conn.inner.commit()
    monkeypatch.setattr(polywar, "get_connection", lambda: conn)

    with pytest.raises(sqlite3.OperationalError, match="starts_at"):
        squad.run_squad_maintenance_once()
