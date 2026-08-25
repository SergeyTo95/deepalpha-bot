from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Mapping

from services import velia_software_factory_reviewer_service as reviewer


logger = logging.getLogger(__name__)
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()


def _review_result(autopilot: Any, run: Mapping[str, Any], original_result: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(original_result or {})
    if str(result.get("status") or "") != "ready_for_review":
        return result

    user_id = int(run.get("user_id") or 0)
    task = autopilot.get_task(user_id, str(run.get("task_id") or ""))
    if not reviewer.review_required(task):
        return result

    mission = autopilot.get_mission(user_id, str(run.get("mission_id") or ""))
    project = autopilot.project_service.get_project(user_id, str(mission.get("project_id") or ""))
    execution_result = result.get("result") if isinstance(result.get("result"), Mapping) else {}
    report = reviewer.review_execution(
        user_id=user_id,
        run_id=str(run.get("run_id") or ""),
        task=task,
        mission=mission,
        project=project,
        execution_result=execution_result,
    )

    final_result = dict(execution_result)
    final_result["reviewer"] = report
    review_status = str(report.get("status") or "blocked")
    branch = str(final_result.get("work_branch") or "")
    pull_request = final_result.get("pull_request") if isinstance(final_result.get("pull_request"), Mapping) else {}
    pull_request_number = int(pull_request.get("number") or 0)
    pull_request_url = str(pull_request.get("url") or "")
    estimated_cost_usd = float(final_result.get("estimated_cost_usd") or 0.0)

    if review_status == "passed":
        autopilot._transition(
            run,
            "ready_for_review",
            result=final_result,
            work_branch=branch,
            pull_request_number=pull_request_number,
            pull_request_url=pull_request_url,
            estimated_cost_usd=estimated_cost_usd,
            finished=True,
        )
        autopilot._record_event(
            run,
            "reviewer.passed",
            {
                "summary": str(report.get("summary") or "")[:2000],
                "finding_count": len(report.get("findings") or []),
                "changed_files": int((report.get("evidence") or {}).get("changed_files") or 0),
            },
        )
        result["result"] = final_result
        return result

    error_code = (
        "velia_factory_reviewer_failed"
        if review_status == "failed"
        else "velia_factory_reviewer_blocked"
    )
    autopilot._transition(
        run,
        "blocked",
        result=final_result,
        error_code=error_code,
        work_branch=branch,
        pull_request_number=pull_request_number,
        pull_request_url=pull_request_url,
        estimated_cost_usd=estimated_cost_usd,
        finished=True,
    )
    autopilot._record_event(
        run,
        f"reviewer.{review_status if review_status in {'failed', 'blocked'} else 'blocked'}",
        {
            "error_code": error_code,
            "summary": str(report.get("summary") or "")[:2000],
            "findings": list(report.get("findings") or [])[:20],
        },
    )
    result["status"] = "blocked"
    result["error_code"] = error_code
    result["result"] = final_result
    return result


def install(autopilot_module: Any = None) -> bool:
    global _INSTALLED
    with _INSTALL_LOCK:
        if autopilot_module is None:
            from services import velia_agent_coding_autopilot_service as autopilot_module

        if getattr(autopilot_module, "_velia_factory_reviewer_gate_installed", False):
            _INSTALLED = True
            return True

        original_execute_claimed = autopilot_module._execute_claimed

        def execute_claimed_with_reviewer(run: Mapping[str, Any]) -> Dict[str, Any]:
            original_result = original_execute_claimed(run)
            try:
                return _review_result(autopilot_module, run, original_result)
            except Exception as exc:
                logger.exception(
                    "VELIA_SOFTWARE_FACTORY_REVIEWER_INTERNAL_ERROR run_id=%s",
                    str(run.get("run_id") or "")[:120],
                )
                # The reviewer gate is fail-closed only for eligible Factory tasks.
                # Non-Factory and disabled-reviewer tasks retain legacy behavior.
                user_id = int(run.get("user_id") or 0)
                try:
                    task = autopilot_module.get_task(user_id, str(run.get("task_id") or ""))
                except Exception:
                    return dict(original_result or {})
                if not reviewer.review_required(task):
                    return dict(original_result or {})
                execution_result = dict((original_result or {}).get("result") or {})
                report = {
                    "status": "blocked",
                    "summary": "Senior Reviewer gate failed closed after an internal error.",
                    "findings": [
                        {
                            "severity": "high",
                            "code": "reviewer_internal_error",
                            "message": exc.__class__.__name__,
                            "path": "",
                        }
                    ],
                    "acceptance": [],
                }
                execution_result["reviewer"] = report
                error_code = "velia_factory_reviewer_blocked"
                pull_request = execution_result.get("pull_request") if isinstance(execution_result.get("pull_request"), Mapping) else {}
                autopilot_module._transition(
                    run,
                    "blocked",
                    result=execution_result,
                    error_code=error_code,
                    work_branch=str(execution_result.get("work_branch") or ""),
                    pull_request_number=int(pull_request.get("number") or 0),
                    pull_request_url=str(pull_request.get("url") or ""),
                    estimated_cost_usd=float(execution_result.get("estimated_cost_usd") or 0.0),
                    finished=True,
                )
                result = dict(original_result or {})
                result["status"] = "blocked"
                result["error_code"] = error_code
                result["result"] = execution_result
                return result

        autopilot_module._execute_claimed = execute_claimed_with_reviewer
        autopilot_module._velia_factory_reviewer_gate_installed = True
        _INSTALLED = True
        logger.info(
            "VELIA_SOFTWARE_FACTORY_REVIEWER_GATE_INSTALLED enabled=%s scope=factory_and_workspace read_only=true",
            str(reviewer.reviewer_enabled()).lower(),
        )
        return True
