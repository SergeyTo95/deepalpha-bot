from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple
from urllib.parse import quote

from services import velia_agent_coding_autopilot_review_github_service as review_github
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service


class CodingAutopilotMergeGithubError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 502) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


def _access(project: Mapping[str, Any]) -> Tuple[str, str, str]:
    try:
        installation_id, repository_id, full_name, _ = write_service._project_values(dict(project))
        owner, name = github_service._validate_full_name(full_name)
        token = github_service._installation_token(installation_id, [repository_id])
        return owner, name, token
    except Exception as exc:
        raise CodingAutopilotMergeGithubError(
            str(getattr(exc, "code", "github_merge_policy_access_failed")),
            detail=str(getattr(exc, "detail", "")),
            status=int(getattr(exc, "status", 502)),
        ) from exc


def _request(*args: Any, **kwargs: Any) -> Any:
    try:
        return github_service._request(*args, **kwargs)
    except github_service.DeveloperGithubError as exc:
        raise CodingAutopilotMergeGithubError(exc.code, detail=exc.detail, status=exc.status) from exc


def _bounded(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "")[:limit]


def _files(owner: str, name: str, token: str, pull_number: int) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for page in range(1, 4):
        raw = _request(
            "GET",
            f"/repos/{quote(owner)}/{quote(name)}/pulls/{pull_number}/files",
            token=token,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(raw, list):
            break
        for item in raw:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "filename": _bounded(item.get("filename"), 500),
                    "previous_filename": _bounded(item.get("previous_filename"), 500),
                    "status": _bounded(item.get("status"), 40).lower(),
                    "additions": int(item.get("additions") or 0),
                    "deletions": int(item.get("deletions") or 0),
                    "changes": int(item.get("changes") or 0),
                    "patch_present": isinstance(item.get("patch"), str),
                }
            )
        if len(raw) < 100:
            break
    return result[:300]


def pull_snapshot(project: Mapping[str, Any], pull_number: int) -> Dict[str, Any]:
    number = int(pull_number or 0)
    if number <= 0:
        raise CodingAutopilotMergeGithubError(
            "velia_coding_autopilot_merge_policy_pr_invalid", status=400
        )
    owner, name, token = _access(project)
    raw = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/pulls/{number}",
        token=token,
    )
    if not isinstance(raw, dict):
        raise CodingAutopilotMergeGithubError("github_invalid_response")
    base = raw.get("base") if isinstance(raw.get("base"), dict) else {}
    head = raw.get("head") if isinstance(raw.get("head"), dict) else {}
    return {
        "number": number,
        "state": _bounded(raw.get("state"), 30).lower(),
        "draft": bool(raw.get("draft")),
        "mergeable": raw.get("mergeable") if isinstance(raw.get("mergeable"), bool) else None,
        "mergeable_state": _bounded(raw.get("mergeable_state"), 60).lower(),
        "base_ref": _bounded(base.get("ref"), 300),
        "base_sha": _bounded(base.get("sha"), 80),
        "head_ref": _bounded(head.get("ref"), 300),
        "head_sha": _bounded(head.get("sha"), 80),
        "html_url": _bounded(raw.get("html_url"), 500),
        "files": _files(owner, name, token, number),
        "reviews": review_github.list_review_evidence(project, number),
    }
