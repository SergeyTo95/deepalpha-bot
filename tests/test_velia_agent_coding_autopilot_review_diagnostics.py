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


def test_recent_merge_policy_targets_are_select_only_and_bounded(monkeypatch):
    connection = _Connection([("run-427", 123456, 427)])
    monkeypatch.setattr(diagnostics, "get_connection", lambda: connection)

    result = diagnostics.recent_merge_policy_targets(999)

    assert result == [{"run_id": "run-427", "user_id": 123456, "pr": 427}]
    assert connection.cursor_obj.params == (3,)
    assert connection.cursor_obj.sql.lstrip().startswith("SELECT")
    upper_sql = connection.cursor_obj.sql.upper()
    assert "INSERT " not in upper_sql
    assert "UPDATE " not in upper_sql
    assert "DELETE " not in upper_sql
    assert connection.cursor_obj.closed is True
    assert connection.closed is True


def test_safe_merge_policy_result_does_not_expose_user_or_review_authors():
    safe = diagnostics._safe_merge_policy_result(
        {
            "run_id": "run-427",
            "mode": "dry_run",
            "execution_supported": False,
            "auto_merge": False,
            "deployment": False,
            "recommendation": "not_ready",
            "would_allow_merge": False,
            "reasons": [{"code": "merge_policy_approval_required", "detail": "secret-user"}],
            "gates": {
                "run_status": "ready_for_review",
                "branch_head": "abc",
                "ci_attempt": {"attempt_number": 2, "status": "success", "head_sha": "abc"},
                "requested_changes": ["reviewer-login"],
                "unresolved_review_actions": 0,
                "diff": {"file_count": 1, "additions": 1, "deletions": 1, "changes": 2},
                "pull_request": {
                    "number": 427,
                    "state": "open",
                    "draft": True,
                    "mergeable": True,
                    "mergeable_state": "clean",
                    "base_ref": "main",
                    "head_ref": "velia/test",
                    "head_sha": "abc",
                },
            },
        },
        427,
    )

    assert safe["mode"] == "dry_run"
    assert safe["execution_supported"] is False
    assert safe["requested_changes_count"] == 1
    assert safe["reason_codes"] == ["merge_policy_approval_required"]
    rendered = str(safe)
    assert "secret-user" not in rendered
    assert "reviewer-login" not in rendered
    assert "user_id" not in rendered


def test_log_merge_policy_snapshot_executes_real_evaluator_contract(monkeypatch, caplog):
    monkeypatch.setattr(diagnostics.merge_policy, "merge_policy_enabled", lambda: True)
    monkeypatch.setattr(
        diagnostics,
        "recent_merge_policy_targets",
        lambda: [{"run_id": "run-427", "user_id": 987654, "pr": 427}],
    )
    calls = []

    def evaluate(user_id, run_id):
        calls.append((user_id, run_id))
        return {
            "run_id": run_id,
            "mode": "dry_run",
            "execution_supported": False,
            "auto_merge": False,
            "deployment": False,
            "recommendation": "not_ready",
            "would_allow_merge": False,
            "reasons": [{"code": "merge_policy_pull_request_is_draft", "detail": ""}],
            "gates": {
                "run_status": "ready_for_review",
                "branch_head": "abc",
                "ci_attempt": {"attempt_number": 2, "status": "success", "head_sha": "abc"},
                "requested_changes": [],
                "unresolved_review_actions": 0,
                "diff": {"file_count": 1, "additions": 1, "deletions": 1, "changes": 2},
                "pull_request": {"number": 427, "state": "open", "draft": True},
            },
        }

    monkeypatch.setattr(diagnostics.merge_policy, "evaluate_merge_policy", evaluate)
    with caplog.at_level(logging.INFO):
        diagnostics.log_merge_policy_dry_run_snapshot()

    assert calls == [(987654, "run-427")]
    assert "VELIA_AUTOPILOT_MERGE_POLICY_DRY_RUN_SNAPSHOT enabled=True" in caplog.text
    assert '"mode":"dry_run"' in caplog.text
    assert "987654" not in caplog.text


def test_log_runtime_snapshot_is_fail_open(monkeypatch, caplog):
    monkeypatch.setattr(
        diagnostics,
        "recent_review_run_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )
    monkeypatch.setattr(diagnostics, "log_merge_policy_dry_run_snapshot", lambda: None)

    with caplog.at_level(logging.WARNING):
        diagnostics.log_runtime_snapshot()

    assert "VELIA_AUTOPILOT_REVIEW_RUNTIME_SNAPSHOT_FAILED" in caplog.text
    assert "RuntimeError" in caplog.text
