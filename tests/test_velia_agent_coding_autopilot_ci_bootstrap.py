from pathlib import Path


def test_ci_patch_installs_before_autopilot_worker_registration():
    source = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")
    ci_index = source.index("setup_velia_coding_autopilot_ci_routes(app, routes_module)")
    worker_index = source.index("setup_velia_coding_autopilot_routes(app, routes_module)")
    assert ci_index < worker_index


def test_ci_repair_is_separately_feature_gated_and_bounded():
    source = Path("services/velia_agent_coding_autopilot_ci_service.py").read_text(
        encoding="utf-8"
    )
    assert '"VELIA_DEVELOPER_AUTOPILOT_CI_ENABLED", False' in source
    assert '"VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_ENABLED", False' in source
    assert '"VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS", 2, 0, 2' in source
    assert "attempt_number BETWEEN 0 AND 2" in source


def test_ci_repair_never_exposes_merge_or_deployment_routes():
    routes = Path("services/velia_agent_coding_autopilot_ci_routes.py").read_text(
        encoding="utf-8"
    )
    service = Path("services/velia_agent_coding_autopilot_ci_service.py").read_text(
        encoding="utf-8"
    )
    assert '"auto_merge": False' in routes
    assert '"deployment": False' in routes
    assert "merge_pull_request" not in service
    assert "enable_auto_merge" not in service
    assert "deployment" not in service.casefold() or "deployment configuration" in service
    assert "app.router.add_post" not in routes


def test_repair_prompt_restricts_scope_and_protected_areas():
    source = Path("services/velia_agent_coding_autopilot_ci_service.py").read_text(
        encoding="utf-8"
    )
    assert "Modify only the listed allowed files from the original approved plan." in source
    assert "Do not change workflows, secrets, credentials, auth policy, billing, migrations" in source
    assert "Do not create a new branch or PR. Do not merge or deploy." in source
    assert "policy_service.validate_plan" in source
    assert "velia_coding_autopilot_branch_head_changed" in source


def test_infrastructure_failures_do_not_trigger_product_code_changes():
    source = Path("services/velia_agent_coding_autopilot_ci_service.py").read_text(
        encoding="utf-8"
    )
    assert "_INFRA_FAILURE_RE" in source
    assert 'code = "velia_coding_autopilot_ci_infrastructure_failure"' in source
    assert "and not infrastructure" in source


def test_ci_routes_are_read_only_and_user_scoped():
    source = Path("services/velia_agent_coding_autopilot_ci_routes.py").read_text(
        encoding="utf-8"
    )
    assert 'app.router.add_get(f"{_PREFIX}/ci/status", status)' in source
    assert 'app.router.add_get(f"{_PREFIX}/runs/{run_id}/ci", attempts)' not in source
    assert 'app.router.add_get(f"{_PREFIX}/runs/{{run_id}}/ci", attempts)' in source
    assert "int(auth[\"user_id\"])" in source
