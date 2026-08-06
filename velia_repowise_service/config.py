from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


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


def _repositories(mirror_root: Path) -> Dict[str, Path]:
    raw = str(os.getenv("VELIA_REPOWISE_REPOSITORIES_JSON", "{}") or "{}").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("VELIA_REPOWISE_REPOSITORIES_JSON is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("VELIA_REPOWISE_REPOSITORIES_JSON must be an object")
    result: Dict[str, Path] = {}
    for full_name, configured_path in payload.items():
        repository = str(full_name or "").strip()
        if repository.count("/") != 1:
            raise ConfigurationError(f"invalid repository allowlist key: {repository}")
        path = Path(str(configured_path or "")).expanduser().resolve()
        try:
            path.relative_to(mirror_root)
        except ValueError as exc:
            raise ConfigurationError(
                f"mirror path for {repository} must be under {mirror_root}"
            ) from exc
        if not path.exists():
            raise ConfigurationError(f"mirror path for {repository} does not exist")
        result[repository] = path
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

    @classmethod
    def load(cls) -> "Settings":
        mirror_root = _resolved_dir(
            "VELIA_REPOWISE_MIRROR_ROOT", "/data/velia-repowise/mirrors"
        )
        workspace_root = _resolved_dir(
            "VELIA_REPOWISE_WORKSPACE_ROOT", "/data/velia-repowise/workspaces"
        )
        token = str(os.getenv("VELIA_REPOWISE_SERVICE_TOKEN", "") or "").strip()
        if len(token) < 24:
            raise ConfigurationError(
                "VELIA_REPOWISE_SERVICE_TOKEN must contain at least 24 characters"
            )
        return cls(
            mirror_root=mirror_root,
            workspace_root=workspace_root,
            repositories=_repositories(mirror_root),
            service_token=token,
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
        )
