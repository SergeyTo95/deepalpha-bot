from pathlib import Path

from services import kimi_gateway


def test_ordinary_chat_and_developer_route_use_fast_path():
    runtime = Path("services/velia_developer_chat_runtime_patch.py").read_text(encoding="utf-8")
    routes = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")
    assert "velia_developer_fast_path_service as agent_service" in runtime
    assert "velia_developer_fast_path_service as agent_service" in routes
    assert "velia_developer_agent_service as agent_service" not in runtime
    assert "velia_developer_agent_service as agent_service" not in routes


def test_fast_path_completion_default_is_bounded():
    assert kimi_gateway._feature_default_completion_tokens("velia_developer_fast") == 2048
    assert kimi_gateway._initial_completion_limit("velia_developer_fast", 512) == 2048
