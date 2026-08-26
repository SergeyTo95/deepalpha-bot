from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from typing import Any, Dict, Mapping

from db.database import get_connection
from services import velia_agent_coding_autopilot_merge_policy_service as merge_policy
from services import velia_software_factory_delivery_gate_service as delivery
from services import velia_software_factory_release_execution_service as release_execution
from services import velia_software_factory_stage8_release_runtime_patch as release_runtime
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False

_RELEASE_ACTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.UNICODE)
    for pattern in (
        r"\b(?:build|implement|fix|create|finish|complete)\b[^.!?\n]{0,100}\b(?:and|then)\s+(?:deploy|publish|ship|release)\b",
        r"\b(?:deploy|publish|ship|release)\s+(?:it|this|the\s+(?:app|application|project|service|build|release))\b",
        r"\b(?:deploy|ship|push|roll(?:\s+out)?)\b[^.!?\n]{0,50}\b(?:to|into)\s+(?:prod|production)\b",
        r"\b(?:merge|squash)\b[^.!?\n]{0,30}\b(?:and|&)\b[^.!?\n]{0,30}\b(?:deploy|publish|ship|release)\b",
        r"\b(?:deploy|publish|ship|release)\s+(?:now|today)\b",
        r"\bgo\s+live\b",
        r"\b(?:задеплой|деплойни|выкати|выкатывай|опубликуй|зарелизь)\b",
        r"\b(?:смержи|мержи|слей)\b[^.!?\n]{0,30}\b(?:и|&)\b[^.!?\n]{0,30}\b(?:задеплой|деплойни|выкати|опубликуй|зарелизь)\b",
        r"\b(?:сделай|создай|доделай|заверши|почини)\b[^.!?\n]{0,100}\b(?:и|затем)\s*(?:задеплой|деплойни|выкати|опубликуй|зарелизь)\b",
        r"\b(?:выложи|выкати|запусти)\b[^.!?\n]{0,50}\b(?:в\s+прод|в\s+production)\b",
    )
)

