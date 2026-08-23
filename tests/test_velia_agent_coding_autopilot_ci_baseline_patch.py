from __future__ import annotations

from services import velia_agent_coding_autopilot_ci_baseline_patch as baseline


def _check(name: str, conclusion: str = "success", *, status: str = "completed", source: str = "check_run"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "source": source,
        "url": f"https://example.test/{name}",
    }


def _green_attempt(*checks, number: int = 1):
    return {
        "attempt_id": f"attempt-{number}",
        "run_id": "run-1",
        "attempt_number": number,
        "head_sha": "a" * 40,
        "status": "success",
        "checks": list(checks),
    }


def test_same_run_green_baseline_ignores_only_post_baseline_check():
    previous = _green_attempt(_check("agent-core"), _check("ton-smoke"))
    observed = [
        _check("agent-core"),
        _check("ton-smoke"),
        _check("gram-treasury-withdraw", "failure"),
    ]

    effective, meta = baseline.apply_baseline_contract(observed, previous)

    assert [item["name"] for item in effective] == ["agent-core", "ton-smoke"]
    assert baseline.ci._checks_state(effective) == "success"
    assert meta == {
        "active": True,
        "baseline_attempt_number": 1,
        "required": ["agent-core", "ton-smoke"],
        "ignored": ["gram-treasury-withdraw"],
    }


def test_baseline_required_failure_still_fails_closed():
    previous = _green_attempt(_check("agent-core"), _check("ton-smoke"))
    observed = [
        _check("agent-core", "failure"),
        _check("ton-smoke"),
        _check("new-check"),
    ]

    effective, meta = baseline.apply_baseline_contract(observed, previous)

    assert meta["active"] is True
    assert baseline.ci._checks_state(effective) == "failure"


def test_missing_baseline_check_never_becomes_success():
    previous = _green_attempt(_check("agent-core"), _check("ton-smoke"))

    effective, meta = baseline.apply_baseline_contract([_check("agent-core")], previous)

    assert meta["active"] is True
    assert effective[1]["name"] == "ton-smoke"
    assert effective[1]["status"] == "missing"
    assert baseline.ci._checks_state(effective) == "pending"


def test_no_green_baseline_keeps_full_current_policy():
    observed = [_check("agent-core"), _check("new-mandatory-check", "failure")]

    effective, meta = baseline.apply_baseline_contract(observed, None)

    assert effective == observed
    assert meta["active"] is False
    assert baseline.ci._checks_state(effective) == "failure"


def test_failed_previous_attempt_is_not_a_baseline():
    previous = {
        **_green_attempt(_check("agent-core")),
        "status": "failure",
    }
    observed = [_check("agent-core"), _check("new-check", "failure")]

    effective, meta = baseline.apply_baseline_contract(observed, previous)

    assert effective == observed
    assert meta["active"] is False


def test_commit_status_source_is_part_of_baseline_identity():
    previous = _green_attempt(
        _check("railway", source="commit_status"),
        number=1,
    )
    observed = [
        _check("railway", "failure", source="check_run"),
        _check("railway", source="commit_status"),
    ]

    effective, meta = baseline.apply_baseline_contract(observed, previous)

    assert len(effective) == 1
    assert effective[0]["source"] == "commit_status"
    assert baseline.ci._checks_state(effective) == "success"
    assert meta["ignored"] == ["railway"]


def test_recovery_requeues_only_when_exact_head_baseline_is_green(monkeypatch):
    run = {
        "run_id": "run-1",
        "task_id": "task-1",
        "user_id": 7,
        "work_branch": "velia/review",
    }
    attempt = {
        "attempt_id": "attempt-2",
        "run_id": "run-1",
        "attempt_number": 2,
        "head_sha": "b" * 40,
        "status": "failure",
    }
    previous = _green_attempt(_check("agent-core"), _check("ton-smoke"), number=1)
    captured = []

    monkeypatch.setattr(baseline.ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(baseline, "_blocked_candidate", lambda: run)
    monkeypatch.setattr(baseline.ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(baseline, "_previous_successful_attempt", lambda *args: previous)
    monkeypatch.setattr(baseline.ci, "_project_and_mission", lambda value: ({"id": "p"}, {}))
    monkeypatch.setattr(
        baseline.ci.write_service,
        "branch_head",
        lambda project, branch: {"branch": branch, "sha": "b" * 40},
    )
    monkeypatch.setattr(
        baseline,
        "_requeue_blocked_attempt",
        lambda value, current, checks, meta: captured.append((checks, meta)) or {**run, "status": "waiting_ci"},
    )

    result = baseline.recover_blocked_baseline_once(
        lambda project, sha: {
            "checks": [
                _check("agent-core"),
                _check("ton-smoke"),
                _check("gram-treasury-withdraw", "failure"),
            ]
        }
    )

    assert result["status"] == "waiting_ci"
    assert [item["name"] for item in captured[0][0]] == ["agent-core", "ton-smoke"]
    assert captured[0][1]["ignored"] == ["gram-treasury-withdraw"]


def test_recovery_does_not_requeue_when_required_baseline_check_fails(monkeypatch):
    run = {"run_id": "run-1", "user_id": 7, "work_branch": "velia/review"}
    attempt = {
        "attempt_id": "attempt-2",
        "run_id": "run-1",
        "attempt_number": 2,
        "head_sha": "b" * 40,
        "status": "failure",
    }
    previous = _green_attempt(_check("agent-core"), number=1)

    monkeypatch.setattr(baseline.ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(baseline, "_blocked_candidate", lambda: run)
    monkeypatch.setattr(baseline.ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(baseline, "_previous_successful_attempt", lambda *args: previous)
    monkeypatch.setattr(baseline.ci, "_project_and_mission", lambda value: ({"id": "p"}, {}))
    monkeypatch.setattr(
        baseline.ci.write_service,
        "branch_head",
        lambda project, branch: {"branch": branch, "sha": "b" * 40},
    )
    monkeypatch.setattr(
        baseline,
        "_requeue_blocked_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not requeue")),
    )

    result = baseline.recover_blocked_baseline_once(
        lambda project, sha: {"checks": [_check("agent-core", "failure"), _check("new-check")]}
    )

    assert result is None


def test_recovery_does_not_requeue_on_branch_head_drift(monkeypatch):
    run = {"run_id": "run-1", "user_id": 7, "work_branch": "velia/review"}
    attempt = {
        "attempt_id": "attempt-2",
        "run_id": "run-1",
        "attempt_number": 2,
        "head_sha": "b" * 40,
        "status": "failure",
    }
    previous = _green_attempt(_check("agent-core"), number=1)

    monkeypatch.setattr(baseline.ci, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(baseline, "_blocked_candidate", lambda: run)
    monkeypatch.setattr(baseline.ci, "_current_attempt", lambda run_id: attempt)
    monkeypatch.setattr(baseline, "_previous_successful_attempt", lambda *args: previous)
    monkeypatch.setattr(baseline.ci, "_project_and_mission", lambda value: ({"id": "p"}, {}))
    monkeypatch.setattr(
        baseline.ci.write_service,
        "branch_head",
        lambda project, branch: {"branch": branch, "sha": "c" * 40},
    )

    assert baseline.recover_blocked_baseline_once(lambda *args: {"checks": []}) is None
