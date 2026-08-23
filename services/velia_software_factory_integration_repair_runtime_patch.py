from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from db.database import get_connection
from services import velia_software_factory_integration_repair_service as repair_service
from services import velia_software_factory_workspace_execution_hardening_patch as execution_hardening
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False
_REPAIR_BLOCKERS = {
    "velia_factory_integration_repair_waiting_ci",
}


def _repair_candidates(execution_module: Any, limit: int) -> list[tuple[int, str]]:
    repair_service.ensure_integration_repair_tables(execution_module)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT user_id,execution_id FROM velia_software_factory_workspace_executions "
            "WHERE status='blocked' AND blocker_json LIKE %s ORDER BY updated_at ASC LIMIT %s",
            (
                "%velia_factory_integration_repair_waiting_ci%",
                min(100, max(1, int(limit))),
            ),
        )
        return [(int(row[0]), str(row[1])) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def install(workspace_module: Any, execution_module: Any, integration_runtime: Any) -> None:
    global _INSTALLED
    if getattr(execution_module, "_workspace_integration_repair_installed", False):
        return

    original_get_execution = execution_module.get_execution
    original_set_execution_state = execution_module._set_execution_state
    original_resume_execution = getattr(execution_module, "resume_execution", None)
    original_supervisor_once = execution_module.run_workspace_supervisor_once

    def finalize_validation_pass(
        module: Any,
        user_id: int,
        execution_id: str,
        validation_id: str,
    ) -> None:
        latest = integration_runtime.latest_validation(module, int(user_id), str(execution_id))
        report = latest.get("report") if isinstance(latest.get("report"), Mapping) else {}
        if (
            str(latest.get("validation_id") or "") != str(validation_id)
            or str(report.get("status") or latest.get("status") or "") != "passed"
        ):
            raise SoftwareFactoryError(
                "velia_factory_integration_repair_pass_evidence_stale", status=409
            )
        execution_hardening._set_terminal_state_and_archive_missions(
            module,
            str(execution_id),
            int(user_id),
            "review_ready",
            {},
        )

    integration_runtime.finalize_validation_pass = finalize_validation_pass

    def get_execution(user_id: int, execution_id: str) -> Dict[str, Any]:
        result = original_get_execution(int(user_id), str(execution_id))
        result["integration_repair"] = repair_service.latest_repair(
            execution_module, int(user_id), str(execution_id)
        )
        result["integration_repair_enabled"] = repair_service.integration_repair_enabled()
        return result

    def set_execution_state(
        execution_id: str,
        user_id: int,
        status: str,
        blocker: Optional[Mapping[str, Any]] = None,
    ) -> None:
        original_set_execution_state(
            str(execution_id), int(user_id), str(status), blocker
        )
        if str(status) != "review_ready" or not repair_service.integration_repair_enabled():
            return
        current = original_get_execution(int(user_id), str(execution_id))
        current_blocker = (
            current.get("blocker") if isinstance(current.get("blocker"), Mapping) else {}
        )
        if (
            str(current.get("status") or "") == "blocked"
            and str(current_blocker.get("code") or "")
            == "velia_factory_workspace_integration_validation_failed"
        ):
            repair_service.process_execution(
                execution_module,
                integration_runtime,
                int(user_id),
                str(execution_id),
            )

    def resume_execution(user_id: int, execution_id: str) -> Dict[str, Any]:
        current = original_get_execution(int(user_id), str(execution_id))
        blocker = current.get("blocker") if isinstance(current.get("blocker"), Mapping) else {}
        code = str(blocker.get("code") or "")
        if code in _REPAIR_BLOCKERS or code.startswith("velia_factory_integration_repair_"):
            if not repair_service.integration_repair_enabled():
                raise SoftwareFactoryError(
                    "velia_factory_integration_repair_disabled", status=503
                )
            return repair_service.process_execution(
                execution_module,
                integration_runtime,
                int(user_id),
                str(execution_id),
            )
        if callable(original_resume_execution):
            return original_resume_execution(int(user_id), str(execution_id))
        raise SoftwareFactoryError("velia_factory_workspace_execution_not_resumable", status=409)

    def run_workspace_supervisor_once() -> list[Dict[str, Any]]:
        results = list(original_supervisor_once() or [])
        if not repair_service.integration_repair_enabled():
            return results
        if not execution_module.workspace_supervisor_enabled():
            return results
        limit = execution_module._env_int(
            "VELIA_SOFTWARE_FACTORY_WORKSPACE_SUPERVISOR_MAX_RUNS_PER_TICK", 20, 1, 100
        )
        for user_id, execution_id in _repair_candidates(execution_module, limit):
            if not execution_module.rollout.user_allowed(int(user_id)):
                continue
            try:
                results.append(
                    repair_service.process_execution(
                        execution_module,
                        integration_runtime,
                        int(user_id),
                        str(execution_id),
                    )
                )
            except Exception:
                logger.exception(
                    "VELIA_WORKSPACE_INTEGRATION_REPAIR_SUPERVISOR_FAILED execution_id=%s",
                    execution_id,
                )
        return results

    execution_module.get_execution = get_execution
    execution_module._set_execution_state = set_execution_state
    execution_module.resume_execution = resume_execution
    execution_module.run_workspace_supervisor_once = run_workspace_supervisor_once
    execution_module.process_integration_repair = lambda user_id, execution_id: repair_service.process_execution(
        execution_module, integration_runtime, int(user_id), str(execution_id)
    )
    execution_module.latest_integration_repair = lambda user_id, execution_id: repair_service.latest_repair(
        execution_module, int(user_id), str(execution_id)
    )
    execution_module.integration_repair_enabled = repair_service.integration_repair_enabled
    execution_module.integration_repair_status = repair_service.public_status
    execution_module._workspace_integration_repair_installed = True
    _INSTALLED = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_INSTALLED enabled=%s max_attempts=%s",
        str(repair_service.integration_repair_enabled()).lower(),
        repair_service.integration_repair_max_attempts(),
    )
