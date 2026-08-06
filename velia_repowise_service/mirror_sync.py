from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from velia_repowise_service.config import Settings, SyncRepository


logger = logging.getLogger(__name__)
RUN = subprocess.run


class MirrorSyncError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]


@dataclass(frozen=True)
class MirrorSyncResult:
    repository_full_name: str
    mirror_path: Path
    cloned: bool
    duration_seconds: float
    head_count: int


class MirrorSyncManager:
    """Maintain local bare mirrors using read-only GitHub credentials.

    The token is supplied only through a GIT_ASKPASS environment variable. It
    is never embedded in a remote URL, command argument, status payload or log.
    The manager exposes clone/fetch operations only and has no push surface.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._locks_guard = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}
        self._state_guard = threading.Lock()
        self._state: Dict[str, Dict[str, Any]] = {}
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.sync_repositories and self.settings.github_read_token
        )

    def _lock(self, repository: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(repository)
            if lock is None:
                lock = threading.Lock()
                self._locks[repository] = lock
            return lock

    def _set_state(self, repository: str, **values: Any) -> None:
        with self._state_guard:
            current = dict(self._state.get(repository) or {})
            current.update(values)
            self._state[repository] = current

    def public_status(self) -> Dict[str, Any]:
        with self._state_guard:
            states = {key: dict(value) for key, value in self._state.items()}
        ready = sum(1 for value in states.values() if value.get("ready") is True)
        failed = sum(1 for value in states.values() if value.get("error_code"))
        return {
            "configured": bool(self.settings.sync_repositories),
            "enabled": self.enabled,
            "repositories": len(self.settings.sync_repositories),
            "ready": ready,
            "failed": failed,
            "interval_seconds": self.settings.sync_interval_seconds,
            "last_success_at": max(
                [int(value.get("last_success_at") or 0) for value in states.values()]
                or [0]
            ),
        }

    def _askpass_path(self) -> Path:
        path = (self.settings.mirror_root / ".velia-git-askpass.sh").resolve()
        path.relative_to(self.settings.mirror_root)
        content = (
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *) printf '%s\\n' \"$VELIA_REPOWISE_GITHUB_READ_TOKEN\" ;;\n"
            "esac\n"
        )
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            path.chmod(0o700)
        return path

    def _git_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "GIT_ASKPASS": str(self._askpass_path()),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "VELIA_REPOWISE_GITHUB_READ_TOKEN": self.settings.github_read_token,
            }
        )
        return env

    def _redact(self, value: str) -> str:
        text = str(value or "")
        token = self.settings.github_read_token
        if token:
            text = text.replace(token, "[REDACTED]")
        return text[-500:]

    def _run(
        self,
        command: Sequence[str],
        *,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = RUN(
                list(command),
                env=self._git_env(),
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout or self.settings.sync_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise MirrorSyncError("sync_timeout", str(command[0])) from exc
        except Exception as exc:
            raise MirrorSyncError("sync_unavailable", exc.__class__.__name__) from exc
        if result.returncode != 0:
            detail = self._redact(result.stderr or result.stdout or "")
            raise MirrorSyncError("sync_command_failed", detail)
        return result

    @staticmethod
    def _git_dir(mirror: Path) -> list[str]:
        return ["git", f"--git-dir={mirror}"]

    def _clone(self, target: SyncRepository) -> None:
        temporary = target.mirror_path.with_name(
            f".{target.mirror_path.name}.clone-{os.getpid()}-{threading.get_ident()}"
        )
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            self._run(
                ["git", "clone", "--mirror", target.remote_url, str(temporary)],
                timeout=self.settings.sync_timeout_seconds,
            )
            if not (temporary / "HEAD").is_file() or not (
                temporary / "objects"
            ).is_dir():
                raise MirrorSyncError("mirror_invalid_after_clone")
            if target.mirror_path.exists():
                shutil.rmtree(target.mirror_path)
            temporary.replace(target.mirror_path)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def _fetch(self, target: SyncRepository) -> None:
        prefix = self._git_dir(target.mirror_path)
        self._run([*prefix, "remote", "set-url", "origin", target.remote_url])
        self._run(
            [
                *prefix,
                "fetch",
                "--prune",
                "--tags",
                "origin",
                "+refs/heads/*:refs/heads/*",
            ],
            timeout=self.settings.sync_timeout_seconds,
        )

    def _head_count(self, mirror: Path) -> int:
        result = self._run(
            [*self._git_dir(mirror), "for-each-ref", "--format=%(refname)", "refs/heads/"]
        )
        return len([line for line in result.stdout.splitlines() if line.strip()])

    def sync_one(self, repository: str) -> MirrorSyncResult:
        target = self.settings.sync_repositories.get(repository)
        if target is None:
            raise MirrorSyncError("repository_not_sync_configured")
        started = time.monotonic()
        self._set_state(
            repository,
            ready=False,
            syncing=True,
            error_code="",
            last_attempt_at=int(time.time()),
        )
        with self._lock(repository):
            try:
                cloned = not target.mirror_path.exists()
                target.mirror_path.parent.mkdir(parents=True, exist_ok=True)
                if cloned:
                    self._clone(target)
                else:
                    self._fetch(target)
                count = self._head_count(target.mirror_path)
                duration = time.monotonic() - started
                self._set_state(
                    repository,
                    ready=True,
                    syncing=False,
                    error_code="",
                    last_success_at=int(time.time()),
                    duration_seconds=round(duration, 3),
                    head_count=count,
                )
                return MirrorSyncResult(
                    repository_full_name=repository,
                    mirror_path=target.mirror_path,
                    cloned=cloned,
                    duration_seconds=duration,
                    head_count=count,
                )
            except MirrorSyncError as exc:
                self._set_state(
                    repository,
                    ready=False,
                    syncing=False,
                    error_code=exc.code,
                )
                raise

    def sync_all(self) -> Dict[str, MirrorSyncResult]:
        results: Dict[str, MirrorSyncResult] = {}
        for repository in sorted(self.settings.sync_repositories):
            try:
                results[repository] = self.sync_one(repository)
            except MirrorSyncError as exc:
                logger.warning(
                    "VELIA_REPOWISE_MIRROR_SYNC_FAILED repository=%s code=%s detail=%s",
                    repository,
                    exc.code,
                    self._redact(exc.detail),
                )
        return results

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            await asyncio.to_thread(self.sync_all)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.sync_interval_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run_forever(), name="velia-repowise-mirror-sync"
        )

    async def close(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
