import ast
from pathlib import Path


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def test_agent_chat_is_outer_action_layer_after_developer():
    source = Path("run_web_process.py").read_text(encoding="utf-8")
    developer_index = source.index("install_velia_developer_chat(velia_chat_service_module)")
    agent_index = source.index("install_velia_agent_chat(velia_chat_service_module)")
    streaming_route_index = source.index("setup_velia_mobile_streaming_route(")

    assert developer_index < agent_index < streaming_route_index


def test_agent_chat_schema_and_install_are_registered_once():
    source = Path("run_web_process.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    install_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "install_velia_agent_chat"
    ]
    schema_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "ensure_velia_agent_chat_tables"
    ]

    assert len(install_calls) == 1
    assert len(schema_calls) == 1


def test_agent_chat_repository_guard_preserves_developer_requests():
    source = Path("services/velia_agent_chat_runtime_patch.py").read_text(encoding="utf-8")

    assert "_REPOSITORY_SCOPE_RE" in source
    assert "if not active and _REPOSITORY_SCOPE_RE.search(message):" in source
    assert "return original_generate(" in source


def test_agent_chat_is_feature_gated_and_cost_bounded():
    planner = Path("services/velia_agent_chat_planner_service.py").read_text(encoding="utf-8")

    assert "VELIA_AGENT_CHAT_ENABLED" in planner
    assert "VELIA_AGENT_CHAT_PLAN_MAX_COST_USD" in planner
    assert "VELIA_AGENT_CHAT_PLAN_OUTPUT_TOKENS" in planner
    assert "cost_service._estimate_cost" in planner
    assert "temperature=0.0" in planner
