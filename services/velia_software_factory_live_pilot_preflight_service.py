from __future__ import annotations

from typing import Any, Dict, Mapping

from services import velia_developer_project_service as project_service
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_live_pilot_control_service as control
from services import velia_software_factory_live_pilot_guard_service as guard
from services import velia_software_factory_rollout_service as rollout
from services.velia_admin_security_service import configured_admin_id
from services.velia_software_factory_core_service import SoftwareFactoryError


_ALLOWED_STATES = frozenset({"ready", "planning", "executing"})
_MAX_PATHS = 64
_MAX_REFS = 32


def _admin_actor(user_id: int) -> int:
    try:
        candidate = int(user_id)
    except (TypeError, ValueError) as exc:
        raise SoftwareFactoryError("velia_factory_live_pilot_admin_required", status=403) from exc
    expected = configured_admin_id()
    if expected <= 0 or candidate != expected:
        raise SoftwareFactoryError("velia_factory_live_pilot_admin_required", status=403)
    return candidate


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _append_once(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _allowed_paths(run: Mapping[str, Any]) -> list[str]:
    spec = run.get("spec") if isinstance(run.get("spec"), Mapping) else {}
    result: list[str] = []
    for raw in spec.get("allowed_paths") or []:
        value = _text(raw)
        if value and value not in result:
            result.append(value)
        if len(result) >= _MAX_PATHS:
            break
    return result


def _dispatched_refs(run: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for raw in run.get("dag") or []:
        if not isinstance(raw, Mapping):
            continue
        value = _text(raw.get("external_ref"), 160)
        if value and value not in result:
            result.append(value)
        if len(result) >= _MAX_REFS:
            break
    return result


def preflight_candidate(
    user_id: int,
    run_id: str,
    repository_full_name: str,
) -> Dict[str, Any]:
    """Read-only exact-run preflight for a future one-shot live pilot.

    This function intentionally does not read or create a live-pilot grant and
    never advances a Factory run. It separates intrinsic candidate safety from
    current runtime rollout readiness so an owner can inspect a candidate while
    every production execution gate remains closed.
    """

    actor = _admin_actor(user_id)
    requested_run_id = _text(run_id, 160)
    if not requested_run_id:
        raise SoftwareFactoryError("velia_factory_live_pilot_run_required", status=400)

    run = dict(factory.get_run(actor, requested_run_id))
    actual_run_id = _text(run.get("run_id"), 160)
    project_id = _text(run.get("project_id"), 160)
    if not project_id:
        raise SoftwareFactoryError("velia_factory_live_pilot_project_required", status=409)
    project = dict(project_service.get_project(actor, project_id))

    expected_repository = _text(project.get("repository_full_name"))
    supplied_repository = _text(repository_full_name)
    project_identity = _text(project.get("id") or project.get("project_id"), 160)
    spec_fingerprint = _text(run.get("spec_fingerprint"), 160)
    state = _text(run.get("state"), 80).lower()
    allowed_paths = _allowed_paths(run)
    dispatched_refs = _dispatched_refs(run)

    candidate_blockers: list[str] = []
    if not actual_run_id or actual_run_id != requested_run_id:
        _append_once(candidate_blockers, "run_identity_mismatch")
    if not expected_repository:
        _append_once(candidate_blockers, "repository_missing")
    if not supplied_repository or supplied_repository.casefold() != expected_repository.casefold():
        _append_once(candidate_blockers, "repository_confirmation_mismatch")
    if project_identity and project_identity != project_id:
        _append_once(candidate_blockers, "project_identity_mismatch")
    if not spec_fingerprint:
        _append_once(candidate_blockers, "spec_fingerprint_missing")
    if state not in _ALLOWED_STATES:
        _append_once(candidate_blockers, "run_state_not_dispatchable")
    if not allowed_paths:
        _append_once(candidate_blockers, "write_scope_missing")
    if dispatched_refs:
        _append_once(candidate_blockers, "work_already_dispatched")

    rollout_status = dict(rollout.public_status(actor))
    readiness = dict(rollout_status.get("pilot_readiness") or {})
    build_review = dict(readiness.get("build_review") or {})
    control_enabled = bool(control.live_pilot_control_enabled())
    guard_status = dict(guard.public_status())
    guard_enabled = bool(guard_status.get("enabled"))
    eligibility_source = _text(rollout_status.get("eligibility_source"), 80)
    rollout_mode = _text(rollout_status.get("mode"), 40)
    build_review_ready = bool(build_review.get("ready"))
    missing_flags = [
        _text(value, 160)
        for value in (build_review.get("missing_flags") or [])
        if _text(value, 160)
    ][:64]

    runtime_blockers: list[str] = []
    if not control_enabled:
        _append_once(runtime_blockers, "control_disabled")
    if not guard_enabled:
        _append_once(runtime_blockers, "guard_disabled")
    if eligibility_source != "admin_pilot":
        _append_once(runtime_blockers, "admin_eligibility_required")
    if rollout_mode != "live":
        _append_once(runtime_blockers, "live_rollout_required")
    if not build_review_ready:
        _append_once(runtime_blockers, "build_review_not_ready")

    candidate_safe = not candidate_blockers
    runtime_ready = not runtime_blockers
    return {
        "available": True,
        "read_only": True,
        "admin_only": True,
        "grant_read": False,
        "grant_issue": False,
        "dispatch": False,
        "environment_mutation": False,
        "candidate": {
            "requested_run_id": requested_run_id,
            "run_id": actual_run_id,
            "project_id": project_id,
            "repository_full_name": expected_repository,
            "repository_confirmation": supplied_repository,
            "repository_matches": bool(
                supplied_repository
                and expected_repository
                and supplied_repository.casefold() == expected_repository.casefold()
            ),
            "state": state,
            "spec_fingerprint": spec_fingerprint,
            "allowed_paths": allowed_paths,
            "dispatched_external_refs": dispatched_refs,
        },
        "runtime": {
            "control_enabled": control_enabled,
            "guard_enabled": guard_enabled,
            "rollout_mode": rollout_mode,
            "eligibility_source": eligibility_source,
            "build_review_ready": build_review_ready,
            "missing_build_review_flags": missing_flags,
            "max_dispatches_per_run": 1,
        },
        "candidate_blockers": candidate_blockers,
        "runtime_blockers": runtime_blockers,
        "candidate_safe_to_arm_when_runtime_ready": candidate_safe,
        "runtime_ready_now": runtime_ready,
        "pilot_candidate_ready_now": candidate_safe and runtime_ready,
    }