_DEFERRED_USER_APPROVAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.UNICODE)
    for pattern in (
        r"\b(?:only\s+)?(?:after|once|when)\s+(?:i|we)\s+(?:approve|confirm|authorize|permit|say\s+go|give\s+(?:the\s+)?go(?:-ahead)?)\b",
        r"\b(?:only\s+)?after\s+(?:my|our)\s+(?:approval|confirmation|authorization|permission|go(?:-ahead)?)\b",
        r"\b(?:wait\s+for|require|get|ask\s+for)\s+(?:my|our)\s+(?:approval|confirmation|authorization|permission|go(?:-ahead)?)\b",
        r"\b(?:until|before)\s+(?:i|we)\s+(?:approve|confirm|authorize|permit|say\s+go|give\s+(?:the\s+)?go(?:-ahead)?)\b",
        r"\b(?:if|provided(?:\s+that)?)\s+(?:i|we)\s+(?:approve|confirm|authorize|permit|say\s+go|give\s+(?:the\s+)?go(?:-ahead)?)\b",
        r"\b(?:if|provided(?:\s+that)?|subject\s+to)\s+(?:my|our)\s+(?:approval|confirmation|authorization|permission|go(?:-ahead)?)\b",
        r"\bsubject\s+to\s+(?:approval|confirmation|authorization|permission)\s+(?:from|by)\s+(?:me|us)\b",
        r"\b(?:if|provided(?:\s+that)?|after|once|when|until|before)\b[^.!?\n]{0,80}\b(?:i|we)\b[^.!?\n]{0,30}\b(?:approve|confirm|authorize|permit|say\s+go|give\s+(?:the\s+)?go(?:-ahead)?)\b",
        r"\b(?:if|provided(?:\s+that)?|subject\s+to|after|once|when|until|before)\b[^.!?\n]{0,80}\b(?:approved|confirmed|authorized|permitted)\b[^.!?\n]{0,30}\bby\s+(?:me|us)\b",
        r"\b(?:if|provided(?:\s+that)?|subject\s+to|after|once|when|until|before)\b[^.!?\n]{0,80}\b(?:approval|confirmation|authorization|permission)\b[^.!?\n]{0,40}\b(?:is|was|has\s+been|had\s+been|will\s+be)\s+(?:given|granted|provided|issued|approved|confirmed|authorized|permitted)\b[^.!?\n]{0,30}\bby\s+(?:me|us)\b",
        r"\b(?:if|provided(?:\s+that)?|subject\s+to)\b[^.!?\n]{0,80}\b(?:my|our)\b[^.!?\n]{0,20}\b(?:approval|confirmation|authorization|permission|go(?:-ahead)?)\b",
        r"\b(?:только\s+)?после\s+(?:того\s+как\s+)?(?:я|мы)\s+(?:одобрю|одобрим|подтвержу|подтвердим|разрешу|разрешим|скажу|скажем|дам|дадим)\b",
        r"\b(?:только\s+)?после\s+(?:моего|нашего)\s+(?:одобрения|подтверждения|разрешения|согласия|гоу?|go)\b",
        r"\bкогда\s+(?:я|мы)\s+(?:одобрю|одобрим|подтвержу|подтвердим|разрешу|разрешим|скажу|скажем|дам|дадим)\b",
        r"\bесли\s+(?:я|мы)\s+(?:одобрю|одобрим|подтвержу|подтвердим|разрешу|разрешим|скажу|скажем|дам|дадим)\b",
        r"\bпри\s+условии\s+(?:моего|нашего)\s+(?:одобрения|подтверждения|разрешения|согласия)\b",
        r"\bпри\s+(?:моем|моём|нашем)\s+(?:одобрении|подтверждении|разрешении|согласии)\b",
        r"\b(?:сначала\s+)?(?:спроси|получи|дождись)\b[^.!?\n]{0,50}\b(?:моего|моё|мое|нашего|наше)\s+(?:одобрения|подтверждения|разрешения|согласия)\b",
        r"\b(?:до|перед)\s+(?:тем\s+как\s+)?(?:я|мы)\s+(?:одобрю|одобрим|подтвержу|подтвердим|разрешу|разрешим|скажу|скажем|дам|дадим)\b",
        r"\b(?:если|когда|после|до|перед)\b[^.!?\n]{0,80}\b(?:я|мы)\b[^.!?\n]{0,30}(?:одобр|подтверд|подтверж|разреш|соглас)\w*",
        r"\b(?:если|когда|после|до|перед|при\s+условии)\b[^.!?\n]{0,80}(?:(?:одобрен|подтвержд[её]н|разреш[её]н|утвержд[её]н)(?:о|а|ы)?|согласован(?:о|а|ы)?)[^.!?\n]{0,30}\b(?:мной|нами)\b",
    )
)

_PREMERGE_RETRY_BLOCKERS = {
    "velia_factory_delivery_candidate_stale",
    "velia_factory_release_preflight_stale",
    "velia_factory_release_preflight_not_prepared",
}
_ZERO_MERGE_RETRYABLE_STATUSES = {"blocked", "failed"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@contextmanager
def _release_operation_lock(release_execution_id: str):
    """Serialize Stage 8 retry rotation with merge execution and explicit stop requests."""
    release_id = str(release_execution_id or "").strip()
    if not release_id:
        raise SoftwareFactoryError("velia_factory_stage8_release_lock_id_missing", status=409)
    conn = get_connection()
    cursor = conn.cursor()
    locked = False
    key = release_execution._lock_key(release_id)
    try:
        cursor.execute("SELECT pg_advisory_lock(%s)", (key,))
        locked = True
        yield conn, cursor
    finally:
        if locked:
            try:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (key,))
                conn.commit()
            except Exception:
                conn.rollback()
        cursor.close()
        conn.close()


