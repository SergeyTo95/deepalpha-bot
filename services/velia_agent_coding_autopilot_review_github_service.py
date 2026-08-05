from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Tuple
from urllib.parse import quote

from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service

logger = logging.getLogger(__name__)
_MAX_REVIEWS = 100
_MAX_INLINE_COMMENTS = 100
_MAX_ISSUE_COMMENTS = 100


class CodingAutopilotReviewGithubError(RuntimeError):
    def __init__(self, code: str, *, status: int = 502, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:500]


def _bounded(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "")[:limit]


def _access(project: Mapping[str, Any]) -> Tuple[str, str, str]:
    try:
        installation_id, repository_id, full_name, _ = write_service._project_values(dict(project))
        owner, name = github_service._validate_full_name(full_name)
        token = github_service._installation_token(installation_id, [repository_id])
        return owner, name, token
    except (github_service.DeveloperGithubError, write_service.DeveloperWriteError) as exc:
        raise CodingAutopilotReviewGithubError(
            str(getattr(exc, "code", "github_review_access_failed")),
            status=int(getattr(exc, "status", 502)),
            detail=str(getattr(exc, "detail", "")),
        ) from exc


def _request(*args: Any, **kwargs: Any) -> Any:
    try:
        return github_service._request(*args, **kwargs)
    except github_service.DeveloperGithubError as exc:
        raise CodingAutopilotReviewGithubError(exc.code, status=exc.status, detail=exc.detail) from exc


def _paged(
    path: str,
    *,
    token: str,
    maximum: int,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for page in range(1, 3):
        data = _request(
            "GET",
            path,
            token=token,
            params={"per_page": min(100, maximum), "page": page},
        )
        if not isinstance(data, list):
            break
        items.extend(item for item in data if isinstance(item, dict))
        if len(data) < 100 or len(items) >= maximum:
            break
    return items[:maximum]


def list_review_evidence(project: Mapping[str, Any], pull_number: int) -> List[Dict[str, Any]]:
    number = int(pull_number or 0)
    if number <= 0:
        raise CodingAutopilotReviewGithubError("velia_coding_autopilot_review_pr_invalid", status=400)
    owner, name, token = _access(project)
    base = f"/repos/{quote(owner)}/{quote(name)}"
    reviews = _paged(
        f"{base}/pulls/{number}/reviews",
        token=token,
        maximum=_MAX_REVIEWS,
    )
    inline = _paged(
        f"{base}/pulls/{number}/comments",
        token=token,
        maximum=_MAX_INLINE_COMMENTS,
    )
    issue_comments = _paged(
        f"{base}/issues/{number}/comments",
        token=token,
        maximum=_MAX_ISSUE_COMMENTS,
    )

    inline_by_review: Dict[int, List[Dict[str, Any]]] = {}
    for item in inline:
        review_id = int(item.get("pull_request_review_id") or 0)
        if review_id <= 0:
            continue
        inline_by_review.setdefault(review_id, []).append(
            {
                "comment_id": int(item.get("id") or 0),
                "path": _bounded(item.get("path"), 500),
                "line": int(item.get("line") or item.get("original_line") or 0),
                "side": _bounded(item.get("side") or item.get("original_side"), 20),
                "body": _bounded(item.get("body"), 4000),
                "commit_id": _bounded(item.get("commit_id"), 80),
                "html_url": _bounded(item.get("html_url"), 500),
            }
        )

    events: List[Dict[str, Any]] = []
    for review in reviews:
        review_id = int(review.get("id") or 0)
        if review_id <= 0:
            continue
        author = review.get("user") if isinstance(review.get("user"), dict) else {}
        state = _bounded(review.get("state"), 40).upper()
        events.append(
            {
                "review_key": f"review:{review_id}",
                "review_id": review_id,
                "kind": "review",
                "state": state,
                "author_login": _bounded(author.get("login"), 160),
                "body": _bounded(review.get("body"), 8000),
                "commit_id": _bounded(review.get("commit_id"), 80),
                "submitted_at": _bounded(review.get("submitted_at"), 80),
                "html_url": _bounded(review.get("html_url"), 500),
                "comments": inline_by_review.get(review_id, [])[:20],
            }
        )

    for comment in issue_comments:
        comment_id = int(comment.get("id") or 0)
        if comment_id <= 0:
            continue
        author = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        events.append(
            {
                "review_key": f"issue_comment:{comment_id}",
                "review_id": 0,
                "kind": "issue_comment",
                "state": "COMMENTED",
                "author_login": _bounded(author.get("login"), 160),
                "body": _bounded(comment.get("body"), 8000),
                "commit_id": "",
                "submitted_at": _bounded(comment.get("created_at"), 80),
                "html_url": _bounded(comment.get("html_url"), 500),
                "comments": [],
            }
        )

    events.sort(key=lambda item: (str(item.get("submitted_at") or ""), str(item.get("review_key") or "")))
    return events[:200]


def reply_after_commit(
    project: Mapping[str, Any],
    pull_number: int,
    evidence: Mapping[str, Any],
    commit_sha: str,
) -> Dict[str, Any]:
    owner, name, token = _access(project)
    number = int(pull_number or 0)
    sha = _bounded(commit_sha, 80)
    body = (
        f"Addressed the requested changes in commit `{sha[:12]}`. "
        "Exact-head CI is running. VELIA did not merge or deploy."
    )
    replied: List[int] = []
    failures: List[int] = []
    comments = evidence.get("comments") if isinstance(evidence.get("comments"), list) else []
    for item in comments[:5]:
        if not isinstance(item, Mapping):
            continue
        comment_id = int(item.get("comment_id") or 0)
        if comment_id <= 0:
            continue
        try:
            _request(
                "POST",
                (
                    f"/repos/{quote(owner)}/{quote(name)}/pulls/{number}/comments/"
                    f"{comment_id}/replies"
                ),
                token=token,
                body={"body": body},
                expected=(201,),
            )
            replied.append(comment_id)
        except Exception:
            logger.exception("VELIA_AUTOPILOT_REVIEW_REPLY_FAILED comment_id=%s", comment_id)
            failures.append(comment_id)
    if not replied and not comments:
        try:
            _request(
                "POST",
                f"/repos/{quote(owner)}/{quote(name)}/issues/{number}/comments",
                token=token,
                body={"body": body},
                expected=(201,),
            )
        except Exception:
            logger.exception("VELIA_AUTOPILOT_REVIEW_PR_COMMENT_FAILED pr=%s", number)
            failures.append(0)
    return {"replied_comment_ids": replied, "reply_failures": failures}
