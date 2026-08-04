import ast
from pathlib import Path


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
