from __future__ import annotations

import re
from typing import Any, Dict, Mapping
from urllib.parse import quote

from services import velia_agent_coding_autopilot_merge_github_service as merge_github
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service
from services import velia_software_factory_release_merge_github_service as release_github

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseVerificationGithubError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 502) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


def _request(*args: Any, **kwargs: Any) -> Any:
    try:
        return github_service._request(*args, **kwargs)
    except github_service.DeveloperGithubError as exc:
        raise ReleaseVerificationGithubError(exc.code, detail=exc.detail, status=exc.status) from exc


def _sha(value: Any, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(text):
        raise ReleaseVerificationGithubError(code, detail=text[:80], status=409)
    return text


def verify_merged_pull(
    project: Mapping[str, Any],
    *,
    pull_number: int,
    expected_head_sha: str,
    expected_merge_commit_sha: str = "",
) -> Dict[str, Any]:
    number = int(pull_number or 0)
    if number <= 0:
        raise ReleaseVerificationGithubError("velia_factory_release_verification_pr_invalid", status=400)
    expected_head = _sha(expected_head_sha, "velia_factory_release_verification_head_invalid")
    state = release_github.pull_state(project, number)
    actual_head = _sha(state.get("head_sha"), "velia_factory_release_verification_head_missing")
    if actual_head != expected_head:
        raise ReleaseVerificationGithubError(
            "velia_factory_release_verification_head_mismatch",
            detail=f"expected={expected_head[:12]} actual={actual_head[:12]}",
            status=409,
        )
    if state.get("merged") is not True or str(state.get("state") or "") != "closed":
        raise ReleaseVerificationGithubError(
            "velia_factory_release_verification_pr_not_merged",
            detail=str(state.get("state") or ""),
            status=409,
        )
    merge_sha = _sha(
        state.get("merge_commit_sha"),
        "velia_factory_release_verification_merge_commit_missing",
    )
    expected_merge = str(expected_merge_commit_sha or "").strip().lower()
    if expected_merge:
        expected_merge = _sha(
            expected_merge,
            "velia_factory_release_verification_expected_merge_commit_invalid",
        )
        if merge_sha != expected_merge:
            raise ReleaseVerificationGithubError(
                "velia_factory_release_verification_merge_commit_mismatch",
                detail=f"expected={expected_merge[:12]} actual={merge_sha[:12]}",
                status=409,
            )

    installation_id, repository_id, full_name, base_branch = write_service._project_values(dict(project))
    del installation_id, repository_id
    if str(state.get("base_ref") or "") != str(base_branch or ""):
        raise ReleaseVerificationGithubError(
            "velia_factory_release_verification_base_branch_changed",
            detail=str(state.get("base_ref") or ""),
            status=409,
        )
    base = write_service.branch_head(dict(project), str(base_branch))
    base_head = _sha(
        base.get("sha"),
        "velia_factory_release_verification_base_head_missing",
    )
    owner, name, token = merge_github._access(project)
    comparison_status = "identical"
    ahead_by = 0
    behind_by = 0
    if base_head != merge_sha:
        raw = _request(
            "GET",
            f"/repos/{quote(owner)}/{quote(name)}/compare/{quote(merge_sha)}...{quote(base_head)}",
            token=token,
        )
        if not isinstance(raw, dict):
            raise ReleaseVerificationGithubError("github_invalid_response")
        comparison_status = str(raw.get("status") or "").strip().lower()
        ahead_by = int(raw.get("ahead_by") or 0)
        behind_by = int(raw.get("behind_by") or 0)
        if comparison_status not in {"ahead", "identical"} or behind_by != 0:
            raise ReleaseVerificationGithubError(
                "velia_factory_release_verification_merge_not_in_base",
                detail=f"status={comparison_status} ahead={ahead_by} behind={behind_by}",
                status=409,
            )

    return {
        "ok": True,
        "verified": True,
        "repository_full_name": str(full_name),
        "pull_request_number": number,
        "expected_head_sha": expected_head,
        "actual_head_sha": actual_head,
        "merge_commit_sha": merge_sha,
        "base_branch": str(base_branch),
        "base_head_sha": base_head,
        "comparison_status": comparison_status,
        "ahead_by": ahead_by,
        "behind_by": behind_by,
    }
