from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse


class ConfigurationError(RuntimeError):
    pass


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _resolved_dir(name: str, default: str) -> Path:
    value = Path(str(os.getenv(name, default) or default)).expanduser().resolve()
    value.mkdir(parents=True, exist_ok=True)
    if not value.is_dir():
        raise ConfigurationError(f"{name} must be a directory")
    return value


def _repository_name(value: object) -> str:
    repository = str(value or "").strip()
    parts = repository.split("/")
    if len(parts) != 2 or not all(
        re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts
    ):
        raise ConfigurationError(f"invalid repository allowlist key: {repository}")
    return repository


def _json_object(name: str) -> Dict[str, object]:
    raw = str(os.getenv(name, "{}") or "{}").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{name} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"{name} must be an object")
    return payload


def _confined_path(mirror_root: Path, value: object, repository: str) -> Path:
    path = Path(str(value or "")).expanduser().resolve()
    try:
        path.relative_to(mirror_root)
    except ValueError as exc:
        raise ConfigurationError(
            f"mirror path for {repository} must be under {mirror_root}"
        ) from exc
    return path


def _static_repositories(mirror_root: Path) -> Dict[str, Path]:
    payload = _json_object("VELIA_REPOWISE_REPOSITORIES_JSON")
    result: Dict[str, Path] = {}
    for full_name, configured_path in payload.items():
        repository = _repository_name(full_name)
        path = _confined_path(mirror_root, configured_path, repository)
        if not path.exists():
            raise ConfigurationError(f"mirror path for {repository} does not exist")
        result[repository] = path
    return result


def _validated_remote(repository: str, value: object) -> str:
    remote = str(value or "").strip()
    try:
        parsed = urlparse(remote)
    except Exception as exc:
        raise ConfigurationError(f"invalid sync remote for {repository}") from exc
    if (
        parsed.scheme != "https"
        or str(parsed.hostname or "").casefold() != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.params
    ):
        raise ConfigurationError(
            f"sync remote for {repository} must be a credential-free GitHub HTTPS URL"
        )
    expected = f"/{repository}".casefold()
    actual = str(parsed.path or "").rstrip("/")
    if actual.casefold().endswith(".git"):
        actual = actual[:-4]
    if actual.casefold() != expected:
        raise ConfigurationError(f"sync remote does not match repository {repository}")
    return f"https://github.com/{repository}.git"


def _mirror_name(repository: str) -> str:
    return f"{repository.replace('/', '--')}.git"


@dataclass(frozen=True)
class SyncRepository:
    repository_full_name: str
    remote_url: str
    mirror_path: Path


def _sync_repositories(mirror_root: Path) -> Dict[str, SyncRepository]:
    payload = _json_object("VELIA_REPOWISE_GITHUB_REPOSITORIES_JSON")
    result: Dict[str, SyncRepository] = {}
    for full_name, configured_remote in payload.items():
        repository = _repository_name(full_name)
        mirror_path = (mirror_root / _mirror_name(repository)).resolve()
        mirror_path.relative_to(mirror_root)
        result[repository] = SyncRepository(
            repository_full_name=repository,
            remote_url=_validated_remote(repository, configured_remote),
            mirror_path=mirror_path,
        )
    return result


@dataclass(frozen=True)
class Settings:
    mirror_root: Path
    workspace_root: Path
    repositories: Dict[str, Path]
    service_token: str
    port: int
    command_timeout_seconds: int
    index_timeout_seconds: int
    mcp_timeout_seconds: int
    max_workspaces_per_repo: int
    max_request_bytes: int
    max_context_chars: int
    max_candidate_paths: int
    max_concurrency: int
    sync_repositories: Dict[str, SyncRepository] = field(default_factory=dict)
    github_read_token: str = ""
    sync_interval_seconds: int = 60
    sync_timeout_seconds: int = 600

    @classmethod
    def load(cls) -> "Settings":
        mirror_root = _resolved_dir(
            "VELIA_REPOWISE_MIRROR_ROOT", "/data/velia-repowise/mirrors"
        )
        workspace_root = _resolved_dir(
            "VELIA_REPOWISE_WORKSPACE_ROOT", "/data/velia-repowise/workspaces"
        )
        service_token = str(
            os.getenv("VELIA_REPOWISE_SERVICE_TOKEN", "") or ""
        ).strip()
        if len(service_token) < 24:
            raise ConfigurationError(
                "VELIA_REPOWISE_SERVICE_TOKEN must contain at least 24 characters"
            )

        sync_repositories = _sync_repositories(mirror_root)
        github_read_token = str(
            os.getenv("VELIA_REPOWISE_GITHUB_READ_TOKEN", "") or ""
        ).strip()
        if sync_repositories and len(github_read_token) < 24:
            raise ConfigurationError(
                "VELIA_REPOWISE_GITHUB_READ_TOKEN must contain at least 24 characters when mirror sync is configured"
            )

        repositories = _static_repositories(mirror_root)
        for repository, target in sync_repositories.items():
            existing = repositories.get(repository)
            if existing is not None and existing != target.mirror_path:
                raise ConfigurationError(
                    f"conflicting mirror paths configured for {repository}"
                )
            repositories[repository] = target.mirror_path

        return cls(
            mirror_root=mirror_root,
            workspace_root=workspace_root,
            repositories=repositories,
            service_token=service_token,
            port=_env_int("PORT", 7337, 1, 65535),
            command_timeout_seconds=_env_int(
                "VELIA_REPOWISE_COMMAND_TIMEOUT_SECONDS", 60, 5, 300
            ),
            index_timeout_seconds=_env_int(
                "VELIA_REPOWISE_INDEX_TIMEOUT_SECONDS", 1200, 60, 3600
            ),
            mcp_timeout_seconds=_env_int(
                "VELIA_REPOWISE_MCP_TIMEOUT_SECONDS", 30, 5, 120
            ),
            max_workspaces_per_repo=_env_int(
                "VELIA_REPOWISE_MAX_WORKSPACES_PER_REPO", 3, 1, 8
            ),
            max_request_bytes=_env_int(
                "VELIA_REPOWISE_MAX_REQUEST_BYTES", 65536, 4096, 262144
            ),
            max_context_chars=_env_int(
                "VELIA_REPOWISE_MAX_CONTEXT_CHARS", 12000, 2000, 24000
            ),
            max_candidate_paths=_env_int(
                "VELIA_REPOWISE_MAX_CANDIDATE_PATHS", 20, 1, 40
            ),
            max_concurrency=_env_int("VELIA_REPOWISE_MAX_CONCURRENCY", 2, 1, 8),
            sync_repositories=sync_repositories,
            github_read_token=github_read_token,
            sync_interval_seconds=_env_int(
                "VELIA_REPOWISE_SYNC_INTERVAL_SECONDS", 60, 30, 3600
            ),
            sync_timeout_seconds=_env_int(
                "VELIA_REPOWISE_SYNC_TIMEOUT_SECONDS", 600, 60, 1800
            ),
        )
