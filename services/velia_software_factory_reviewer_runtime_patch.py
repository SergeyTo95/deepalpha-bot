from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Mapping

from services import velia_software_factory_reviewer_service as reviewer


logger = logging.getLogger(__name__)
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()
_REVIEW_CONTEXT = threading.local()

# GitHub commit statuses can surface a hard failure as conclusion="error".
# Keep this deterministic blocker next to the runtime integration until the
# reviewer service's public conclusion contract is extracted separately.
reviewer._FAILING_CHECK_CONCLUSIONS.add("error")


def _context() -> Dict[str, Dict[str, Any]]:
    value = getattr(_REVIEW_CONTEXT, "decisions", None)
    if not isinstance(value, dict):
        value = {}
        _REVIEW_CONTEXT.decisions = value
    return value


def _run_key(run: Mapping[str, Any]) -> str:
    return str(run.get("run_id") or "")


def _build_blocked_report(exc: Exception) -> Dict[str, Any]:
    return {
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


def _review_transition(
    autopilot: Any,
    original_transition: Any,
    run: Mapping[str, Any],
    status: str,
    **kwargs: Any,
) -> None:
    if str(status or "") != "ready_for_review" or not reviewer.reviewer_enabled():
        original_transition(run, status, **kwargs)
        return

    user_id = int(run.get("user_id") or 0)
    try:
        task = autopilot.get_task(user_id, str(run.get("task_id") or ""))
    except Exception:
        # The original Autopilot already resolved this task before reaching the
        # terminal transition. Preserve legacy behavior if the second read is
        # unexpectedly unavailable rather than widening the reviewer scope.
        original_transition(run, status, **kwargs)
        return
    if not reviewer.review_required(task):
        original_transition(run, status, **kwargs)
        return

    mission = autopilot.get_mission(user_id, str(run.get("mission_id") or ""))
    project = autopilot.project_service.get_project(user_id, str(mission.get("project_id") or ""))
    execution_result = kwargs.get("result") if isinstance(kwargs.get("result"), Mapping) else {}
    try:
        report = reviewer.review_execution(
            user_id=user_id,
            run_id=str(run.get("run_id") or ""),
            task=task,
            mission=mission,
            project=project,
            execution_result=execution_result,
        )
    except Exception as exc:
        logger.exception(
            "VELIA_SOFTWARE_FACTORY_REVIEWER_INTERNAL_ERROR run_id=%s",
            str(run.get("run_id") or "")[:120],
        )
        report = _build_blocked_report(exc)

    final_result = dict(execution_result)
    final_result["reviewer"] = report
    review_status = str(report.get("status") or "blocked")
    decision: Dict[str, Any] = {
        "status": review_status,
        "report": report,
        "result": final_result,
    }

    if review_status == "passed":
        next_kwargs = dict(kwargs)
        next_kwargs["result"] = final_result
        _context()[_run_key(run)] = decision
        original_transition(run, "ready_for_review", **next_kwargs)
        return

    error_code = (
        "velia_factory_reviewer_failed"
        if review_status == "failed"
        else "velia_factory_reviewer_blocked"
    )
    decision["error_code"] = error_code
    next_kwargs = dict(kwargs)
    next_kwargs["result"] = final_result
    next_kwargs["error_code"] = error_code
    _context()[_run_key(run)] = decision
    # Atomic boundary: an eligible Factory run never persists ready_for_review
    # before the independent reviewer verdict is known.
    original_transition(run, "blocked", **next_kwargs)


def _record_reviewer_event(autopilot: Any, run: Mapping[str, Any], decision: Mapping[str, Any]) -> None:
    report = decision.get("report") if isinstance(decision.get("report"), Mapping) else {}
    review_status = str(decision.get("status") or "blocked")
    if review_status == "passed":
        autopilot._record_event(
            run,
            "reviewer.passed",
            {
                "summary": str(report.get("summary") or "")[:2000],
                "finding_count": len(report.get("findings") or []),
                "changed_files": int((report.get("evidence") or {}).get("changed_files") or 0),
            },
        )
        return
    autopilot._record_event(
        run,
        f"reviewer.{review_status if review_status in {'failed', 'blocked'} else 'blocked'}",
        {
            "error_code": str(decision.get("error_code") or "velia_factory_reviewer_blocked"),
            "summary": str(report.get("summary") or "")[:2000],
            "findings": list(report.get("findings") or [])[:20],
        },
    )


def _rewrite_result(original_result: Mapping[str, Any], decision: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(original_result or {})
    final_result = decision.get("result") if isinstance(decision.get("result"), Mapping) else {}
    result["result"] = dict(final_result)
    if str(decision.get("status") or "") == "passed":
        result["status"] = "ready_for_review"
        return result
    result["status"] = "blocked"
    result["error_code"] = str(decision.get("error_code") or "velia_factory_reviewer_blocked")
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
        original_transition = autopilot_module._transition
        original_record_event = autopilot_module._record_event

        def transition_with_reviewer(run: Mapping[str, Any], status: str, **kwargs: Any) -> None:
            _review_transition(
                autopilot_module,
                original_transition,
                run,
                status,
                **kwargs,
            )

        def record_event_with_reviewer(run: Mapping[str, Any], event_type: str, payload: Any = None) -> None:
            decision = _context().get(_run_key(run))
            if str(event_type or "") == "draft_pr_ready" and isinstance(decision, Mapping):
                if str(decision.get("status") or "") != "passed":
                    original_record_event(
                        run,
                        "draft_pr_created_review_blocked",
                        {
                            "pull_request": payload if isinstance(payload, Mapping) else {},
                            "reviewer_status": str(decision.get("status") or "blocked"),
                        },
                    )
                    return
            original_record_event(run, event_type, payload)

        def execute_claimed_with_reviewer(run: Mapping[str, Any]) -> Dict[str, Any]:
            key = _run_key(run)
            _context().pop(key, None)
            try:
                original_result = original_execute_claimed(run)
                decision = _context().pop(key, None)
                if not isinstance(decision, Mapping):
                    return dict(original_result or {})
                _record_reviewer_event(autopilot_module, run, decision)
                return _rewrite_result(original_result, decision)
            finally:
                _context().pop(key, None)

        autopilot_module._transition = transition_with_reviewer
        autopilot_module._record_event = record_event_with_reviewer
        autopilot_module._execute_claimed = execute_claimed_with_reviewer
        autopilot_module._velia_factory_reviewer_gate_installed = True
        _INSTALLED = True
        logger.info(
            "VELIA_SOFTWARE_FACTORY_REVIEWER_GATE_INSTALLED enabled=%s scope=factory_and_workspace read_only=true atomic_transition=true",
            str(reviewer.reviewer_enabled()).lower(),
        )
        return True
