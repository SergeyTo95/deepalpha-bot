from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Mapping, Tuple
from urllib.parse import quote

from services import velia_agent_coding_autopilot_ci_service as ci
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service

logger = logging.getLogger(__name__)
_INSTALLED = False
_AUTH_RE = re.compile(r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?[^\s,;]+")
_SECRET_RE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*([^\s,;]+)"
)
_URL_SECRET_RE = re.compile(r"(?i)(https?://)[^\s/@]+@")
_LOG_INFRA_FAILURE_RE = re.compile(
    r"(?:"
    r"(?:hosted|self-hosted)?\s*runner\s+(?:lost communication|is offline|is unavailable|failed to start|was terminated)"
    r"|infrastructure"
    r"|service unavailable"
    r"|rate limit"
    r"|network(?: error| failure| unavailable)"
    r"|unable to resolve"
    r"|temporary failure"
    r"|connection reset"
    r"|no space left"
    r"|artifact upload(?: failed| failure)"
    r"|checkout failed"
    r"|cancelled|canceled|timed out|timeout"
    r"|billing"
    r"|permission denied"
    r")",
    re.IGNORECASE,
)
_MAX_RUNS = 8
_MAX_JOBS = 4


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def logs_enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_AUTOPILOT_CI_LOGS_ENABLED", False)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _access(project: Mapping[str, Any]) -> Tuple[str, str, str]:
    installation_id, repository_id, full_name, _ = write_service._project_values(dict(project))
    owner, name = github_service._validate_full_name(full_name)
    token = github_service._installation_token(installation_id, [repository_id])
    return owner, name, token


def _scrub(value: Any, limit: int) -> str:
    text = ci._bounded_text(value, limit * 2)
    text = _AUTH_RE.sub("Authorization=[REDACTED]", text)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _URL_SECRET_RE.sub(r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)x-access-token:[^@\s]+", "x-access-token:[REDACTED]", text)
    lines = []
    size = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("##[group]") or line.startswith("##[endgroup]"):
            continue
        bounded = line[:2000]
        lines.append(bounded)
        size += len(bounded) + 1
        if size >= limit:
            break
    return "\n".join(lines)[:limit]


def _download_job_log(owner: str, name: str, token: str, job_id: int) -> str:
    max_bytes = _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_CI_LOG_MAX_BYTES", 131072, 8192, 262144
    )
    timeout = _env_int("VELIA_DEVELOPER_GITHUB_TIMEOUT_SECONDS", 20, 3, 60)
    response = github_service.HTTP.request(
        "GET",
        (
            f"https://api.github.com/repos/{quote(owner)}/{quote(name)}/"
            f"actions/jobs/{int(job_id)}/logs"
        ),
        headers=github_service._headers(token),
        timeout=timeout,
        allow_redirects=True,
        stream=True,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise github_service.DeveloperGithubError(
            "github_actions_logs_unavailable",
            status=503 if status >= 500 else status,
            detail=str(status),
        )
    chunks: List[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        remaining = max_bytes - total
        if remaining <= 0:
            break
        part = bytes(chunk[:remaining])
        chunks.append(part)
        total += len(part)
        if total >= max_bytes:
            break
    try:
        response.close()
    except Exception:
        pass
    return _scrub(b"".join(chunks).decode("utf-8", errors="replace"), max_bytes)


def _actions_job_logs(project: Mapping[str, Any], sha: str) -> Dict[str, Any]:
    owner, name, token = _access(project)
    raw_runs = github_service._request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/actions/runs",
        token=token,
        params={"head_sha": str(sha), "per_page": 50},
    )
    workflow_runs = raw_runs.get("workflow_runs") if isinstance(raw_runs, dict) else []
    matching = [
        item
        for item in (workflow_runs or [])
        if isinstance(item, dict) and str(item.get("head_sha") or "") == str(sha)
    ][:_MAX_RUNS]
    logs: List[Dict[str, Any]] = []
    for run in matching:
        run_id = int(run.get("id") or 0)
        if run_id <= 0:
            continue
        raw_jobs = github_service._request(
            "GET",
            f"/repos/{quote(owner)}/{quote(name)}/actions/runs/{run_id}/jobs",
            token=token,
            params={"per_page": 100},
        )
        jobs = raw_jobs.get("jobs") if isinstance(raw_jobs, dict) else []
        for job in jobs or []:
            if not isinstance(job, dict):
                continue
            conclusion = str(job.get("conclusion") or "").lower()
            if conclusion not in ci._FAILURE_CONCLUSIONS:
                continue
            job_id = int(job.get("id") or 0)
            if job_id <= 0:
                continue
            text = _download_job_log(owner, name, token, job_id)
            logs.append(
                {
                    "source": "actions_job_log",
                    "workflow": _scrub(run.get("name"), 240),
                    "name": _scrub(job.get("name"), 240),
                    "conclusion": conclusion,
                    "url": _scrub(job.get("html_url"), 500),
                    "text": text,
                }
            )
            if len(logs) >= _MAX_JOBS:
                return {"available": True, "logs": logs}
    return {"available": True, "logs": logs}


def _job_log_is_infrastructure(item: Mapping[str, Any]) -> bool:
    conclusion = str(item.get("conclusion") or "").lower()
    if conclusion in {"cancelled", "timed_out", "startup_failure", "action_required"}:
        return True
    return bool(_LOG_INFRA_FAILURE_RE.search(str(item.get("text") or "")))


def enrich_failure(project: Mapping[str, Any], sha: str, failure: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(failure)
    if not logs_enabled() or bool(result.get("repairable")) or bool(result.get("infrastructure")):
        return result
    try:
        payload = _actions_job_logs(project, sha)
    except Exception as exc:
        code = str(getattr(exc, "code", "github_actions_logs_unavailable"))[:120]
        result["log_fallback"] = {"available": False, "error_code": code}
        logger.warning("VELIA_AUTOPILOT_CI_LOG_FALLBACK_UNAVAILABLE sha=%s code=%s", sha, code)
        return result
    logs = list(payload.get("logs") or [])[:_MAX_JOBS]
    result["log_fallback"] = {"available": True, "job_count": len(logs)}
    if not logs:
        return result
    failures = list(result.get("failures") or [])
    infrastructure = any(_job_log_is_infrastructure(item) for item in logs)
    actionable = any(str(item.get("text") or "").strip() for item in logs)
    result["failures"] = (logs + failures)[:20]
    result["infrastructure"] = infrastructure
    result["repairable"] = bool(actionable and not infrastructure)
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = ci._failure_details

    def failure_details_with_logs(
        project: Dict[str, Any],
        sha: str,
        checks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return enrich_failure(project, sha, original(project, sha, checks))

    ci._failure_details = failure_details_with_logs
    _INSTALLED = True
