from services import velia_agent_coding_autopilot_review_recovery_service as recovery


def _run():
    return {
        "run_id": "run-427",
        "task_id": "task-427",
        "mission_id": "mission-1",
        "user_id": 7,
        "project_id": "project-1",
        "status": "blocked",
        "work_branch": "velia/smoke",
        "pull_request_number": 427,
        "error_code": "github_not_found",
        "result": {"existing": True},
    }


def _project():
    return {"repository_full_name": "SergeyTo95/deepalpha-bot"}


def _job():
    return {"base_branch": "feature/turbo-short-term-btc"}


def _attempt(status="success", head="abc123"):
    return {"attempt_number": 1, "status": status, "head_sha": head}


def _pull(**overrides):
    value = {
        "number": 427,
        "state": "open",
        "draft": True,
        "head_ref": "velia/smoke",
        "head_sha": "abc123",
        "head_repo": "SergeyTo95/deepalpha-bot",
        "base_ref": "feature/turbo-short-term-btc",
        "merged": False,
    }
    value.update(overrides)
    return value


def test_exact_open_pr_with_green_exact_head_is_recoverable(monkeypatch):
    monkeypatch.setattr(recovery.review_store, "addressed_count", lambda _run_id: 0)
    monkeypatch.setattr(recovery.review_service, "_env_int", lambda *args, **kwargs: 2)

    reason = recovery._recovery_reason(
        _run(), _project(), _job(), _attempt(), _pull(), "abc123"
    )

    assert reason == ""


def test_recovery_reason_fails_closed_on_pr_and_head_drift(monkeypatch):
    monkeypatch.setattr(recovery.review_store, "addressed_count", lambda _run_id: 0)
    monkeypatch.setattr(recovery.review_service, "_env_int", lambda *args, **kwargs: 2)

    cases = [
        (_attempt(status="failure"), _pull(), "abc123", "ci_not_green"),
        (_attempt(), _pull(), "different", "branch_head_drift"),
        (_attempt(), _pull(state="closed"), "abc123", "pr_not_open"),
        (_attempt(), _pull(merged=True), "abc123", "pr_not_open"),
        (_attempt(), _pull(head_ref="velia/other"), "abc123", "pr_head_branch_mismatch"),
        (_attempt(), _pull(head_sha="different"), "abc123", "pr_head_sha_mismatch"),
        (_attempt(), _pull(base_ref="main"), "abc123", "pr_base_mismatch"),
        (_attempt(), _pull(head_repo="other/repo"), "abc123", "pr_repository_mismatch"),
    ]

    for attempt, pull, branch_sha, expected in cases:
        assert (
            recovery._recovery_reason(
                _run(), _project(), _job(), attempt, pull, branch_sha
            )
            == expected
        )


def test_recovery_reason_fails_closed_when_review_budget_exhausted(monkeypatch):
    monkeypatch.setattr(recovery.review_store, "addressed_count", lambda _run_id: 2)
    monkeypatch.setattr(recovery.review_service, "_env_int", lambda *args, **kwargs: 2)

    assert (
        recovery._recovery_reason(
            _run(), _project(), _job(), _attempt(), _pull(), "abc123"
        )
        == "review_actions_exhausted"
    )


