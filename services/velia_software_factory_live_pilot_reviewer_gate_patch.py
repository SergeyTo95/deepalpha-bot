from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Mapping

from services.velia_software_factory_core_service import SoftwareFactoryError


logger = logging.getLogger(__name__)
_INSTALL_LOCK = threading.Lock()
_INSTALLED = False


def _reviewer_modules():
    from services import velia_software_factory_reviewer_runtime_patch as reviewer_runtime
    from services import velia_software_factory_reviewer_service as reviewer

    return reviewer, reviewer_runtime


def reviewer_readiness() -> Dict[str, Any]:
    """Side-effect-free readiness for live-pilot use of Senior Reviewer.

    Enabling the env flag is not enough: the runtime hook must also have been
    installed in this process. This prevents a live pilot from being armed in a
    partially bootstrapped process where final-head review would be bypassed.
    """

    blockers: list[str] = []
    try:
        reviewer, reviewer_runtime = _reviewer_modules()
        enabled = bool(reviewer.reviewer_enabled())
        installed = bool(getattr(reviewer_runtime, "_INSTALLED", False))
    except Exception as exc:
        logger.exception("VELIA_SOFTWARE_FACTORY_REVIEWER_READINESS_ERROR")
        enabled = False
        installed = False
        blockers.append(f"readiness_error:{exc.__class__.__name__}")

    if not enabled:
        blockers.append("reviewer_disabled")
    if not installed:
        blockers.append("reviewer_runtime_not_installed")

    ready = not blockers
    return {
        "available": True,
        "ready": ready,
        "enabled": enabled,
        "runtime_installed": installed,
        "read_only_reviewer": True,
        "final_head_after_ci": True,
        "required_for_live_pilot": True,
        "required_for_dry_run": False,
        "blockers": blockers,
    }


def _require_reviewer_ready() -> Dict[str, Any]:
    status = reviewer_readiness()
    if bool(status.get("ready")):
        return status
    raise SoftwareFactoryError(
        "velia_factory_live_pilot_reviewer_not_ready",
        detail=",".join(str(item) for item in (status.get("blockers") or []))[:1000],
        status=409,
    )


def _admin_pilot_for_user(user_id: int) -> bool:
    from services import velia_software_factory_rollout_service as rollout

    return rollout.eligibility_source(int(user_id)) == "admin_pilot"


def _install_preflight(preflight_module: Any) -> None:
    if getattr(preflight_module, "_velia_factory_reviewer_pilot_gate_installed", False):
        return
    original = preflight_module.preflight_candidate

    def preflight_with_reviewer(user_id: int, run_id: str, repository_full_name: str):
        result = dict(original(user_id, run_id, repository_full_name) or {})
        reviewer_status = reviewer_readiness()
        runtime = dict(result.get("runtime") or {})
        runtime["reviewer_ready"] = bool(reviewer_status.get("ready"))
        runtime["reviewer"] = reviewer_status
        result["runtime"] = runtime

        blockers = [str(item) for item in (result.get("runtime_blockers") or []) if str(item)]
        if not bool(reviewer_status.get("ready")) and "reviewer_not_ready" not in blockers:
            blockers.append("reviewer_not_ready")
        result["runtime_blockers"] = blockers
        runtime_ready = not blockers
        result["runtime_ready_now"] = runtime_ready
        result["pilot_candidate_ready_now"] = bool(
            result.get("candidate_safe_to_arm_when_runtime_ready") and runtime_ready
        )
        return result

    preflight_module.preflight_candidate = preflight_with_reviewer
    preflight_module._velia_factory_reviewer_pilot_gate_installed = True


def _install_control(control_module: Any) -> None:
    if getattr(control_module, "_velia_factory_reviewer_pilot_gate_installed", False):
        return
    original_require = control_module._require_live_control
    original_status = control_module.public_status

    def require_live_control_with_reviewer(user_id: int):
        result = dict(original_require(user_id) or {})
        result["reviewer"] = _require_reviewer_ready()
        return result

    def public_status_with_reviewer(user_id: int):
        result = dict(original_status(user_id) or {})
        result["reviewer"] = reviewer_readiness()
        return result

    control_module._require_live_control = require_live_control_with_reviewer
    control_module.public_status = public_status_with_reviewer
    control_module._velia_factory_reviewer_pilot_gate_installed = True


def _install_guard(guard_module: Any) -> None:
    if getattr(guard_module, "_velia_factory_reviewer_pilot_gate_installed", False):
        return
    original_issue = guard_module.issue_grant
    original_claim = guard_module.claim_dispatch

    def issue_grant_with_reviewer(user_id: int, run: Mapping[str, Any], project: Mapping[str, Any], **kwargs: Any):
        if guard_module.live_pilot_guard_enabled() and _admin_pilot_for_user(int(user_id)):
            _require_reviewer_ready()
        return original_issue(user_id, run, project, **kwargs)

    def claim_dispatch_with_reviewer(user_id: int, run: Mapping[str, Any], project: Mapping[str, Any], **kwargs: Any):
        # Revalidate at the deepest pre-dispatch boundary. A reviewer flag/runtime
        # change after preflight or grant issuance therefore fails closed before
        # Coding Autopilot can be enqueued.
        if guard_module.live_pilot_guard_enabled() and _admin_pilot_for_user(int(user_id)):
            _require_reviewer_ready()
        return original_claim(user_id, run, project, **kwargs)

    guard_module.issue_grant = issue_grant_with_reviewer
    guard_module.claim_dispatch = claim_dispatch_with_reviewer
    guard_module._velia_factory_reviewer_pilot_gate_installed = True


def install(preflight_module: Any = None, control_module: Any = None, guard_module: Any = None) -> bool:
    global _INSTALLED
    with _INSTALL_LOCK:
        if preflight_module is None:
            from services import velia_software_factory_live_pilot_preflight_service as preflight_module
        if control_module is None:
            from services import velia_software_factory_live_pilot_control_service as control_module
        if guard_module is None:
            from services import velia_software_factory_live_pilot_guard_service as guard_module

        _install_preflight(preflight_module)
        _install_control(control_module)
        _install_guard(guard_module)
        _INSTALLED = True
        status = reviewer_readiness()
        logger.info(
            "VELIA_SOFTWARE_FACTORY_REVIEWER_PILOT_GATE_INSTALLED ready=%s enabled=%s runtime_installed=%s live_only=true dry_run_required=false",
            str(bool(status.get("ready"))).lower(),
            str(bool(status.get("enabled"))).lower(),
            str(bool(status.get("runtime_installed"))).lower(),
        )
        return True
