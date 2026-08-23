from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from db.database import get_connection
from services import velia_agent_coding_autopilot_ci_classifier as ci_classifier
from services import velia_agent_coding_autopilot_ci_service as ci
from services import velia_agent_coding_autopilot_service as autopilot

logger = logging.getLogger(__name__)
_INSTALLED = False
_RECOVERABLE_ERROR = "velia_coding_autopilot_ci_evidence_insufficient"
_ACTIVE_ATTEMPT: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "velia_autopilot_ci_baseline_attempt",
    default=None,
)


def _check_name(item: Mapping[str, Any]) -> str:
    return str(item.get("name") or "").strip()


def _check_source(item: Mapping[str, Any]) -> str:
    return str(item.get("source") or "").strip().casefold()


def _same_check(required: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    required_name = _check_name(required).casefold()
    observed_name = _check_name(observed).casefold()
    if not required_name or required_name != observed_name:
        return False
    required_source = _check_source(required)
    observed_source = _check_source(observed)
    return not required_source or required_source == observed_source


def _successful_baseline_checks(attempt: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if str(attempt.get("status") or "") != "success":
        return []
    checks = [
        dict(item)
        for item in (attempt.get("checks") or [])
        if isinstance(item, Mapping) and _check_name(item)
    ]
    if not checks or ci._checks_state(checks) != "success":
        return []
    return checks


def apply_baseline_contract(
    observed_checks: Sequence[Mapping[str, Any]],
    baseline_attempt: Optional[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Require only checks proven green by an earlier success in the same run.

    With no valid green baseline the current full check set is returned unchanged,
    so new runs keep the existing fail-closed CI policy.
    """
    observed = [dict(item) for item in observed_checks if isinstance(item, Mapping)]
    baseline_checks = _successful_baseline_checks(baseline_attempt or {})
    if not baseline_checks:
        return observed, {
            "active": False,
            "baseline_attempt_number": None,
            "required": [],
            "ignored": [],
        }

    effective: List[Dict[str, Any]] = []
    matched_indexes = set()
    for required in baseline_checks:
        match_index = next(
            (
                index
                for index, item in enumerate(observed)
                if index not in matched_indexes and _same_check(required, item)
            ),
            None,
        )
        if match_index is None:
            effective.append(
                {
                    "name": _check_name(required),
                    "source": str(required.get("source") or ""),
                    "status": "missing",
                    "conclusion": "",
                    "url": "",
                }
            )
            continue
        matched_indexes.add(match_index)
        effective.append(observed[match_index])

    ignored = [
        _check_name(item)
        for index, item in enumerate(observed)
        if index not in matched_indexes and _check_name(item)
    ]
    return effective, {
        "active": True,
        "baseline_attempt_number": int((baseline_attempt or {}).get("attempt_number") or 0),
        "required": [_check_name(item) for item in baseline_checks][:30],
        "ignored": ignored[:30],
    }


def _previous_successful_attempt(run_id: str, attempt_number: int) -> Optional[Dict[str, Any]]:
    if int(attempt_number) <= 0:
        return None
    ci.ensure_coding_autopilot_ci_tables()
    conn = get_connection()
    cursor = ci._dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {ci._ATTEMPT_COLUMNS} FROM velia_developer_autopilot_ci_attempts "
            "WHERE run_id=%s AND attempt_number<%s AND status='success' "
            "ORDER BY attempt_number DESC LIMIT 1",
            (str(run_id), int(attempt_number)),
        )
        row = cursor.fetchone()
        return ci._attempt_from_row(row) if row else None
    finally:
        cursor.close()
        conn.close()


def _filter_failure_to_effective_checks(
    failure: Mapping[str, Any],
    effective_checks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    required = [dict(item) for item in effective_checks if isinstance(item, Mapping)]
    if not required:
        return dict(failure)
    result = dict(failure)
    filtered = []
    for item in result.get("failures") or []:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source") or "")
        comparable = dict(item)
        if source == "actions_job_log" and item.get("workflow"):
            comparable["name"] = str(item.get("workflow") or "")
            comparable["source"] = "check_run"
        if any(_same_check(check, comparable) for check in required):
            filtered.append(dict(item))
    result["failures"] = filtered[:20]
    return ci_classifier.classify_failure_payload(result)


def _blocked_candidate() -> Optional[Dict[str, Any]]:
    ci.ensure_coding_autopilot_ci_tables()
    conn = get_connection()
    cursor = ci._dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT run_id,user_id
            FROM velia_developer_autopilot_runs
            WHERE status='blocked' AND error_code=%s
              AND pull_request_number>0
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (_RECOVERABLE_ERROR,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        run_id = str(ci._value(row, "run_id", 0, ""))
        user_id = int(ci._value(row, "user_id", 1, 0) or 0)
    finally:
        cursor.close()
        conn.close()
    if not run_id or user_id <= 0:
        return None
    return autopilot.get_run(user_id, run_id)


def _requeue_blocked_attempt(
    run: Mapping[str, Any],
    attempt: Mapping[str, Any],
    effective_checks: Sequence[Mapping[str, Any]],
    baseline_meta: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    now = ci._utcnow()
    result = ci._append_ci_result(
        run,
        status="pending",
        head_sha=str(attempt.get("head_sha") or ""),
        attempt_number=int(attempt.get("attempt_number") or 0),
        checks=[dict(item) for item in effective_checks],
        failure={},
        error_code=None,
        baseline_contract=dict(baseline_meta),
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_runs
            SET status='waiting_ci',result_json=%s,error_code=NULL,
                claimed_by='',claimed_until=%s,finished_at=NULL,updated_at=%s
            WHERE run_id=%s AND status='blocked' AND error_code=%s
            """,
            (
                ci._json(result),
                now,
                now,
                str(run.get("run_id") or ""),
                _RECOVERABLE_ERROR,
            ),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            conn.rollback()
            return None
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_ci_attempts
            SET status='waiting',checks_json=%s,failure_json='{}',error_code=NULL,
                first_seen_at=%s,last_checked_at=NULL,finished_at=NULL,updated_at=%s
            WHERE attempt_id=%s AND run_id=%s AND status='failure' AND head_sha=%s
            """,
            (
                ci._json(list(effective_checks)),
                now,
                now,
                str(attempt.get("attempt_id") or ""),
                str(run.get("run_id") or ""),
                str(attempt.get("head_sha") or ""),
            ),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            conn.rollback()
            return None
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_tasks
            SET status='waiting_ci',result_json=%s,error_code=NULL,updated_at=%s
            WHERE task_id=%s AND status='blocked'
            """,
            (ci._json(result), now, str(run.get("task_id") or "")),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    recovered = {**dict(run), "status": "waiting_ci", "error_code": None, "result": result}
    try:
        autopilot._record_event(
            recovered,
            "ci_baseline_recovered",
            {
                "head_sha": str(attempt.get("head_sha") or ""),
                "attempt_number": int(attempt.get("attempt_number") or 0),
                "baseline_attempt_number": baseline_meta.get("baseline_attempt_number"),
                "required_checks": list(baseline_meta.get("required") or [])[:30],
                "ignored_checks": list(baseline_meta.get("ignored") or [])[:30],
            },
        )
    except Exception:
        logger.exception(
            "VELIA_AUTOPILOT_CI_BASELINE_RECOVERY_EVENT_FAILED run=%s",
            str(run.get("run_id") or ""),
        )
    return recovered


def recover_blocked_baseline_once(
    commit_status: Optional[Callable[[Dict[str, Any], str], Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    if not ci.ci_watch_enabled():
        return None
    run = _blocked_candidate()
    if not run:
        return None
    attempt = ci._current_attempt(str(run.get("run_id") or ""))
    if not attempt or str(attempt.get("status") or "") != "failure":
        return None
    baseline = _previous_successful_attempt(
        str(run.get("run_id") or ""), int(attempt.get("attempt_number") or 0)
    )
    if not baseline:
        return None
    project, _mission = ci._project_and_mission(run)
    head = ci.write_service.branch_head(project, str(run.get("work_branch") or ""))
    if str(head.get("sha") or "") != str(attempt.get("head_sha") or ""):
        return None
    reader = commit_status or ci.write_service.commit_status
    payload = reader(project, str(attempt.get("head_sha") or ""))
    observed = payload.get("checks") if isinstance(payload, Mapping) else []
    effective, meta = apply_baseline_contract(
        observed if isinstance(observed, list) else [], baseline
    )
    if not meta.get("active") or ci._checks_state(effective) != "success":
        return None
    recovered = _requeue_blocked_attempt(run, attempt, effective, meta)
    if recovered:
        logger.info(
            "VELIA_AUTOPILOT_CI_BASELINE_RECOVERED run=%s attempt=%s baseline=%s required=%s ignored=%s",
            str(run.get("run_id") or ""),
            int(attempt.get("attempt_number") or 0),
            meta.get("baseline_attempt_number"),
            len(meta.get("required") or []),
            len(meta.get("ignored") or []),
        )
    return recovered


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_current_attempt = ci._current_attempt
    original_commit_status = ci.write_service.commit_status
    original_failure_details = ci._failure_details
    original_process_ci_once = ci.process_ci_once
    original_run_once = autopilot.run_autopilot_once

    def current_attempt_with_context(run_id: str) -> Optional[Dict[str, Any]]:
        attempt = original_current_attempt(run_id)
        _ACTIVE_ATTEMPT.set(
            {
                "run_id": str(run_id),
                "attempt": dict(attempt) if isinstance(attempt, Mapping) else None,
            }
        )
        return attempt

    def commit_status_with_baseline(project: Dict[str, Any], sha: str) -> Dict[str, Any]:
        payload = original_commit_status(project, sha)
        context = _ACTIVE_ATTEMPT.get() or {}
        attempt = context.get("attempt") if isinstance(context, Mapping) else None
        if not isinstance(attempt, Mapping):
            return payload
        if str(attempt.get("head_sha") or "") != str(sha):
            return payload
        baseline = _previous_successful_attempt(
            str(context.get("run_id") or ""), int(attempt.get("attempt_number") or 0)
        )
        if not baseline:
            return payload
        checks = payload.get("checks") if isinstance(payload, Mapping) else []
        effective, meta = apply_baseline_contract(
            checks if isinstance(checks, list) else [], baseline
        )
        if not meta.get("active"):
            return payload
        logger.info(
            "VELIA_AUTOPILOT_CI_BASELINE_CONTRACT run=%s attempt=%s baseline=%s required=%s ignored=%s",
            str(context.get("run_id") or ""),
            int(attempt.get("attempt_number") or 0),
            meta.get("baseline_attempt_number"),
            len(meta.get("required") or []),
            len(meta.get("ignored") or []),
        )
        return {**dict(payload), "total": len(effective), "checks": effective, "baseline_contract": meta}

    def failure_details_with_baseline(
        project: Dict[str, Any], sha: str, checks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        failure = original_failure_details(project, sha, checks)
        context = _ACTIVE_ATTEMPT.get() or {}
        attempt = context.get("attempt") if isinstance(context, Mapping) else None
        if not isinstance(attempt, Mapping) or str(attempt.get("head_sha") or "") != str(sha):
            return failure
        baseline = _previous_successful_attempt(
            str(context.get("run_id") or ""), int(attempt.get("attempt_number") or 0)
        )
        if not baseline:
            return failure
        _effective, meta = apply_baseline_contract(checks, baseline)
        if not meta.get("active"):
            return failure
        return _filter_failure_to_effective_checks(failure, checks)

    def process_ci_once_with_context() -> Optional[Dict[str, Any]]:
        token = _ACTIVE_ATTEMPT.set(None)
        try:
            return original_process_ci_once()
        finally:
            _ACTIVE_ATTEMPT.reset(token)

    def run_once_with_baseline_recovery():
        recovered = recover_blocked_baseline_once(original_commit_status)
        if recovered:
            logger.info(
                "VELIA_AUTOPILOT_CI_BASELINE_RECOVERY_HANDOFF run=%s",
                str(recovered.get("run_id") or ""),
            )
        return original_run_once()

    ci._current_attempt = current_attempt_with_context
    ci.write_service.commit_status = commit_status_with_baseline
    ci._failure_details = failure_details_with_baseline
    ci.process_ci_once = process_ci_once_with_context
    autopilot.run_autopilot_once = run_once_with_baseline_recovery
    _INSTALLED = True
    logger.info("VELIA_AUTOPILOT_CI_BASELINE_PATCH_INSTALLED enabled=%s", ci.ci_watch_enabled())
