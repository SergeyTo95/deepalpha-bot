from __future__ import annotations

import re
from typing import Any, Dict, Mapping
from urllib.parse import quote

from services import velia_agent_coding_autopilot_merge_github_service as merge_github
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseMergeGithubError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 502) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


def _valid_sha(value: Any) -> str:
    sha = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(sha):
        raise ReleaseMergeGithubError("velia_factory_release_head_sha_invalid", status=400)
    return sha


def _request(*args: Any, **kwargs: Any) -> Any:
    try:
        return github_service._request(*args, **kwargs)
    except github_service.DeveloperGithubError as exc:
        raise ReleaseMergeGithubError(exc.code, detail=exc.detail, status=exc.status) from exc


def pull_state(project: Mapping[str, Any], pull_number: int) -> Dict[str, Any]:
    number = int(pull_number or 0)
    if number <= 0:
        raise ReleaseMergeGithubError("velia_factory_release_pr_invalid", status=400)
    owner, name, token = merge_github._access(project)
    raw = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/pulls/{number}",
        token=token,
    )
    if not isinstance(raw, dict):
        raise ReleaseMergeGithubError("github_invalid_response")
    head = raw.get("head") if isinstance(raw.get("head"), dict) else {}
    base = raw.get("base") if isinstance(raw.get("base"), dict) else {}
    return {
        "number": number,
        "state": str(raw.get("state") or "").strip().lower()[:30],
        "merged": bool(raw.get("merged")),
        "mergeable": raw.get("mergeable") if isinstance(raw.get("mergeable"), bool) else None,
        "mergeable_state": str(raw.get("mergeable_state") or "").strip().lower()[:60],
        "head_sha": str(head.get("sha") or "").strip().lower()[:80],
        "head_ref": str(head.get("ref") or "").strip()[:300],
        "base_sha": str(base.get("sha") or "").strip().lower()[:80],
        "base_ref": str(base.get("ref") or "").strip()[:300],
        "merge_commit_sha": str(raw.get("merge_commit_sha") or "").strip().lower()[:80],
        "html_url": str(raw.get("html_url") or "")[:500],
    }


def merge_exact_head(
    project: Mapping[str, Any],
    *,
    pull_number: int,
    expected_head_sha: str,
    merge_method: str = "merge",
) -> Dict[str, Any]:
    number = int(pull_number or 0)
    if number <= 0:
        raise ReleaseMergeGithubError("velia_factory_release_pr_invalid", status=400)
    expected = _valid_sha(expected_head_sha)
    method = str(merge_method or "merge").strip().lower()
    if method not in {"merge", "squash", "rebase"}:
        raise ReleaseMergeGithubError("velia_factory_release_merge_method_invalid", status=400)

    write_service.require_write_permissions(dict(project))
    before = pull_state(project, number)
    if before.get("merged") is True:
        if str(before.get("head_sha") or "") != expected:
            raise ReleaseMergeGithubError(
                "velia_factory_release_already_merged_head_mismatch",
                detail=str(before.get("head_sha") or ""),
                status=409,
            )
        return {
            "ok": True,
            "merged": True,
            "already_merged": True,
            "pull_request_number": number,
            "head_sha": expected,
            "merge_commit_sha": str(before.get("merge_commit_sha") or ""),
            "state": "closed",
        }
    if str(before.get("state") or "") != "open":
        raise ReleaseMergeGithubError(
            "velia_factory_release_pr_not_open",
            detail=str(before.get("state") or ""),
            status=409,
        )
    if str(before.get("head_sha") or "") != expected:
        raise ReleaseMergeGithubError(
            "velia_factory_release_head_sha_stale",
            detail=str(before.get("head_sha") or ""),
            status=409,
        )

    owner, name, token = merge_github._access(project)
    raw = _request(
        "PUT",
        f"/repos/{quote(owner)}/{quote(name)}/pulls/{number}/merge",
        token=token,
        body={"sha": expected, "merge_method": method},
        expected=(200,),
    )
    if not isinstance(raw, dict) or raw.get("merged") is not True:
        raise ReleaseMergeGithubError(
            "velia_factory_release_merge_rejected",
            detail=str((raw or {}).get("message") if isinstance(raw, dict) else ""),
            status=409,
        )
    merge_sha = str(raw.get("sha") or "").strip().lower()
    after = pull_state(project, number)
    if after.get("merged") is not True:
        raise ReleaseMergeGithubError("velia_factory_release_merge_not_confirmed", status=502)
    return {
        "ok": True,
        "merged": True,
        "already_merged": False,
        "pull_request_number": number,
        "head_sha": expected,
        "merge_commit_sha": str(after.get("merge_commit_sha") or merge_sha),
        "state": str(after.get("state") or "closed"),
    }
