from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from velia_repowise_service.config import (
    ConfigurationError,
    Settings,
    SyncRepository,
)
from velia_repowise_service.mirror_sync import MirrorSyncManager


SERVICE_TOKEN = "service-token-with-at-least-24-chars"
GITHUB_TOKEN = "github-read-token-with-at-least-24-chars"


def _run(*args: str) -> str:
    return subprocess.run(
        list(args), check=True, text=True, capture_output=True
    ).stdout.strip()


def _source_repository(path: Path) -> tuple[str, str]:
    path.mkdir(parents=True)
    _run("git", "init", "-q", str(path))
    _run("git", "-C", str(path), "config", "user.email", "test@example.com")
    _run("git", "-C", str(path), "config", "user.name", "VELIA Test")
    (path / "app.py").write_text("print('first')\n", encoding="utf-8")
    _run("git", "-C", str(path), "add", "app.py")
    _run("git", "-C", str(path), "commit", "-q", "-m", "initial")
    sha = _run("git", "-C", str(path), "rev-parse", "HEAD")
    branch = _run("git", "-C", str(path), "branch", "--show-current")
    return sha, branch


def _settings(
    tmp_path: Path, target: SyncRepository, *, token: str = GITHUB_TOKEN
) -> Settings:
    mirror_root = tmp_path / "mirrors"
    workspace_root = tmp_path / "workspaces"
    mirror_root.mkdir(exist_ok=True)
    workspace_root.mkdir(exist_ok=True)
    return Settings(
        mirror_root=mirror_root.resolve(),
        workspace_root=workspace_root.resolve(),
        repositories={target.repository_full_name: target.mirror_path.resolve()},
        service_token=SERVICE_TOKEN,
        port=7337,
        command_timeout_seconds=30,
        index_timeout_seconds=120,
        mcp_timeout_seconds=10,
        max_workspaces_per_repo=2,
        max_request_bytes=65536,
        max_context_chars=12000,
        max_candidate_paths=20,
        max_concurrency=2,
        sync_repositories={target.repository_full_name: target},
        github_read_token=token,
        sync_interval_seconds=30,
        sync_timeout_seconds=120,
    )


def test_sync_configuration_requires_credential_free_github_url_and_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror_root = tmp_path / "mirrors"
    workspace_root = tmp_path / "workspaces"
    mirror_root.mkdir()
    workspace_root.mkdir()
    monkeypatch.setenv("VELIA_REPOWISE_MIRROR_ROOT", str(mirror_root))
    monkeypatch.setenv("VELIA_REPOWISE_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("VELIA_REPOWISE_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("VELIA_REPOWISE_REPOSITORIES_JSON", "{}")
    monkeypatch.setenv(
        "VELIA_REPOWISE_GITHUB_REPOSITORIES_JSON",
        json.dumps(
            {
                "SergeyTo95/deepalpha-bot":
                    "https://github.com/SergeyTo95/deepalpha-bot"
            }
        ),
    )
    monkeypatch.delenv("VELIA_REPOWISE_GITHUB_READ_TOKEN", raising=False)

    with pytest.raises(ConfigurationError, match="GITHUB_READ_TOKEN"):
        Settings.load()

    monkeypatch.setenv("VELIA_REPOWISE_GITHUB_READ_TOKEN", GITHUB_TOKEN)
    settings = Settings.load()
    target = settings.sync_repositories["SergeyTo95/deepalpha-bot"]
    assert target.remote_url == "https://github.com/SergeyTo95/deepalpha-bot.git"
    assert target.mirror_path == (
        mirror_root / "SergeyTo95--deepalpha-bot.git"
    ).resolve()
    assert settings.repositories["SergeyTo95/deepalpha-bot"] == target.mirror_path
    assert target.mirror_path.exists() is False

    monkeypatch.setenv(
        "VELIA_REPOWISE_GITHUB_REPOSITORIES_JSON",
        json.dumps(
            {
                "SergeyTo95/deepalpha-bot":
                    "https://secret@github.com/SergeyTo95/deepalpha-bot.git"
            }
        ),
    )
    with pytest.raises(ConfigurationError, match="credential-free"):
        Settings.load()


def test_mirror_sync_clones_fetches_and_keeps_token_out_of_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    first_sha, branch = _source_repository(source)
    remote = tmp_path / "remote.git"
    _run("git", "clone", "-q", "--bare", str(source), str(remote))
    _run("git", "-C", str(source), "remote", "add", "origin", str(remote))

    mirror_root = tmp_path / "mirrors"
    mirror_root.mkdir()
    target = SyncRepository(
        repository_full_name="owner/repo",
        remote_url=str(remote),
        mirror_path=(mirror_root / "owner--repo.git").resolve(),
    )
    settings = _settings(tmp_path, target)
    manager = MirrorSyncManager(settings)

    original_run = subprocess.run
    observed: list[tuple[list[str], dict[str, str]]] = []

    def recording_run(command: list[str], **kwargs: Any) -> Any:
        observed.append((list(command), dict(kwargs.get("env") or {})))
        return original_run(command, **kwargs)

    monkeypatch.setattr("velia_repowise_service.mirror_sync.RUN", recording_run)

    first = manager.sync_one("owner/repo")
    assert first.cloned is True
    assert first.head_count >= 1
    _run("git", f"--git-dir={target.mirror_path}", "cat-file", "-e", f"{first_sha}^{{commit}}")

    (source / "app.py").write_text("print('second')\n", encoding="utf-8")
    _run("git", "-C", str(source), "add", "app.py")
    _run("git", "-C", str(source), "commit", "-q", "-m", "second")
    second_sha = _run("git", "-C", str(source), "rev-parse", "HEAD")
    _run("git", "-C", str(source), "push", "-q", "origin", f"HEAD:{branch}")

    second = manager.sync_one("owner/repo")
    assert second.cloned is False
    _run("git", f"--git-dir={target.mirror_path}", "cat-file", "-e", f"{second_sha}^{{commit}}")

    assert observed
    for command, env in observed:
        assert GITHUB_TOKEN not in " ".join(command)
        assert "x-access-token@" not in " ".join(command)
        assert env["VELIA_REPOWISE_GITHUB_READ_TOKEN"] == GITHUB_TOKEN
        assert env["GIT_TERMINAL_PROMPT"] == "0"

    status = manager.public_status()
    assert status["configured"] is True
    assert status["enabled"] is True
    assert status["repositories"] == 1
    assert status["ready"] == 1
    assert status["failed"] == 0


def test_sync_error_redacts_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mirror_root = tmp_path / "mirrors"
    mirror_root.mkdir()
    target = SyncRepository(
        repository_full_name="owner/repo",
        remote_url="https://github.com/owner/repo.git",
        mirror_path=(mirror_root / "owner--repo.git").resolve(),
    )
    manager = MirrorSyncManager(_settings(tmp_path, target))

    def failed_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=f"authentication failed for {GITHUB_TOKEN}",
        )

    monkeypatch.setattr("velia_repowise_service.mirror_sync.RUN", failed_run)
    with pytest.raises(Exception) as captured:
        manager.sync_one("owner/repo")
    assert GITHUB_TOKEN not in str(captured.value)
    assert "[REDACTED]" in getattr(captured.value, "detail", "")
