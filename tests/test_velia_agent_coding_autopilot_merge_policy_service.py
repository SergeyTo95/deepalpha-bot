from services import velia_agent_coding_autopilot_merge_policy_service as policy


def _run():
    return {
        "run_id": "run-1",
        "user_id": 7,
        "mission_id": "mission-1",
        "project_id": "project-1",
        "status": "ready_for_review",
        "work_branch": "velia/docs-test",
        "pull_request_number": 42,
    }


def _mission():
    return {
        "mission_id": "mission-1",
        "mode": "draft_pr_only",
        "base_branch": "feature/turbo-short-term-btc",
        "allowed_paths": ["docs"],
        "blocked_paths": [],
        "max_steps": 2,
        "max_files": 2,
    }


def _snapshot(*, draft=False, files=None, reviews=None):
    return {
        "number": 42,
        "state": "open",
        "draft": draft,
        "mergeable": True,
        "mergeable_state": "clean",
        "base_ref": "feature/turbo-short-term-btc",
        "head_ref": "velia/docs-test",
        "head_sha": "a" * 40,
        "files": files
        if files is not None
        else [
            {
                "filename": "docs/guide.md",
                "previous_filename": "",
                "status": "modified",
                "additions": 8,
                "deletions": 2,
                "changes": 10,
                "patch_present": True,
            }
        ],
        "reviews": reviews
        if reviews is not None
        else [
            {
                "kind": "review",
                "review_id": 9,
                "author_login": "reviewer",
                "state": "APPROVED",
            }
        ],
    }


def _install_common(monkeypatch, *, snapshot=None, attempt=None, persisted=None):
    run = _run()
    mission = _mission()
    monkeypatch.setattr(policy, "merge_policy_enabled", lambda: True)
    monkeypatch.setattr(policy.autopilot, "get_run", lambda user_id, run_id: run)
    monkeypatch.setattr(
        policy.ci_service,
        "_project_and_mission",
        lambda value: ({"repository_full_name": "owner/repo"}, mission),
    )
    monkeypatch.setattr(
        policy.ci_service,
        "_coding_job",
        lambda value: {"goal": "Update docs", "plan": {"steps": []}},
    )
    monkeypatch.setattr(
        policy.ci_service,
        "_allowed_repair_files",
        lambda job, value: ["docs/guide.md"],
    )
    monkeypatch.setattr(
        policy.merge_github,
        "pull_snapshot",
        lambda project, number: snapshot or _snapshot(),
    )
    monkeypatch.setattr(
        policy.write_service,
        "branch_head",
        lambda project, branch: {"sha": "a" * 40},
    )
    monkeypatch.setattr(
        policy.ci_service,
        "_current_attempt",
        lambda run_id: attempt
        if attempt is not None
        else {"status": "success", "head_sha": "a" * 40, "attempt_number": 0},
    )
    monkeypatch.setattr(
        policy.review_store,
        "list_review_actions",
        lambda user_id, run_id: persisted or [],
    )
    return run


def test_clean_approved_exact_head_run_is_eligible(monkeypatch):
    _install_common(monkeypatch)

    result = policy.evaluate_merge_policy(7, "run-1")

    assert result["recommendation"] == "eligible"
    assert result["would_allow_merge"] is True
    assert result["execution_supported"] is False
    assert result["auto_merge"] is False
    assert result["deployment"] is False
    assert result["reasons"] == []


def test_clean_draft_is_only_ready_to_mark_ready(monkeypatch):
    _install_common(monkeypatch, snapshot=_snapshot(draft=True))

    result = policy.evaluate_merge_policy(7, "run-1")

    assert result["recommendation"] == "ready_to_mark_ready"
    assert result["would_allow_merge"] is False
    assert [item["code"] for item in result["reasons"]] == [
        "merge_policy_pull_request_is_draft"
    ]


def test_outside_scope_and_deletion_fail_closed(monkeypatch):
    snapshot = _snapshot(
        files=[
            {
                "filename": "auth/session.py",
                "previous_filename": "",
                "status": "removed",
                "additions": 0,
                "deletions": 20,
                "changes": 20,
                "patch_present": True,
            }
        ]
    )
    _install_common(monkeypatch, snapshot=snapshot)

    result = policy.evaluate_merge_policy(7, "run-1")
    codes = {item["code"] for item in result["reasons"]}

    assert result["recommendation"] == "not_ready"
    assert result["would_allow_merge"] is False
    assert "merge_policy_file_not_in_approved_plan" in codes
    assert "merge_policy_path_denied" in codes
    assert "merge_policy_deletion_not_allowed" in codes


def test_requested_changes_and_stale_ci_block_recommendation(monkeypatch):
    snapshot = _snapshot(
        reviews=[
            {
                "kind": "review",
                "review_id": 10,
                "author_login": "reviewer",
                "state": "CHANGES_REQUESTED",
            }
        ]
    )
    _install_common(
        monkeypatch,
        snapshot=snapshot,
        attempt={"status": "success", "head_sha": "b" * 40, "attempt_number": 0},
    )

    result = policy.evaluate_merge_policy(7, "run-1")
    codes = {item["code"] for item in result["reasons"]}

    assert result["recommendation"] == "not_ready"
    assert "merge_policy_ci_head_stale" in codes
    assert "merge_policy_changes_requested" in codes
    assert "merge_policy_approval_required" in codes


def test_unresolved_persisted_review_action_blocks(monkeypatch):
    _install_common(
        monkeypatch,
        persisted=[{"status": "actionable", "review_key": "review:11"}],
    )

    result = policy.evaluate_merge_policy(7, "run-1")

    assert result["recommendation"] == "not_ready"
    assert "merge_policy_review_actions_unresolved" in {
        item["code"] for item in result["reasons"]
    }
