from pathlib import Path


def test_autopilot_routes_are_installed_through_agent_extensions():
    routes = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")
    autopilot_routes = Path("services/velia_agent_coding_autopilot_routes.py").read_text(
        encoding="utf-8"
    )

    assert "setup_velia_coding_autopilot_routes" in routes
    assert "setup_velia_coding_autopilot_routes(app, routes_module)" in routes
    assert routes.index("setup_velia_google_calendar_routes(app, routes_module)") < routes.index(
        "setup_velia_coding_autopilot_routes(app, routes_module)"
    )
    assert '/mobile-api/v1/developer/autopilot' in autopilot_routes
    assert 'missions_start_paused' in autopilot_routes
    assert '"auto_merge": False' in autopilot_routes
    assert '"deployment": False' in autopilot_routes
    assert '"ci_repair": False' in autopilot_routes
    assert '"write_enabled": write' in autopilot_routes
    assert '"worker_ready": enabled and worker and coding and write' in autopilot_routes


def test_autopilot_routes_require_all_write_boundaries():
    source = Path("services/velia_agent_coding_autopilot_routes.py").read_text(
        encoding="utf-8"
    )

    assert "project_service.developer_enabled()" in source
    assert "coding_service.coding_enabled()" in source
    assert "autopilot.autopilot_enabled()" in source
    assert "github_service.github_app_configured()" in source
    assert "write_service.write_enabled()" in source
    assert "routes_module._require_mobile_auth" in source
    assert "run_autopilot_once" not in source
    assert "/tick" not in source
