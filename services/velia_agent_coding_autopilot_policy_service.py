from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from services import velia_developer_github_service as github_service


class CodingAutopilotPolicyError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


_PROTECTED_PREFIXES = (
    ".github",
    ".env",
    "secrets",
    "credentials",
    "private_keys",
    "auth",
    "billing",
    "migrations",
    "infrastructure",
    "terraform",
)


def _path_prefix(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/").strip("/")
    if not raw or len(raw) > 300 or "\x00" in raw or "*" in raw:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_path_invalid")
    try:
        return github_service.validate_path(raw)
    except github_service.DeveloperGithubError as exc:
        raise CodingAutopilotPolicyError(
            "velia_coding_autopilot_path_invalid",
            detail=raw,
        ) from exc


def _matches(path: str, prefix: str) -> bool:
    normalized_path = str(path or "").strip("/")
    normalized_prefix = str(prefix or "").strip("/")
    return normalized_path == normalized_prefix or normalized_path.startswith(normalized_prefix + "/")


def _dedupe(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        normalized = str(value or "")
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def normalize_policy(
    *,
    allowed_paths: Any,
    blocked_paths: Any = None,
    max_steps: Any = 4,
    max_files: Any = 8,
) -> Dict[str, Any]:
    if not isinstance(allowed_paths, list) or not allowed_paths:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_allowed_paths_required")
    if len(allowed_paths) > 20:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_allowed_paths_too_many")
    allowed = _dedupe(_path_prefix(item) for item in allowed_paths)

    extras = blocked_paths if isinstance(blocked_paths, list) else []
    if len(extras) > 30:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_blocked_paths_too_many")
    blocked = _dedupe([*_PROTECTED_PREFIXES, *(_path_prefix(item) for item in extras)])

    for prefix in allowed:
        if any(_matches(prefix, protected) or _matches(protected, prefix) for protected in _PROTECTED_PREFIXES):
            raise CodingAutopilotPolicyError(
                "velia_coding_autopilot_protected_path",
                detail=prefix,
                status=403,
            )

    try:
        steps = int(max_steps)
        files = int(max_files)
    except (TypeError, ValueError) as exc:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_limits_invalid") from exc
    if steps < 1 or steps > 5:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_max_steps_invalid")
    if files < 1 or files > 12:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_max_files_invalid")

    return {
        "allowed_paths": allowed,
        "blocked_paths": blocked,
        "max_steps": steps,
        "max_files": files,
        "draft_pr_only": True,
    }


def path_allowed(path: str, policy: Mapping[str, Any]) -> bool:
    normalized = _path_prefix(path)
    allowed = [str(item) for item in policy.get("allowed_paths") or []]
    blocked = [str(item) for item in policy.get("blocked_paths") or []]
    if not any(_matches(normalized, prefix) for prefix in allowed):
        return False
    return not any(_matches(normalized, prefix) for prefix in blocked)


def validate_plan(plan: Any, policy: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise CodingAutopilotPolicyError("velia_coding_autopilot_plan_invalid", status=422)
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise CodingAutopilotPolicyError("velia_coding_autopilot_plan_empty", status=422)
    if len(steps) > int(policy.get("max_steps") or 0):
        raise CodingAutopilotPolicyError(
            "velia_coding_autopilot_plan_steps_exceeded",
            detail=str(len(steps)),
            status=422,
        )

    files: List[str] = []
    seen = set()
    for step in steps:
        if not isinstance(step, Mapping):
            raise CodingAutopilotPolicyError("velia_coding_autopilot_plan_invalid", status=422)
        raw_files = step.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise CodingAutopilotPolicyError("velia_coding_autopilot_plan_files_empty", status=422)
        for raw_path in raw_files:
            path = _path_prefix(raw_path)
            if not path_allowed(path, policy):
                raise CodingAutopilotPolicyError(
                    "velia_coding_autopilot_plan_path_denied",
                    detail=path,
                    status=403,
                )
            if path not in seen:
                seen.add(path)
                files.append(path)
    if len(files) > int(policy.get("max_files") or 0):
        raise CodingAutopilotPolicyError(
            "velia_coding_autopilot_plan_files_exceeded",
            detail=str(len(files)),
            status=422,
        )
    return {
        "steps": len(steps),
        "files": files,
        "draft_pr_only": True,
    }
