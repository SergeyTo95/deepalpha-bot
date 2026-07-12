import logging
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.polywar_service as polywar
import services.polywar_map_service as map_rules
import services.polywar_sector_service as sectors
import services.polywar_capital_service as capitals
import services.polywar_world_service as world


class CountingConnection:
    def __init__(self, inner, counts):
        self.inner = inner
        self.counts = counts
    def cursor(self):
        return CountingCursor(self.inner.cursor(), self.counts)
    def __getattr__(self, name):
        return getattr(self.inner, name)


class CountingCursor:
    def __init__(self, inner, counts):
        self.inner = inner
        self.counts = counts
    def execute(self, sql, params=()):
        self.counts.append(str(sql))
        return self.inner.execute(sql, params)
    def __iter__(self):
        return iter(self.inner)
    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture()
def polydb(monkeypatch):
    uri = f"file:polywar_bootstrap_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=20)
    keeper.row_factory = sqlite3.Row
    settings = {
        "polywar_map_width": "10000",
        "polywar_map_height": "10000",
        "polywar_starting_area_size": "15",
        "polywar_sector_size": "100",
        "polywar_enabled": "true",
    }
    def connect(counts=None):
        c = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=20)
        c.row_factory = sqlite3.Row
        return CountingConnection(c, counts) if counts is not None else c
    monkeypatch.setattr(polywar, "get_connection", lambda: connect())
    monkeypatch.setattr(polywar, "get_setting", lambda k, d="": settings.get(k, d))
    monkeypatch.setattr(polywar, "get_airdrop_points_balance", lambda uid: {"total": 0, "balance": 0})
    c = connect(); polywar.init_polywar_schema(c); map_rules.init_polywar_map_schema(c); capitals.init_polywar_capital_schema(c); world.init_world_schema(c); c.close()
    yield connect, settings
    keeper.close()


def _season(connect):
    c = connect()
    tx = polywar.begin_serialized_transaction(c)
    season = polywar.ensure_active_season_in_transaction(c)
    c.commit(); c.close()
    return int(season["id"])


def test_first_bootstrap_counts_and_bounded_sql(polydb, monkeypatch):
    connect, _ = polydb; sid = _season(connect); counts = []
    calls = {"n": 0}
    def fail_start_owner(x, y):
        calls["n"] += 1
        raise AssertionError("_start_owner must not be used by starting bootstrap")
    monkeypatch.setattr(map_rules, "_start_owner", fail_start_owner)
    c = connect(counts); t0 = time.monotonic(); assert sectors.ensure_starting_territories_bootstrap(c, sid) is True; c.commit(); elapsed = time.monotonic() - t0
    rows = c.inner.execute("select faction_id,controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id between 1 and 7", (sid,)).fetchall()
    assert {int(r["faction_id"]): int(r["controlled_cells_count"]) for r in rows} == {i: 225 for i in range(1, 8)}
    owner_selects = [q for q in counts if "FROM polywar_cells WHERE season_id" in q and "x=%s" in q and "y=%s" in q]
    assert owner_selects == []
    sector_cell_queries = [q for q in counts if "SELECT x,y,owner_faction_id FROM polywar_cells" in q]
    assert len(sector_cell_queries) < 150
    assert calls["n"] == 0
    assert elapsed < 5
    c.close()


def test_materialized_override_adjusts_counts(polydb):
    connect, _ = polydb; sid = _season(connect); bx, by = map_rules.faction_base_positions()[1]
    c = connect(); c.execute("insert into polywar_cells (season_id,x,y,owner_faction_id) values (?,?,?,?)", (sid, bx, by, 2)); c.commit()
    sectors.ensure_starting_territories_bootstrap(c, sid); c.commit()
    rows = {r["faction_id"]: r["controlled_cells_count"] for r in c.execute("select faction_id,controlled_cells_count from polywar_faction_season_stats where season_id=? and faction_id in (1,2)", (sid,))}
    assert rows[1] == 224 and rows[2] == 225
    stat = c.execute("select controlled_cells_count from polywar_sector_faction_stats where season_id=? and faction_id=2", (sid,)).fetchone()
    assert stat[0] >= 1
    c.close()


def test_repeat_bootstrap_noop_does_not_double_counts(polydb):
    connect, _ = polydb; sid = _season(connect); c = connect()
    assert sectors.ensure_starting_territories_bootstrap(c, sid) is True; c.commit()
    first = c.execute("select sum(controlled_cells_count) from polywar_faction_season_stats where season_id=?", (sid,)).fetchone()[0]
    assert sectors.ensure_starting_territories_bootstrap(c, sid) is False; c.commit()
    second = c.execute("select sum(controlled_cells_count) from polywar_faction_season_stats where season_id=?", (sid,)).fetchone()[0]
    assert first == second == 1575
    assert c.execute("select count(*) from polywar_sector_initializations where season_id=? and sector_x=-1 and sector_y=-1", (sid,)).fetchone()[0] == 1
    c.close()


def test_concurrent_first_callers_finish_once(polydb):
    connect, _ = polydb; sid = _season(connect); errors = []
    def worker():
        c = connect()
        try:
            sectors.ensure_starting_territories_bootstrap(c, sid); c.commit()
        except Exception as exc:
            errors.append(str(exc))
        finally:
            c.close()
    ts = [threading.Thread(target=worker), threading.Thread(target=worker)]
    [t.start() for t in ts]; [t.join() for t in ts]
    c = connect()
    assert errors == []
    assert c.execute("select count(*) from polywar_sector_initializations where season_id=? and sector_x=-1 and sector_y=-1", (sid,)).fetchone()[0] == 1
    assert c.execute("select sum(controlled_cells_count) from polywar_faction_season_stats where season_id=?", (sid,)).fetchone()[0] == 1575
    c.close()


def test_get_state_first_request_finishes(polydb):
    t0 = time.monotonic(); state = polywar.get_state(7100); elapsed = time.monotonic() - t0
    assert state["ok"] is True
    assert elapsed < 10


def test_frontend_timeout_static_contract():
    js = Path("webapp/polywar.js").read_text()
    assert "AbortController" in js
    assert "request_timeout" in js
    assert "PolyWar is taking too long to initialize. Please retry." in js
    assert "clearTimeout(timer)" in js


def test_world_initialized_info_only_on_real_initialization(polydb, caplog):
    connect, _ = polydb; sid = _season(connect); c = connect()
    caplog.set_level(logging.INFO, logger="services.polywar_world_service")
    # Reset world rows because _season only creates the season.
    world.ensure_world_initialized_in_transaction(c, sid)
    world.ensure_world_initialized_in_transaction(c, sid)
    assert [r for r in caplog.records if r.message.startswith("polywar_world_initialized season_id=")] and len([r for r in caplog.records if r.message.startswith("polywar_world_initialized season_id=")]) == 1
    c.close()
