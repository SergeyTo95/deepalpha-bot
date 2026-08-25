from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_software_factory_live_pilot_control_service as control
from services import velia_software_factory_live_pilot_guard_service as guard
from services import velia_software_factory_live_pilot_preflight_service as preflight
from services import velia_software_factory_reviewer_remediation_service as remediation
from services import velia_software_factory_reviewer_runtime_patch as reviewer_runtime
from services import velia_software_factory_reviewer_service as reviewer
from services import velia_software_factory_rollout_service as rollout
from services.velia_admin_security_service import configured_admin_id
from services.velia_software_factory_core_service import SoftwareFactoryError

_ACCEPTANCE_FLAG = "VELIA_SOFTWARE_FACTORY_ADMIN_ACCEPTANCE_ENABLED"
_ACCEPTANCE_SOURCE = "control_center_stage6_7_acceptance"
_DEFAULT_TTL_SECONDS = 900


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


def admin_acceptance_enabled() -> bool:
    return _env_bool(_ACCEPTANCE_FLAG, False)


def _admin_actor(user_id: int) -> int:
    try:
        actor = int(user_id)
    except (TypeError, ValueError) as exc:
        raise SoftwareFactoryError("velia_factory_admin_acceptance_admin_required", status=403) from exc
    expected = configured_admin_id()
    if expected <= 0 or actor != expected:
        raise SoftwareFactoryError("velia_factory_admin_acceptance_admin_required", status=403)
    return actor


def expected_confirmation(action: str, run_id: str, repository_full_name: str, grant_id: str = "") -> str:
    verb = str(action or "").strip().lower()
    if verb not in {"arm", "dispatch"}:
        raise SoftwareFactoryError("velia_factory_admin_acceptance_confirmation_action_invalid", status=400)
    prefix = "accept" if verb == "arm" else "accept-dispatch"
    parts = [prefix, str(run_id or "").strip(), str(repository_full_name or "").strip()]
    if verb == "dispatch":
        parts.append(str(grant_id or "").strip())
    return ":".join(parts)


def _require_confirmation(action: str, run_id: str, repository: str, confirmation: str, *, grant_id: str = "") -> None:
    expected = expected_confirmation(action, run_id, repository, grant_id)
    if str(confirmation or "").strip() != expected:
        raise SoftwareFactoryError("velia_factory_admin_acceptance_explicit_confirmation_required", status=409)


def public_status(user_id: int) -> Dict[str, Any]:
    actor = _admin_actor(user_id)
    readiness = rollout.pilot_readiness(actor)
    build_review = dict(readiness.get("build_review") or {})
    reviewer_enabled = bool(reviewer.reviewer_enabled())
    reviewer_installed = bool(getattr(reviewer_runtime, "_INSTALLED", False))
    remediation_attempt_budget = int(remediation.remediation_max_attempts())
    remediation_ready = bool(
        remediation.remediation_enabled(ci_service) and remediation_attempt_budget > 0
    )
    blockers = []
    if not admin_acceptance_enabled():
        blockers.append("acceptance_disabled")
    if not control.live_pilot_control_enabled():
        blockers.append("control_disabled")
    if not guard.live_pilot_guard_enabled():
        blockers.append("guard_disabled")
    if rollout.eligibility_source(actor) != "admin_pilot":
        blockers.append("admin_eligibility_required")
    if not rollout.live_execution_allowed(actor):
        blockers.append("live_rollout_required")
    if not bool(build_review.get("ready")):
        blockers.append("build_review_not_ready")
    if not reviewer_enabled or not reviewer_installed:
        blockers.append("reviewer_not_ready")
    if not remediation_ready:
        blockers.append("reviewer_remediation_not_ready")

    non_flag_blockers = [item for item in blockers if item != "acceptance_disabled"]
    return {
        "available": True,
        "enabled": admin_acceptance_enabled(),
        "mode": "one_shot_full_reviewer_remediation_acceptance",
        "admin_only": True,
        "approval_source": _ACCEPTANCE_SOURCE,
        "grant_is_session_id": True,
        "max_dispatches_per_session": 1,
        "requires_reviewer_remediation_observed": True,
        "requires_final_exact_head_reviewer_pass": True,
        "merge_supported": False,
        "deployment_supported": False,
        "automatic_rollout_change": False,
        "automatic_dispatch": False,
        "reviewer": {
            "enabled": reviewer_enabled,
            "runtime_installed": reviewer_installed,
        },
        "remediation": {
            "ready": remediation_ready,
            "max_attempts": remediation_attempt_budget,
        },
        "build_review": build_review,
        "blockers": blockers,
        "prerequisites_ready_if_enabled": not non_flag_blockers,
        "ready_now": not blockers,
    }


