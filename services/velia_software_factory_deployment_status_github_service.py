from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping
from urllib.parse import quote, urlparse

from services import velia_developer_github_service as github_service

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RAILWAY_HOSTS = {"railway.com", "www.railway.com", "railway.app", "www.railway.app"}


class DeploymentStatusGithubError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 502) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


def _bounded(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "")[:limit]


def _sha(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(text):
        raise DeploymentStatusGithubError(
            "velia_factory_deployment_status_sha_invalid",
            detail=text[:80],
            status=400,
        )
    return text


def _access(project: Mapping[str, Any]) -> tuple[str, str, str]:
    try:
        installation_id = int(project.get("installation_id") or 0)
        repository_id = int(project.get("repository_id") or 0)
        full_name = str(project.get("repository_full_name") or "").strip()
        if installation_id <= 0 or repository_id <= 0:
            raise DeploymentStatusGithubError(
                "velia_factory_deployment_project_access_invalid", status=409
            )
        owner, name = github_service._validate_full_name(full_name)
        token = github_service._installation_token(installation_id, [repository_id])
        return owner, name, token
    except DeploymentStatusGithubError:
        raise
    except Exception as exc:
        raise DeploymentStatusGithubError(
            str(getattr(exc, "code", "velia_factory_deployment_github_access_failed")),
            detail=str(getattr(exc, "detail", str(exc)) or ""),
            status=int(getattr(exc, "status", 502)),
        ) from exc


def _request(*args: Any, **kwargs: Any) -> Any:
    try:
        return github_service._request(*args, **kwargs)
    except github_service.DeveloperGithubError as exc:
        raise DeploymentStatusGithubError(
            exc.code,
            detail=exc.detail,
            status=exc.status,
        ) from exc


def _normalize_status(item: Mapping[str, Any]) -> Dict[str, Any]:
    creator = item.get("creator") if isinstance(item.get("creator"), Mapping) else {}
    return {
        "context": _bounded(item.get("context"), 240),
        "state": _bounded(item.get("state"), 40).lower(),
        "description": _bounded(item.get("description"), 500),
        "target_url": _bounded(item.get("target_url"), 1000),
        "creator_login": _bounded(creator.get("login"), 160),
        "updated_at": _bounded(item.get("updated_at"), 80),
    }


def commit_status_snapshot(project: Mapping[str, Any], commit_sha: str) -> Dict[str, Any]:
    exact_sha = _sha(commit_sha)
    owner, name, token = _access(project)
    raw = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/commits/{quote(exact_sha)}/status",
        token=token,
        params={"per_page": 100},
    )
    if not isinstance(raw, Mapping):
        raise DeploymentStatusGithubError("github_invalid_response")

    by_context: Dict[str, Dict[str, Any]] = {}
    raw_statuses = raw.get("statuses") if isinstance(raw.get("statuses"), list) else []
    for raw_item in raw_statuses:
        if not isinstance(raw_item, Mapping):
            continue
        item = _normalize_status(raw_item)
        context = str(item.get("context") or "").strip()
        if not context or context in by_context:
            continue
        by_context[context] = item

    statuses: List[Dict[str, Any]] = sorted(
        by_context.values(), key=lambda item: str(item.get("context") or "").casefold()
    )
    return {
        "ok": True,
        "repository_full_name": f"{owner}/{name}",
        "commit_sha": exact_sha,
        "combined_state": _bounded(raw.get("state"), 40).lower(),
        "total_count": int(raw.get("total_count") or len(statuses)),
        "statuses": statuses,
        "contexts": {
            str(item.get("context") or ""): str(item.get("state") or "")
            for item in statuses
        },
    }


def _target_host(target_url: Any) -> str:
    try:
        return str(urlparse(str(target_url or "")).hostname or "").strip().lower()
    except Exception:
        return ""


def railway_context_candidates(status_snapshot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in status_snapshot.get("statuses") or []:
        if not isinstance(item, Mapping):
            continue
        if _target_host(item.get("target_url")) not in _RAILWAY_HOSTS:
            continue
        result.append(
            {
                "context": str(item.get("context") or ""),
                "state": str(item.get("state") or ""),
                "target_url": str(item.get("target_url") or ""),
            }
        )
    return result
