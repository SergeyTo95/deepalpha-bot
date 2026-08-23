from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False
_TERMINAL_ARCHIVE_STATES = {"review_ready", "cancelled"}


def _preflight_mission_conflicts(execution_module: Any, user_id: int, workspace_id: str, plan_payload: Mapping[str, Any]) -> None:
    workspace = execution_module.workspace_service.get_workspace(int(user_id), str(workspace_id))
    plan = execution_module.workspace_service.normalize_workspace_plan(plan_payload, workspace)
    if not bool(plan.get("execution_ready")):
        raise SoftwareFactoryError("velia_factory_workspace_scopes_not_approved", status=409)
    projects = {str(task.get("project_id") or "") for task in plan.get("tasks") or []}
    for mission in execution_module.autopilot.list_missions(int(user_id)):
        if str(mission.get("project_id") or "") not in projects:
            continue
        if str(mission.get("status") or "") in {"paused", "active"}:
            raise SoftwareFactoryError(
                "velia_factory_workspace_mission_conflict",
                detail=str(mission.get("mission_id") or ""),
                status=409,
            )


def _set_terminal_state_and_archive_missions(
    execution_module: Any,
    execution_id: str,
    user_id: int,
    status: str,
    blocker: Optional[Mapping[str, Any]] = None,
) -> None:
    """Atomically terminalize a workspace execution and free only its own mission slots."""
    if str(status) not in _TERMINAL_ARCHIVE_STATES:
        raise SoftwareFactoryError("velia_factory_workspace_terminal_state_invalid")
    conn = execution_module.get_connection()
    cursor = conn.cursor()
    try:
        now = execution_module._utcnow()
        cursor.execute(
            "UPDATE velia_software_factory_workspace_executions "
            "SET status=%s,blocker_json=%s,updated_at=%s "
            "WHERE execution_id=%s AND user_id=%s AND status IN ('created','running','blocked')",
            (
                str(status),
                execution_module._json(dict(blocker or {})),
                now,
                str(execution_id),
                int(user_id),
            ),
        )
        if cursor.rowcount != 1:
            raise SoftwareFactoryError("velia_factory_workspace_execution_state_conflict", status=409)
        cursor.execute(
            "UPDATE velia_developer_autopilot_missions SET status='archived',updated_at=%s "
            "WHERE user_id=%s AND status IN ('paused','active') AND mission_id IN ("
            "SELECT mission_id FROM velia_software_factory_workspace_execution_missions "
            "WHERE execution_id=%s AND user_id=%s"
            ")",
            (now, int(user_id), str(execution_id), int(user_id)),
        )
        execution_module._append_event(
            cursor,
            str(execution_id),
            int(user_id),
            "workspace_missions.archived",
            {"terminal_state": str(status), "mission_count": int(cursor.rowcount or 0)},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def install(execution_module: Any) -> None:
    global _INSTALLED
    if getattr(execution_module, "_workspace_execution_hardening_installed", False):
        return
    original_create = execution_module.create_execution
    original_set_execution_state = execution_module._set_execution_state

    def create_execution(user_id: int, workspace_id: str, plan_payload: Mapping[str, Any]) -> Dict[str, Any]:
        execution_module._require_live(int(user_id))
        _preflight_mission_conflicts(execution_module, int(user_id), str(workspace_id), plan_payload)
        return original_create(int(user_id), str(workspace_id), plan_payload)

    def set_execution_state(
        execution_id: str,
        user_id: int,
        status: str,
        blocker: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if str(status) in _TERMINAL_ARCHIVE_STATES:
            _set_terminal_state_and_archive_missions(
                execution_module,
                str(execution_id),
                int(user_id),
                str(status),
                blocker,
            )
            return
        original_set_execution_state(str(execution_id), int(user_id), str(status), blocker)

    def resume_execution(user_id: int, execution_id: str) -> Dict[str, Any]:
        execution_module._require_live(int(user_id))
        current = execution_module.get_execution(int(user_id), str(execution_id))
        if str(current.get("status") or "") != "blocked":
            raise SoftwareFactoryError("velia_factory_workspace_execution_not_resumable", status=409)
        blocker = current.get("blocker") if isinstance(current.get("blocker"), Mapping) else {}
        if str(blocker.get("code") or "") not in {
            "velia_factory_workspace_mission_conflict",
            "velia_factory_workspace_mission_setup_failed",
        }:
            raise SoftwareFactoryError("velia_factory_workspace_execution_requires_manual_repair", status=409)
        execution_module._set_execution_state(str(execution_id), int(user_id), "created", {})
        return execution_module.tick_execution(int(user_id), str(execution_id))

    def run_workspace_supervisor_once():
        # New work still requires full live rollout. Stop reconciliation is safer:
        # once a user requested stop, keep observing in-flight tasks until the
        # first safe boundary even if rollout is subsequently disabled.
        if not (execution_module.autonomy.supervisor_enabled() and execution_module.autopilot.worker_enabled()):
            return []
        results = []
        for user_id, execution_id in execution_module._candidate_executions(
            execution_module._env_int("VELIA_SOFTWARE_FACTORY_WORKSPACE_SUPERVISOR_MAX_RUNS_PER_TICK", 20, 1, 100)
        ):
            try:
                current = execution_module.get_execution(int(user_id), str(execution_id))
                if bool(current.get("stop_requested")):
                    results.append(execution_module.request_stop(int(user_id), str(execution_id)))
                    continue
                if not execution_module.workspace_supervisor_enabled():
                    continue
                if not execution_module.rollout.user_allowed(int(user_id)):
                    continue
                results.append(execution_module.tick_execution(int(user_id), str(execution_id)))
            except Exception:
                logger.exception("VELIA_WORKSPACE_SUPERVISOR_EXECUTION_FAILED execution_id=%s", execution_id)
        return results

    execution_module.create_execution = create_execution
    execution_module._set_execution_state = set_execution_state
    execution_module.resume_execution = resume_execution
    execution_module.run_workspace_supervisor_once = run_workspace_supervisor_once
    execution_module._workspace_execution_hardening_installed = True
    _INSTALLED = True
