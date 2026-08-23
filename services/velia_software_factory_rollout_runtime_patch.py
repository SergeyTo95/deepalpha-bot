from __future__ import annotations

import logging
from typing import Any, Dict, Mapping

from services import velia_developer_project_service as project_service
from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_stage2_runtime_patch as stage2_runtime
from services import velia_software_factory_team_service as team_service
from services.velia_software_factory_core_service import ProjectSpec, SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False


def _plan_only(factory_module: Any, user_id: int, run_id: str) -> Dict[str, Any]:
    """Build Architect/Designer/Planner artifacts and DAG without calling Coding Autopilot."""
    factory_module.ensure_software_factory_tables()
    run = factory_module.get_run(int(user_id), str(run_id))
    state = str(run.get("state") or "")
    if state in {"completed", "failed", "cancelled", "clarifying", "blocked"}:
        result = dict(run)
        result["rollout_mode"] = rollout.ROLLOUT_DRY_RUN
        result["dry_run"] = True
        return result

    if state == "ready":
        stage2_runtime._transition_ready_to_planning(factory_module, run)
        run = factory_module.get_run(int(user_id), str(run_id))
        state = str(run.get("state") or "")

    if state == "planning" and not stage2_runtime._latest_artifact(str(run_id), int(user_id), "team_plan"):
        spec = ProjectSpec.from_payload(run.get("spec") or {})
        project = project_service.get_project(int(user_id), str(run["project_id"]))
        bundle = team_service.build_team_bundle(
            spec,
            run.get("brain") or [],
            repository=str(project.get("repository_full_name") or ""),
            branch=str(project.get("selected_branch") or ""),
            user_id=int(user_id),
            run_id=str(run_id),
        )
        stage2_runtime._persist_team_bundle(factory_module, run, bundle)
        logger.info(
            "VELIA_SOFTWARE_FACTORY_DRY_RUN_PLANNED run_id=%s tasks=%s roles=%s",
            str(run_id)[:80],
            len((bundle.get("plan") or {}).get("tasks") or []),
            ",".join((bundle.get("manifest") or {}).get("execution_roles") or []),
        )

    result = factory_module.get_run(int(user_id), str(run_id))
    result["rollout_mode"] = rollout.ROLLOUT_DRY_RUN
    result["dry_run"] = True
    result["execution_blocked"] = True
    return result


def install(factory_module: Any, autonomy_module: Any) -> bool:
    global _INSTALLED
    if _INSTALLED or getattr(factory_module, "_velia_factory_rollout_runtime_installed", False):
        return True

    # Stage 2 must already own Factory create/get/clarify/advance before rollout wraps it.
    if not hasattr(factory_module, "team_runtime_enabled"):
        return False

    original_create = factory_module.create_run
    original_clarify = factory_module.answer_clarifications
    original_advance = factory_module.advance_run
    original_supervisor_once = autonomy_module.run_supervisor_once

    def _require_user(user_id: int) -> None:
        if not rollout.intake_allowed(int(user_id)):
            raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)

    def create_run(user_id: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
        _require_user(int(user_id))
        run = original_create(int(user_id), payload)
        run["rollout_mode"] = rollout.rollout_mode()
        run["dry_run"] = rollout.dry_run_enabled(int(user_id))
        return run

    def answer_clarifications(user_id: int, run_id: str, answers: Mapping[str, Any]) -> Dict[str, Any]:
        _require_user(int(user_id))
        run = original_clarify(int(user_id), str(run_id), answers)
        run["rollout_mode"] = rollout.rollout_mode()
        run["dry_run"] = rollout.dry_run_enabled(int(user_id))
        return run

    def advance_run(user_id: int, run_id: str) -> Dict[str, Any]:
        _require_user(int(user_id))
        if rollout.dry_run_enabled(int(user_id)):
            return _plan_only(factory_module, int(user_id), str(run_id))
        if not rollout.live_execution_allowed(int(user_id)):
            raise SoftwareFactoryError("velia_factory_execution_not_allowed", status=403)
        return original_advance(int(user_id), str(run_id))

    def run_supervisor_once():
        # Defense in depth: even if the legacy supervisor env flag is accidentally
        # true, rollout dry-run/off can never advance repository execution.
        if not rollout.supervisor_allowed():
            return []
        return original_supervisor_once()

    factory_module.create_run = create_run
    factory_module.answer_clarifications = answer_clarifications
    factory_module.advance_run = advance_run
    factory_module.factory_rollout_status = rollout.public_status
    factory_module.factory_rollout_mode = rollout.rollout_mode
    autonomy_module.run_supervisor_once = run_supervisor_once
    autonomy_module.rollout_supervisor_allowed = rollout.supervisor_allowed
    factory_module._velia_factory_rollout_runtime_installed = True
    _INSTALLED = True
    logger.info("VELIA_SOFTWARE_FACTORY_ROLLOUT_RUNTIME_INSTALLED mode=%s", rollout.rollout_mode())
    return True
