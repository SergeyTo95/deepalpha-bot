from pathlib import Path


def test_merge_policy_routes_are_read_only_and_installed_before_worker():
    agent_routes = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")
    routes = Path(
        "services/velia_agent_coding_autopilot_merge_policy_routes.py"
    ).read_text(encoding="utf-8")

    merge_index = agent_routes.index(
        "setup_velia_coding_autopilot_merge_policy_routes(app, routes_module)"
    )
    worker_index = agent_routes.index(
        "setup_velia_coding_autopilot_routes(app, routes_module)"
    )
    assert merge_index < worker_index
    assert 'app.router.add_get(f"{_PREFIX}/merge-policy/status", status)' in routes
    assert (
        'app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/merge-policy", evaluate)'
        in routes
    )
    assert "app.router.add_post" not in routes
    assert "app.router.add_patch" not in routes
    assert "app.router.add_delete" not in routes


def test_merge_policy_is_feature_gated_and_execution_is_impossible():
    service = Path(
        "services/velia_agent_coding_autopilot_merge_policy_service.py"
    ).read_text(encoding="utf-8")
    github = Path(
        "services/velia_agent_coding_autopilot_merge_github_service.py"
    ).read_text(encoding="utf-8")

    assert '"VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED", False' in service
    assert '"mode": "dry_run"' in service
    assert '"execution_supported": False' in service
    assert '"auto_merge": False' in service
    assert '"deployment": False' in service
    assert "merge_pull_request" not in service
    assert "enable_auto_merge" not in service
    assert "commit_operations" not in service
    assert "merge_pull_request" not in github
    assert "enable_auto_merge" not in github


def test_merge_policy_requires_exact_head_ci_review_and_approved_scope():
    service = Path(
        "services/velia_agent_coding_autopilot_merge_policy_service.py"
    ).read_text(encoding="utf-8")

    assert "merge_policy_exact_head_ci_not_success" in service
    assert "merge_policy_ci_head_stale" in service
    assert "merge_policy_file_not_in_approved_plan" in service
    assert "merge_policy_changes_requested" in service
    assert "merge_policy_approval_required" in service
    assert "merge_policy_review_actions_unresolved" in service
    assert "merge_policy_deletion_not_allowed" in service
    assert "merge_policy_rename_not_allowed" in service
    assert "merge_policy_unreviewable_diff" in service