def _acceptance_profile_fingerprint(
    project_id: str,
    profile: Mapping[str, Any] | None,
    deployment_profile: Mapping[str, Any] | None,
) -> str:
    """Recompute the exact persisted Stage 5 acceptance-profile fingerprint."""
    if (
        not isinstance(profile, Mapping)
        or not isinstance(deployment_profile, Mapping)
        or not bool(profile.get("enabled"))
        or not bool(deployment_profile.get("enabled"))
    ):
        return ""
    deployment_fingerprint = str(deployment_profile.get("profile_fingerprint") or "").strip()
    if not deployment_fingerprint:
        return ""
    contexts = [
        str(item).strip()
        for item in profile.get("expected_contexts") or []
        if str(item).strip()
    ]
    if not contexts:
        return ""
    payload = {
        "project_id": str(project_id),
        "repository_full_name": str(profile.get("repository_full_name") or ""),
        "branch": str(profile.get("base_branch") or profile.get("branch") or ""),
        "expected_contexts": contexts,
        "deployment_profile_fingerprint": deployment_fingerprint,
        "enabled": True,
    }
    return _fingerprint(payload)


def _deferred_user_approval(objective: str) -> bool:
    text = str(objective or "").strip()
    return bool(text) and any(pattern.search(text) for pattern in _DEFERRED_USER_APPROVAL_PATTERNS)


def _strict_release_authorized(execution: Mapping[str, Any]) -> bool:
    plan = execution.get("plan") if isinstance(execution.get("plan"), Mapping) else {}
    objective = str(plan.get("objective") or "").strip()
    if (
        not objective
        or release_runtime._negative_release_intent(objective)
        or _deferred_user_approval(objective)
    ):
        return False
    return any(pattern.search(objective) for pattern in _RELEASE_ACTION_PATTERNS)


def _live_pr_binding(user_id: int, item: Mapping[str, Any]) -> Dict[str, str]:
    run_id = str(item.get("run_id") or "").strip()
    if not run_id:
        raise SoftwareFactoryError("velia_factory_stage8_release_pr_binding_missing", status=409)
    policy = merge_policy.evaluate_merge_policy(int(user_id), run_id)
    gates = policy.get("gates") if isinstance(policy.get("gates"), Mapping) else {}
    pull = gates.get("pull_request") if isinstance(gates.get("pull_request"), Mapping) else {}
    base_branch = str(pull.get("base_ref") or "").strip()
    head_sha = str(pull.get("head_sha") or gates.get("branch_head") or "").strip()
    number = str(pull.get("number") or "").strip()
    expected_head = str(item.get("head_sha") or "").strip()
    expected_number = str(item.get("pull_request_number") or "").strip()
    if not base_branch or not head_sha or (expected_head and head_sha != expected_head):
        raise SoftwareFactoryError("velia_factory_stage8_release_pr_binding_changed", status=409)
    if expected_number and number and number != expected_number:
        raise SoftwareFactoryError("velia_factory_stage8_release_pr_binding_changed", status=409)
    return {"base_branch": base_branch, "head_sha": head_sha, "pull_request_number": number}


def _bind_candidate_base_branches(
    original_build: Any,
    execution_module: Any,
    user_id: int,
    execution_id: str,
) -> Dict[str, Any]:
    snapshot = dict(original_build(execution_module, int(user_id), str(execution_id)) or {})
    repositories = [
        dict(item) for item in snapshot.get("repositories") or [] if isinstance(item, Mapping)
    ]
    blockers = [
        dict(item) for item in snapshot.get("blockers") or [] if isinstance(item, Mapping)
    ]
    bindings: list[Dict[str, str]] = []
    for item in repositories:
        try:
            binding = _live_pr_binding(int(user_id), item)
            item["base_branch"] = binding["base_branch"]
            bindings.append(
                {
                    "project_id": str(item.get("project_id") or ""),
                    "run_id": str(item.get("run_id") or ""),
                    "head_sha": binding["head_sha"],
                    "base_branch": binding["base_branch"],
                    "pull_request_number": binding["pull_request_number"],
                }
            )
        except Exception as exc:
            item["base_branch"] = ""
            blockers.append(
                {
                    "code": str(getattr(exc, "code", "delivery_pr_base_branch_unverified"))[:120],
                    "task_id": "",
                    "detail": str(item.get("repository_full_name") or item.get("project_id") or "")[:300],
                }
            )
    snapshot["repositories"] = repositories
    snapshot["blockers"] = blockers[:100]
    if blockers or not repositories or any(not str(item.get("base_branch") or "") for item in repositories):
        snapshot["status"] = "blocked"
        snapshot["release_eligible"] = False
    snapshot["source_fingerprint"] = _fingerprint(
        {
            "prior_source_fingerprint": str(snapshot.get("source_fingerprint") or ""),
            "pr_base_bindings": bindings,
        }
    )
    return snapshot


