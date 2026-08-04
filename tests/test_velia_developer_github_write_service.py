import pytest

from services import velia_developer_github_write_service as write_service


PROJECT = {
    "id": "project-1",
    "installation_id": 11,
    "repository_id": 22,
    "repository_full_name": "owner/repo",
    "selected_branch": "develop",
}


def test_work_branch_must_be_isolated(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_WORK_BRANCH_PREFIX", "velia/")
    with pytest.raises(write_service.DeveloperWriteError) as exc:
        write_service._validate_work_branch("develop", "develop")
    assert exc.value.code == "developer_unsafe_write_branch"
    assert write_service._validate_work_branch("velia/task-1", "develop") == "velia/task-1"


def test_write_permissions_require_contents_and_pull_requests(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_WRITE_ENABLED", "true")
    monkeypatch.setattr(write_service.github_service, "_app_jwt", lambda: "jwt")
    monkeypatch.setattr(
        write_service.github_service,
        "_request",
        lambda *args, **kwargs: {"permissions": {"contents": "write", "pull_requests": "write"}},
    )
    assert write_service.require_write_permissions(PROJECT)["contents"] == "write"

    monkeypatch.setattr(
        write_service.github_service,
        "_request",
        lambda *args, **kwargs: {"permissions": {"contents": "read", "pull_requests": "write"}},
    )
    with pytest.raises(write_service.DeveloperWriteError) as exc:
        write_service.require_write_permissions(PROJECT)
    assert exc.value.code == "github_contents_write_permission_required"


def test_protected_paths_are_blocked(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_WORKFLOW_WRITE_ENABLED", raising=False)
    assert write_service._protected_path(".env") is True
    assert write_service._protected_path("config/private_key.pem") is True
    assert write_service._protected_path(".github/workflows/deploy.yml") is True
    assert write_service._protected_path("services/example.py") is False


def test_commit_operations_creates_one_atomic_commit(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_WRITE_ENABLED", "true")
    monkeypatch.setattr(write_service, "require_write_permissions", lambda project: {})
    monkeypatch.setattr(write_service, "_token", lambda project: "token")
    monkeypatch.setattr(write_service, "branch_head", lambda project, branch: {"branch": branch, "sha": "parent"})
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("body")))
        if method == "GET" and "/git/commits/" in path:
            return {"tree": {"sha": "base-tree"}}
        if path.endswith("/git/blobs"):
            return {"sha": "blob-sha"}
        if path.endswith("/git/trees"):
            return {"sha": "tree-sha"}
        if path.endswith("/git/commits"):
            return {"sha": "commit-sha"}
        return {}

    monkeypatch.setattr(write_service, "_request", fake_request)
    result = write_service.commit_operations(
        PROJECT,
        branch="velia/task-1",
        operations=[{"op": "upsert", "path": "services/example.py", "content": "print('ok')\n"}],
        message="task 1",
    )
    assert result["commit_sha"] == "commit-sha"
    assert result["files"] == ["services/example.py"]
    assert [item[0] for item in calls] == ["GET", "POST", "POST", "POST", "PATCH"]
    assert calls[-1][2] == {"sha": "commit-sha", "force": False}
