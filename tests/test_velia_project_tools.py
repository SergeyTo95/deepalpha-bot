import os
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from services import velia_project_tools as tools


PROJECT = {
    "id": "project-1", "installation_id": 99, "repository_id": 234,
    "repository_full_name": "owner/example", "selected_branch": "feat/game",
}
REPOSITORY = {
    "full_name": "owner/example", "default_branch": "main", "private": False,
    "visibility": "public", "archived": False,
}
ACTOR = {"github_user_id": 42, "github_user_login": "owner"}


def test_cloud_shell_opens_only_public_repositories_and_carries_game_workspace():
    workspace = "games/velia-5cc8b9ac-1f09-4c8f-ac1e-c04ea468470b"
    url = urlparse(tools.cloud_shell_url(REPOSITORY, "feat/game", workspace))
    assert url.scheme == "https" and url.netloc == "shell.cloud.google.com"
    assert parse_qs(url.query) == {
        "cloudshell_git_repo": ["https://github.com/owner/example"],
        "cloudshell_git_branch": ["feat/game"], "show": ["ide"],
        "cloudshell_workspace": [workspace],
    }
    assert tools.cloud_shell_url({**REPOSITORY, "private": True}, "main") is None
    assert tools.cloud_shell_url({**REPOSITORY, "visibility": "internal"}, "main") is None


@pytest.fixture
def github(monkeypatch):
    seen = []
    options = {"permission": "write", "actions": "write", "actor_id": 42, "run_id": 111}

    def request(method, path, **kwargs):
        seen.append((method, path, kwargs))
        if path.endswith("/contents/.github/workflows/velia-tests.yml"):
            assert kwargs["params"] == {"ref": "main"}
            return {"type": "file", "path": tools.WORKFLOW_PATH}
        if path.endswith("/collaborators/owner/permission"):
            return {"user": {"id": options["actor_id"]}, "permission": options["permission"]}
        if path.endswith("/actions/workflows/velia-tests.yml/dispatches"):
            assert kwargs["body"] == {"ref": "main"}
            assert kwargs["api_version"] == "2026-03-10"
            return {"workflow_run_id": options["run_id"]}
        if path.endswith("/actions/runs/111"):
            return {"id": 111, "status": "completed", "conclusion": "success",
                    "head_sha": "a" * 40, "event": "workflow_dispatch"}
        pytest.fail(f"Unexpected GitHub request: {method} {path}")

    monkeypatch.setattr(tools, "metadata", lambda _: REPOSITORY)
    monkeypatch.setattr(tools.github, "_installation_token", lambda installation, ids: "scoped-test-token")
    monkeypatch.setattr(tools.github, "_request", request)
    monkeypatch.setattr(tools.github, "installation_details", lambda _: {"actions_permission": options["actions"]})
    return seen, options


@pytest.mark.parametrize("permission", ["read", "triage"])
def test_live_github_write_permission_required(github, permission):
    seen, options = github
    options["permission"] = permission
    assert tools._configured_workflow(PROJECT, REPOSITORY, ACTOR) == "project_tests_write_required"
    assert all(call[0] == "GET" for call in seen)


def test_legacy_installation_and_app_permission_fail_closed(github):
    seen, options = github
    assert tools._configured_workflow(PROJECT, REPOSITORY, {}) == "project_tests_reconnect"
    options["actor_id"] = 77
    assert tools._configured_workflow(PROJECT, REPOSITORY, ACTOR) == "project_tests_write_required"
    options["actions"] = "read"
    assert tools._configured_workflow(PROJECT, REPOSITORY, ACTOR) == "project_tests_actions_permission"
    assert all(call[0] == "GET" for call in seen)


def test_dispatch_does_not_accept_user_supplied_workflow_branch_or_inputs(github):
    seen, _ = github
    for data in ({"request_id": str(uuid4()), "ref": "untrusted"}, {"request_id": "bad"},
                 {"workflow": "release.yml", "inputs": {"deploy": True}}, None):
        with pytest.raises(tools.projects.DeveloperProjectError, match="project_tests_invalid_request"):
            tools.dispatch(PROJECT, ACTOR, data)
    assert seen == []


def test_dispatch_and_status_are_durable_idempotent_and_project_scoped(github, monkeypatch):
    dsn = os.getenv("VELIA_PROJECT_TOOLS_TEST_DSN")
    if not dsn:
        pytest.skip("requires isolated PostgreSQL")
    import psycopg2

    seen, options = github
    schema = f"test_tools_{uuid4().hex}"
    with psycopg2.connect(dsn) as conn, conn.cursor() as cursor:
        cursor.execute(f"CREATE SCHEMA {schema}")
    connect = lambda: psycopg2.connect(dsn, options=f"-csearch_path={schema}")
    monkeypatch.setattr(tools, "get_connection", connect)
    monkeypatch.setattr(tools.projects, "get_connection", connect)
    monkeypatch.setattr(tools.projects, "_SCHEMA_READY", False)
    try:
        github_record = {"installation_id": 99, "account_login": "owner",
                         "authorized_user_id": 42, "authorized_user_login": "owner"}
        tools.projects.record_installation(7, github_record)
        assert "github_user_id" not in tools.projects.get_installation(7, 99)
        installation = tools.projects.get_installation(7, 99, include_actor=True)
        assert installation["github_user_id"] == 42
        project = tools.projects.create_project(7, 99, {**REPOSITORY, "id": 234}, "feat/game")

        payload = {"request_id": str(uuid4())}
        queued = tools.dispatch(project, installation, payload)
        assert queued == {"branch": "main", "status": "queued", "id": 111, "head_sha": None, "conclusion": None}
        assert tools.dispatch(project, installation, payload)["conclusion"] == "success"
        assert tools.latest(project)["head_sha"] == "a" * 40
        with pytest.raises(tools.projects.DeveloperProjectError, match="project_tests_limit"):
            tools.dispatch(project, installation, {"request_id": str(uuid4())})
        assert tools.latest({**project, "id": "other-project"}) is None
        dispatches = [call for call in seen if call[0] == "POST"]
        assert len(dispatches) == 1 and dispatches[0][2]["body"] == {"ref": "main"}

        with tools._cursor() as cursor:
            cursor.execute("UPDATE velia_project_test_launches SET created_at = now() - interval '3 minutes'")
        options["run_id"] = None
        uncertain = {"request_id": str(uuid4())}
        assert tools.dispatch(project, installation, uncertain)["status"] == "unknown"
        assert tools.dispatch(project, installation, uncertain)["status"] == "unknown"
        assert len([call for call in seen if call[0] == "POST"]) == 2
    finally:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA {schema} CASCADE")