def _assert_profiles_ready(execution_module: Any, user_id: int, candidate: Mapping[str, Any]) -> None:
    get_deployment = getattr(execution_module, "get_deployment_profile", None)
    get_acceptance = getattr(execution_module, "get_acceptance_profile", None)
    if not callable(get_deployment) or not callable(get_acceptance):
        raise SoftwareFactoryError("velia_factory_stage8_release_profiles_runtime_missing", status=503)

    repositories = [item for item in candidate.get("repositories") or [] if isinstance(item, Mapping)]
    if not repositories:
        raise SoftwareFactoryError("velia_factory_stage8_release_profiles_required", status=409)

    for item in repositories:
        project_id = str(item.get("project_id") or "").strip()
        repository = str(item.get("repository_full_name") or "").strip()
        binding = _live_pr_binding(int(user_id), item)
        branch = str(item.get("base_branch") or binding.get("base_branch") or "").strip()
        if not project_id or not repository or not branch or branch != binding.get("base_branch"):
            raise SoftwareFactoryError(
                "velia_factory_stage8_release_pr_binding_changed",
                detail=repository or project_id,
                status=409,
            )
        try:
            deployment_profile = get_deployment(int(user_id), project_id, branch)
            acceptance_profile = get_acceptance(int(user_id), project_id, branch)
        except Exception as exc:
            raise SoftwareFactoryError(
                "velia_factory_stage8_release_profiles_required",
                detail=f"{repository}:{branch}",
                status=409,
            ) from exc

        expected_repo = repository.casefold()
        for kind, profile in (("deployment", deployment_profile), ("acceptance", acceptance_profile)):
            if not isinstance(profile, Mapping) or not bool(profile.get("enabled")):
                raise SoftwareFactoryError(
                    "velia_factory_stage8_release_profiles_required",
                    detail=f"{repository}:{kind}",
                    status=409,
                )
            profile_repo = str(profile.get("repository_full_name") or "").strip().casefold()
            profile_branch = str(profile.get("base_branch") or profile.get("branch") or "").strip()
            contexts = {
                str(value).strip()
                for value in profile.get("expected_contexts") or []
                if str(value).strip()
            }
            if profile_repo != expected_repo or profile_branch != branch or not contexts:
                raise SoftwareFactoryError(
                    "velia_factory_stage8_release_profiles_required",
                    detail=f"{repository}:{kind}",
                    status=409,
                )

        deployment_contexts = {
            str(value).strip()
            for value in deployment_profile.get("expected_contexts") or []
            if str(value).strip()
        }
        acceptance_contexts = {
            str(value).strip()
            for value in acceptance_profile.get("expected_contexts") or []
            if str(value).strip()
        }
        if deployment_contexts & acceptance_contexts:
            raise SoftwareFactoryError(
                "velia_factory_stage8_release_profiles_overlap",
                detail=repository,
                status=409,
            )
        expected_acceptance_fingerprint = _acceptance_profile_fingerprint(
            project_id, acceptance_profile, deployment_profile
        )
        actual_acceptance_fingerprint = str(
            acceptance_profile.get("profile_fingerprint") or ""
        ).strip()
        if (
            not expected_acceptance_fingerprint
            or actual_acceptance_fingerprint != expected_acceptance_fingerprint
        ):
            raise SoftwareFactoryError(
                "velia_factory_acceptance_profile_stale",
                detail=repository,
                status=409,
            )


def _observation_profiles_current(
    execution_module: Any,
    user_id: int,
    observation: Mapping[str, Any],
) -> bool:
    get_deployment = getattr(execution_module, "get_deployment_profile", None)
    repositories = [
        item for item in observation.get("repositories") or [] if isinstance(item, Mapping)
    ]
    if not callable(get_deployment) or not repositories:
        return False
    for item in repositories:
        project_id = str(item.get("project_id") or "").strip()
        branch = str(item.get("branch") or "").strip()
        evidence_fingerprint = str(item.get("profile_fingerprint") or "").strip()
        if not project_id or not branch or not evidence_fingerprint:
            return False
        try:
            current = get_deployment(int(user_id), project_id, branch)
        except Exception:
            return False
        if (
            not isinstance(current, Mapping)
            or not bool(current.get("enabled"))
            or str(current.get("profile_fingerprint") or "").strip() != evidence_fingerprint
        ):
            return False
    return True


