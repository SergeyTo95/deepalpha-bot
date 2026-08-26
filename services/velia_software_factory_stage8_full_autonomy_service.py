from __future__ import annotations

import os
from typing import Any, Dict, Iterable

from services import velia_agent_coding_autopilot_ci_service as ci_service
from services import velia_agent_coding_autopilot_merge_policy_service as merge_policy
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_github_write_service as github_write
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_delivery_approval_service as approval
from services import velia_software_factory_delivery_gate_service as delivery
from services import velia_software_factory_deployment_observer_service as deployment_observer
from services import velia_software_factory_greenfield_repository_creation_service as greenfield_creation
from services import velia_software_factory_integration_repair_service as integration_repair
from services import velia_software_factory_release_completion_service as completion
from services import velia_software_factory_release_execution_service as release_execution
from services import velia_software_factory_release_passport_service as passport
from services import velia_software_factory_release_post_merge_service as verification
from services import velia_software_factory_release_preflight_service as preflight
from services import velia_software_factory_reviewer_remediation_service as remediation
from services import velia_software_factory_reviewer_runtime_patch as reviewer_runtime
from services import velia_software_factory_reviewer_service as reviewer
from services import velia_software_factory_workspace_execution_service as workspace_execution

_STAGE8_FLAG = "VELIA_SOFTWARE_FACTORY_STAGE8_FULL_AUTONOMY_ENABLED"
_GREENFIELD_FLAG = "VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED"
_GREENFIELD_CREATE_FLAG = "VELIA_SOFTWARE_FACTORY_GREENFIELD_REPOSITORY_CREATION_ENABLED"
_WORKSPACE_EXECUTION_FLAG = "VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED"
_INTEGRATION_VALIDATOR_FLAG = "VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_ENABLED"
_CODING_FLAG = "VELIA_DEVELOPER_CODING_ENABLED"
_CI_FLAG = "VELIA_DEVELOPER_AUTOPILOT_CI_ENABLED"
_CI_REPAIR_FLAG = "VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_ENABLED"

_RELEASE_FLAGS = (
    "VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED",
    "VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED",
    "VELIA_DEVELOPER_WRITE_ENABLED",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return _env_bool(_STAGE8_FLAG, False)


def _flags_ready(names: Iterable[str]) -> tuple[bool, list[str]]:
    missing = [name for name in names if not _env_bool(name, False)]
    return not missing, missing


def _runtime_readiness() -> Dict[str, Any]:
    release_flags_ready, release_missing = _flags_ready(_RELEASE_FLAGS)
    creation_status = greenfield_creation.public_status()
    return {
        "autonomy": bool(autonomy.supervisor_enabled()),
        "autopilot": bool(autopilot.autopilot_enabled()),
        "worker": bool(autopilot.worker_enabled()),
        "coding": _env_bool(_CODING_FLAG, False),
        "ci": _env_bool(_CI_FLAG, False),
        "ci_repair": _env_bool(_CI_REPAIR_FLAG, False),
        "workspace_execution": _env_bool(_WORKSPACE_EXECUTION_FLAG, False),
        "integration_validator": _env_bool(_INTEGRATION_VALIDATOR_FLAG, False),
        "integration_repair": bool(
            integration_repair.integration_repair_enabled()
            and integration_repair.integration_repair_max_attempts() > 0
        ),
        "integration_repair_runtime": bool(
            getattr(workspace_execution, "_workspace_integration_repair_installed", False)
        ),
        "reviewer": bool(
            reviewer.reviewer_enabled() and getattr(reviewer_runtime, "_INSTALLED", False)
        ),
        "reviewer_remediation": bool(
            remediation.remediation_enabled(ci_service)
            and remediation.remediation_max_attempts() > 0
        ),
        "delivery_gate": bool(delivery.delivery_gate_enabled()),
        "delivery_approval": bool(approval.approval_enabled()),
        "release_preflight": bool(preflight.preflight_enabled()),
        "release_execution": bool(release_execution.execution_enabled()),
        "release_verification": bool(verification.verification_enabled()),
        "deployment_observer": bool(deployment_observer.deployment_observer_enabled()),
        "release_completion": bool(completion.completion_enabled()),
        "release_passport": bool(passport.passport_enabled()),
        "stage8_release_runtime": bool(
            getattr(workspace_execution, "_workspace_stage8_release_runtime_installed", False)
        ),
        "merge_policy": bool(merge_policy.merge_policy_enabled()),
        "github_write": bool(github_write.write_enabled()),
        "release_flags_ready": release_flags_ready,
        "release_missing_flags": release_missing,
        "greenfield_bootstrap": _env_bool(_GREENFIELD_FLAG, False),
        "greenfield_repository_creation": _env_bool(_GREENFIELD_CREATE_FLAG, False),
        "greenfield_repository_creation_provider": bool(creation_status.get("configured")),
    }


def public_status(user_id: int, *, user_eligible: bool = False) -> Dict[str, Any]:
    runtime = _runtime_readiness()
    blockers: list[str] = []
    if not enabled():
        blockers.append("stage8_disabled")
    if not user_eligible:
        blockers.append("user_not_eligible")

    for name in (
        "autonomy",
        "autopilot",
        "worker",
        "coding",
        "ci",
        "ci_repair",
        "workspace_execution",
        "integration_validator",
        "integration_repair",
        "integration_repair_runtime",
        "reviewer",
        "reviewer_remediation",
        "delivery_gate",
        "delivery_approval",
        "release_preflight",
        "release_execution",
        "release_verification",
        "deployment_observer",
        "release_completion",
        "release_passport",
        "stage8_release_runtime",
        "merge_policy",
        "github_write",
    ):
        if not runtime.get(name):
            blockers.append(f"{name}_not_ready")

    greenfield_ready = bool(
        runtime.get("greenfield_bootstrap")
        and runtime.get("greenfield_repository_creation")
        and runtime.get("greenfield_repository_creation_provider")
    )
    if not greenfield_ready:
        blockers.append("greenfield_repository_creation_not_ready")

    return {
        "available": True,
        "enabled": enabled(),
        "mode": "full_autonomy",
        "user_id": int(user_id),
        "user_eligible": bool(user_eligible),
        "authenticated_users_only": True,
        "anonymous_execution_supported": False,
        "reviewer_required": True,
        "reviewer_remediation_required": True,
        "exact_head_merge_required": True,
        "integration_validation_required": True,
        "integration_repair_supported": bool(
            runtime.get("integration_repair") and runtime.get("integration_repair_runtime")
        ),
        "merge_supported": bool(
            runtime.get("release_execution")
            and runtime.get("stage8_release_runtime")
            and runtime.get("merge_policy")
            and runtime.get("github_write")
        ),
        "release_supported": bool(
            runtime.get("release_flags_ready") and runtime.get("stage8_release_runtime")
        ),
        "deployment_strategy": "source_auto_deploy_observed",
        "deployment_trigger_supported": False,
        "post_deploy_verification_supported": bool(
            runtime.get("deployment_observer") and runtime.get("release_completion")
        ),
        "greenfield_bootstrap_supported": bool(runtime.get("greenfield_bootstrap")),
        "greenfield_repository_creation_supported": greenfield_ready,
        "greenfield_repository_creation_provider": greenfield_creation.public_status(),
        "runtime": runtime,
        "blockers": blockers,
        "ready_now": not blockers,
    }


def execution_allowed(user_id: int, *, user_eligible: bool) -> bool:
    return bool(
        public_status(int(user_id), user_eligible=bool(user_eligible)).get("ready_now")
    )