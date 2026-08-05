from pathlib import Path


def test_review_patch_installs_after_ci_and_before_worker():
    source = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")
    ci_index = source.index("setup_velia_coding_autopilot_ci_routes(app, routes_module)")
    review_index = source.index("setup_velia_coding_autopilot_review_routes(app, routes_module)")
    worker_index = source.index("setup_velia_coding_autopilot_routes(app, routes_module)")
    assert ci_index < review_index < worker_index


def test_review_loop_is_separately_gated_and_bounded():
    source = Path("services/velia_agent_coding_autopilot_review_service.py").read_text(
        encoding="utf-8"
    )
    assert '"VELIA_DEVELOPER_AUTOPILOT_REVIEW_ENABLED", False' in source
    assert '"VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_ACTIONS", 2, 0, 2' in source
    assert '"VELIA_DEVELOPER_AUTOPILOT_REVIEW_MAX_COST_USD", 0.06' in source
    assert "review_loop_enabled()" in source


def test_review_routes_are_read_only_and_user_scoped():
    source = Path("services/velia_agent_coding_autopilot_review_routes.py").read_text(
        encoding="utf-8"
    )
    assert 'app.router.add_get(f"{_PREFIX}/review/status", status)' in source
    assert 'app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/reviews", actions)' in source
    assert "int(auth[\"user_id\"])" in source
    assert "app.router.add_post" not in source
    assert "app.router.add_patch" not in source
    assert "app.router.add_delete" not in source


def test_review_repair_never_merges_deploys_or_resolves_threads():
    service = Path("services/velia_agent_coding_autopilot_review_service.py").read_text(
        encoding="utf-8"
    )
    github = Path("services/velia_agent_coding_autopilot_review_github_service.py").read_text(
        encoding="utf-8"
    )
    assert "merge_pull_request" not in service
    assert "enable_auto_merge" not in service
    assert "resolve_review_thread" not in service
    assert "deploy" not in github.casefold()
    assert "VELIA did not merge or deploy" in github


def test_review_prompt_restricts_changes_to_original_plan():
    source = Path("services/velia_agent_coding_autopilot_review_service.py").read_text(
        encoding="utf-8"
    )
    assert "Allowed files from the original approved plan" in source
    assert "Modify only listed files from the original approved plan." in source
    assert "Do not reinterpret ordinary comments or questions as permission to write." in source
    assert "Do not create a new branch or PR. Do not merge, deploy, approve or resolve" in source
    assert "_validate_review_scope" in source
    assert "velia_coding_autopilot_branch_head_changed" in source
