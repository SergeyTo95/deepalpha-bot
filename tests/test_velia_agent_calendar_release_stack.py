from pathlib import Path


def test_calendar_connector_coexists_with_ordinary_chat_agent():
    bootstrap = Path("run_web_process.py").read_text(encoding="utf-8")
    routes = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")
    runtime = Path("services/velia_agent_runtime_service.py").read_text(encoding="utf-8")

    assert "install_velia_agent_chat(velia_chat_service_module)" in bootstrap
    assert "ensure_velia_agent_chat_tables()" in bootstrap
    assert "setup_velia_google_calendar_routes(app, routes_module)" in routes
    assert "VELIA_GOOGLE_CALENDAR_ENABLED" in runtime


def test_calendar_connector_remains_disabled_without_explicit_flag():
    service = Path("services/velia_agent_google_calendar_service.py").read_text(
        encoding="utf-8"
    )

    assert 'def enabled() -> bool:' in service
    assert '_env_bool("VELIA_GOOGLE_CALENDAR_ENABLED", False)' in service
    assert 'ActionRisk.WRITE_EXTERNAL' in service
    assert 'params={"sendUpdates": "none"}' in service
