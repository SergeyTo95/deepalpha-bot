from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, Mapping
from urllib.parse import urlparse

import requests

from services import velia_developer_github_service as github_service


HTTP = requests
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_ALLOWED_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _base_url() -> str:
    return str(os.getenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_URL", "") or "").strip().rstrip("/")


def _token() -> str:
    return str(os.getenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_TOKEN", "") or "").strip()


def _url_is_allowed(value: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
    except Exception:
        return False
    host = str(parsed.hostname or "").casefold()
    if parsed.scheme == "https" and host:
        return True
    return bool(
        parsed.scheme == "http"
        and host
        and (host in _ALLOWED_HTTP_HOSTS or host.endswith(".railway.internal"))
    )


def context_configured() -> bool:
    return _url_is_allowed(_base_url())


def context_enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_REPOWISE_CONTEXT_ENABLED", False) and context_configured()


def status() -> Dict[str, Any]:
    return {
        "enabled": context_enabled(),
        "configured": context_configured(),
        "read_only": True,
        "fail_open": True,
        "exact_sha_required": True,
        "timeout_seconds": _env_int(
            "VELIA_DEVELOPER_REPOWISE_CONTEXT_TIMEOUT_SECONDS", 8, 2, 30
        ),
        "max_context_chars": _env_int(
            "VELIA_DEVELOPER_REPOWISE_CONTEXT_MAX_CHARS", 12000, 2000, 24000
        ),
    }


def _normalize_sha(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if _SHA_RE.fullmatch(normalized) else ""


def _project_head_sha(project: Mapping[str, Any]) -> str:
    branch = str(project.get("selected_branch") or "").strip()
    if not branch:
        return ""
    try:
        branches = github_service.list_branches(
            int(project["installation_id"]),
            int(project["repository_id"]),
            str(project["repository_full_name"]),
        )
    except Exception:
        return ""
    for item in branches:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("name") or "") != branch:
            continue
        return _normalize_sha(item.get("sha"))
    return ""


def _candidate_paths(values: Iterable[Any], limit: int = 20) -> list[str]:
    paths: list[str] = []
    seen = set()
    for value in values:
        try:
            path = github_service.validate_path(str(value or ""))
        except Exception:
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def _fallback(
    evidence: str,
    *,
    error_code: str = "",
    requested_sha: str = "",
    indexed_sha: str = "",
) -> Dict[str, Any]:
    return {
        "used": False,
        "source": "github",
        "evidence": str(evidence or ""),
        "requested_sha": requested_sha,
        "indexed_sha": indexed_sha,
        "error_code": str(error_code or "")[:120],
        "read_only": True,
    }


def _headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "VELIA-Developer-Repowise/1.0",
        "X-VELIA-Mode": "read-only",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_planning_context(
    project: Mapping[str, Any],
    *,
    goal: str,
    candidate_paths: Iterable[Any],
    fallback_evidence: str,
) -> Dict[str, Any]:
    """Return exact-head read-only planning context or the GitHub fallback.

    This client never receives GitHub installation tokens and never exposes a
    write action. Every failure is fail-open to the existing GitHub evidence.
    """

    if not context_enabled():
        return _fallback(fallback_evidence, error_code="disabled")

    repository = str(project.get("repository_full_name") or "").strip()
    branch = str(project.get("selected_branch") or "").strip()
    if not repository or not branch:
        return _fallback(fallback_evidence, error_code="project_identity_missing")

    requested_sha = _project_head_sha(project)
    if not requested_sha:
        return _fallback(fallback_evidence, error_code="branch_head_unavailable")

    maximum = _env_int(
        "VELIA_DEVELOPER_REPOWISE_CONTEXT_MAX_CHARS", 12000, 2000, 24000
    )
    payload = {
        "repository_full_name": repository,
        "repository_id": int(project.get("repository_id") or 0),
        "branch": branch,
        "requested_sha": requested_sha,
        "goal": str(goal or "").strip()[:8000],
        "candidate_paths": _candidate_paths(candidate_paths),
        "max_context_chars": maximum,
        "mode": "read_only",
    }
    timeout = _env_int(
        "VELIA_DEVELOPER_REPOWISE_CONTEXT_TIMEOUT_SECONDS", 8, 2, 30
    )
    try:
        response = HTTP.post(
            f"{_base_url()}/v1/context/planning",
            headers=_headers(),
            json=payload,
            timeout=timeout,
            allow_redirects=False,
        )
    except Exception as exc:
        return _fallback(
            fallback_evidence,
            error_code=f"unavailable:{exc.__class__.__name__}",
            requested_sha=requested_sha,
        )

    if int(getattr(response, "status_code", 0) or 0) != 200:
        return _fallback(
            fallback_evidence,
            error_code=f"http_{int(getattr(response, 'status_code', 0) or 0)}",
            requested_sha=requested_sha,
        )
    try:
        data = response.json()
    except Exception:
        return _fallback(
            fallback_evidence,
            error_code="invalid_json",
            requested_sha=requested_sha,
        )
    if not isinstance(data, Mapping):
        return _fallback(
            fallback_evidence,
            error_code="invalid_payload",
            requested_sha=requested_sha,
        )

    indexed_sha = _normalize_sha(data.get("indexed_sha"))
    if indexed_sha != requested_sha:
        return _fallback(
            fallback_evidence,
            error_code="stale_index",
            requested_sha=requested_sha,
            indexed_sha=indexed_sha,
        )
    if str(data.get("repository_full_name") or "") != repository:
        return _fallback(
            fallback_evidence,
            error_code="repository_mismatch",
            requested_sha=requested_sha,
            indexed_sha=indexed_sha,
        )
    if str(data.get("mode") or "").casefold() != "read_only" or data.get("read_only") is not True:
        return _fallback(
            fallback_evidence,
            error_code="read_only_contract_missing",
            requested_sha=requested_sha,
            indexed_sha=indexed_sha,
        )

    context = str(data.get("context") or "").strip()
    if not context:
        return _fallback(
            fallback_evidence,
            error_code="empty_context",
            requested_sha=requested_sha,
            indexed_sha=indexed_sha,
        )
    if len(context) > maximum:
        return _fallback(
            fallback_evidence,
            error_code="context_too_large",
            requested_sha=requested_sha,
            indexed_sha=indexed_sha,
        )

    return {
        "used": True,
        "source": "repowise",
        "evidence": (
            "REPOWISE EXACT-SHA READ-ONLY CONTEXT\n"
            f"Repository: {repository}\n"
            f"Branch: {branch}\n"
            f"Indexed SHA: {indexed_sha}\n\n"
            f"{context}"
        ),
        "requested_sha": requested_sha,
        "indexed_sha": indexed_sha,
        "error_code": "",
        "read_only": True,
    }
