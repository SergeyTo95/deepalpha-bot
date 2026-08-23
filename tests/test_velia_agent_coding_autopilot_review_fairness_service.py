from services import velia_agent_coding_autopilot_review_fairness_service as fairness


def _no_action(run_id: str):
    return {
        "run_id": run_id,
        "status": "ready_for_review",
        "review_events_observed": 1,
    }


def _enable_worker(monkeypatch):
    monkeypatch.setattr(fairness.autopilot, "worker_enabled", lambda: True)
    monkeypatch.setattr(fairness.coding_service, "coding_enabled", lambda: True)
    monkeypatch.setattr(fairness.review_service, "review_loop_enabled", lambda: True)
    monkeypatch.setattr(
        fairness.recovery_service,
        "recover_reopened_github_not_found_once",
        lambda: None,
    )


def test_recovery_happens_before_review_poll(monkeypatch):
    _enable_worker(monkeypatch)
    calls = []
    monkeypatch.setattr(
        fairness.recovery_service,
        "recover_reopened_github_not_found_once",
        lambda: calls.append("recover")
        or {"run_id": "recovered-run", "pull_request_number": 427},
    )
    monkeypatch.setattr(
        fairness.review_service,
        "process_review_once",
        lambda: calls.append("review")
        or {"run_id": "recovered-run", "status": "waiting_ci"},
    )

    result = fairness._run_once_with_review_fairness(
        lambda: (_ for _ in ()).throw(AssertionError("ordinary worker must not run"))
    )

    assert result == [{"run_id": "recovered-run", "status": "waiting_ci"}]
    assert calls == ["recover", "review"]


def test_older_non_actionable_review_does_not_starve_newer_actionable_review(monkeypatch):
    _enable_worker(monkeypatch)
    results = iter(
        [
            _no_action("older-run"),
            {"run_id": "actionable-run", "status": "waiting_ci"},
        ]
    )
    calls = []
    monkeypatch.setattr(fairness, "max_polls_per_tick", lambda: 3)
    monkeypatch.setattr(
        fairness.review_service,
        "process_review_once",
        lambda: calls.append("review") or next(results),
    )

    def original():
        raise AssertionError("ordinary worker must not run after actionable review repair")

    result = fairness._run_once_with_review_fairness(original)

    assert result == [{"run_id": "actionable-run", "status": "waiting_ci"}]
    assert calls == ["review", "review"]


def test_bounded_non_actionable_scan_falls_through_to_original_worker(monkeypatch):
    _enable_worker(monkeypatch)
    review_calls = []
    original_calls = []
    monkeypatch.setattr(fairness, "max_polls_per_tick", lambda: 3)
    monkeypatch.setattr(
        fairness.review_service,
        "process_review_once",
        lambda: review_calls.append("review") or _no_action(f"run-{len(review_calls)}"),
    )

    def original():
        original_calls.append("original")
        return [{"status": "queued"}]

    result = fairness._run_once_with_review_fairness(original)

    assert result == [{"status": "queued"}]
    assert review_calls == ["review", "review", "review"]
    assert original_calls == ["original"]


def test_transient_github_defer_does_not_starve_original_worker(monkeypatch):
    _enable_worker(monkeypatch)
    review_calls = []
    original_calls = []
    monkeypatch.setattr(fairness, "max_polls_per_tick", lambda: 3)
    monkeypatch.setattr(
        fairness.review_service,
        "process_review_once",
        lambda: review_calls.append("review")
        or {
            "run_id": "run-1",
            "status": "ready_for_review",
            "review_poll_error": "github_unavailable",
        },
    )

    def original():
        original_calls.append("original")
        return [{"status": "waiting_ci"}]

    result = fairness._run_once_with_review_fairness(original)

    assert result == [{"status": "waiting_ci"}]
    assert review_calls == ["review"]
    assert original_calls == ["original"]


def test_empty_review_queue_falls_through_without_extra_poll(monkeypatch):
    _enable_worker(monkeypatch)
    review_calls = []
    monkeypatch.setattr(fairness, "max_polls_per_tick", lambda: 3)
    monkeypatch.setattr(
        fairness.review_service,
        "process_review_once",
        lambda: review_calls.append("review") or None,
    )

    result = fairness._run_once_with_review_fairness(
        lambda: [{"status": "executing"}]
    )

    assert result == [{"status": "executing"}]
    assert review_calls == ["review"]


def test_disabled_review_loop_calls_original_without_recovery_or_polling(monkeypatch):
    monkeypatch.setattr(fairness.autopilot, "worker_enabled", lambda: True)
    monkeypatch.setattr(fairness.coding_service, "coding_enabled", lambda: True)
    monkeypatch.setattr(fairness.review_service, "review_loop_enabled", lambda: False)
    monkeypatch.setattr(
        fairness.recovery_service,
        "recover_reopened_github_not_found_once",
        lambda: (_ for _ in ()).throw(AssertionError("recovery must not run")),
    )
    monkeypatch.setattr(
        fairness.review_service,
        "process_review_once",
        lambda: (_ for _ in ()).throw(AssertionError("review poll must not run")),
    )

    result = fairness._run_once_with_review_fairness(
        lambda: [{"status": "planning"}]
    )

    assert result == [{"status": "planning"}]


def test_poll_limit_is_bounded_by_environment(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_POLLS_PER_TICK", "999")
    assert fairness.max_polls_per_tick() == 10
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_POLLS_PER_TICK", "0")
    assert fairness.max_polls_per_tick() == 1