def test_successful_recovery_is_state_only_then_returns_ready_run(monkeypatch):
    run = _run()
    restored = {**run, "status": "ready_for_review", "error_code": None}
    calls = []
    candidates = iter([run])
    monkeypatch.setattr(recovery.review_service, "review_loop_enabled", lambda: True)
    monkeypatch.setattr(recovery, "_claim_candidate", lambda: next(candidates))
    monkeypatch.setattr(
        recovery.ci_service,
        "_project_and_mission",
        lambda _run: (_project(), {"mission_id": "mission-1"}),
    )
    monkeypatch.setattr(recovery.ci_service, "_coding_job", lambda _run: _job())
    monkeypatch.setattr(recovery.ci_service, "_current_attempt", lambda _run_id: _attempt())
    monkeypatch.setattr(
        recovery.write_service,
        "branch_head",
        lambda project, branch: {"branch": branch, "sha": "abc123"},
    )
    monkeypatch.setattr(recovery, "_pull_metadata", lambda project, pr: _pull())
    monkeypatch.setattr(recovery.review_store, "addressed_count", lambda _run_id: 0)
    monkeypatch.setattr(recovery.review_service, "_env_int", lambda *args, **kwargs: 2)
    monkeypatch.setattr(
        recovery,
        "_restore_ready_for_review",
        lambda candidate: calls.append(("restore", candidate["run_id"])) or restored,
    )
    monkeypatch.setattr(
        recovery.autopilot,
        "_record_event",
        lambda candidate, event, payload: calls.append(
            ("event", event, payload["pull_request_number"])
        ),
    )

    result = recovery.recover_reopened_github_not_found_once()

    assert result["status"] == "ready_for_review"
    assert calls == [
        ("restore", "run-427"),
        ("event", "review_run_recovered", 427),
    ]


def test_github_404_never_restores_run(monkeypatch):
    run = _run()
    candidates = iter([run, None])
    restored = []
    monkeypatch.setattr(recovery.review_service, "review_loop_enabled", lambda: True)
    monkeypatch.setattr(recovery, "max_candidates_per_tick", lambda: 2)
    monkeypatch.setattr(recovery, "_claim_candidate", lambda: next(candidates))
    monkeypatch.setattr(
        recovery.ci_service,
        "_project_and_mission",
        lambda _run: (_project(), {"mission_id": "mission-1"}),
    )
    monkeypatch.setattr(recovery.ci_service, "_coding_job", lambda _run: _job())
    monkeypatch.setattr(recovery.ci_service, "_current_attempt", lambda _run_id: _attempt())
    monkeypatch.setattr(
        recovery.write_service,
        "branch_head",
        lambda project, branch: {"branch": branch, "sha": "abc123"},
    )
    monkeypatch.setattr(
        recovery,
        "_pull_metadata",
        lambda project, pr: (_ for _ in ()).throw(
            recovery.review_github.CodingAutopilotReviewGithubError(
                "github_not_found", status=404
            )
        ),
    )
    monkeypatch.setattr(
        recovery,
        "_restore_ready_for_review",
        lambda candidate: restored.append(candidate) or candidate,
    )

    result = recovery.recover_reopened_github_not_found_once()

    assert result is None
    assert restored == []


def test_recovery_candidate_limit_is_hard_capped(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_REVIEW_RECOVERY_MAX_PER_TICK", "999")
    assert recovery.max_candidates_per_tick() == 5
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_REVIEW_RECOVERY_MAX_PER_TICK", "0")
    assert recovery.max_candidates_per_tick() == 1


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executions = []
        self.closed = False

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, rows):
        self.cursor_obj = _Cursor(rows)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_restore_transition_is_atomically_guarded(monkeypatch):
    restored_row = {
        **_run(),
        "status": "ready_for_review",
        "error_code": None,
        "claimed_by": "",
    }
    connection = _Connection([restored_row])
    monkeypatch.setattr(recovery, "get_connection", lambda: connection)

    result = recovery._restore_ready_for_review(_run())

    assert result["status"] == "ready_for_review"
    run_sql, run_params = connection.cursor_obj.executions[0]
    task_sql, _task_params = connection.cursor_obj.executions[1]
    normalized = " ".join(run_sql.split())
    assert "status='blocked'" in normalized
    assert "error_code=%s" in normalized
    assert "claimed_by LIKE 'review:recovery:%%'" in normalized
    assert run_params[-1] == "github_not_found"
    assert "UPDATE velia_developer_autopilot_tasks" in task_sql
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.cursor_obj.closed is True
    assert connection.closed is True
