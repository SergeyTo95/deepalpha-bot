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


def test_agent_execution_uses_atomic_job_claim_and_locked_approval_transition():
    job_source = Path("services/velia_agent_job_service.py").read_text(encoding="utf-8")
    runtime_source = Path("services/velia_agent_runtime_service.py").read_text(encoding="utf-8")

    assert "def claim_job_for_execution" in job_source
    assert "UPDATE velia_agent_jobs AS j" in job_source
    assert "AND j.status=%s" in job_source
    assert "RETURNING j.job_id" in job_source
    assert "FOR UPDATE OF a, j" in job_source
    claim_index = runtime_source.index("jobs.claim_job_for_execution")
    read_index = runtime_source.index("job = jobs.get_job")
    assert claim_index < read_index


def test_agent_reserved_metadata_cannot_be_forged_and_is_injected_server_side():
    try:
        agent_protocol.validate_arguments({"_velia_idempotency_key": "forged"})
        raise AssertionError("reserved argument accepted")
    except agent_protocol.AgentProtocolError as exc:
        assert exc.code == "velia_agent_argument_reserved"

    runtime_source = Path("services/velia_agent_runtime_service.py").read_text(encoding="utf-8")
    assert '"_velia_action_id": active_action_id' in runtime_source
    assert '"_velia_job_id": str(job_id)' in runtime_source
    assert '"_velia_idempotency_key": str(action.get("idempotency_key") or "")' in runtime_source


def test_google_calendar_connector_is_encrypted_feature_gated_and_approval_gated():
    service = Path("services/velia_agent_google_calendar_service.py").read_text(encoding="utf-8")
    crypto = Path("services/velia_agent_connector_crypto_service.py").read_text(encoding="utf-8")
    routes = Path("services/velia_agent_google_calendar_routes.py").read_text(encoding="utf-8")
    agent_routes = Path("services/velia_agent_routes.py").read_text(encoding="utf-8")

    assert 'VELIA_GOOGLE_CALENDAR_ENABLED' in service
    assert 'VELIA_GOOGLE_OAUTH_CLIENT_ID' in service
    assert 'VELIA_GOOGLE_OAUTH_CLIENT_SECRET' in service
    assert 'VELIA_GOOGLE_OAUTH_CALLBACK_URL' in service
    assert '"access_type": "offline"' in service
    assert '"prompt": "consent"' in service
    assert 'ActionRisk.WRITE_EXTERNAL' in service
    assert '_event_id(str(arguments.get("_velia_idempotency_key") or ""))' in service
    assert 'VELIA_CONNECTOR_FERNET_KEY' in crypto
    assert '.encrypt(' in crypto and '.decrypt(' in crypto
    assert 'request.query.get("state")' in routes
    assert 'request.query.get("code")' in routes
    assert 'javascript:' not in routes
    assert 'setup_velia_google_calendar_routes(app, routes_module)' in agent_routes