def _zero_merge_terminal_release(release: Mapping[str, Any]) -> bool:
    """Only reset terminal release attempts when merge side effects and stop intent are absent."""
    if str(release.get("status") or "") not in _ZERO_MERGE_RETRYABLE_STATUSES:
        return False
    if bool(release.get("stop_requested")):
        return False
    if int(release.get("merged_count") or 0) != 0:
        return False
    items = [item for item in release.get("items") or [] if isinstance(item, Mapping)]
    if not items:
        return False
    for item in items:
        if str(item.get("status") or "") == "merged":
            return False
        if str(item.get("merge_commit_sha") or "").strip():
            return False
    return True


def _hardened_request_stop(
    original_request_stop: Any,
    execution_module: Any,
    user_id: int,
    release_execution_id: str,
) -> Dict[str, Any]:
    """Serialize stop with execute/retry and preserve stops that arrive as a zero-merge failure terminalizes."""
    release_id = str(release_execution_id)
    with _release_operation_lock(release_id) as (conn, cursor):
        current = release_execution.get_execution(execution_module, int(user_id), release_id)
        if _zero_merge_terminal_release(current):
            cursor.execute(
                "UPDATE velia_software_factory_release_executions "
                "SET stop_requested=TRUE,updated_at=%s "
                "WHERE execution_id=%s AND user_id=%s AND status IN ('blocked','failed') "
                "AND merged_count=0 AND stop_requested=FALSE RETURNING execution_id",
                (release_execution._utcnow(), release_id, int(user_id)),
            )
            marked = cursor.fetchone()
            conn.commit()
            if marked:
                release_execution._event(
                    release_id,
                    int(user_id),
                    "release_execution.stop_requested",
                    {"terminal_zero_merge": True},
                )
            return release_execution.get_execution(execution_module, int(user_id), release_id)
        return original_request_stop(execution_module, int(user_id), release_id)


def _rotate_zero_merge_preflight(
    execution_module: Any,
    user_id: int,
    state: Mapping[str, Any],
    release: Mapping[str, Any],
) -> None:
    """Retire zero-merge state while holding the same advisory lock used by stop and merge execution."""
    release_id = str(release.get("execution_id") or state.get("release_execution_id") or "").strip()
    if not release_id:
        raise SoftwareFactoryError("velia_factory_stage8_zero_merge_release_recheck_unavailable", status=503)
    with _release_operation_lock(release_id):
        current = execution_module.get_release_execution(int(user_id), release_id)
        if not isinstance(current, Mapping) or not _zero_merge_terminal_release(current):
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_release_no_longer_retryable", status=409
            )
        cancel = getattr(execution_module, "cancel_release_preflight", None)
        if not callable(cancel):
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_preflight_rotation_unavailable", status=503
            )
        plan_id = str(state.get("plan_id") or current.get("plan_id") or release.get("plan_id") or "").strip()
        if not plan_id:
            raise SoftwareFactoryError("velia_factory_stage8_zero_merge_preflight_missing", status=409)
        rotated = cancel(int(user_id), plan_id)
        status = str((rotated or {}).get("status") or "") if isinstance(rotated, Mapping) else ""
        if status not in {"cancelled", "stale"}:
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_preflight_not_rotated",
                detail=status or plan_id,
                status=409,
            )
        release_runtime._save_state(
            execution_module,
            int(user_id),
            str(state.get("workspace_execution_id") or ""),
        ) if False else None


