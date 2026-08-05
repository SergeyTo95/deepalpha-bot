from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_merge_github_service as merge_github
from services import velia_agent_coding_autopilot_policy_service as policy_service
from services import velia_agent_coding_autopilot_review_store as review_store
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service


class CodingAutopilotMergePolicyError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


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


def merge_policy_enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED", False)


def merge_policy_status() -> Dict[str, Any]:
    return {
        "enabled": merge_policy_enabled(),
        "mode": "dry_run",
        "execution_supported": False,
        "auto_merge": False,
        "deployment": False,
        "require_approval": _env_bool(
            "VELIA_DEVELOPER_AUTOPILOT_MERGE_REQUIRE_APPROVAL", True
        ),
        "allow_deletions": False,
        "allow_renames": False,
        "approved_plan_files_only": True,
        "max_changed_lines": _env_int(
            "VELIA_DEVELOPER_AUTOPILOT_MERGE_MAX_CHANGED_LINES", 600, 20, 2000
        ),
    }


def _reason(code: str, detail: Any = "") -> Dict[str, str]:
    return {"code": str(code)[:120], "detail": str(detail or "")[:500]}


def _latest_review_states(events: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for event in events:
        if str(event.get("kind") or "") != "review":
            continue
        author = str(event.get("author_login") or "").strip().casefold()
        key = author or f"review:{event.get('review_id')}"
        latest[key] = event
    approvals: List[str] = []
    requested_changes: List[str] = []
    for key, event in latest.items():
        state = str(event.get("state") or "").upper()
        author = str(event.get("author_login") or key)[:160]
        if state == "APPROVED":
            approvals.append(author)
        elif state in {"CHANGES_REQUESTED", "REQUEST_CHANGES"}:
            requested_changes.append(author)
    return approvals, requested_changes


def _validate_files(
    files: List[Dict[str, Any]],
    *,
    allowed_files: List[str],
    mission: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    reasons: List[Dict[str, Any]] = []
    approved = set(allowed_files)
    policy = policy_service.normalize_policy(
        allowed_paths=list(mission.get("allowed_paths") or []),
        blocked_paths=list(mission.get("blocked_paths") or []),
        max_steps=int(mission.get("max_steps") or 1),
        max_files=int(mission.get("max_files") or 1),
    )
    additions = 0
    deletions = 0
    changes = 0
    normalized_files: List[Dict[str, Any]] = []
    for item in files:
        raw_path = str(item.get("filename") or "").strip()
        try:
            path = github_service.validate_path(raw_path)
        except Exception:
            reasons.append(_reason("merge_policy_path_invalid", raw_path))
            continue
        status = str(item.get("status") or "").lower()
        additions += max(0, int(item.get("additions") or 0))
        deletions += max(0, int(item.get("deletions") or 0))
        changes += max(0, int(item.get("changes") or 0))
        normalized_files.append({**dict(item), "filename": path})
        if path not in approved:
            reasons.append(_reason("merge_policy_file_not_in_approved_plan", path))
        try:
            if not policy_service.path_allowed(path, policy):
                reasons.append(_reason("merge_policy_path_denied", path))
        except Exception:
            reasons.append(_reason("merge_policy_path_denied", path))
        if status == "removed":
            reasons.append(_reason("merge_policy_deletion_not_allowed", path))
        if status == "renamed" or str(item.get("previous_filename") or "").strip():
            reasons.append(_reason("merge_policy_rename_not_allowed", path))
        if not bool(item.get("patch_present")):
            reasons.append(_reason("merge_policy_unreviewable_diff", path))

    max_files = int(mission.get("max_files") or 0)
    if len(normalized_files) > max_files:
        reasons.append(_reason("merge_policy_file_limit_exceeded", len(normalized_files)))
    max_changed = _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_MERGE_MAX_CHANGED_LINES", 600, 20, 2000
    )
    max_additions = _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_MERGE_MAX_ADDITIONS", 400, 10, 1500
    )
    max_deletions = _env_int(
        "VELIA_DEVELOPER_AUTOPILOT_MERGE_MAX_DELETIONS", 200, 0, 1000
    )
    if changes > max_changed:
        reasons.append(_reason("merge_policy_changed_lines_exceeded", changes))
    if additions > max_additions:
        reasons.append(_reason("merge_policy_additions_exceeded", additions))
    if deletions > max_deletions:
        reasons.append(_reason("merge_policy_deletions_exceeded", deletions))
    return reasons, {
        "files": normalized_files,
        "file_count": len(normalized_files),
        "additions": additions,
        "deletions": deletions,
        "changes": changes,
    }


