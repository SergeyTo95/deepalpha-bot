import sqlite3
import uuid

import pytest

from services import polywar_service as polywar


class TupleCursor:
    def __init__(self, rows=(), description=()):
        self.rows = list(rows)
        self.description = description
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows = self.rows
        self.rows = []
        return rows


def pg_description(*names):
    return [(name, None, None, None, None, None, None) for name in names]


def test_row_to_dict_maps_postgres_tuple_row_from_description():
    cursor = TupleCursor(description=pg_description("id", "name", "is_playable"))

    assert polywar._row_to_dict(cursor, (1, "Alpha", True)) == {
        "id": 1,
        "name": "Alpha",
        "is_playable": True,
    }


def test_fetchall_maps_multiple_postgres_tuple_rows():
    cursor = TupleCursor(
        rows=[(1, "Alpha", True), (2, "Beta", False)],
        description=pg_description("id", "name", "is_playable"),
    )

    assert polywar._fetchall(cursor, "SELECT id,name,is_playable FROM polywar_factions") == [
        {"id": 1, "name": "Alpha", "is_playable": True},
        {"id": 2, "name": "Beta", "is_playable": False},
    ]


def test_fetchone_maps_postgres_tuple_row():
    cursor = TupleCursor(rows=[(1, "Alpha")], description=pg_description("id", "name"))

    assert polywar._fetchone(cursor, "SELECT id,name FROM polywar_factions LIMIT 1") == {
        "id": 1,
        "name": "Alpha",
    }


def test_fetchone_without_result_returns_none():
    cursor = TupleCursor(rows=[], description=pg_description("id", "name"))

    assert polywar._fetchone(cursor, "SELECT id,name FROM polywar_factions WHERE id=-1") is None


def test_dict_row_is_preserved_as_plain_dict():
    row = {"id": 1, "name": "Alpha"}

    assert polywar._row_to_dict(None, row) == row
    assert polywar._row_to_dict(None, row) is not row


def test_sqlite_row_is_preserved_as_plain_dict():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("CREATE TABLE factions (id INTEGER, name TEXT, is_playable INTEGER)")
        conn.execute("INSERT INTO factions VALUES (1, 'Alpha', 1)")
        cursor = conn.execute("SELECT id,name,is_playable FROM factions")
        row = cursor.fetchone()

        assert polywar._row_to_dict(cursor, row) == {
            "id": 1,
            "name": "Alpha",
            "is_playable": 1,
        }
    finally:
        conn.close()


def test_tuple_column_value_length_mismatch_is_explicit_runtime_error():
    cursor = TupleCursor(description=pg_description("id", "name", "is_playable"))

    with pytest.raises(RuntimeError, match="polywar_row_column_mismatch"):
        polywar._row_to_dict(cursor, (1, "Alpha"))


def test_list_factions_returns_dicts_with_tuple_cursor(monkeypatch):
    conn = sqlite3.connect(":memory:")
    try:
        polywar.init_polywar_schema(conn)
        conn.commit()
        polywar.ensure_factions(conn)

        factions = polywar.list_factions(conn)

        assert factions
        assert all(isinstance(faction, dict) for faction in factions)
        assert {"id", "name", "is_playable"}.issubset(factions[0])
    finally:
        conn.close()


def test_get_state_accepts_production_shaped_tuple_rows(monkeypatch):
    uri = f"file:polywar_pg_tuple_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=20)

    def connect():
        return sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=20)

    monkeypatch.setattr(polywar, "get_connection", connect)
    monkeypatch.setattr(polywar, "get_setting", lambda key, default="": default)
    monkeypatch.setattr(polywar, "get_airdrop_points_balance", lambda user_id: {"total": 0, "balance": 0})

    try:
        response = polywar.get_state(9001)

        assert response["ok"] is True
        assert isinstance(response["factions"], list)
        assert response["factions"]
        assert all(isinstance(faction, dict) for faction in response["factions"])
        assert response["season"]["id"]
        assert response["player"]["user_id"] == 9001
    finally:
        keeper.close()
