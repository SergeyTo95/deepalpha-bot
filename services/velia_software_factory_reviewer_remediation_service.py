from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional

from db.database import get_connection
from services import velia_software_factory_reviewer_service as reviewer


_REMEDIATION_ADVISORY_KEY = 8_618_270_433
_OWNER = "reviewer_remediation"
_NON_REMEDIABLE_CODES = {
    "reviewer_ci_failure_observed",
    "reviewer_diff_empty",
    "reviewer_files_exceeded",
    "reviewer_path_outside_scope",
    "reviewer_pr_head_changed",
    "reviewer_pr_head_missing",
    "reviewer_pr_not_draft",
    "reviewer_pr_not_open",
    "reviewer_pr_revalidation_unavailable",
    "reviewer_pr_unavailable",
    "reviewer_diff_unavailable",
    "reviewer_model_unavailable",
    "reviewer_internal_error",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def remediation_max_attempts() -> int:
    return _env_int("VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_MAX_ATTEMPTS", 2, 0, 2)


def remediation_enabled(ci_module: Any) -> bool:
    try:
        return bool(
            _env_bool("VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_ENABLED", False)
            and reviewer.reviewer_enabled()
            and ci_module.ci_repair_enabled()
        )
    except Exception:
        return False


def _now_iso(ci_module: Any) -> str:
    try:
        return ci_module._utcnow().isoformat() + "Z"
    except Exception:
        return datetime.utcnow().isoformat() + "Z"


def _state(result: Mapping[str, Any]) -> Dict[str, Any]:
    value = result.get("reviewer_remediation")
    return dict(value) if isinstance(value, Mapping) else {}


def owns_run(run: Mapping[str, Any]) -> bool:
    result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
    state = _state(result)
    return (
        str(run.get("status") or "") == "executing"
        and str(state.get("owner") or "") == _OWNER
        and str(state.get("phase") or "") == "waiting_ci"
    )


def _repairable_findings(
    report: Mapping[str, Any],
    allowed_files: List[str],
) -> List[Dict[str, str]]:
    allowed = {str(item) for item in allowed_files if str(item)}
    findings: List[Dict[str, str]] = []
    for raw in report.get("findings") or []:
        if not isinstance(raw, Mapping):
            continue
        code = str(raw.get("code") or "reviewer_finding")[:120]
        severity = str(raw.get("severity") or "medium").lower()
        path = str(raw.get("path") or "").strip()[:500]
        message = str(raw.get("message") or "")[:1600]
        if code in _NON_REMEDIABLE_CODES or code.startswith("reviewer_pr_"):
            return []
        if severity not in {"high", "critical"}:
            continue
        if not path or path not in allowed:
            return []
        findings.append(
            {
                "code": code,
                "severity": severity,
                "path": path,
                "message": message,
            }
        )
    if not findings:
        return []
    acceptance = [
        item
        for item in (report.get("acceptance") or [])
        if isinstance(item, Mapping) and str(item.get("status") or "") == "not_met"
    ]
    # Acceptance-only failures do not have a deterministic file boundary. They
    # remain blocked until a later stage can map criteria to approved files.
    if acceptance and not findings:
        return []
    return findings[:20]


def _reviewer_failure_payload(
    reviewed_head_sha: str,
    findings: List[Dict[str, str]],
) -> Dict[str, Any]:
    return {
        "head_sha": reviewed_head_sha,
        "checks": [],
        "failures": [
            {
                "source": "senior_reviewer",
                "name": item.get("code") or "reviewer_finding",
                "conclusion": "failure",
                "url": "",
                "title": item.get("code") or "reviewer_finding",
                "summary": item.get("message") or "",
                "text": item.get("message") or "",
                "annotations": [
                    {
                        "path": item.get("path") or "",
                        "start_line": 0,
                        "end_line": 0,
                        "level": item.get("severity") or "high",
                        "title": item.get("code") or "reviewer_finding",
                        "message": item.get("message") or "",
                        "raw_details": "Senior Reviewer finding",
                    }
                ],
            }
            for item in findings
        ],
        "repairable": True,
        "infrastructure": False,
    }


def _persist_active(
    ci_module: Any,
    run: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    now = ci_module._utcnow()
    lease_seconds = ci_module._env_int(
        "VELIA_DEVELOPER_AUTOPILOT_LEASE_SECONDS", 3600, 300, 7200
    )
    payload = ci_module._json(dict(result))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_runs
            SET status='executing',result_json=%s,error_code=NULL,
                claimed_until=%s,finished_at=NULL,updated_at=%s
            WHERE run_id=%s
            """,
            (
                payload,
                now + timedelta(seconds=lease_seconds),
                now,
                str(run.get("run_id") or ""),
            ),
        )
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_tasks
            SET status='executing',result_json=%s,error_code=NULL,updated_at=%s
            WHERE task_id=%s
            """,
            (
                payload,
                now,
                str(run.get("task_id") or ""),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def schedule_after_failed_review(
    autopilot_module: Any,
    ci_module: Any,
    run: Mapping[str, Any],
    execution_result: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    if str(decision.get("status") or "") != "failed":
        return None
    if not remediation_enabled(ci_module):
        return None

    report = decision.get("report") if isinstance(decision.get("report"), Mapping) else {}
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    reviewed_head_sha = str(evidence.get("reviewed_head_sha") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", reviewed_head_sha):
        return None

    result = dict(execution_result)
    current_state = _state(result)
    prior_attempts = [
        dict(item)
        for item in (current_state.get("attempts") or [])
        if isinstance(item, Mapping)
    ][-2:]
    maximum = remediation_max_attempts()
    if maximum <= 0 or len(prior_attempts) >= maximum:
        return None

    project, mission = ci_module._project_and_mission(run)
    job = ci_module._coding_job(run)
    allowed_files = ci_module._allowed_repair_files(job, mission)
    findings = _repairable_findings(report, allowed_files)
    if not findings:
        return None

    current_head = ci_module.write_service.branch_head(
        project,
        str(run.get("work_branch") or ""),
    )
    if str(current_head.get("sha") or "").lower() != reviewed_head_sha:
        return None

    attempt_number = len(prior_attempts) + 1
    synthetic_attempt = {
        "head_sha": reviewed_head_sha,
        "attempt_number": attempt_number - 1,
    }
    repair = ci_module._execute_repair(
        run,
        synthetic_attempt,
        _reviewer_failure_payload(reviewed_head_sha, findings),
    )
    new_head = str(repair.get("commit_sha") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", new_head) or new_head == reviewed_head_sha:
        return None

    history = [
        dict(item)
        for item in (result.get("reviewer_history") or [])
        if isinstance(item, Mapping)
    ][-2:]
    history.append(dict(report))
    result["reviewer_history"] = history[-3:]

    attempt_record = {
        "attempt_number": attempt_number,
        "from_head_sha": reviewed_head_sha,
        "to_head_sha": new_head,
        "summary": str(repair.get("summary") or "")[:2000],
        "files": [str(item)[:500] for item in (repair.get("files") or [])][:20],
        "findings": findings,
        "estimated_cost_usd": float(repair.get("estimated_cost_usd") or 0.0),
    }
    now_iso = _now_iso(ci_module)
    state = {
        "owner": _OWNER,
        "phase": "waiting_ci",
        "attempt_number": attempt_number,
        "max_attempts": maximum,
        "reviewed_head_sha": reviewed_head_sha,
        "head_sha": new_head,
        "started_at": now_iso,
        "last_checked_at": None,
        "checks": [],
        "observer_error_count": 0,
        "attempts": [*prior_attempts, attempt_record][-maximum:],
    }
    result["reviewer_remediation"] = state
    result["estimated_cost_usd"] = float(result.get("estimated_cost_usd") or 0.0) + float(
        repair.get("estimated_cost_usd") or 0.0
    )
    _persist_active(ci_module, run, result)
    autopilot_module._record_event(
        run,
        "reviewer.remediation_committed",
        {
            "attempt_number": attempt_number,
            "from_head_sha": reviewed_head_sha,
            "head_sha": new_head,
            "files": attempt_record["files"],
            "finding_count": len(findings),
        },
    )
    return {
        "status": "executing",
        "result": result,
        "attempt_number": attempt_number,
        "head_sha": new_head,
    }


def mark_review_passed(
    ci_module: Any,
    execution_result: Mapping[str, Any],
    report: Mapping[str, Any],
) -> Dict[str, Any]:
    result = dict(execution_result)
    state = _state(result)
    if str(state.get("owner") or "") != _OWNER:
        return result
    state["phase"] = "completed"
    state["completed_at"] = _now_iso(ci_module)
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    state["completed_head_sha"] = str(
        evidence.get("current_head_sha") or evidence.get("reviewed_head_sha") or state.get("head_sha") or ""
    )[:40]
    result["reviewer_remediation"] = state
    return result


def mark_review_blocked(
    ci_module: Any,
    execution_result: Mapping[str, Any],
    error_code: str,
) -> Dict[str, Any]:
    result = dict(execution_result)
    state = _state(result)
    if str(state.get("owner") or "") != _OWNER:
        return result
    state["phase"] = "blocked"
    state["blocked_at"] = _now_iso(ci_module)
    state["error_code"] = str(error_code or "velia_factory_reviewer_failed")[:120]
    result["reviewer_remediation"] = state
    return result


def _block(
    autopilot_module: Any,
    ci_module: Any,
    run: Mapping[str, Any],
    result: Mapping[str, Any],
    code: str,
    *,
    checks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload = dict(result)
    state = _state(payload)
    state["phase"] = "blocked"
    state["error_code"] = str(code)[:120]
    state["blocked_at"] = _now_iso(ci_module)
    if checks is not None:
        state["checks"] = checks[:30]
    payload["reviewer_remediation"] = state
    ci_module._set_run_state(
        run,
        "blocked",
        result=payload,
        error_code=code,
        finished=True,
    )
    autopilot_module._record_event(
        run,
        "reviewer.remediation_blocked",
        {
            "error_code": code,
            "head_sha": str(state.get("head_sha") or "")[:40],
            "attempt_number": int(state.get("attempt_number") or 0),
        },
    )
    return {**dict(run), "status": "blocked", "result": payload, "error_code": code}


def _poll_timing(ci_module: Any, state: Mapping[str, Any]) -> tuple[float, int, int]:
    first_seen_raw = str(state.get("started_at") or "")
    try:
        first_seen = datetime.fromisoformat(first_seen_raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        first_seen = ci_module._utcnow()
    age = max(0.0, (ci_module._utcnow() - first_seen).total_seconds())
    max_wait = _env_int(
        "VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_CI_MAX_WAIT_MINUTES",
        45,
        5,
        180,
    ) * 60
    grace = _env_int(
        "VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_CI_GRACE_SECONDS",
        90,
        30,
        900,
    )
    return age, max_wait, grace


def _observer_retry(
    autopilot_module: Any,
    ci_module: Any,
    run: Mapping[str, Any],
    result: Mapping[str, Any],
    exc: Exception,
    *,
    age: float,
    max_wait: int,
) -> Dict[str, Any]:
    payload = dict(result)
    state = _state(payload)
    error_count = int(state.get("observer_error_count") or 0) + 1
    max_errors = _env_int(
        "VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_OBSERVER_MAX_ERRORS",
        5,
        1,
        20,
    )
    state["observer_error_count"] = error_count
    state["last_observer_error"] = exc.__class__.__name__[:120]
    state["last_checked_at"] = _now_iso(ci_module)
    payload["reviewer_remediation"] = state

    if age > max_wait or error_count >= max_errors:
        return _block(
            autopilot_module,
            ci_module,
            run,
            payload,
            "velia_factory_reviewer_remediation_observer_unavailable",
        )

    _persist_active(ci_module, run, payload)
    autopilot_module._record_event(
        run,
        "reviewer.remediation_observer_retry",
        {
            "head_sha": str(state.get("head_sha") or "")[:40],
            "attempt_number": int(state.get("attempt_number") or 0),
            "error_count": error_count,
            "max_errors": max_errors,
            "error": exc.__class__.__name__[:120],
        },
    )
    return {**dict(run), "status": "executing", "result": payload}


def _process_run(
    autopilot_module: Any,
    ci_module: Any,
    run: Mapping[str, Any],
) -> Dict[str, Any]:
    result = dict(run.get("result") or {}) if isinstance(run.get("result"), Mapping) else {}
    state = _state(result)
    if not remediation_enabled(ci_module):
        return _block(
            autopilot_module,
            ci_module,
            run,
            result,
            "velia_factory_reviewer_remediation_disabled",
        )

    head_sha = str(state.get("head_sha") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        return _block(
            autopilot_module,
            ci_module,
            run,
            result,
            "velia_factory_reviewer_remediation_head_missing",
        )

    age, max_wait, grace = _poll_timing(ci_module, state)

    try:
        project, _mission = ci_module._project_and_mission(run)
        current_head = ci_module.write_service.branch_head(
            project,
            str(run.get("work_branch") or ""),
        )
    except Exception as exc:
        return _observer_retry(
            autopilot_module,
            ci_module,
            run,
            result,
            exc,
            age=age,
            max_wait=max_wait,
        )

    if str(current_head.get("sha") or "").lower() != head_sha:
        return _block(
            autopilot_module,
            ci_module,
            run,
            result,
            "velia_factory_reviewer_remediation_head_changed",
        )

    try:
        checks_payload = ci_module.write_service.commit_status(project, head_sha)
        checks = checks_payload.get("checks") if isinstance(checks_payload, Mapping) else []
        if not isinstance(checks, list):
            checks = []
        checks = [dict(item) for item in checks if isinstance(item, Mapping)][:30]
        check_state = ci_module._checks_state(checks)
    except Exception as exc:
        return _observer_retry(
            autopilot_module,
            ci_module,
            run,
            result,
            exc,
            age=age,
            max_wait=max_wait,
        )

    if int(state.get("observer_error_count") or 0) > 0:
        state["observer_recovered_at"] = _now_iso(ci_module)
    state["observer_error_count"] = 0
    state["last_observer_error"] = None

    if check_state in {"missing", "pending"}:
        if age > max_wait:
            return _block(
                autopilot_module,
                ci_module,
                run,
                result,
                "velia_factory_reviewer_remediation_ci_timeout",
                checks=checks,
            )
        if check_state == "missing" and age > grace:
            return _block(
                autopilot_module,
                ci_module,
                run,
                result,
                "velia_factory_reviewer_remediation_ci_checks_missing",
                checks=[],
            )
        state["checks"] = checks
        state["last_checked_at"] = _now_iso(ci_module)
        result["reviewer_remediation"] = state
        _persist_active(ci_module, run, result)
        return {**dict(run), "status": "executing", "result": result}

    if check_state != "success":
        result["reviewer_remediation"] = state
        return _block(
            autopilot_module,
            ci_module,
            run,
            result,
            "velia_factory_reviewer_remediation_ci_failed",
            checks=checks,
        )

    state["phase"] = "reviewing"
    state["checks"] = checks
    state["last_checked_at"] = _now_iso(ci_module)
    result["reviewer_remediation"] = state
    result["checks"] = {
        "head_sha": head_sha,
        "total": len(checks),
        "checks": checks,
    }
    autopilot_module._record_event(
        run,
        "reviewer.remediation_ci_success",
        {
            "head_sha": head_sha,
            "attempt_number": int(state.get("attempt_number") or 0),
            "check_count": len(checks),
        },
    )
    # The Senior Reviewer runtime owns this transition. Calling the wrapped
    # state setter here guarantees a fresh authoritative review of this exact
    # remediation head before ready_for_review can persist.
    ci_module._set_run_state(
        run,
        "ready_for_review",
        result=result,
        error_code="",
        finished=True,
    )
    return {**dict(run), "status": "ready_for_review", "result": result}


def process_once(
    autopilot_module: Any,
    ci_module: Any,
) -> Optional[Dict[str, Any]]:
    # The runtime wrapper calls this only while Stage 6.6 is enabled. Once an
    # owned run is selected, _process_run revalidates the flag so a configuration
    # change between the wrapper check and the DB claim still fails closed.
    conn = get_connection()
    cursor = conn.cursor()
    locked = False
    try:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (_REMEDIATION_ADVISORY_KEY,))
        row = cursor.fetchone()
        locked = bool(row and row[0])
        if not locked:
            return None
        cursor.execute(
            f"SELECT {autopilot_module._RUN_COLUMNS} "
            "FROM velia_developer_autopilot_runs "
            "WHERE status='executing' "
            "AND COALESCE(result_json,'{}')::jsonb #>> '{reviewer_remediation,owner}'=%s "
            "AND COALESCE(result_json,'{}')::jsonb #>> '{reviewer_remediation,phase}'='waiting_ci' "
            "ORDER BY updated_at ASC LIMIT 1",
            (_OWNER,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        run = autopilot_module._run_from_row(row)
        return _process_run(autopilot_module, ci_module, run)
    finally:
        if locked:
            try:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (_REMEDIATION_ADVISORY_KEY,))
                conn.commit()
            except Exception:
                conn.rollback()
        cursor.close()
        conn.close()