def evaluate_merge_policy(user_id: int, run_id: str) -> Dict[str, Any]:
    if not merge_policy_enabled():
        raise CodingAutopilotMergePolicyError(
            "velia_coding_autopilot_merge_policy_disabled", status=503
        )
    run = autopilot.get_run(int(user_id), str(run_id))
    project, mission = ci_service._project_and_mission(run)
    job = ci_service._coding_job(run)
    allowed_files = ci_service._allowed_repair_files(job, mission)
    snapshot = merge_github.pull_snapshot(
        project,
        int(run.get("pull_request_number") or 0),
    )
    reasons: List[Dict[str, Any]] = []

    if str(run.get("status") or "") != "ready_for_review":
        reasons.append(_reason("merge_policy_run_not_ready", run.get("status")))
    if str(mission.get("mode") or "") != "draft_pr_only":
        reasons.append(_reason("merge_policy_mode_invalid", mission.get("mode")))
    if str(snapshot.get("state") or "") != "open":
        reasons.append(_reason("merge_policy_pull_request_not_open", snapshot.get("state")))
    if str(snapshot.get("base_ref") or "") != str(mission.get("base_branch") or ""):
        reasons.append(_reason("merge_policy_base_branch_changed", snapshot.get("base_ref")))
    if str(snapshot.get("head_ref") or "") != str(run.get("work_branch") or ""):
        reasons.append(_reason("merge_policy_work_branch_changed", snapshot.get("head_ref")))

    branch_head = write_service.branch_head(project, str(run.get("work_branch") or ""))
    branch_sha = str(branch_head.get("sha") or "")
    if branch_sha != str(snapshot.get("head_sha") or ""):
        reasons.append(_reason("merge_policy_pull_head_mismatch", branch_sha))

    attempt = ci_service._current_attempt(str(run.get("run_id") or ""))
    if not attempt or str(attempt.get("status") or "") != "success":
        reasons.append(_reason("merge_policy_exact_head_ci_not_success"))
    elif str(attempt.get("head_sha") or "") != branch_sha:
        reasons.append(_reason("merge_policy_ci_head_stale", attempt.get("head_sha")))

    file_reasons, diff = _validate_files(
        list(snapshot.get("files") or []),
        allowed_files=allowed_files,
        mission=mission,
    )
    reasons.extend(file_reasons)

    approvals, requested_changes = _latest_review_states(
        list(snapshot.get("reviews") or [])
    )
    if requested_changes:
        reasons.append(_reason("merge_policy_changes_requested", ",".join(requested_changes)))
    if _env_bool("VELIA_DEVELOPER_AUTOPILOT_MERGE_REQUIRE_APPROVAL", True) and not approvals:
        reasons.append(_reason("merge_policy_approval_required"))

    try:
        persisted = review_store.list_review_actions(int(user_id), str(run_id))
    except Exception:
        persisted = []
    unresolved = [
        item for item in persisted
        if str(item.get("status") or "") in {"actionable", "repairing", "blocked"}
    ]
    if unresolved:
        reasons.append(_reason("merge_policy_review_actions_unresolved", len(unresolved)))

    if snapshot.get("mergeable") is not True:
        reasons.append(_reason("merge_policy_not_mergeable", snapshot.get("mergeable_state")))
    elif str(snapshot.get("mergeable_state") or "") not in {"clean", "has_hooks"}:
        reasons.append(_reason("merge_policy_mergeable_state_not_clean", snapshot.get("mergeable_state")))
    if bool(snapshot.get("draft")):
        reasons.append(_reason("merge_policy_pull_request_is_draft"))

    codes = [str(item.get("code") or "") for item in reasons]
    if not reasons:
        recommendation = "eligible"
        would_allow_merge = True
    elif set(codes) == {"merge_policy_pull_request_is_draft"}:
        recommendation = "ready_to_mark_ready"
        would_allow_merge = False
    else:
        recommendation = "not_ready"
        would_allow_merge = False

    return {
        "ok": True,
        "run_id": str(run.get("run_id") or ""),
        "mode": "dry_run",
        "execution_supported": False,
        "auto_merge": False,
        "deployment": False,
        "recommendation": recommendation,
        "would_allow_merge": would_allow_merge,
        "reasons": reasons,
        "gates": {
            "run_status": str(run.get("status") or ""),
            "branch_head": branch_sha,
            "ci_attempt": attempt or {},
            "approvals": approvals,
            "requested_changes": requested_changes,
            "unresolved_review_actions": len(unresolved),
            "diff": diff,
            "pull_request": {
                "number": int(snapshot.get("number") or 0),
                "state": str(snapshot.get("state") or ""),
                "draft": bool(snapshot.get("draft")),
                "mergeable": snapshot.get("mergeable"),
                "mergeable_state": str(snapshot.get("mergeable_state") or ""),
                "base_ref": str(snapshot.get("base_ref") or ""),
                "head_ref": str(snapshot.get("head_ref") or ""),
                "head_sha": str(snapshot.get("head_sha") or ""),
            },
        },
        "evaluated_at": datetime.utcnow().isoformat() + "Z",
    }
