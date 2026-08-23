from __future__ import annotations

import logging
from typing import Any

from services import velia_software_factory_delivery_approval_service as approval
from services import velia_software_factory_delivery_gate_service as delivery
from services import velia_software_factory_deployment_observer_service as deployment_observer
from services import velia_software_factory_release_execution_hardening_patch as release_hardening
from services import velia_software_factory_release_execution_service as release_execution
from services import velia_software_factory_release_post_merge_service as post_merge
from services import velia_software_factory_release_preflight_service as preflight

logger = logging.getLogger(__name__)
_INSTALLED = False


def install(execution_module: Any) -> None:
    global _INSTALLED
    if getattr(execution_module, "_workspace_delivery_gate_installed", False):
        return

    delivery.ensure_delivery_tables(execution_module)
    approval.ensure_approval_tables(execution_module)
    preflight.ensure_preflight_tables(execution_module)
    release_hardening.install(release_execution, execution_module)
    release_execution.ensure_execution_tables(execution_module)
    post_merge.ensure_post_merge_tables(execution_module)
    deployment_status = deployment_observer.public_status()
    if bool(deployment_status.get("enabled")):
        deployment_observer.ensure_deployment_observer_tables(execution_module)

    execution_module.delivery_gate_status = delivery.public_status
    execution_module.delivery_approval_status = approval.public_status
    execution_module.release_preflight_status = preflight.public_status
    execution_module.release_execution_status = release_execution.public_status
    execution_module.release_verification_status = post_merge.public_status
    execution_module.deployment_observer_status = deployment_observer.public_status
    execution_module.evaluate_delivery_candidate = lambda user_id, execution_id: delivery.evaluate_workspace_candidate(
        execution_module, int(user_id), str(execution_id), persist=True
    )
    execution_module.preview_delivery_candidate = lambda user_id, execution_id: delivery.evaluate_workspace_candidate(
        execution_module, int(user_id), str(execution_id), persist=False
    )
    execution_module.get_delivery_candidate = lambda user_id, candidate_id: delivery.get_candidate(
        execution_module, int(user_id), str(candidate_id)
    )
    execution_module.list_delivery_candidates = lambda user_id, execution_id, limit=20: delivery.list_candidates(
        execution_module, int(user_id), str(execution_id), int(limit)
    )
    execution_module.get_delivery_approval = lambda user_id, candidate_id: approval.latest_decision(
        execution_module, int(user_id), str(candidate_id)
    )
    execution_module.record_delivery_decision = lambda user_id, candidate_id, decision, note="": approval.record_decision(
        execution_module,
        int(user_id),
        str(candidate_id),
        str(decision),
        note=str(note or ""),
    )
    execution_module.require_current_delivery_approval = lambda user_id, candidate_id: approval.require_current_approval(
        execution_module, int(user_id), str(candidate_id)
    )
    execution_module.prepare_release_preflight = lambda user_id, candidate_id: preflight.prepare_plan(
        execution_module, int(user_id), str(candidate_id)
    )
    execution_module.validate_release_preflight = lambda user_id, plan_id: preflight.validate_plan(
        execution_module, int(user_id), str(plan_id)
    )
    execution_module.get_release_preflight = lambda user_id, plan_id: preflight.get_plan(
        execution_module, int(user_id), str(plan_id)
    )
    execution_module.list_release_preflights = lambda user_id, candidate_id, limit=20: preflight.list_plans(
        execution_module, int(user_id), str(candidate_id), int(limit)
    )
    execution_module.cancel_release_preflight = lambda user_id, plan_id: preflight.cancel_plan(
        execution_module, int(user_id), str(plan_id)
    )
    execution_module.create_release_execution = lambda user_id, plan_id: release_execution.create_execution(
        execution_module, int(user_id), str(plan_id)
    )
    execution_module.execute_release = lambda user_id, release_execution_id: release_execution.execute_release(
        execution_module, int(user_id), str(release_execution_id)
    )
    execution_module.get_release_execution = lambda user_id, release_execution_id: release_execution.get_execution(
        execution_module, int(user_id), str(release_execution_id)
    )
    execution_module.stop_release_execution = lambda user_id, release_execution_id: release_execution.request_stop(
        execution_module, int(user_id), str(release_execution_id)
    )
    execution_module.verify_release_execution = lambda user_id, release_execution_id: post_merge.verify_release(
        execution_module, int(user_id), str(release_execution_id), persist=True
    )
    execution_module.preview_release_verification = lambda user_id, release_execution_id: post_merge.verify_release(
        execution_module, int(user_id), str(release_execution_id), persist=False
    )
    execution_module.get_release_verification = lambda user_id, verification_id: post_merge.get_verification(
        execution_module, int(user_id), str(verification_id)
    )
    execution_module.build_release_recovery_artifact = lambda user_id, verification_id: post_merge.build_recovery_artifact(
        execution_module, int(user_id), str(verification_id)
    )
    execution_module.configure_deployment_profile = lambda user_id, project_id, branch, expected_contexts, enabled=True: deployment_observer.configure_profile(
        execution_module,
        int(user_id),
        str(project_id),
        branch=str(branch),
        expected_contexts=list(expected_contexts or []),
        enabled=bool(enabled),
    )
    execution_module.get_deployment_profile = lambda user_id, project_id, branch: deployment_observer.get_profile(
        execution_module, int(user_id), str(project_id), str(branch)
    )
    execution_module.list_deployment_profiles = lambda user_id: deployment_observer.list_profiles(
        execution_module, int(user_id)
    )
    execution_module.discover_deployment_contexts = lambda user_id, verification_id: deployment_observer.discover_context_candidates(
        execution_module, int(user_id), str(verification_id)
    )
    execution_module.observe_release_deployment = lambda user_id, verification_id: deployment_observer.observe_release_deployment(
        execution_module, int(user_id), str(verification_id), persist=True
    )
    execution_module.preview_release_deployment = lambda user_id, verification_id: deployment_observer.observe_release_deployment(
        execution_module, int(user_id), str(verification_id), persist=False
    )
    execution_module.get_deployment_observation = lambda user_id, observation_id: deployment_observer.get_observation(
        execution_module, int(user_id), str(observation_id)
    )
    execution_module.list_deployment_observations = lambda user_id, release_execution_id, limit=20: deployment_observer.list_observations(
        execution_module, int(user_id), str(release_execution_id), int(limit)
    )

    execution_module._workspace_delivery_gate_installed = True
    _INSTALLED = True
    status = delivery.public_status()
    approval_status = approval.public_status()
    preflight_status = preflight.public_status()
    execution_status = release_execution.public_status()
    verification_status = post_merge.public_status()
    logger.info(
        "VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_INSTALLED enabled=%s mode=%s approval_enabled=%s approval_mode=%s preflight_enabled=%s preflight_mode=%s release_execution_enabled=%s release_execution_mode=%s release_verification_enabled=%s release_verification_mode=%s deployment_observer_enabled=%s deployment_observer_mode=%s deployment_trigger_supported=false execution_supported=%s merge_supported=%s deployment_supported=false",
        str(bool(status.get("enabled"))).lower(),
        str(status.get("mode") or "read_only_candidate"),
        str(bool(approval_status.get("enabled"))).lower(),
        str(approval_status.get("mode") or "record_only"),
        str(bool(preflight_status.get("enabled"))).lower(),
        str(preflight_status.get("mode") or "preflight_only"),
        str(bool(execution_status.get("enabled"))).lower(),
        str(execution_status.get("mode") or "controlled_merge"),
        str(bool(verification_status.get("enabled"))).lower(),
        str(verification_status.get("mode") or "post_merge_read_only"),
        str(bool(deployment_status.get("enabled"))).lower(),
        str(deployment_status.get("mode") or "github_commit_status_observer"),
        str(bool(execution_status.get("execution_supported"))).lower(),
        str(bool(execution_status.get("merge_supported"))).lower(),
    )
