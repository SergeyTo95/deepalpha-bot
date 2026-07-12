import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import services.polywar_service as polywar


@pytest.fixture()
def polydb(monkeypatch):
    uri = f"file:polywar_test_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = sqlite3.connect(uri, uri=True, check_same_thread=False)
    keeper.row_factory = sqlite3.Row

    def connect():
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(polywar, "get_connection", connect)
    monkeypatch.setattr(polywar, "get_setting", lambda key, default="": default)
    monkeypatch.setattr(polywar, "get_airdrop_points_balance", lambda user_id: {"total": 1234, "balance": 999})
    conn = connect()
    polywar.init_polywar_schema(conn)
    conn.close()
    yield connect
    keeper.close()


def test_first_season_and_idempotent_initialization(polydb):
    conn = polydb()
    first = polywar.ensure_active_season(); conn.commit()
    second = polywar.ensure_active_season_in_transaction(conn); conn.commit()
    polywar.init_polywar_schema(conn)
    assert first["id"] == second["id"]
    assert first["status"] == "active"
    assert "secret_seed" not in first
    conn.close()


def test_expired_season_is_completed_and_next_season_created(polydb):
    conn = polydb()
    expired_start = datetime.utcnow() - timedelta(days=31)
    expired_end = datetime.utcnow() - timedelta(minutes=1)
    conn.execute(
        "INSERT INTO polywar_seasons (name, status, starts_at, ends_at, secret_seed, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("Expired", "active", expired_start, expired_end, "server-only", expired_start),
    )
    conn.commit()
    active = polywar.ensure_active_season_in_transaction(conn); conn.commit()
    completed = conn.execute("SELECT status, completed_at FROM polywar_seasons WHERE name = 'Expired'").fetchone()
    assert active["name"] != "Expired"
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None
    conn.close()


def test_concurrent_initialization_leaves_single_active_season(polydb):
    errors = []

    def worker():
        conn = polydb()
        try:
            polywar.ensure_active_season()
        except Exception as exc:  # pragma: no cover - surfaced by assertion
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    conn = polydb()
    active_count = conn.execute("SELECT COUNT(*) FROM polywar_seasons WHERE status = 'active'").fetchone()[0]
    assert errors == []
    assert active_count == 1
    conn.close()


def test_seven_factions_and_season_stats_created(polydb):
    conn = polydb()
    factions = polywar.ensure_factions(conn)
    season = polywar.ensure_active_season_in_transaction(conn)
    with_stats = polywar.list_factions_with_stats(season["id"], conn)
    stats_count = conn.execute("SELECT COUNT(*) FROM polywar_faction_season_stats WHERE season_id = ?", (season["id"],)).fetchone()[0]
    assert len(factions) == 7
    assert len(with_stats) == 7
    assert stats_count == 7
    assert {f["slug"] for f in factions} >= {"red-dominion", "purple-empire"}
    assert "influence_score" in with_stats[0]
    conn.close()


def test_join_faction_is_atomic_and_rejects_repeat_or_unknown(polydb):
    state = polywar.join_faction(42, 1)
    assert state["selected_faction"]["name"] == "Red Dominion"
    with pytest.raises(ValueError, match="faction_already_selected"):
        polywar.join_faction(42, 2)
    with pytest.raises(ValueError, match="unknown_faction"):
        polywar.join_faction(43, 999)
    conn = polydb()
    season_id = state["season"]["id"]
    stats = conn.execute("SELECT active_members_count FROM polywar_faction_season_stats WHERE season_id = ? AND faction_id = 1", (season_id,)).fetchone()
    events = conn.execute("SELECT COUNT(*) FROM polywar_events WHERE season_id = ? AND user_id = 42", (season_id,)).fetchone()[0]
    assert stats["active_members_count"] == 1
    assert events == 1
    conn.close()


def test_two_competing_join_attempts_create_one_membership_event_and_stat(polydb):
    results = []
    errors = []

    def worker(fid):
        try:
            results.append(polywar.join_faction(77, fid)["selected_faction"]["id"])
        except ValueError as exc:
            errors.append(str(exc))

    first = threading.Thread(target=worker, args=(1,))
    second = threading.Thread(target=worker, args=(2,))
    first.start(); second.start(); first.join(); second.join()

    conn = polydb()
    player = conn.execute("SELECT faction_id, season_id FROM polywar_players WHERE user_id = 77").fetchone()
    events = conn.execute("SELECT COUNT(*) FROM polywar_events WHERE user_id = 77").fetchone()[0]
    member_sum = conn.execute("SELECT SUM(active_members_count) FROM polywar_faction_season_stats WHERE season_id = ?", (player["season_id"],)).fetchone()[0]
    assert len(results) == 1
    assert errors == ["faction_already_selected"]
    assert player["faction_id"] in {1, 2}
    assert events == 1
    assert member_sum == 1
    conn.close()


def test_energy_recovers_offline_and_caps_at_max(polydb):
    conn = polydb()
    polywar.ensure_factions(conn)
    season = polywar.ensure_active_season_in_transaction(conn)
    polywar.get_or_create_player(55, season["id"], conn)
    old = datetime.utcnow() - timedelta(hours=20)
    conn.execute(
        "UPDATE polywar_players SET current_energy = ?, max_energy = ?, energy_updated_at = ? WHERE user_id = ? AND season_id = ?",
        (2, 10, old, 55, season["id"]),
    )
    conn.commit()
    player = polywar.get_or_create_player(55, season["id"], conn)
    assert player["current_energy"] == 10
    conn.close()
    assert polywar.get_state(55)["energy"]["current_energy"] == 10


def test_secret_seed_never_in_state_and_airdrop_lifetime_is_external(polydb):
    state = polywar.get_state(77)
    assert "secret_seed" not in state["season"]
    assert "secret_seed" not in str(state)
    assert "lifetime_earned_points" not in state["player"]
    assert state["player"]["lifetime_airdrop_points"] == 1234


def test_disabled_state_is_explicit(polydb, monkeypatch):
    monkeypatch.setattr(polywar, "get_setting", lambda key, default="": "false" if key == "polywar_enabled" else default)
    state = polywar.get_state(88)
    assert state["enabled"] is False
    assert state["message"] == "PolyWar is temporarily unavailable"
