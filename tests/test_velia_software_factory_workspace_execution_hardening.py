from datetime import datetime
from types import SimpleNamespace

import pytest

from services.velia_software_factory_core_service import SoftwareFactoryError
from services import velia_software_factory_workspace_execution_hardening_patch as hardening


class _Cursor:
    def __init__(self, execution_rowcount=1, mission_rowcount=2):
        self.calls = []
        self.rowcount = 0
        self._execution_rowcount = execution_rowcount
        self._mission_rowcount = mission_rowcount

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "UPDATE velia_software_factory_workspace_executions" in sql:
            self.rowcount = self._execution_rowcount
        elif "UPDATE velia_developer_autopilot_missions" in sql:
            self.rowcount = self._mission_rowcount
        else:
            self.rowcount = 1

    def close(self):
        pass


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _module(cursor):
    conn = _Connection(cursor)
    events = []
    module = SimpleNamespace(
        get_connection=lambda: conn,
        _utcnow=lambda: datetime(2026, 8, 23, 11, 0, 0),
        _json=lambda value: "{}",
        _append_event=lambda cur, execution_id, user_id, event_type, payload: events.append(
            (execution_id, user_id, event_type, payload)
        ),
    )
    return module, conn, events


def test_review_ready_archives_only_execution_owned_missions_atomically():
    cursor = _Cursor(execution_rowcount=1, mission_rowcount=2)
    module, conn, events = _module(cursor)

    hardening._set_terminal_state_and_archive_missions(
        module,
        "execution-1",
        7,
        "review_ready",
        {},
    )

    assert conn.committed is True
    assert conn.rolled_back is False
    assert len(cursor.calls) == 2
    mission_sql, mission_params = cursor.calls[1]
    assert "SET status='archived'" in mission_sql
    assert "velia_software_factory_workspace_execution_missions" in mission_sql
    assert mission_params[1:] == (7, "execution-1", 7)
    assert events == [
        (
            "execution-1",
            7,
            "workspace_missions.archived",
            {"terminal_state": "review_ready", "mission_count": 2},
        )
    ]


def test_terminal_archive_rolls_back_when_execution_state_conflicts():
    cursor = _Cursor(execution_rowcount=0, mission_rowcount=2)
    module, conn, events = _module(cursor)

    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._set_terminal_state_and_archive_missions(
            module,
            "execution-1",
            7,
            "cancelled",
            {},
        )
    assert exc.value.code == "velia_factory_workspace_execution_state_conflict"
    assert conn.committed is False
    assert conn.rolled_back is True
    assert len(cursor.calls) == 1
    assert events == []


def test_terminal_archive_rejects_non_terminal_state():
    cursor = _Cursor()
    module, conn, _ = _module(cursor)
    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._set_terminal_state_and_archive_missions(module, "execution-1", 7, "blocked", {})
    assert exc.value.code == "velia_factory_workspace_terminal_state_invalid"
    assert conn.committed is False
    assert cursor.calls == []
