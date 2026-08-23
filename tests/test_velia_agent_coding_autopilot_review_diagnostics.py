import logging

from services import velia_agent_coding_autopilot_review_diagnostics as diagnostics


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = None
        self.closed = False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, rows):
        self.cursor_obj = _Cursor(rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_recent_review_run_snapshot_is_read_only_and_bounded(monkeypatch):
    row = (
        "run-1",
        "blocked",
        "velia_coding_autopilot_review_cost_limit",
        427,
        "2026-08-23T05:00:00Z",
        1,
        "e5b65cbe36aa5b4731f2e9fdb500f700e7968a19",
        "success",
        None,
        3348921530,
        "CHANGES_REQUESTED",
        "blocked",
        "velia_coding_autopilot_review_cost_limit",
        "",
        "2026-08-23T05:00:01Z",
    )
    connection = _Connection([row])
    monkeypatch.setattr(diagnostics, "get_connection", lambda: connection)

    result = diagnostics.recent_review_run_snapshot(999)

    assert result[0]["pr"] == 427
    assert result[0]["run_status"] == "blocked"
    assert result[0]["ci_attempt"] == 1
    assert result[0]["review_state"] == "CHANGES_REQUESTED"
    assert result[0]["review_status"] == "blocked"
    assert connection.cursor_obj.params == (50,)
    assert connection.cursor_obj.sql.lstrip().startswith("SELECT")
    upper_sql = connection.cursor_obj.sql.upper()
    assert "INSERT " not in upper_sql
    assert "UPDATE " not in upper_sql
    assert "DELETE " not in upper_sql
    assert connection.cursor_obj.closed is True
    assert connection.closed is True


def test_log_runtime_snapshot_is_fail_open(monkeypatch, caplog):
    monkeypatch.setattr(
        diagnostics,
        "recent_review_run_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    with caplog.at_level(logging.WARNING):
        diagnostics.log_runtime_snapshot()

    assert "VELIA_AUTOPILOT_REVIEW_RUNTIME_SNAPSHOT_FAILED" in caplog.text
    assert "RuntimeError" in caplog.text