def _refresh_retryable_evidence(execution_module: Any, user_id: int, execution_id: str) -> None:
    state = release_runtime._state(execution_module, int(user_id), str(execution_id))

    release_execution_id = str(state.get("release_execution_id") or "")
    if release_execution_id:
        try:
            release = execution_module.get_release_execution(int(user_id), release_execution_id)
        except Exception:
            release = {}
        if isinstance(release, Mapping) and _zero_merge_terminal_release(release):
            _rotate_zero_merge_preflight(execution_module, int(user_id), state, release)
            release_runtime._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                candidate_id="",
                plan_id="",
                release_execution_id="",
                verification_id="",
                observation_id="",
                certificate_id="",
                passport_id="",
                status="retrying_candidate",
                blocker_code="",
                blocker_detail="",
            )
            return

    if not release_execution_id:
        blocker_code = str(state.get("blocker_code") or "")
        plan_id = str(state.get("plan_id") or "")
        stale_plan = False
        if plan_id:
            try:
                plan = execution_module.get_release_preflight(int(user_id), plan_id)
                stale_plan = str(plan.get("status") or "") != "prepared"
            except SoftwareFactoryError as exc:
                stale_plan = str(getattr(exc, "code", "")) == "velia_factory_release_preflight_not_found"
        if stale_plan or blocker_code in _PREMERGE_RETRY_BLOCKERS:
            release_runtime._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                candidate_id="",
                plan_id="",
                status="retrying_candidate",
                blocker_code="",
                blocker_detail="",
            )
            return

    verification_id = str(state.get("verification_id") or "")
    if verification_id:
        try:
            verification = execution_module.get_release_verification(int(user_id), verification_id)
        except Exception:
            verification = {}
        if str(verification.get("verification_status") or "") != "verified":
            release_runtime._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                verification_id="",
                observation_id="",
                certificate_id="",
                passport_id="",
                status="merged",
                blocker_code="",
                blocker_detail="",
            )
            return

    observation_id = str(state.get("observation_id") or "")
    if observation_id:
        try:
            observation = execution_module.get_deployment_observation(
                int(user_id), observation_id
            )
        except Exception:
            observation = {}
        if (
            str(observation.get("status") or "") == "success"
            and bool(observation.get("deployment_complete"))
            and not _observation_profiles_current(execution_module, int(user_id), observation)
        ):
            release_runtime._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                observation_id="",
                certificate_id="",
                passport_id="",
                status="deployment_observing",
                blocker_code="",
                blocker_detail="",
            )
            return

    certificate_id = str(state.get("certificate_id") or "")
    if certificate_id:
        try:
            certificate = execution_module.get_release_completion_certificate(
                int(user_id), certificate_id
            )
        except Exception:
            certificate = {}
        if not (
            str(certificate.get("status") or "") == "complete"
            and bool(certificate.get("release_complete"))
        ):
            release_runtime._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                certificate_id="",
                passport_id="",
                status="deployment_success",
                blocker_code="",
                blocker_detail="",
            )


def install(execution_module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if not getattr(execution_module, "_workspace_stage8_release_runtime_installed", False):
        raise RuntimeError("stage8_final_hardening_requires_release_runtime")

    original_build = delivery.build_workspace_candidate_snapshot
    original_progress = release_runtime._progress_release
    original_request_stop = release_execution.request_stop

    def build_workspace_candidate_snapshot(
        execution_module_arg: Any, user_id: int, execution_id: str
    ) -> Dict[str, Any]:
        return _bind_candidate_base_branches(
            original_build,
            execution_module_arg,
            int(user_id),
            str(execution_id),
        )

    def progress_release(execution_module_arg: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
        _refresh_retryable_evidence(
            execution_module_arg, int(user_id), str(execution_id)
        )
        return original_progress(
            execution_module_arg, int(user_id), str(execution_id)
        )

    def request_stop(execution_module_arg: Any, user_id: int, release_execution_id: str) -> Dict[str, Any]:
        return _hardened_request_stop(
            original_request_stop,
            execution_module_arg,
            int(user_id),
            str(release_execution_id),
        )

    delivery.build_workspace_candidate_snapshot = build_workspace_candidate_snapshot
    release_runtime._explicit_release_authorized = _strict_release_authorized
    release_runtime._assert_release_profiles_ready = _assert_profiles_ready
    release_runtime._progress_release = progress_release
    release_execution.request_stop = request_stop
    execution_module._workspace_stage8_final_hardening_installed = True
    _INSTALLED = True
    logger.info("VELIA_SOFTWARE_FACTORY_STAGE8_FINAL_HARDENING_INSTALLED")