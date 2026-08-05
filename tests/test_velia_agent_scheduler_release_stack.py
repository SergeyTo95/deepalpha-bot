from pathlib import Path


def test_scheduler_coexists_with_chat_and_calendar_extensions():
    bootstrap = Path("run_web_process.py").read_text(encoding="utf-8")
    routes = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")

    assert "install_velia_agent_chat(velia_chat_service_module)" in bootstrap
    assert "ensure_velia_agent_chat_tables()" in bootstrap
    assert "setup_velia_google_calendar_routes(app, routes_module)" in routes
    assert "setup_velia_agent_scheduler_routes(app, routes_module)" in routes
    assert routes.index("setup_velia_google_calendar_routes(app, routes_module)") < routes.index(
        "setup_velia_agent_scheduler_routes(app, routes_module)"
    )


def test_scheduler_remains_explicitly_disabled_and_fail_closed():
    service = Path("services/velia_agent_scheduler_service.py").read_text(
        encoding="utf-8"
    )

    assert '_env_bool("VELIA_AGENT_SCHEDULER_ENABLED", False)' in service
    assert ") VALUES (%s,%s,%s,%s,%s,FALSE,%s,%s,NULL,%s,%s)" in service
    assert 'payload={"schedule_id": schedule_id, "enabled": False}' in service
    assert "permissions.evaluate_action(definition.risk)" in service
    assert "PermissionDecisionType.DENY" in service
    assert "velia_agent_schedule_action_denied" in service
