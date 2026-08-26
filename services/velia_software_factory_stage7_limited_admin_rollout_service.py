from __future__ import annotations

import os
import re
from typing import Any, Dict

from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_software_factory_admin_acceptance_service as acceptance
from services import velia_software_factory_live_pilot_control_service as control
from services import velia_software_factory_live_pilot_guard_service as guard
from services import velia_software_factory_reviewer_remediation_service as remediation
from services import velia_software_factory_reviewer_runtime_patch as reviewer_runtime
from services import velia_software_factory_reviewer_service as reviewer
from services.velia_admin_security_service import configured_admin_id


_STAGE7_FLAG = "VELIA_SOFTWARE_FACTORY_STAGE7_LIMITED_ADMIN_ROLLOUT_ENABLED"
_ACCEPTANCE_RUN_ENV = "VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_RUN_ID"
_ACCEPTANCE_REPOSITORY_ENV = "VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_REPOSITORY"
_ACCEPTANCE_CERTIFICATE_ENV = "VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_CERTIFICATE_ID"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Stage 7 is intentionally build/review only. These capabilities must remain
# closed while the limited-admin mode is active, even if they are read-only in
# some earlier Stage 5 layers. This prevents accidental progression into the
# release pipeline from the permanent admin pilot.
_FORBIDDEN_RELEASE_FLAGS = (
    "VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED",
)

# These optional autonomy surfaces are deliberately outside the first limited
# admin rollout. They can be accepted later as separate stages.
_FORBIDDEN_EXPANSION_FLAGS = (
    "VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED",
    "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def limited_admin_rollout_enabled() -> bool:
    return _env_bool(_STAGE7_FLAG, False)


def _text_env(name: str, limit: int) -> str:
    return str(os.getenv(name, "") or "").strip()[:limit]


def _acceptance_proof(actor: int) -> Dict[str, Any]:
    run_id = _text_env(_ACCEPTANCE_RUN_ENV, 160)
    repository = _text_env(_ACCEPTANCE_REPOSITORY_ENV, 240)
    certificate_id = _text_env(_ACCEPTANCE_CERTIFICATE_ENV, 64).lower()
    configured = bool(run_id and repository and _HEX64_RE.fullmatch(certificate_id))
    if not configured:
        return {
            "configured": False,
            "verified": False,
            "error": "acceptance_proof_not_configured",
        }
    try:
        verified = acceptance.verify_passed_certificate(
            actor,
            run_id,
            repository,
            certificate_id,
        )
    except Exception as exc:
        return {
            "configured": True,
            "verified": False,
            "error": "acceptance_proof_unavailable",
            "observer_error": exc.__class__.__name__,
        }
    return {
        "configured": True,
        "verified": bool(verified.get("verified")),
        "error": "" if bool(verified.get("verified")) else str(verified.get("error") or "acceptance_proof_invalid"),
        "run_status": str(verified.get("run_status") or ""),
        "reviewer_status": str(verified.get("reviewer_status") or ""),
        "remediation_attempt_count": int(verified.get("remediation_attempt_count") or 0),
        "reviewed_head_sha": str(verified.get("reviewed_head_sha") or "")[:40],
    }


def public_status(user_id: int, *, verify_acceptance: bool = True) -> Dict[str, Any]:
    try:
        actor = int(user_id)
    except (TypeError, ValueError):
        actor = 0
    expected_admin = configured_admin_id()
    admin_ok = bool(expected_admin > 0 and actor == expected_admin)
    enabled = limited_admin_rollout_enabled()
    control_ready = bool(control.live_pilot_control_enabled())
    guard_ready = bool(guard.live_pilot_guard_enabled())
    reviewer_ready = bool(reviewer.reviewer_enabled() and getattr(reviewer_runtime, "_INSTALLED", False))
    remediation_ready = bool(
        remediation.remediation_enabled(ci_service) and remediation.remediation_max_attempts() > 0
    )
    acceptance_harness_closed = not acceptance.admin_acceptance_enabled()
    forbidden_enabled = [
        name
        for name in (*_FORBIDDEN_RELEASE_FLAGS, *_FORBIDDEN_EXPANSION_FLAGS)
        if _env_bool(name, False)
    ]

    proof = (
        _acceptance_proof(actor)
        if admin_ok and verify_acceptance
        else {
            "configured": bool(
                _text_env(_ACCEPTANCE_RUN_ENV, 160)
                and _text_env(_ACCEPTANCE_REPOSITORY_ENV, 240)
                and _HEX64_RE.fullmatch(_text_env(_ACCEPTANCE_CERTIFICATE_ENV, 64).lower())
            ),
            "verified": False,
            "verification_deferred": True,
            "error": "",
        }
    )

    blockers = []
    if not enabled:
        blockers.append("stage7_disabled")
    if not admin_ok:
        blockers.append("admin_required")
    if not control_ready:
        blockers.append("control_disabled")
    if not guard_ready:
        blockers.append("guard_disabled")
    if not reviewer_ready:
        blockers.append("reviewer_not_ready")
    if not remediation_ready:
        blockers.append("reviewer_remediation_not_ready")
    if not acceptance_harness_closed:
        blockers.append("stage67_acceptance_harness_must_be_closed")
    if forbidden_enabled:
        blockers.append("release_or_expansion_capability_open")
    if verify_acceptance and admin_ok and not bool(proof.get("verified")):
        blockers.append(str(proof.get("error") or "acceptance_proof_invalid"))

    return {
        "available": True,
        "enabled": enabled,
        "mode": "limited_admin_build_review",
        "admin_only": True,
        "max_dispatches_per_run": 1,
        "draft_pr_only": True,
        "requires_stage67_passed_certificate": True,
        "acceptance_harness_must_be_closed": True,
        "reviewer_required": True,
        "reviewer_remediation_required": True,
        "merge_supported": False,
        "release_supported": False,
        "deployment_supported": False,
        "control_ready": control_ready,
        "guard_ready": guard_ready,
        "reviewer_ready": reviewer_ready,
        "reviewer_remediation_ready": remediation_ready,
        "acceptance_proof": proof,
        "forbidden_enabled_flags": forbidden_enabled,
        "blockers": blockers,
        "ready_now": not blockers,
    }


def execution_allowed(user_id: int) -> bool:
    return bool(public_status(int(user_id), verify_acceptance=True).get("ready_now"))
