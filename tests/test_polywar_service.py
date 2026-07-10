import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta

import pytest

import services.polywar_service as polywar


@pytest.fixture()
def polydb(monkeypatch):
    uri = "file:polywar_test?mode=memory&cache=shared"
    keeper = sqlite3.connect(uri, uri=True)
    keeper.row_factory = sqlite3.Row

    def connect():
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(polywar, "get_connection", connect)
    monkeypatch.setattr(polywar, "get_setting", lambda key, default="": default)
    conn = connect()
    polywar.init_polywar_schema(conn)
    conn.close()
    yield connect
    keeper.close()


def test_first_season_and_idempotent_initialization(polydb):
    conn = polydb()
    first = polywar.ensure_active_season(conn)
    second = polywar.ensure_active_season(conn)
    polywar.init_polywar_schema(conn)
    assert first["id"] == second["id"]
    assert first["status"] == "active"
    assert "secret_seed" not in first
    conn.close()


def test_seven_factions_created(polydb):
    conn = polydb()
    factions = polywar.ensure_factions(conn)
    assert len(factions) == 7
    assert {f["slug"] for f in factions} >= {"blue-coalition", "purple-pact"}
    conn.close()


def test_join_faction_and_reject_repeat_or_unknown(polydb):
    state = polywar.join_faction(42, 1)
    assert state["selected_faction"]["name"] == "Blue Coalition"
    with pytest.raises(ValueError, match="faction_already_selected"):
        polywar.join_faction(42, 2)
    with pytest.raises(ValueError, match="unknown_faction"):
        polywar.join_faction(43, 999)


def test_energy_recovers_offline_and_caps_at_max(polydb, monkeypatch):
    conn = polydb()
    polywar.ensure_factions(conn)
    season = polywar.ensure_active_season(conn)
    player = polywar.get_or_create_player(55, season["id"], conn)
    old = datetime.utcnow() - timedelta(hours=20)
    conn.execute(
        "UPDATE polywar_players SET current_energy = ?, max_energy = ?, energy_updated_at = ? WHERE user_id = ? AND season_id = ?",
        (2, 10, old, 55, season["id"]),
    )
    conn.commit()
    player = polywar.get_or_create_player(55, season["id"], conn)
    assert player["current_energy"] == 10
    assert polywar.get_state(55, conn)["energy"]["current_energy"] == 10
    conn.close()


def test_secret_seed_never_in_state(polydb):
    state = polywar.get_state(77)
    assert "secret_seed" not in state["season"]
    assert "secret_seed" not in str(state)
