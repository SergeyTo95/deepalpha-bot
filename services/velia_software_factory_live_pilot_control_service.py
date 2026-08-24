from __future__ import annotations

import os
from typing import Any, Dict, Mapping

from services import velia_developer_project_service as project_service
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_live_pilot_guard_service as guard
from services import velia_software_factory_rollout_service as rollout
from services.velia_admin_security_service import configured_admin_id
from services.velia_software_factory_core_service import SoftwareFactoryError

_CONTROL_FLAG = "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_CONTROL_ENABLED"
_APPROVAL_SOURCE = "control_center_stage6_3"
_DEFAULT_TTL_SECONDS = 600


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def live_pilot_control_enabled() -> bool:
    return _env_bool(_CONTROL_FLAG, False)


def _admin_actor(user_id: int) -> int:
    try:
        candidate = int(user_id)
    except (TypeError, ValueError) as exc:
        raise SoftwareFactoryError("velia_factory_live_pilot_admin_required", status=403) from exc
    expected = configured_admin_id()
    if expected <= 0 or candidate != expected:
        raise SoftwareFactoryError("velia_factory_live_pilot_admin_required", status=403)
    return candidate


def _load_run_project(user_id: int, run_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    actor = _admin_actor(int(user_id))
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise SoftwareFactoryError("velia_factory_live_pilot_run_required", status=400)
    run = factory.get_run(actor, normalized_run_id)
    project_id = str(run.get("project_id") or "").strip()
    if not project_id:
        raise SoftwareFactoryError("velia_factory_live_pilot_project_required", status=409)
    project = project_service.get_project(actor, project_id)
    return dict(run), dict(project)


def _repository(project: Mapping[str, Any]) -> str:
    value = str(project.get("repository_full_name") or "").strip()
    if not value:
        raise SoftwareFactoryError("velia_factory_live_pilot_repository_required", status=409)
    return value


def _confirm_repository(project: Mapping[str, Any], repository_full_name: str) -> str:
    expected = _repository(project)
    supplied = str(repository_full_name or "").strip()
    if not supplied or supplied.casefold() != expected.casefold():
        raise SoftwareFactoryError("velia_factory_live_pilot_repository_confirmation_mismatch", status=409)
    return expected


def expected_confirmation(action: str, run_id: str, repository_full_name: str, grant_id: str = "") -> str:
    verb = str(action or "").strip().lower()
    if verb not in {"arm", "dispatch"}:
        raise SoftwareFactoryError("velia_factory_live_pilot_confirmation_action_invalid", status=400)
    parts = [verb, str(run_id or "").strip(), str(repository_full_name or "").strip()]
    if verb == "dispatch":
        parts.append(str(grant_id or "").strip())
    return ":".join(parts)


def _require_confirmation(
    action: str,
    run_id: str,
    repository_full_name: str,
    confirmation: str,
    *,
    grant_id: str = "",
) -> None:
    expected = expected_confirmation(action, run_id, repository_full_name, grant_id)
    if not expected or str(confirmation or "").strip() != expected:
        raise SoftwareFactoryError("velia_factory_live_pilot_explicit_confirmation_required", status=409)


def _require_live_control(user_id: int) -> Dict[str, Any]:
    actor = _admin_actor(int(user_id))
    if not live_pilot_control_enabled():
        raise SoftwareFactoryError("velia_factory_live_pilot_control_disabled", status=503)
    if not guard.live_pilot_guard_enabled():
        raise SoftwareFactoryError("velia_factory_live_pilot_guard_disabled", status=503)
    if rollout.eligibility_source(actor) != "admin_pilot":
        raise SoftwareFactoryError("velia_factory_live_pilot_admin_eligibility_required", status=403)
    if not rollout.live_execution_allowed(actor):
        raise SoftwareFactoryError("velia_factory_live_pilot_live_rollout_required", status=409)
    readiness = rollout.pilot_readiness(actor)
    build_review = dict(readiness.get("build_review") or {})
    if not bool(build_review.get("ready")):
        missing = ",".join(str(item) for item in (build_review.get("missing_flags") or []))
        raise SoftwareFactoryError(
            "velia_factory_live_pilot_build_review_not_ready",
            detail=missing,
            status=409,
        )
    return {
        "actor_user_id": actor,
        "readiness": readiness,
    }


def public_status(user_id: int) -> Dict[str, Any]:
    actor = _admin_actor(int(user_id))
    rollout_status = rollout.public_status(actor)
    return {
        "available": True,
        "enabled": live_pilot_control_enabled(),
        "mode": "explicit_owner_one_shot",
        "max_dispatches_per_run": 1,
        "admin_only": True,
        "csrf_required_by_route": True,
        "automatic_grant_issue": False,
        "automatic_dispatch": False,
        "merge_supported": False,
        "deployment_supported": False,
        "guard": guard.public_status(),
        "rollout": rollout_status,
    }


def grant_status(user_id: int, run_id: str, repository_full_name: str) -> Dict[str, Any]:
    actor = _admin_actor(int(user_id))
    run, project = _load_run_project(actor, run_id)
    repository = _confirm_repository(project, repository_full_name)
    grant = guard.get_grant(actor, str(run.get("run_id") or run_id))
    if str(grant.get("repository_full_name") or "").casefold() != repository.casefold():
        raise SoftwareFactoryError("velia_factory_live_pilot_grant_identity_mismatch", status=409)
    return {
        "run": run,
        "project": project,
        "grant": grant,
        "expected_arm_confirmation": expected_confirmation("arm", str(run.get("run_id") or run_id), repository),
        "expected_dispatch_confirmation": expected_confirmation(
            "dispatch",
            str(run.get("run_id") or run_id),
            repository,
            str(grant.get("grant_id") or ""),
        ),
    }


def arm_grant(
    user_id: int,
    run_id: str,
    repository_full_name: str,
    confirmation: str,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    actor = _admin_actor(int(user_id))
    _require_live_control(actor)
    run, project = _load_run_project(actor, run_id)
    normalized_run_id = str(run.get("run_id") or run_id)
    repository = _confirm_repository(project, repository_full_name)
    _require_confirmation("arm", normalized_run_id, repository, confirmation)
    grant = guard.issue_grant(
        actor,
        run,
        project,
        approval_source=_APPROVAL_SOURCE,
        ttl_seconds=int(ttl_seconds or _DEFAULT_TTL_SECONDS),
    )
    return {
        "run": run,
        "project": project,
        "grant": grant,
        "expected_dispatch_confirmation": expected_confirmation(
            "dispatch",
            normalized_run_id,
            repository,
            str(grant.get("grant_id") or ""),
        ),
    }


def revoke_grant(user_id: int, run_id: str, repository_full_name: str) -> Dict[str, Any]:
    # Revocation is intentionally available even after live rollout/control flags
    # are closed. It is a safety action and never dispatches repository work.
    actor = _admin_actor(int(user_id))
    run, project = _load_run_project(actor, run_id)
    repository = _confirm_repository(project, repository_full_name)
    current = guard.get_grant(actor, str(run.get("run_id") or run_id))
    if str(current.get("repository_full_name") or "").casefold() != repository.casefold():
        raise SoftwareFactoryError("velia_factory_live_pilot_grant_identity_mismatch", status=409)
    revoked = guard.revoke_pending_grant(actor, str(run.get("run_id") or run_id))
    return {"run": run, "project": project, "grant": revoked}


def dispatch_once(
    user_id: int,
    run_id: str,
    repository_full_name: str,
    grant_id: str,
    confirmation: str,
) -> Dict[str, Any]:
    actor = _admin_actor(int(user_id))
    _require_live_control(actor)
    run, project = _load_run_project(actor, run_id)
    normalized_run_id = str(run.get("run_id") or run_id)
    repository = _confirm_repository(project, repository_full_name)
    grant = guard.get_grant(actor, normalized_run_id)
    expected_grant_id = str(grant.get("grant_id") or "").strip()
    if not expected_grant_id or str(grant_id or "").strip() != expected_grant_id:
        raise SoftwareFactoryError("velia_factory_live_pilot_grant_confirmation_mismatch", status=409)
    if str(grant.get("status") or "") not in {"pending", "claimed"}:
        raise SoftwareFactoryError(
            "velia_factory_live_pilot_dispatch_budget_exhausted",
            detail=str(grant.get("status") or ""),
            status=409,
        )
    _require_confirmation(
        "dispatch",
        normalized_run_id,
        repository,
        confirmation,
        grant_id=expected_grant_id,
    )
    advanced = factory.advance_run(actor, normalized_run_id)
    updated_grant = guard.get_grant(actor, normalized_run_id)
    return {
        "run": advanced,
        "project": project,
        "grant": updated_grant,
        "max_dispatches": 1,
    }