def _require_ready(user_id: int) -> Dict[str, Any]:
    status = public_status(user_id)
    blockers = list(status.get("blockers") or [])
    if blockers:
        raise SoftwareFactoryError(
            "velia_factory_admin_acceptance_not_ready",
            detail=",".join(str(item) for item in blockers),
            status=409,
        )
    return status


def _acceptance_grant(view: Mapping[str, Any]) -> Dict[str, Any]:
    grant = dict(view.get("grant") or {})
    if not grant:
        raise SoftwareFactoryError("velia_factory_admin_acceptance_grant_required", status=404)
    if str(grant.get("approval_source") or "") != _ACCEPTANCE_SOURCE:
        raise SoftwareFactoryError("velia_factory_admin_acceptance_grant_conflict", status=409)
    return grant


def arm_acceptance(
    user_id: int,
    run_id: str,
    repository_full_name: str,
    confirmation: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    actor = _admin_actor(user_id)
    _require_ready(actor)
    inspected = preflight.preflight_candidate(actor, run_id, repository_full_name)
    if not bool(inspected.get("candidate_safe_to_arm_when_runtime_ready")):
        raise SoftwareFactoryError(
            "velia_factory_admin_acceptance_candidate_unsafe",
            detail=",".join(str(item) for item in (inspected.get("candidate_blockers") or [])),
            status=409,
        )
    if not bool(inspected.get("runtime_ready_now")):
        raise SoftwareFactoryError(
            "velia_factory_admin_acceptance_runtime_not_ready",
            detail=",".join(str(item) for item in (inspected.get("runtime_blockers") or [])),
            status=409,
        )
    candidate = dict(inspected.get("candidate") or {})
    normalized_run_id = str(candidate.get("run_id") or run_id)
    repository = str(candidate.get("repository_full_name") or repository_full_name)
    _require_confirmation("arm", normalized_run_id, repository, confirmation)
    result = control.arm_grant(
        actor,
        normalized_run_id,
        repository,
        control.expected_confirmation("arm", normalized_run_id, repository),
        ttl_seconds=min(1800, max(60, int(ttl_seconds or _DEFAULT_TTL_SECONDS))),
        approval_source=_ACCEPTANCE_SOURCE,
    )
    grant = _acceptance_grant(result)
    grant_status = str(grant.get("status") or "")
    if grant_status != "pending":
        raise SoftwareFactoryError(
            "velia_factory_admin_acceptance_grant_not_pending",
            detail=grant_status or "unknown",
            status=409,
        )
    return {
        **dict(result),
        "acceptance": {
            "acceptance_id": str(grant.get("grant_id") or ""),
            "status": "armed",
            "approval_source": _ACCEPTANCE_SOURCE,
            "max_dispatches": 1,
            "expected_dispatch_confirmation": expected_confirmation(
                "dispatch", normalized_run_id, repository, str(grant.get("grant_id") or "")
            ),
        },
    }


def dispatch_acceptance(
    user_id: int,
    run_id: str,
    repository_full_name: str,
    grant_id: str,
    confirmation: str,
) -> Dict[str, Any]:
    actor = _admin_actor(user_id)
    _require_ready(actor)
    view = control.grant_status(actor, run_id, repository_full_name)
    grant = _acceptance_grant(view)
    expected_grant_id = str(grant.get("grant_id") or "")
    if not expected_grant_id or str(grant_id or "").strip() != expected_grant_id:
        raise SoftwareFactoryError("velia_factory_admin_acceptance_grant_confirmation_mismatch", status=409)
    run = dict(view.get("run") or {})
    project = dict(view.get("project") or {})
    normalized_run_id = str(run.get("run_id") or run_id)
    repository = str(project.get("repository_full_name") or repository_full_name)
    _require_confirmation(
        "dispatch", normalized_run_id, repository, confirmation, grant_id=expected_grant_id
    )
    result = control.dispatch_once(
        actor,
        normalized_run_id,
        repository,
        expected_grant_id,
        control.expected_confirmation("dispatch", normalized_run_id, repository, expected_grant_id),
    )
    updated = _acceptance_grant(result)
    return {
        **dict(result),
        "acceptance": {
            "acceptance_id": expected_grant_id,
            "status": str(updated.get("status") or ""),
            "approval_source": _ACCEPTANCE_SOURCE,
            "max_dispatches": 1,
        },
    }


def revoke_acceptance(user_id: int, run_id: str, repository_full_name: str) -> Dict[str, Any]:
    # Revocation is a safety action: it remains available even after the Stage
    # 6.7 flag or live rollout is closed.
    actor = _admin_actor(user_id)
    view = control.grant_status(actor, run_id, repository_full_name)
    _acceptance_grant(view)
    result = control.revoke_grant(actor, run_id, repository_full_name)
    return {**dict(result), "acceptance": {"status": "revoked"}}


def _parse_time(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _autopilot_evidence(actor: int, grant: Mapping[str, Any]) -> Dict[str, Any]:
    task_id = str(grant.get("autopilot_task_id") or "")
    evidence: Dict[str, Any] = {
        "autopilot_task_id": task_id,
        "task_status": "",
        "autopilot_run_id": "",
        "run_status": "",
        "error_code": "",
        "pull_request_number": 0,
        "pull_request_url": "",
        "reviewer_status": "",
        "reviewed_head_sha": "",
        "reviewer_history_count": 0,
        "remediation_phase": "",
        "remediation_attempt_count": 0,
        "remediation_attempts": [],
    }
    if not task_id:
        return evidence
    try:
        task = autopilot.get_task(actor, task_id)
    except Exception as exc:
        evidence["observer_error"] = exc.__class__.__name__
        return evidence
    evidence["task_status"] = str(task.get("status") or "")
    run_id = str(task.get("latest_run_id") or "")
    evidence["autopilot_run_id"] = run_id
    if not run_id:
        return evidence
    try:
        run = autopilot.get_run(actor, run_id)
    except Exception as exc:
        evidence["observer_error"] = exc.__class__.__name__
        return evidence
    evidence["run_status"] = str(run.get("status") or "")
    evidence["error_code"] = str(run.get("error_code") or "")
    evidence["pull_request_number"] = int(run.get("pull_request_number") or 0)
    evidence["pull_request_url"] = str(run.get("pull_request_url") or "")
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    report = result.get("reviewer") if isinstance(result.get("reviewer"), Mapping) else {}
    report_evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    state = (
        result.get("reviewer_remediation")
        if isinstance(result.get("reviewer_remediation"), Mapping)
        else {}
    )
    attempts = [
        dict(item) for item in (state.get("attempts") or []) if isinstance(item, Mapping)
    ][:2]
    history = [item for item in (result.get("reviewer_history") or []) if isinstance(item, Mapping)]
    evidence.update(
        {
            "reviewer_status": str(report.get("status") or ""),
            "reviewed_head_sha": str(
                report_evidence.get("reviewed_head_sha")
                or report_evidence.get("current_head_sha")
                or state.get("completed_head_sha")
                or ""
            )[:40],
            "reviewer_history_count": len(history),
            "remediation_phase": str(state.get("phase") or ""),
            "remediation_attempt_count": len(attempts),
            "remediation_attempts": attempts,
        }
    )
    return evidence


def inspect_acceptance(user_id: int, run_id: str, repository_full_name: str) -> Dict[str, Any]:
    actor = _admin_actor(user_id)
    status = public_status(actor)
    inspected = preflight.preflight_candidate(actor, run_id, repository_full_name)
    grant: Dict[str, Any] = {}
    grant_error = ""
    try:
        view = control.grant_status(actor, run_id, repository_full_name)
        grant = dict(view.get("grant") or {})
    except SoftwareFactoryError as exc:
        grant_error = str(exc.code or "")
        if grant_error != "velia_factory_live_pilot_grant_not_found":
            raise

    is_acceptance = bool(grant) and str(grant.get("approval_source") or "") == _ACCEPTANCE_SOURCE
    evidence = _autopilot_evidence(actor, grant) if is_acceptance else {}
    created_at = _parse_time(grant.get("created_at")) if is_acceptance else None
    age_seconds = max(0.0, (datetime.utcnow() - created_at).total_seconds()) if created_at else 0.0
    max_age_seconds = _env_int(
        "VELIA_SOFTWARE_FACTORY_ADMIN_ACCEPTANCE_MAX_WAIT_MINUTES", 90, 15, 240
    ) * 60
    timed_out = bool(is_acceptance and created_at and age_seconds > max_age_seconds)

    grant_status = str(grant.get("status") or "")
    run_status = str(evidence.get("run_status") or "")
    reviewer_status = str(evidence.get("reviewer_status") or "")
    remediation_phase = str(evidence.get("remediation_phase") or "")
    remediation_attempts = int(evidence.get("remediation_attempt_count") or 0)
    acceptance_passed = bool(
        is_acceptance
        and grant_status == "consumed"
        and run_status == "ready_for_review"
        and reviewer_status == "passed"
        and remediation_phase == "completed"
        and remediation_attempts >= 1
        and str(evidence.get("reviewed_head_sha") or "")
        and not timed_out
    )
    blocked = run_status in {"blocked", "failed", "cancelled"}
    incomplete_no_remediation = bool(
        is_acceptance
        and run_status == "ready_for_review"
        and reviewer_status == "passed"
        and remediation_attempts < 1
    )
    terminal = bool(acceptance_passed or blocked or incomplete_no_remediation or timed_out)
    if acceptance_passed:
        outcome = "passed"
    elif blocked:
        outcome = "blocked" if run_status == "blocked" else "failed"
    elif incomplete_no_remediation:
        outcome = "incomplete_no_remediation_observed"
    elif timed_out:
        outcome = "timed_out"
    elif is_acceptance:
        outcome = "in_progress"
    elif grant:
        outcome = "foreign_grant"
    else:
        outcome = "not_armed"

    certificate_payload = {
        "acceptance_id": str(grant.get("grant_id") or "") if is_acceptance else "",
        "factory_run_id": str((inspected.get("candidate") or {}).get("run_id") or run_id),
        "project_id": str((inspected.get("candidate") or {}).get("project_id") or ""),
        "repository_full_name": str(
            (inspected.get("candidate") or {}).get("repository_full_name") or repository_full_name
        ),
        "spec_fingerprint": str((inspected.get("candidate") or {}).get("spec_fingerprint") or ""),
        "grant_status": grant_status if is_acceptance else "",
        "outcome": outcome,
        "terminal": terminal,
        "acceptance_passed": acceptance_passed,
        "evidence": evidence,
    }
    certificate = {
        **certificate_payload,
        "certificate_id": _fingerprint(certificate_payload) if terminal and is_acceptance else "",
        "issued": bool(terminal and is_acceptance),
        "read_only": True,
        "merge_authority": False,
        "deployment_authority": False,
    }
    return {
        "status": status,
        "preflight": inspected,
        "grant": grant if is_acceptance else {},
        "grant_error": grant_error,
        "foreign_grant_present": bool(grant and not is_acceptance),
        "acceptance": {
            "armed": is_acceptance,
            "acceptance_id": str(grant.get("grant_id") or "") if is_acceptance else "",
            "approval_source": str(grant.get("approval_source") or "") if is_acceptance else "",
            "grant_status": grant_status if is_acceptance else "",
            "age_seconds": age_seconds,
            "max_wait_seconds": max_age_seconds,
            "timed_out": timed_out,
            "outcome": outcome,
            "terminal": terminal,
            "acceptance_passed": acceptance_passed,
        },
        "evidence": evidence,
        "certificate": certificate,
        "expected_arm_confirmation": expected_confirmation(
            "arm",
            str((inspected.get("candidate") or {}).get("run_id") or run_id),
            str((inspected.get("candidate") or {}).get("repository_full_name") or repository_full_name),
        ),
        "expected_dispatch_confirmation": (
            expected_confirmation(
                "dispatch",
                str((inspected.get("candidate") or {}).get("run_id") or run_id),
                str((inspected.get("candidate") or {}).get("repository_full_name") or repository_full_name),
                str(grant.get("grant_id") or ""),
            )
            if is_acceptance
            else ""
        ),
    }
