from services import velia_agent_coding_autopilot_review_service as review


def _run():
    return {
        "run_id": "run-1",
        "user_id": 7,
        "mission_id": "mission-1",
        "project_id": "project-1",
        "pull_request_number": 42,
        "work_branch": "velia/review-test",
        "status": "ready_for_review",
        "result": {},
    }


def test_ordinary_comments_never_trigger_code_repair(monkeypatch):
    run = _run()
    events = [
        {
            "review_key": "issue_comment:1",
            "review_id": 0,
            "kind": "issue_comment",
            "state": "COMMENTED",
            "author_login": "reviewer",
            "body": "Could you explain this?",
            "comments": [],
        }
    ]
    calls = []
    monkeypatch.setattr(review, "review_loop_enabled", lambda: True)
    monkeypatch.setattr(review.review_store, "claim_ready_run", lambda: run)
    monkeypatch.setattr(review.ci_service, "_project_and_mission", lambda value: ({}, {}))
    monkeypatch.setattr(review.review_github, "list_review_evidence", lambda project, pr: events)
    monkeypatch.setattr(review.review_store, "observe_review_events", lambda value, items: calls.append("observe"))
    monkeypatch.setattr(review.review_store, "next_actionable", lambda run_id: None)
    monkeypatch.setattr(review.review_store, "defer_review_poll", lambda run_id: calls.append("defer"))
    monkeypatch.setattr(
        review,
        "_execute_review_repair",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("repair must not run")),
    )

    result = review.process_review_once()

    assert result["status"] == "ready_for_review"
    assert calls == ["observe", "defer"]


def test_explicit_request_changes_is_the_only_actionable_write_path(monkeypatch):
    run = _run()
    action = {
        "action_id": "action-1",
        "review_key": "review:99",
        "review_id": 99,
        "state": "CHANGES_REQUESTED",
        "status": "actionable",
        "body": "Update the wording.",
        "comments": [],
    }
    events = [{**action, "kind": "review", "author_login": "reviewer"}]
    monkeypatch.setattr(review, "review_loop_enabled", lambda: True)
    monkeypatch.setattr(review.review_store, "claim_ready_run", lambda: run)
    monkeypatch.setattr(review.ci_service, "_project_and_mission", lambda value: ({}, {}))
    monkeypatch.setattr(review.review_github, "list_review_evidence", lambda project, pr: events)
    monkeypatch.setattr(review.review_store, "observe_review_events", lambda value, items: None)
    monkeypatch.setattr(review.review_store, "next_actionable", lambda run_id: action)
    monkeypatch.setattr(
        review,
        "_execute_review_repair",
        lambda value, selected, evidence: {
            **value,
            "status": "waiting_ci",
            "review_key": selected["review_key"],
            "evidence_state": evidence["state"],
        },
    )

    result = review.process_review_once()

    assert result["status"] == "waiting_ci"
    assert result["review_key"] == "review:99"
    assert result["evidence_state"] == "CHANGES_REQUESTED"


def test_inline_request_outside_approved_scope_fails_closed():
    evidence = {
        "comments": [
            {"path": "auth/session.py", "body": "Change this too."},
        ]
    }

    try:
        review._validate_review_scope(evidence, ["docs/guide.md"])
    except review.CodingAutopilotReviewError as exc:
        assert exc.code == "velia_coding_autopilot_review_outside_approved_scope"
    else:
        raise AssertionError("outside-scope review must be rejected")


def test_transient_github_failure_defers_without_blocking(monkeypatch):
    run = _run()
    calls = []
    monkeypatch.setattr(review, "review_loop_enabled", lambda: True)
    monkeypatch.setattr(review.review_store, "claim_ready_run", lambda: run)
    monkeypatch.setattr(review.ci_service, "_project_and_mission", lambda value: ({}, {}))
    monkeypatch.setattr(
        review.review_github,
        "list_review_evidence",
        lambda project, pr: (_ for _ in ()).throw(
            review.review_github.CodingAutopilotReviewGithubError("github_unavailable")
        ),
    )
    monkeypatch.setattr(review.review_store, "defer_review_poll", lambda run_id: calls.append(run_id))
    monkeypatch.setattr(
        review,
        "_block_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("transient failure must not block")),
    )

    result = review.process_review_once()

    assert result["status"] == "ready_for_review"
    assert result["review_poll_error"] == "github_unavailable"
    assert calls == ["run-1"]
