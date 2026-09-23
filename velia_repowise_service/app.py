from __future__ import annotations

import asyncio
import hmac
import importlib.metadata
import logging
from typing import Any, Awaitable, Callable, Dict, Mapping

from aiohttp import web

from velia_repowise_service.config import ConfigurationError, Settings
from velia_repowise_service.mcp_client import MCPContextError, get_planning_context
from velia_repowise_service.mirror_sync import MirrorSyncManager
from velia_repowise_service.workspace import (
    Workspace,
    WorkspaceError,
    WorkspaceManager,
    normalize_candidate_paths,
    normalize_repository,
    normalize_sha,
)


logger = logging.getLogger(__name__)
ContextLoader = Callable[..., Awaitable[str]]


def _repowise_version() -> str:
    try:
        return importlib.metadata.version("repowise")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _json(payload: Mapping[str, Any], status: int = 200) -> web.Response:
    return web.json_response(dict(payload), status=status)


def _authorized(request: web.Request, token: str) -> bool:
    header = str(request.headers.get("Authorization") or "")
    if not header.startswith("Bearer "):
        return False
    supplied = header[7:].strip()
    return bool(supplied and hmac.compare_digest(supplied, token))


def _branch(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 200
        or normalized.startswith("/")
        or normalized.endswith("/")
        or ".." in normalized
        or "//" in normalized
    ):
        raise WorkspaceError("invalid_branch")
    return normalized


def _goal(value: Any) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise WorkspaceError("goal_required")
    if len(normalized) > 8000:
        raise WorkspaceError("goal_too_large")
    return normalized


def _error_response(exc: Exception) -> web.Response:
    if isinstance(exc, WorkspaceError):
        status = {
            "repository_not_allowlisted": 403,
            "sha_not_in_mirror": 409,
            "invalid_repository": 400,
            "invalid_sha": 400,
            "invalid_branch": 400,
            "goal_required": 400,
            "goal_too_large": 413,
        }.get(exc.code, 503)
        return _json(
            {"ok": False, "error": exc.code, "detail": exc.detail}, status=status
        )
    if isinstance(exc, MCPContextError):
        return _json(
            {"ok": False, "error": exc.code, "detail": exc.detail}, status=503
        )
    logger.exception("VELIA_REPOWISE_CONTEXT_FAILED")
    return _json({"ok": False, "error": "internal_error"}, status=500)


def create_app(
    settings: Settings | None = None,
    *,
    manager: WorkspaceManager | None = None,
    context_loader: ContextLoader = get_planning_context,
    mirror_sync_manager: MirrorSyncManager | None = None,
) -> web.Application:
    current = settings or Settings.load()
    workspace_manager = manager or WorkspaceManager(current)
    sync_manager = mirror_sync_manager or MirrorSyncManager(current)
    app = web.Application(client_max_size=current.max_request_bytes)
    app["settings"] = current
    app["workspace_manager"] = workspace_manager
    app["context_loader"] = context_loader
    app["mirror_sync_manager"] = sync_manager
    app["semaphore"] = asyncio.Semaphore(current.max_concurrency)

    async def health(_request: web.Request) -> web.Response:
        return _json(
            {
                "ok": True,
                "service": "velia-repowise",
                "version": 2,
                "repowise_version": _repowise_version(),
                "mode": "read_only",
                "repositories_configured": len(current.repositories),
                "mirror_sync": sync_manager.public_status(),
                "telemetry": False,
                "llm_generation": False,
            }
        )

    async def license_info(_request: web.Request) -> web.Response:
        return _json(
            {
                "ok": True,
                "component": "repowise",
                "version": _repowise_version(),
                "license": "AGPL-3.0-or-later",
                "source": "https://github.com/repowise-dev/repowise",
                "modified": False,
            }
        )

    async def planning(request: web.Request) -> web.Response:
        if not _authorized(request, current.service_token):
            return _json({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await request.json()
        except Exception:
            return _json({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(payload, dict):
            return _json({"ok": False, "error": "invalid_payload"}, status=400)
        if str(payload.get("mode") or "").casefold() != "read_only":
            return _json(
                {"ok": False, "error": "read_only_mode_required"}, status=400
            )
        try:
            repository = normalize_repository(payload.get("repository_full_name"))
            requested_sha = normalize_sha(payload.get("requested_sha"))
            branch = _branch(payload.get("branch"))
            goal = _goal(payload.get("goal"))
            paths = normalize_candidate_paths(
                payload.get("candidate_paths")
                if isinstance(payload.get("candidate_paths"), list)
                else [],
                current.max_candidate_paths,
            )
            try:
                requested_maximum = int(payload.get("max_context_chars") or 0)
            except (TypeError, ValueError):
                requested_maximum = 0
            maximum = min(
                current.max_context_chars,
                max(2000, requested_maximum or current.max_context_chars),
            )
            semaphore: asyncio.Semaphore = app["semaphore"]
            async with semaphore:
                workspace: Workspace = await asyncio.to_thread(
                    workspace_manager.ensure, repository, requested_sha
                )
                if not workspace_manager.verify(workspace):
                    raise WorkspaceError("workspace_verification_failed")
                loader: ContextLoader = app["context_loader"]
                context = await loader(
                    workspace,
                    candidate_paths=paths,
                    maximum_chars=maximum,
                    timeout_seconds=current.mcp_timeout_seconds,
                )
                if not workspace_manager.verify(workspace):
                    raise WorkspaceError("workspace_changed_during_read")
            return _json(
                {
                    "ok": True,
                    "repository_full_name": repository,
                    "repository_id": int(payload.get("repository_id") or 0),
                    "branch": branch,
                    "requested_sha": requested_sha,
                    "indexed_sha": workspace.indexed_sha,
                    "mode": "read_only",
                    "read_only": True,
                    "context": str(context)[:maximum],
                    "context_chars": min(len(str(context)), maximum),
                    "candidate_paths": paths,
                    "workspace_reused": workspace.reused,
                    "index_seconds": round(workspace.index_seconds, 3),
                    "telemetry": False,
                    "llm_generation": False,
                }
            )
        except Exception as exc:
            return _error_response(exc)

    async def start_background_services(_app: web.Application) -> None:
        await sync_manager.start()

    async def stop_background_services(_app: web.Application) -> None:
        await sync_manager.close()

    app.router.add_get("/health", health)
    app.router.add_get("/v1/license", license_info)
    app.router.add_post("/v1/context/planning", planning)
    app.on_startup.append(start_background_services)
    app.on_cleanup.append(stop_background_services)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = Settings.load()
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc
    web.run_app(create_app(settings), host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
