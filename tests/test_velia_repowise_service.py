from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from aiohttp.test_utils import TestClient, TestServer

from velia_repowise_service.app import create_app
from velia_repowise_service.config import ConfigurationError, Settings
from velia_repowise_service.mcp_client import extract_tool_result, render_context
from velia_repowise_service.workspace import (
    Workspace,
    WorkspaceManager,
    normalize_candidate_paths,
    normalize_sha,
)


TOKEN = "service-token-with-at-least-24-chars"


def _settings(tmp_path: Path, repositories: Dict[str, Path]) -> Settings:
    mirror_root = tmp_path / "mirrors"
    workspace_root = tmp_path / "workspaces"
    mirror_root.mkdir(exist_ok=True)
    workspace_root.mkdir(exist_ok=True)
    return Settings(
        mirror_root=mirror_root.resolve(),
        workspace_root=workspace_root.resolve(),
        repositories={key: value.resolve() for key, value in repositories.items()},
        service_token=TOKEN,
        port=7337,
        command_timeout_seconds=30,
        index_timeout_seconds=120,
        mcp_timeout_seconds=10,
        max_workspaces_per_repo=2,
        max_request_bytes=65536,
        max_context_chars=12000,
        max_candidate_paths=20,
        max_concurrency=2,
    )


def _git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "VELIA Test"],
        check=True,
    )
    (path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "initial"], check=True
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_settings_require_long_token_and_confined_mirrors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror_root = tmp_path / "mirrors"
    workspace_root = tmp_path / "workspaces"
    mirror_root.mkdir()
    workspace_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("VELIA_REPOWISE_MIRROR_ROOT", str(mirror_root))
    monkeypatch.setenv("VELIA_REPOWISE_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("VELIA_REPOWISE_SERVICE_TOKEN", "short")
    monkeypatch.setenv(
        "VELIA_REPOWISE_REPOSITORIES_JSON",
        json.dumps({"owner/repo": str(outside)}),
    )

    with pytest.raises(ConfigurationError, match="token"):
        Settings.load()

    monkeypatch.setenv("VELIA_REPOWISE_SERVICE_TOKEN", TOKEN)
    with pytest.raises(ConfigurationError, match="under"):
        Settings.load()


def test_candidate_paths_are_bounded_deduplicated_and_safe() -> None:
    assert normalize_candidate_paths(
        ["services/a.py", "../secret", "services/a.py", "docs/readme.md"], 2
    ) == ["services/a.py", "docs/readme.md"]
    with pytest.raises(Exception):
        normalize_sha("abc")


def test_workspace_manager_creates_exact_sha_and_reuses_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror_root = tmp_path / "mirrors"
    mirror_root.mkdir()
    mirror = mirror_root / "repo"
    sha = _git_repo(mirror)
    settings = _settings(tmp_path, {"owner/repo": mirror})
    manager = WorkspaceManager(settings)
    index_calls: list[Path] = []

    def fake_index(workspace: Path) -> float:
        index_calls.append(workspace)
        state = workspace / ".repowise" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"last_commit": sha}), encoding="utf-8")
        return 1.25

    monkeypatch.setattr(manager, "_index", fake_index)

    first = manager.ensure("owner/repo", sha)
    assert first.indexed_sha == sha
    assert first.reused is False
    assert first.path.is_dir()
    assert manager.verify(first) is True

    second = manager.ensure("owner/repo", sha)
    assert second.path == first.path
    assert second.reused is True
    assert index_calls == [first.path]


def test_extract_and_bound_mcp_tool_result() -> None:
    result = SimpleNamespace(
        isError=False,
        structuredContent={"targets": [{"path": "services/a.py"}]},
        content=[SimpleNamespace(text="context text")],
    )
    payload = extract_tool_result(result)
    assert payload["structured"]["targets"][0]["path"] == "services/a.py"
    assert payload["text"] == "context text"

    rendered = render_context("get_context", payload, 80)
    assert len(rendered) <= 80
    assert rendered.endswith("[VELIA_REPOWISE_CONTEXT_TRUNCATED]")


class FakeManager:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.calls: list[tuple[str, str]] = []

    def ensure(self, repository: str, sha: str) -> Workspace:
        self.calls.append((repository, sha))
        return self.workspace

    def verify(self, workspace: Workspace) -> bool:
        return workspace == self.workspace


@pytest.mark.asyncio
async def test_api_requires_bearer_and_returns_exact_read_only_contract(
    tmp_path: Path,
) -> None:
    mirror_root = tmp_path / "mirrors"
    mirror_root.mkdir()
    mirror = mirror_root / "repo"
    mirror.mkdir()
    settings = _settings(tmp_path, {"owner/repo": mirror})
    sha = "a" * 40
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = Workspace(
        repository_full_name="owner/repo",
        requested_sha=sha,
        path=workspace_path,
        indexed_sha=sha,
        reused=True,
        index_seconds=0.0,
    )
    manager = FakeManager(workspace)
    loader_calls: list[Dict[str, Any]] = []

    async def loader(_workspace: Workspace, **kwargs: Any) -> str:
        loader_calls.append(kwargs)
        return '{"tool":"get_context","result":{"path":"services/a.py"}}'

    app = create_app(settings, manager=manager, context_loader=loader)
    async with TestClient(TestServer(app)) as client:
        unauthorized = await client.post("/v1/context/planning", json={})
        assert unauthorized.status == 401
        assert manager.calls == []

        response = await client.post(
            "/v1/context/planning",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "repository_full_name": "owner/repo",
                "repository_id": 10,
                "branch": "main",
                "requested_sha": sha,
                "goal": "Inspect planning flow",
                "candidate_paths": ["services/a.py", "../invalid"],
                "max_context_chars": 4000,
                "mode": "read_only",
            },
        )
        assert response.status == 200
        payload = await response.json()

    assert payload["indexed_sha"] == sha
    assert payload["requested_sha"] == sha
    assert payload["read_only"] is True
    assert payload["mode"] == "read_only"
    assert payload["candidate_paths"] == ["services/a.py"]
    assert payload["telemetry"] is False
    assert payload["llm_generation"] is False
    assert manager.calls == [("owner/repo", sha)]
    assert loader_calls[0]["candidate_paths"] == ["services/a.py"]


@pytest.mark.asyncio
async def test_api_rejects_non_read_only_mode_before_workspace_access(
    tmp_path: Path,
) -> None:
    mirror_root = tmp_path / "mirrors"
    mirror_root.mkdir()
    mirror = mirror_root / "repo"
    mirror.mkdir()
    settings = _settings(tmp_path, {"owner/repo": mirror})
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    manager = FakeManager(
        Workspace("owner/repo", "a" * 40, workspace_path, "a" * 40, True, 0.0)
    )
    app = create_app(settings, manager=manager)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/v1/context/planning",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"mode": "write"},
        )
    assert response.status == 400
    assert manager.calls == []


def test_service_source_has_no_git_write_remote_or_deployment_surface() -> None:
    root = Path(__file__).resolve().parents[1] / "velia_repowise_service"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))
    ).casefold()
    for forbidden in (
        "git push",
        "git commit",
        "merge_pull_request",
        "enable_auto_merge",
        "railway up",
        "deployment endpoint",
        "github_app_private_key",
        "github installation token",
    ):
        assert forbidden not in source
    assert "worktree" in source
    assert "read_only" in source
    assert "repowise_telemetry_disabled" in source
