import ast
from pathlib import Path

from services import velia_agent_permission_service as agent_permissions
from services import velia_agent_protocol_service as agent_protocol
from services import velia_agent_runtime_service as agent_runtime
from services import velia_agent_tool_registry_service as agent_registry


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def test_developer_routes_are_registered_once_in_web_process():
    source = Path("run_web_process.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "services.velia_developer_routes"
        and any(alias.name == "setup_velia_developer_routes" for alias in node.names)
    ]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "setup_velia_developer_routes"
    ]

    assert len(imports) == 1
    assert len(calls) == 1
    assert len(calls[0].args) == 2


def test_developer_api_surface_stays_read_only():
    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")

    assert "add_get" in source
    assert "add_post" in source
    assert "add_delete" in source
    assert "create_branch" not in source
    assert "commit_changes" not in source
    assert "open_pull_request" not in source
    assert "run_command" not in source


def test_developer_ask_cancellation_has_cleanup_path():
    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")

    assert "except asyncio.CancelledError:" in source
    assert "developer_run_cancelled" in source
    assert "await asyncio.shield(" in source


def test_github_callback_requires_user_authorization_code():
    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")

    assert 'request.query.get("code")' in source
    assert "github_service.authorize_user_installation" in source
    assert "github_service.installation_details, installation_id" not in source


def test_agent_routes_and_schema_are_registered_once_in_web_process():
    source = Path("run_web_process.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "services.velia_agent_routes"
        and any(alias.name == "setup_velia_agent_routes" for alias in node.names)
    ]
    route_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "setup_velia_agent_routes"
    ]
    schema_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "ensure_velia_agent_tables"
    ]

    assert len(imports) == 1
    assert len(route_calls) == 1
    assert len(route_calls[0].args) == 2
    assert len(schema_calls) == 1


def test_agent_permission_policy_is_fail_closed():
    assert agent_permissions.evaluate_action(agent_protocol.ActionRisk.READ).allowed is True
    assert agent_permissions.evaluate_action(agent_protocol.ActionRisk.WRITE_REVERSIBLE).requires_approval is True
    assert agent_permissions.evaluate_action(agent_protocol.ActionRisk.WRITE_EXTERNAL).requires_approval is True
    assert agent_permissions.evaluate_action(agent_protocol.ActionRisk.DESTRUCTIVE).decision is agent_permissions.PermissionDecisionType.DENY
    assert agent_permissions.evaluate_action(agent_protocol.ActionRisk.FINANCIAL).decision is agent_permissions.PermissionDecisionType.DENY
    assert agent_permissions.evaluate_action(agent_protocol.ActionRisk.CODE_EXECUTION).decision is agent_permissions.PermissionDecisionType.DENY


def test_agent_builtin_tools_are_velia_branded_and_guarded():
    agent_registry.clear_registry_for_tests()
    agent_runtime._BUILTINS_READY = False
    tools = agent_runtime.public_tools()

    assert [item["name"] for item in tools] == [
        "velia.echo",
        "velia.tasks.create_draft",
        "velia.tasks.list",
    ]
    draft = next(item for item in tools if item["name"] == "velia.tasks.create_draft")
    assert draft["requires_approval"] is True
    rendered = str(tools)
    assert "OpenWorker" not in rendered
    assert "Liquid" not in rendered


def test_agent_mobile_surface_is_provider_neutral_and_has_no_shell_tool():
    source = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")
    runtime_source = Path("services/velia_agent_runtime_service.py").read_text(encoding="utf-8")

    assert '"brand": "VELIA"' in source
    assert '"core": "Velyon Core"' in source
    assert "OpenWorker" not in source
    assert "Liquid" not in source
    assert "run_shell" not in runtime_source
    assert "subprocess" not in runtime_source
