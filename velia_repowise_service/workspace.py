from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from velia_repowise_service.config import Settings


_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_PATH_RE = re.compile(r"^[A-Za-z0-9_.@+/-]{1,500}$")
RUN = subprocess.run


class WorkspaceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]


@dataclass(frozen=True)
class Workspace:
    repository_full_name: str
    requested_sha: str
    path: Path
    indexed_sha: str
    reused: bool
    index_seconds: float


def normalize_sha(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(normalized):
        raise WorkspaceError("invalid_sha")
    return normalized


def normalize_repository(value: Any) -> str:
    normalized = str(value or "").strip()
    parts = normalized.split("/")
    if len(parts) != 2 or not all(
        re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts
    ):
        raise WorkspaceError("invalid_repository")
    return normalized


def normalize_candidate_paths(values: Iterable[Any], limit: int) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        normalized = str(value or "").replace("\\", "/").strip().strip("/")
        if (
            not normalized
            or not _PATH_RE.fullmatch(normalized)
            or ".." in normalized.split("/")
        ):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _contains_sha(value: Any, sha: str) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_sha(item, sha) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sha(item, sha) for item in value)
    text = str(value or "").casefold()
    return sha in text or sha[:12] in text


class WorkspaceManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._locks_guard = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}

    def _lock(self, repository: str, sha: str) -> threading.Lock:
        key = f"{repository}@{sha}"
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _mirror(self, repository: str) -> Path:
        path = self.settings.repositories.get(repository)
        if path is None:
            raise WorkspaceError("repository_not_allowlisted")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.settings.mirror_root)
        except ValueError as exc:
            raise WorkspaceError("mirror_outside_root") from exc
        return resolved

    @staticmethod
    def _repo_key(repository: str) -> str:
        readable = repository.replace("/", "--")[:80]
        digest = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:12]
        return f"{readable}-{digest}"

    def _repo_root(self, repository: str) -> Path:
        path = (self.settings.workspace_root / self._repo_key(repository)).resolve()
        try:
            path.relative_to(self.settings.workspace_root)
        except ValueError as exc:
            raise WorkspaceError("workspace_outside_root") from exc
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _workspace_path(self, repository: str, sha: str) -> Path:
        return (self._repo_root(repository) / sha).resolve()

    @staticmethod
    def _git_prefix(mirror: Path) -> list[str]:
        if (mirror / "HEAD").is_file() and (mirror / "objects").is_dir() and not (
            mirror / ".git"
        ).exists():
            return ["git", f"--git-dir={mirror}"]
        return ["git", "-C", str(mirror)]

    def _run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = RUN(
                list(command),
                cwd=str(cwd) if cwd is not None else None,
                env=dict(env) if env is not None else None,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout or self.settings.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError("command_timeout", str(command[0])) from exc
        except Exception as exc:
            raise WorkspaceError("command_unavailable", exc.__class__.__name__) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "")[-500:]
            raise WorkspaceError("command_failed", detail)
        return result

    def _mirror_has_sha(self, mirror: Path, sha: str) -> bool:
        command = [*self._git_prefix(mirror), "cat-file", "-e", f"{sha}^{{commit}}"]
        try:
            self._run(command)
        except WorkspaceError:
            return False
        return True

    def _head(self, workspace: Path) -> str:
        value = self._run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"]
        ).stdout.strip().lower()
        return value if _SHA_RE.fullmatch(value) else ""

    @staticmethod
    def _marker_path(workspace: Path) -> Path:
        return workspace / ".velia-repowise-workspace.json"

    def _write_marker(
        self,
        workspace: Path,
        *,
        repository: str,
        sha: str,
        index_seconds: float,
    ) -> None:
        marker = {
            "repository_full_name": repository,
            "indexed_sha": sha,
            "created_at": int(time.time()),
            "repowise_mode": "no-prose",
            "read_only_api": True,
            "index_seconds": round(index_seconds, 3),
        }
        self._marker_path(workspace).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _marker_valid(self, workspace: Path, repository: str, sha: str) -> bool:
        marker_path = self._marker_path(workspace)
        state_path = workspace / ".repowise" / "state.json"
        if not marker_path.is_file() or not state_path.is_file():
            return False
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return bool(
            marker.get("repository_full_name") == repository
            and marker.get("indexed_sha") == sha
            and self._head(workspace) == sha
            and _contains_sha(state, sha)
        )

    def _remove_workspace(self, mirror: Path, workspace: Path) -> None:
        if not workspace.exists():
            return
        try:
            self._run(
                [*self._git_prefix(mirror), "worktree", "remove", "--force", str(workspace)]
            )
        except WorkspaceError:
            try:
                shutil.rmtree(workspace)
            except FileNotFoundError:
                pass
            try:
                self._run([*self._git_prefix(mirror), "worktree", "prune"])
            except WorkspaceError:
                pass

    def _create_worktree(self, mirror: Path, workspace: Path, sha: str) -> None:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        self._remove_workspace(mirror, workspace)
        self._run(
            [
                *self._git_prefix(mirror),
                "worktree",
                "add",
                "--detach",
                str(workspace),
                sha,
            ]
        )
        if self._head(workspace) != sha:
            self._remove_workspace(mirror, workspace)
            raise WorkspaceError("worktree_head_mismatch")

    def _repowise_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["REPOWISE_TELEMETRY_DISABLED"] = "1"
        env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
        env["DO_NOT_TRACK"] = "1"
        for name in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "OPENROUTER_API_KEY",
        ):
            env.pop(name, None)
        return env

    def _index(self, workspace: Path) -> float:
        started = time.monotonic()
        self._run(
            [
                "repowise",
                "init",
                "--yes",
                "--no-prose",
                "--no-editor-setup",
                "--no-claude-md",
                "--no-agents",
                "--no-codex",
                "--no-workspace",
            ],
            cwd=workspace,
            timeout=self.settings.index_timeout_seconds,
            env=self._repowise_env(),
        )
        return time.monotonic() - started

    def _prune(self, repository: str, mirror: Path, keep: Path) -> None:
        root = self._repo_root(repository)
        candidates = [
            item
            for item in root.iterdir()
            if item.is_dir() and item != keep and _SHA_RE.fullmatch(item.name)
        ]
        candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        keep_others = max(0, self.settings.max_workspaces_per_repo - 1)
        for item in candidates[keep_others:]:
            self._remove_workspace(mirror, item)

    def ensure(self, repository_value: Any, sha_value: Any) -> Workspace:
        repository = normalize_repository(repository_value)
        sha = normalize_sha(sha_value)
        mirror = self._mirror(repository)
        if not self._mirror_has_sha(mirror, sha):
            raise WorkspaceError("sha_not_in_mirror")
        workspace = self._workspace_path(repository, sha)
        with self._lock(repository, sha):
            if workspace.exists() and self._marker_valid(workspace, repository, sha):
                os.utime(workspace, None)
                return Workspace(
                    repository_full_name=repository,
                    requested_sha=sha,
                    path=workspace,
                    indexed_sha=sha,
                    reused=True,
                    index_seconds=0.0,
                )
            self._create_worktree(mirror, workspace, sha)
            try:
                index_seconds = self._index(workspace)
                if not self._marker_valid_after_index(workspace, sha):
                    raise WorkspaceError("index_sha_mismatch")
                self._write_marker(
                    workspace,
                    repository=repository,
                    sha=sha,
                    index_seconds=index_seconds,
                )
                self._prune(repository, mirror, workspace)
                return Workspace(
                    repository_full_name=repository,
                    requested_sha=sha,
                    path=workspace,
                    indexed_sha=sha,
                    reused=False,
                    index_seconds=index_seconds,
                )
            except Exception:
                self._remove_workspace(mirror, workspace)
                raise

    def _marker_valid_after_index(self, workspace: Path, sha: str) -> bool:
        state_path = workspace / ".repowise" / "state.json"
        if not state_path.is_file() or self._head(workspace) != sha:
            return False
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return _contains_sha(state, sha)

    def verify(self, workspace: Workspace) -> bool:
        return self._marker_valid(
            workspace.path, workspace.repository_full_name, workspace.indexed_sha
        )
