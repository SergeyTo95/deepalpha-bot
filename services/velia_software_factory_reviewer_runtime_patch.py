from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Mapping, Optional

from services import velia_software_factory_reviewer_remediation_service as remediation
from services import velia_software_factory_reviewer_service as reviewer


logger = logging.getLogger(__name__)
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()
_REVIEW_CONTEXT = threading.local()

# GitHub commit statuses can surface a hard failure as conclusion="error".
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


def _ci_enabled(ci_module: Any) -> bool:
    try:
        return bool(ci_module is not None and ci_module.ci_watch_enabled())
    except Exception:
        return False


def _review_decision(
    autopilot: Any,
    run: Mapping[str, Any],
    execution_result: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    if not reviewer.reviewer_enabled():
        return None

    user_id = int(run.get("user_id") or 0)
    try:
        task = autopilot.get_task(user_id, str(run.get("task_id") or ""))
    except Exception:
        return None
    if not reviewer.review_required(task):
        return None

    try:
        mission = autopilot.get_mission(
            user_id,
            str(run.get("mission_id") or ""),
        )
        project = autopilot.project_service.get_project(
            user_id,
            str(mission.get("project_id") or ""),
        )
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
    if review_status != "passed":
        decision["error_code"] = (
            "velia_factory_reviewer_failed"
            if review_status == "failed"
            else "velia_factory_reviewer_blocked"
        )
    return decision


def _review_transition(
    autopilot: Any,
    ci_module: Any,
    original_transition: Any,
    run: Mapping[str, Any],
    status: str,
    **kwargs: Any,
) -> None:
    if str(status or "") != "ready_for_review" or not reviewer.reviewer_enabled():
        original_transition(run, status, **kwargs)
        return

    # With CI enabled this is only the pre-CI executor transition. The CI
    # repair loop owns the branch until a stable exact head is green. Reviewing
    # here would attach evidence to a head that a later repair can replace.
    if _ci_enabled(ci_module):
        original_transition(run, status, **kwargs)
        return

    execution_result = (
        kwargs.get("result")
        if isinstance(kwargs.get("result"), Mapping)
        else {}
    )
    decision = _review_decision(autopilot, run, execution_result)
    if not isinstance(decision, Mapping):
        original_transition(run, status, **kwargs)
        return

    next_kwargs = dict(kwargs)
    next_kwargs["result"] = dict(decision.get("result") or {})
    _context()[_run_key(run)] = dict(decision)
    if str(decision.get("status") or "") == "passed":
        original_transition(run, "ready_for_review", **next_kwargs)
        return

    error_code = str(
        decision.get("error_code") or "velia_factory_reviewer_blocked"
    )
    next_kwargs["error_code"] = error_code
    # Atomic boundary for CI-disabled Factory runs: an eligible run never
    # persists ready_for_review before the independent verdict is known.
    original_transition(run, "blocked", **next_kwargs)


def _review_ci_transition(
    autopilot: Any,
    ci_module: Any,
    original_set_run_state: Any,
    run: Mapping[str, Any],
    status: str,
    *,
    result: Optional[Dict[str, Any]] = None,
    error_code: str = "",
    finished: bool = False,
) -> None:
    if (
        str(status or "") != "ready_for_review"
        or not reviewer.reviewer_enabled()
    ):
        original_set_run_state(
            run,
            status,
            result=result,
            error_code=error_code,
            finished=finished,
        )
        return

    execution_result = (
        result if isinstance(result, Mapping) else run.get("result")
    )
    if not isinstance(execution_result, Mapping):
        execution_result = {}

    decision = _review_decision(autopilot, run, execution_result)
    if not isinstance(decision, Mapping):
        original_set_run_state(
            run,
            status,
            result=result,
            error_code=error_code,
            finished=finished,
        )
        return

    key = _run_key(run)
    final_result = dict(decision.get("result") or {})
    review_status = str(decision.get("status") or "")

    if review_status == "passed":
        final_result = remediation.mark_review_passed(
            ci_module,
            final_result,
            decision.get("report") if isinstance(decision.get("report"), Mapping) else {},
        )
        passed_decision = dict(decision)
        passed_decision["result"] = final_result
        _context()[key] = passed_decision
        original_set_run_state(
            run,
            "ready_for_review",
            result=final_result,
            error_code="",
            finished=finished,
        )
        return

    if review_status == "failed":
        try:
            scheduled = remediation.schedule_after_failed_review(
                autopilot,
                ci_module,
                run,
                final_result,
                decision,
            )
        except Exception:
            logger.exception(
                "VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_SCHEDULE_FAILED run_id=%s",
                str(run.get("run_id") or "")[:120],
            )
            scheduled = None
        if isinstance(scheduled, Mapping):
            remediating = dict(decision)
            remediating["status"] = "remediating"
            remediating["result"] = dict(scheduled.get("result") or final_result)
            remediating["remediation"] = {
                "attempt_number": int(scheduled.get("attempt_number") or 0),
                "head_sha": str(scheduled.get("head_sha") or "")[:40],
            }
            remediating.pop("error_code", None)
            _context()[key] = remediating
            # schedule_after_failed_review already persisted the active
            # reviewer-remediation state. Never persist blocked/ready in between.
            return

    final_error = str(
        decision.get("error_code") or "velia_factory_reviewer_blocked"
    )
    final_result = remediation.mark_review_blocked(
        ci_module,
        final_result,
        final_error,
    )
    blocked_decision = dict(decision)
    blocked_decision["result"] = final_result
    _context()[key] = blocked_decision
    original_set_run_state(
        run,
        "blocked",
        result=final_result,
        error_code=final_error,
        finished=True,
    )


def _record_reviewer_event(
    autopilot: Any,
    run: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> None:
    report = (
        decision.get("report")
        if isinstance(decision.get("report"), Mapping)
        else {}
    )
    review_status = str(decision.get("status") or "blocked")
    if review_status == "passed":
        autopilot._record_event(
            run,
            "reviewer.passed",
            {
                "summary": str(report.get("summary") or "")[:2000],
                "finding_count": len(report.get("findings") or []),
                "changed_files": int(
                    (report.get("evidence") or {}).get("changed_files") or 0
                ),
                "reviewed_head_sha": str(
                    (report.get("evidence") or {}).get("reviewed_head_sha")
                    or ""
                )[:40],
            },
        )
        return
    if review_status == "remediating":
        remediation_state = (
            decision.get("remediation")
            if isinstance(decision.get("remediation"), Mapping)
            else {}
        )
        autopilot._record_event(
            run,
            "reviewer.remediation_scheduled",
            {
                "summary": str(report.get("summary") or "")[:2000],
                "finding_count": len(report.get("findings") or []),
                "reviewed_head_sha": str(
                    (report.get("evidence") or {}).get("reviewed_head_sha") or ""
                )[:40],
                "head_sha": str(remediation_state.get("head_sha") or "")[:40],
                "attempt_number": int(remediation_state.get("attempt_number") or 0),
            },
        )
        return
    autopilot._record_event(
        run,
        (
            f"reviewer."
            f"{review_status if review_status in {'failed', 'blocked'} else 'blocked'}"
        ),
        {
            "error_code": str(
                decision.get("error_code")
                or "velia_factory_reviewer_blocked"
            ),
            "summary": str(report.get("summary") or "")[:2000],
            "findings": list(report.get("findings") or [])[:20],
            "reviewed_head_sha": str(
                (report.get("evidence") or {}).get("reviewed_head_sha") or ""
            )[:40],
        },
    )


def _rewrite_result(
    original_result: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> Dict[str, Any]:
    result = dict(original_result or {})
    final_result = (
        decision.get("result")
        if isinstance(decision.get("result"), Mapping)
        else {}
    )
    result["result"] = dict(final_result)
    decision_status = str(decision.get("status") or "")
    if decision_status == "passed":
        result["status"] = "ready_for_review"
        result.pop("error_code", None)
        return result
    if decision_status == "remediating":
        result["status"] = "executing"
        result.pop("error_code", None)
        return result
    result["status"] = "blocked"
    result["error_code"] = str(
        decision.get("error_code") or "velia_factory_reviewer_blocked"
    )
    return result


def install(
    autopilot_module: Any = None,
    ci_module: Any = None,
) -> bool:
    global _INSTALLED
    with _INSTALL_LOCK:
        if autopilot_module is None:
            from services import (
                velia_agent_coding_autopilot_service as autopilot_module,
            )
        if ci_module is None:
            from services import (
                velia_agent_coding_autopilot_ci_service as ci_module,
            )

        if getattr(
            autopilot_module,
            "_velia_factory_reviewer_gate_installed",
            False,
        ):
            _INSTALLED = True
            return True

        original_execute_claimed = autopilot_module._execute_claimed
        original_transition = autopilot_module._transition
        original_record_event = autopilot_module._record_event

        def transition_with_reviewer(
            run: Mapping[str, Any],
            status: str,
            **kwargs: Any,
        ) -> None:
            _review_transition(
                autopilot_module,
                ci_module,
                original_transition,
                run,
                status,
                **kwargs,
            )

        def record_event_with_reviewer(
            run: Mapping[str, Any],
            event_type: str,
            payload: Any = None,
        ) -> None:
            decision = _context().get(_run_key(run))
            if (
                str(event_type or "") == "draft_pr_ready"
                and isinstance(decision, Mapping)
                and str(decision.get("status") or "") != "passed"
            ):
                original_record_event(
                    run,
                    "draft_pr_created_review_blocked",
                    {
                        "pull_request": (
                            payload if isinstance(payload, Mapping) else {}
                        ),
                        "reviewer_status": str(
                            decision.get("status") or "blocked"
                        ),
                    },
                )
                return
            original_record_event(run, event_type, payload)

        def execute_claimed_with_reviewer(
            run: Mapping[str, Any],
        ) -> Dict[str, Any]:
            key = _run_key(run)
            _context().pop(key, None)
            try:
                original_result = original_execute_claimed(run)
                decision = _context().pop(key, None)
                if not isinstance(decision, Mapping):
                    return dict(original_result or {})
                _record_reviewer_event(
                    autopilot_module,
                    run,
                    decision,
                )
                return _rewrite_result(original_result, decision)
            finally:
                _context().pop(key, None)

        autopilot_module._transition = transition_with_reviewer
        autopilot_module._record_event = record_event_with_reviewer
        autopilot_module._execute_claimed = execute_claimed_with_reviewer
        autopilot_module._velia_factory_reviewer_gate_installed = True

        # Install this hook before the CI baseline wrapper is installed. The
        # baseline wrapper captures ci_module.process_ci_once, so the final call
        # chain remains baseline -> reviewer -> CI/repair processor. The state
        # hook evaluates the final exact head before ready_for_review is ever
        # persisted. Reviewer-remediation runs are consumed by this wrapper
        # before generic CI can interpret their state as a normal CI attempt.
        if not getattr(
            ci_module,
            "_velia_factory_reviewer_gate_installed",
            False,
        ):
            original_ci_set_run_state = ci_module._set_run_state
            original_ci_process_once = ci_module.process_ci_once

            def ci_set_run_state_with_reviewer(
                run: Mapping[str, Any],
                status: str,
                *,
                result: Optional[Dict[str, Any]] = None,
                error_code: str = "",
                finished: bool = False,
            ) -> None:
                _review_ci_transition(
                    autopilot_module,
                    ci_module,
                    original_ci_set_run_state,
                    run,
                    status,
                    result=result,
                    error_code=error_code,
                    finished=finished,
                )

            def ci_process_once_with_reviewer():
                remediation_result = remediation.process_once(
                    autopilot_module,
                    ci_module,
                )
                result = (
                    remediation_result
                    if remediation_result is not None
                    else original_ci_process_once()
                )
                if not isinstance(result, Mapping):
                    return result
                key = _run_key(result)
                decision = _context().pop(key, None)
                if not isinstance(decision, Mapping):
                    return result
                _record_reviewer_event(
                    autopilot_module,
                    result,
                    decision,
                )
                return _rewrite_result(result, decision)

            ci_module._set_run_state = ci_set_run_state_with_reviewer
            ci_module.process_ci_once = ci_process_once_with_reviewer
            ci_module._velia_factory_reviewer_gate_installed = True

        _INSTALLED = True
        logger.info(
            "VELIA_SOFTWARE_FACTORY_REVIEWER_GATE_INSTALLED "
            "enabled=%s scope=factory_and_workspace read_only=true "
            "atomic_transition=true final_head_after_ci=true "
            "remediation_enabled=%s remediation_max_attempts=%s",
            str(reviewer.reviewer_enabled()).lower(),
            str(remediation.remediation_enabled(ci_module)).lower(),
            remediation.remediation_max_attempts(),
        )
        return True
