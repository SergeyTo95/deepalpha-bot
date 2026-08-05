from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import quote

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection
from services import velia_agent_coding_autopilot_policy_service as policy_service
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_coding_service as coding_service
from services import velia_developer_fast_path_service as cost_service
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service
from services import velia_developer_project_service as project_service

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_431
_CI_ADVISORY_KEY = 8_618_270_432
_PATCH_INSTALLED = False

_CI_ACTIVE_RUN_STATUSES = ("waiting_ci", "repairing")
_CI_ACTIVE_ATTEMPT_STATUSES = ("waiting", "pending", "repairing")
_FAILURE_CONCLUSIONS = {
    "failure",
    "error",
    "cancelled",
    "timed_out",
    "action_required",
    "startup_failure",
    "stale",
}
_SUCCESS_CONCLUSIONS = {"success", "neutral", "skipped"}
_INFRA_FAILURE_RE = re.compile(
    r"(?:runner|infrastructure|service unavailable|rate limit|network|unable to resolve|"
    r"temporary failure|connection reset|no space left|artifact upload|checkout failed|"
    r"cancelled|canceled|timed out|timeout|billing|permission denied)",
    re.IGNORECASE,
)


class CodingAutopilotCIError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]
        self.status = int(status)


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


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def ci_watch_enabled() -> bool:
    return autopilot.worker_enabled() and _env_bool(
        "VELIA_DEVELOPER_AUTOPILOT_CI_ENABLED", False
    )


def ci_repair_enabled() -> bool:
    return ci_watch_enabled() and _env_bool(
        "VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_ENABLED", False
    )


def _utcnow() -> datetime:
    return datetime.utcnow()


def _dict_cursor(conn):
    factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=factory) if factory else conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _json(value: Any, limit: int = 120000) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def ensure_coding_autopilot_ci_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        autopilot.ensure_coding_autopilot_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                "ALTER TABLE velia_developer_autopilot_runs "
                "DROP CONSTRAINT IF EXISTS velia_developer_autopilot_runs_status_check"
            )
            cursor.execute(
                "ALTER TABLE velia_developer_autopilot_runs "
                "DROP CONSTRAINT IF EXISTS velia_developer_autopilot_runs_status_check_v2"
            )
            cursor.execute(
                """
                ALTER TABLE velia_developer_autopilot_runs
                ADD CONSTRAINT velia_developer_autopilot_runs_status_check_v2
                CHECK (status IN (
                    'claimed','planning','executing','waiting_ci','repairing',
                    'ready_for_review','failed','blocked','cancelled'
                ))
                """
            )
            cursor.execute(
                "ALTER TABLE velia_developer_autopilot_tasks "
                "DROP CONSTRAINT IF EXISTS velia_developer_autopilot_tasks_status_check"
            )
            cursor.execute(
                "ALTER TABLE velia_developer_autopilot_tasks "
                "DROP CONSTRAINT IF EXISTS velia_developer_autopilot_tasks_status_check_v2"
            )
            cursor.execute(
                """
                ALTER TABLE velia_developer_autopilot_tasks
                ADD CONSTRAINT velia_developer_autopilot_tasks_status_check_v2
                CHECK (status IN (
                    'queued','claimed','planning','executing','waiting_ci','repairing',
                    'ready_for_review','failed','blocked','cancelled'
                ))
                """
            )
            cursor.execute("DROP INDEX IF EXISTS ux_velia_autopilot_run_project_active")
            cursor.execute(
                """
                CREATE UNIQUE INDEX ux_velia_autopilot_run_project_active
                ON velia_developer_autopilot_runs(project_id)
                WHERE status IN ('claimed','planning','executing','waiting_ci','repairing')
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_autopilot_ci_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES velia_developer_autopilot_runs(run_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    checks_json TEXT NOT NULL DEFAULT '[]',
                    failure_json TEXT NOT NULL DEFAULT '{}',
                    repair_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NULL,
                    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_checked_at TIMESTAMP NULL,
                    finished_at TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (run_id, attempt_number),
                    CHECK (attempt_number BETWEEN 0 AND 2),
                    CHECK (status IN ('waiting','pending','repairing','success','failure','blocked'))
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_autopilot_ci_active
                ON velia_developer_autopilot_ci_attempts(status,updated_at ASC)
                """
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _attempt_from_row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "attempt_id": str(_value(row, "attempt_id", 0, "")),
        "run_id": str(_value(row, "run_id", 1, "")),
        "user_id": int(_value(row, "user_id", 2, 0) or 0),
        "attempt_number": int(_value(row, "attempt_number", 3, 0) or 0),
        "head_sha": str(_value(row, "head_sha", 4, "")),
        "status": str(_value(row, "status", 5, "")),
        "checks": _loads(_value(row, "checks_json", 6, "[]"), []),
        "failure": _loads(_value(row, "failure_json", 7, "{}"), {}),
        "repair": _loads(_value(row, "repair_json", 8, "{}"), {}),
        "error_code": str(_value(row, "error_code", 9, "") or "") or None,
        "first_seen_at": _iso(_value(row, "first_seen_at", 10)),
        "last_checked_at": _iso(_value(row, "last_checked_at", 11)),
        "finished_at": _iso(_value(row, "finished_at", 12)),
        "created_at": _iso(_value(row, "created_at", 13)),
        "updated_at": _iso(_value(row, "updated_at", 14)),
    }


_ATTEMPT_COLUMNS = (
    "attempt_id,run_id,user_id,attempt_number,head_sha,status,checks_json,"
    "failure_json,repair_json,error_code,first_seen_at,last_checked_at,finished_at,"
    "created_at,updated_at"
)


def list_ci_attempts(user_id: int, run_id: str) -> List[Dict[str, Any]]:
    ensure_coding_autopilot_ci_tables()
    autopilot.get_run(int(user_id), str(run_id))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_ATTEMPT_COLUMNS} FROM velia_developer_autopilot_ci_attempts "
            "WHERE run_id=%s AND user_id=%s ORDER BY attempt_number ASC",
            (str(run_id), int(user_id)),
        )
        return [_attempt_from_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def _current_attempt(run_id: str) -> Optional[Dict[str, Any]]:
    ensure_coding_autopilot_ci_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_ATTEMPT_COLUMNS} FROM velia_developer_autopilot_ci_attempts "
            "WHERE run_id=%s ORDER BY attempt_number DESC LIMIT 1",
            (str(run_id),),
        )
        row = cursor.fetchone()
        return _attempt_from_row(row) if row else None
    finally:
        cursor.close()
        conn.close()


def _create_attempt(run: Mapping[str, Any], head_sha: str, attempt_number: int) -> Dict[str, Any]:
    maximum = _env_int("VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS", 2, 0, 2)
    if int(attempt_number) < 0 or int(attempt_number) > maximum:
        raise CodingAutopilotCIError("velia_coding_autopilot_ci_repairs_exhausted", status=409)
    now = _utcnow()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"""
            INSERT INTO velia_developer_autopilot_ci_attempts (
                attempt_id,run_id,user_id,attempt_number,head_sha,status,
                checks_json,failure_json,repair_json,error_code,first_seen_at,
                created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,'waiting','[]','{{}}','{{}}',NULL,%s,%s,%s)
            ON CONFLICT (run_id,attempt_number) DO UPDATE SET
                head_sha=EXCLUDED.head_sha,
                status='waiting',
                checks_json='[]',
                failure_json='{{}}',
                repair_json='{{}}',
                error_code=NULL,
                first_seen_at=EXCLUDED.first_seen_at,
                last_checked_at=NULL,
                finished_at=NULL,
                updated_at=EXCLUDED.updated_at
            RETURNING {_ATTEMPT_COLUMNS}
            """,
            (
                str(uuid.uuid4()),
                str(run.get("run_id") or ""),
                int(run.get("user_id") or 0),
                int(attempt_number),
                str(head_sha),
                now,
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _attempt_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _set_attempt(
    attempt: Mapping[str, Any],
    status: str,
    *,
    checks: Any = None,
    failure: Any = None,
    repair: Any = None,
    error_code: str = "",
    finished: bool = False,
) -> None:
    if status not in {"waiting", "pending", "repairing", "success", "failure", "blocked"}:
        raise CodingAutopilotCIError("velia_coding_autopilot_ci_state_invalid")
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_ci_attempts
            SET status=%s,
                checks_json=CASE WHEN %s IS NOT NULL THEN %s ELSE checks_json END,
                failure_json=CASE WHEN %s IS NOT NULL THEN %s ELSE failure_json END,
                repair_json=CASE WHEN %s IS NOT NULL THEN %s ELSE repair_json END,
                error_code=%s,last_checked_at=%s,
                finished_at=CASE WHEN %s THEN %s ELSE finished_at END,
                updated_at=%s
            WHERE attempt_id=%s
            """,
            (
                status,
                _json(checks) if checks is not None else None,
                _json(checks) if checks is not None else None,
                _json(failure) if failure is not None else None,
                _json(failure) if failure is not None else None,
                _json(repair) if repair is not None else None,
                _json(repair) if repair is not None else None,
                str(error_code or "")[:120] or None,
                now,
                bool(finished),
                now,
                now,
                str(attempt.get("attempt_id") or ""),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _run_result(run: Mapping[str, Any]) -> Dict[str, Any]:
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    return dict(result)


def _set_run_state(
    run: Mapping[str, Any],
    status: str,
    *,
    result: Optional[Dict[str, Any]] = None,
    error_code: str = "",
    finished: bool = False,
) -> None:
    if status not in {
        "waiting_ci",
        "repairing",
        "ready_for_review",
        "blocked",
        "failed",
    }:
        raise CodingAutopilotCIError("velia_coding_autopilot_ci_run_state_invalid")
    now = _utcnow()
    lease_seconds = _env_int("VELIA_DEVELOPER_AUTOPILOT_LEASE_SECONDS", 3600, 300, 7200)
    payload = result if isinstance(result, dict) else _run_result(run)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_runs
            SET status=%s,result_json=%s,error_code=%s,
                claimed_until=%s,finished_at=CASE WHEN %s THEN %s ELSE NULL END,
                updated_at=%s
            WHERE run_id=%s
            """,
            (
                status,
                _json(payload),
                str(error_code or "")[:120] or None,
                now + timedelta(seconds=lease_seconds),
                bool(finished),
                now,
                now,
                str(run.get("run_id") or ""),
            ),
        )
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_tasks
            SET status=%s,result_json=%s,error_code=%s,updated_at=%s
            WHERE task_id=%s
            """,
            (
                status,
                _json(payload),
                str(error_code or "")[:120] or None,
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


def _project_and_mission(run: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    user_id = int(run.get("user_id") or 0)
    mission = autopilot.get_mission(user_id, str(run.get("mission_id") or ""))
    project = dict(project_service.get_project(user_id, str(run.get("project_id") or "")))
    project["selected_branch"] = str(mission.get("base_branch") or project.get("selected_branch") or "")
    return project, mission


def _checks_state(checks: Iterable[Mapping[str, Any]]) -> str:
    items = [item for item in checks if isinstance(item, Mapping)]
    if not items:
        return "missing"
    pending = False
    for item in items:
        status = str(item.get("status") or "").strip().lower()
        conclusion = str(item.get("conclusion") or "").strip().lower()
        if conclusion in _FAILURE_CONCLUSIONS:
            return "failure"
        if status not in {"completed"} or not conclusion:
            pending = True
        elif conclusion not in _SUCCESS_CONCLUSIONS:
            return "failure"
    return "pending" if pending else "success"


def _bounded_text(value: Any, limit: int) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(value or ""))
    text = text.replace("\x00", "")
    return text[:limit]


def _failure_details(project: Dict[str, Any], sha: str, checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    installation_id, repository_id, full_name, _ = write_service._project_values(project)
    owner, name = github_service._validate_full_name(full_name)
    token = github_service._installation_token(installation_id, [repository_id])
    commit_sha = quote(str(sha or ""), safe="")
    raw_runs = github_service._request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/commits/{commit_sha}/check-runs",
        token=token,
        params={"per_page": 100},
    )
    raw_status = github_service._request(
        "GET",
        f"/repos/{quote(owner)}/{quote(name)}/commits/{commit_sha}/status",
        token=token,
        params={"per_page": 100},
    )
    failures: List[Dict[str, Any]] = []
    for item in (raw_runs.get("check_runs") if isinstance(raw_runs, dict) else []) or []:
        if not isinstance(item, dict):
            continue
        conclusion = str(item.get("conclusion") or "").lower()
        if conclusion not in _FAILURE_CONCLUSIONS:
            continue
        output = item.get("output") if isinstance(item.get("output"), dict) else {}
        annotations: List[Dict[str, Any]] = []
        check_id = int(item.get("id") or 0)
        if check_id > 0:
            try:
                raw_annotations = github_service._request(
                    "GET",
                    f"/repos/{quote(owner)}/{quote(name)}/check-runs/{check_id}/annotations",
                    token=token,
                    params={"per_page": 20},
                )
                if isinstance(raw_annotations, list):
                    for annotation in raw_annotations[:20]:
                        if not isinstance(annotation, dict):
                            continue
                        annotations.append(
                            {
                                "path": _bounded_text(annotation.get("path"), 320),
                                "start_line": int(annotation.get("start_line") or 0),
                                "end_line": int(annotation.get("end_line") or 0),
                                "level": _bounded_text(annotation.get("annotation_level"), 40),
                                "title": _bounded_text(annotation.get("title"), 300),
                                "message": _bounded_text(annotation.get("message"), 1200),
                                "raw_details": _bounded_text(annotation.get("raw_details"), 1200),
                            }
                        )
            except Exception:
                logger.exception("VELIA_AUTOPILOT_CI_ANNOTATIONS_FAILED check_id=%s", check_id)
        failures.append(
            {
                "source": "check_run",
                "name": _bounded_text(item.get("name"), 240),
                "conclusion": conclusion,
                "url": _bounded_text(item.get("html_url"), 500),
                "title": _bounded_text(output.get("title"), 500),
                "summary": _bounded_text(output.get("summary"), 3000),
                "text": _bounded_text(output.get("text"), 4000),
                "annotations": annotations,
            }
        )
    for item in (raw_status.get("statuses") if isinstance(raw_status, dict) else []) or []:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "").lower()
        if state not in {"failure", "error"}:
            continue
        failures.append(
            {
                "source": "commit_status",
                "name": _bounded_text(item.get("context"), 240),
                "conclusion": state,
                "url": _bounded_text(item.get("target_url"), 500),
                "description": _bounded_text(item.get("description"), 1000),
                "annotations": [],
            }
        )
    rendered = _json(failures, 20000)
    infrastructure = bool(_INFRA_FAILURE_RE.search(rendered))
    repairable = bool(
        failures
        and any(item.get("source") == "check_run" for item in failures)
        and any(
            item.get("annotations") or item.get("summary") or item.get("text")
            for item in failures
            if item.get("source") == "check_run"
        )
        and not infrastructure
    )
    return {
        "head_sha": str(sha),
        "checks": checks[:30],
        "failures": failures[:20],
        "repairable": repairable,
        "infrastructure": infrastructure,
    }


def _coding_job(run: Mapping[str, Any]) -> Dict[str, Any]:
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT job_id,user_id,conversation_id,project_id,goal,status,plan_json,
                   step_results_json,current_step,total_steps,base_branch,work_branch,
                   pull_request_number,pull_request_url,estimated_cost_usd,error_code
            FROM velia_developer_coding_jobs
            WHERE job_id=%s AND user_id=%s
            """,
            (str(run.get("coding_job_id") or ""), int(run.get("user_id") or 0)),
        )
        row = cursor.fetchone()
        if not row:
            raise CodingAutopilotCIError("velia_coding_autopilot_coding_job_missing", status=404)
        return coding_service._serialize_job(row)
    finally:
        cursor.close()
        conn.close()


def _allowed_repair_files(job: Mapping[str, Any], mission: Mapping[str, Any]) -> List[str]:
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    policy_service.validate_plan(plan, {
        "allowed_paths": list(mission.get("allowed_paths") or []),
        "blocked_paths": list(mission.get("blocked_paths") or []),
        "max_steps": int(mission.get("max_steps") or 0),
        "max_files": int(mission.get("max_files") or 0),
        "draft_pr_only": True,
    })
    files: List[str] = []
    seen = set()
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for raw in step.get("files") or []:
            path = github_service.validate_path(str(raw or ""))
            if path in seen:
                continue
            seen.add(path)
            files.append(path)
    maximum = min(12, max(1, int(mission.get("max_files") or 1)))
    return files[:maximum]


def _repair_prompt(
    *,
    project: Mapping[str, Any],
    run: Mapping[str, Any],
    job: Mapping[str, Any],
    failure: Mapping[str, Any],
    files: List[str],
    context: str,
    attempt_number: int,
) -> str:
    return f"""You are the bounded CI repair stage of VELIA Coding Autopilot.
Repository: {project.get('repository_full_name')}
Base branch: {job.get('base_branch')}
Existing work branch: {run.get('work_branch')}
Original goal: {job.get('goal')}
Repair attempt: {attempt_number}/2
Allowed files: {json.dumps(files, ensure_ascii=False)}

Exact CI failure evidence:
{_json(failure, 16000)}

Current source excerpts from the existing work branch:
{context[:26000]}

Return ONLY one compact JSON object:
{{
  "summary": "specific root cause and repair",
  "operations": [
    {{"op":"replace","path":"allowed/path.py","old":"exact unique current snippet","new":"replacement"}},
    {{"op":"create","path":"allowed/new_file.py","content":"complete file"}},
    {{"op":"delete","path":"allowed/obsolete.py"}}
  ],
  "checks": ["checks expected to pass after this repair"]
}}
Rules:
- Modify only the listed allowed files from the original approved plan.
- Fix only the supplied CI failure. No refactor or unrelated cleanup.
- Do not change workflows, secrets, credentials, auth policy, billing, migrations, infrastructure or deployment configuration.
- Do not create a new branch or PR. Do not merge or deploy.
- Use exact unique replacements for existing files.
- If the evidence is insufficient, return {{"summary":"insufficient evidence","operations":[],"checks":[]}}.
- No markdown outside JSON.
"""


def _execute_repair(
    run: Mapping[str, Any],
    attempt: Mapping[str, Any],
    failure: Mapping[str, Any],
) -> Dict[str, Any]:
    project, mission = _project_and_mission(run)
    job = _coding_job(run)
    files = _allowed_repair_files(job, mission)
    if not files:
        raise CodingAutopilotCIError("velia_coding_autopilot_repair_files_empty", status=409)
    branch = str(run.get("work_branch") or "")
    before = write_service.branch_head(project, branch)
    if str(before.get("sha") or "") != str(attempt.get("head_sha") or ""):
        raise CodingAutopilotCIError("velia_coding_autopilot_branch_head_changed", status=409)
    synthetic_step = {
        "files": files,
        "objective": "Repair the exact failed CI checks without unrelated changes.",
        "checks": [str(item.get("name") or "") for item in failure.get("failures") or []],
    }
    context, states = coding_service._step_context(
        project,
        branch,
        synthetic_step,
        f"{job.get('goal')}\nCI repair: {_json(failure, 12000)}",
    )
    prompt = _repair_prompt(
        project=project,
        run=run,
        job=job,
        failure=failure,
        files=files,
        context=context,
        attempt_number=int(attempt.get("attempt_number") or 0) + 1,
    )
    max_tokens = _env_int("VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_OUTPUT_TOKENS", 2200, 800, 2800)
    budget = _env_float("VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_MAX_COST_USD", 0.06, 0.01, 0.12)
    if cost_service._estimate_cost(prompt, max_tokens) > budget:
        raise CodingAutopilotCIError("velia_coding_autopilot_ci_repair_cost_limit", status=402)
    total_cost = 0.0
    raw_response = ""
    operations: List[Dict[str, Any]] = []
    payload: Dict[str, Any] = {}
    current_prompt = prompt
    for model_attempt in range(2):
        result = coding_service._model_call(
            prompt=current_prompt,
            feature="velia_developer_autopilot_ci_repair",
            request_id=(
                f"autopilot-ci:{run.get('run_id')}:"
                f"{attempt.get('attempt_number')}:{model_attempt + 1}"
            ),
            user_id=int(run.get("user_id") or 0),
            max_tokens=max_tokens if model_attempt == 0 else 1200,
            timeout=_env_int("VELIA_DEVELOPER_CODING_MODEL_TIMEOUT_SECONDS", 100, 20, 120),
        )
        total_cost += float(result.get("estimated_cost_usd") or 0.0)
        if total_cost > budget:
            raise CodingAutopilotCIError("velia_coding_autopilot_ci_repair_cost_limit", status=402)
        raw_response = str(result.get("text") or "")
        try:
            payload = coding_service._extract_json(raw_response)
            raw_operations = payload.get("operations") if isinstance(payload, dict) else []
            if not isinstance(raw_operations, list) or not raw_operations:
                raise CodingAutopilotCIError("velia_coding_autopilot_ci_evidence_insufficient", status=409)
            operations, _ = coding_service._apply_patch_payload(
                payload,
                allowed_files=files,
                states=states,
            )
            break
        except (coding_service.DeveloperCodingError, CodingAutopilotCIError) as exc:
            if model_attempt >= 1:
                raise
            current_prompt = coding_service._repair_prompt(
                prompt,
                raw_response,
                coding_service.DeveloperCodingError(
                    str(getattr(exc, "code", "velia_coding_autopilot_ci_patch_invalid")),
                    status=int(getattr(exc, "status", 409)),
                    detail=str(getattr(exc, "detail", "")),
                ),
            )
    current = write_service.branch_head(project, branch)
    if str(current.get("sha") or "") != str(before.get("sha") or ""):
        raise CodingAutopilotCIError("velia_coding_autopilot_branch_head_changed", status=409)
    committed = write_service.commit_operations(
        project,
        branch=branch,
        operations=operations,
        message=(
            f"VELIA CI repair {int(attempt.get('attempt_number') or 0) + 1}: "
            f"{str((failure.get('failures') or [{}])[0].get('name') or 'failed checks')[:120]}"
        ),
    )
    return {
        "summary": str(payload.get("summary") or "Repair failed CI checks.")[:2000],
        "checks": [str(item)[:300] for item in (payload.get("checks") or [])][:12],
        "files": list(committed.get("files") or []),
        "commit_sha": str(committed.get("commit_sha") or ""),
        "estimated_cost_usd": total_cost,
    }


def _active_ci_exists() -> bool:
    ensure_coding_autopilot_ci_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT 1 FROM velia_developer_autopilot_runs "
            "WHERE status IN ('waiting_ci','repairing') LIMIT 1"
        )
        return bool(cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def _claim_ci_run() -> Optional[Dict[str, Any]]:
    ensure_coding_autopilot_ci_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (_CI_ADVISORY_KEY,))
        if not bool(_value(cursor.fetchone(), "pg_try_advisory_lock", 0, False)):
            return None
        cursor.execute(
            f"SELECT {autopilot._RUN_COLUMNS} FROM velia_developer_autopilot_runs "
            "WHERE status IN ('waiting_ci','repairing') "
            "ORDER BY updated_at ASC FOR UPDATE SKIP LOCKED LIMIT 1"
        )
        row = cursor.fetchone()
        conn.commit()
        return autopilot._run_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (_CI_ADVISORY_KEY,))
            conn.commit()
        except Exception:
            conn.rollback()
        cursor.close()
        conn.close()


def _append_ci_result(run: Mapping[str, Any], **values: Any) -> Dict[str, Any]:
    result = _run_result(run)
    ci = result.get("ci") if isinstance(result.get("ci"), dict) else {}
    ci = {**ci, **values}
    result["ci"] = ci
    return result


def process_ci_once() -> Optional[Dict[str, Any]]:
    if not ci_watch_enabled():
        return None
    run = _claim_ci_run()
    if not run:
        return None
    attempt = _current_attempt(str(run.get("run_id") or ""))
    if not attempt:
        _set_run_state(
            run,
            "blocked",
            error_code="velia_coding_autopilot_ci_attempt_missing",
            finished=True,
        )
        return {**run, "status": "blocked", "error_code": "velia_coding_autopilot_ci_attempt_missing"}
    project, _mission = _project_and_mission(run)
    current_head = write_service.branch_head(project, str(run.get("work_branch") or ""))
    if str(current_head.get("sha") or "") != str(attempt.get("head_sha") or ""):
        code = "velia_coding_autopilot_branch_head_changed"
        _set_attempt(attempt, "blocked", error_code=code, finished=True)
        _set_run_state(run, "blocked", error_code=code, finished=True)
        return {**run, "status": "blocked", "error_code": code}
    checks_payload = write_service.commit_status(project, str(attempt.get("head_sha") or ""))
    checks = checks_payload.get("checks") if isinstance(checks_payload, dict) else []
    if not isinstance(checks, list):
        checks = []
    state = _checks_state(checks)
    first_seen_raw = str(attempt.get("first_seen_at") or "")
    try:
        first_seen = datetime.fromisoformat(first_seen_raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        first_seen = _utcnow()
    age = max(0.0, (_utcnow() - first_seen).total_seconds())
    max_wait = _env_int("VELIA_DEVELOPER_AUTOPILOT_CI_MAX_WAIT_MINUTES", 45, 5, 180) * 60
    grace = _env_int("VELIA_DEVELOPER_AUTOPILOT_CI_GRACE_SECONDS", 90, 30, 900)
    if state in {"missing", "pending"}:
        if age > max_wait:
            code = "velia_coding_autopilot_ci_timeout"
            _set_attempt(attempt, "blocked", checks=checks, error_code=code, finished=True)
            result = _append_ci_result(run, status="blocked", checks=checks, error_code=code)
            _set_run_state(run, "blocked", result=result, error_code=code, finished=True)
            return {**run, "status": "blocked", "result": result, "error_code": code}
        if state == "missing" and age > grace:
            code = "velia_coding_autopilot_ci_checks_missing"
            _set_attempt(attempt, "blocked", checks=checks, error_code=code, finished=True)
            result = _append_ci_result(run, status="blocked", checks=[], error_code=code)
            _set_run_state(run, "blocked", result=result, error_code=code, finished=True)
            return {**run, "status": "blocked", "result": result, "error_code": code}
        _set_attempt(attempt, "pending", checks=checks)
        result = _append_ci_result(
            run,
            status="pending",
            head_sha=str(attempt.get("head_sha") or ""),
            attempt_number=int(attempt.get("attempt_number") or 0),
            checks=checks,
        )
        _set_run_state(run, "waiting_ci", result=result)
        return {**run, "status": "waiting_ci", "result": result}
    if state == "success":
        _set_attempt(attempt, "success", checks=checks, finished=True)
        result = _append_ci_result(
            run,
            status="success",
            head_sha=str(attempt.get("head_sha") or ""),
            attempt_number=int(attempt.get("attempt_number") or 0),
            checks=checks,
            error_code=None,
        )
        _set_run_state(run, "ready_for_review", result=result, finished=True)
        autopilot._record_event(run, "ci_success", {"head_sha": attempt.get("head_sha"), "checks": checks})
        return {**run, "status": "ready_for_review", "result": result}

    failure = _failure_details(project, str(attempt.get("head_sha") or ""), checks)
    _set_attempt(attempt, "failure", checks=checks, failure=failure, finished=True)
    repair_count = int(attempt.get("attempt_number") or 0)
    max_repairs = _env_int("VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS", 2, 0, 2)
    if not ci_repair_enabled():
        code = "velia_coding_autopilot_ci_repair_disabled"
    elif failure.get("infrastructure"):
        code = "velia_coding_autopilot_ci_infrastructure_failure"
    elif not failure.get("repairable"):
        code = "velia_coding_autopilot_ci_evidence_insufficient"
    elif repair_count >= max_repairs:
        code = "velia_coding_autopilot_ci_repairs_exhausted"
    else:
        code = ""
    if code:
        result = _append_ci_result(
            run,
            status="blocked",
            head_sha=str(attempt.get("head_sha") or ""),
            attempt_number=repair_count,
            checks=checks,
            failure=failure,
            error_code=code,
        )
        _set_run_state(run, "blocked", result=result, error_code=code, finished=True)
        autopilot._record_event(run, "ci_blocked", {"error_code": code, "failure": failure})
        return {**run, "status": "blocked", "result": result, "error_code": code}

    _set_run_state(run, "repairing", result=_append_ci_result(run, status="repairing", failure=failure))
    _set_attempt(attempt, "repairing", checks=checks, failure=failure)
    try:
        repair = _execute_repair(run, attempt, failure)
        new_head = str(repair.get("commit_sha") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", new_head):
            raise CodingAutopilotCIError("velia_coding_autopilot_ci_repair_commit_missing", status=502)
        _set_attempt(attempt, "failure", checks=checks, failure=failure, repair=repair, finished=True)
        next_attempt = _create_attempt(run, new_head, repair_count + 1)
        result = _run_result(run)
        repairs = result.get("repairs") if isinstance(result.get("repairs"), list) else []
        result["repairs"] = [*repairs, repair][-2:]
        result = _append_ci_result(
            {**run, "result": result},
            status="pending",
            head_sha=new_head,
            attempt_number=int(next_attempt.get("attempt_number") or 0),
            checks=[],
            failure={},
            error_code=None,
        )
        result["estimated_cost_usd"] = float(result.get("estimated_cost_usd") or 0.0) + float(
            repair.get("estimated_cost_usd") or 0.0
        )
        _set_run_state(run, "waiting_ci", result=result)
        autopilot._record_event(run, "ci_repair_committed", repair)
        return {**run, "status": "waiting_ci", "result": result}
    except Exception as exc:
        code = str(getattr(exc, "code", "velia_coding_autopilot_ci_repair_failed"))[:120]
        _set_attempt(attempt, "blocked", checks=checks, failure=failure, error_code=code, finished=True)
        result = _append_ci_result(run, status="blocked", failure=failure, error_code=code)
        _set_run_state(run, "blocked", result=result, error_code=code, finished=True)
        autopilot._record_event(run, "ci_repair_failed", {"error_code": code})
        logger.exception("VELIA_AUTOPILOT_CI_REPAIR_FAILED run_id=%s code=%s", run.get("run_id"), code)
        return {**run, "status": "blocked", "result": result, "error_code": code}


def _register_ci_watch(run: Mapping[str, Any], execution_result: Mapping[str, Any]) -> Dict[str, Any]:
    project, _mission = _project_and_mission(run)
    work_branch = str(execution_result.get("work_branch") or run.get("work_branch") or "")
    head = write_service.branch_head(project, work_branch)
    head_sha = str(head.get("sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise CodingAutopilotCIError("velia_coding_autopilot_ci_head_missing", status=502)
    attempt = _create_attempt(run, head_sha, 0)
    result = execution_result.get("result") if isinstance(execution_result.get("result"), dict) else _run_result(run)
    result = _append_ci_result(
        {**run, "result": result},
        status="pending",
        head_sha=head_sha,
        attempt_number=0,
        checks=[],
        max_repairs=_env_int("VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS", 2, 0, 2),
    )
    _set_run_state(run, "waiting_ci", result=result)
    autopilot._record_event(run, "ci_watch_started", {"head_sha": head_sha})
    return {**dict(execution_result), "status": "waiting_ci", "result": result, "ci_attempt": attempt}


def install_ci_repair_loop() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    ensure_coding_autopilot_ci_tables()
    original_execute_claimed = autopilot._execute_claimed
    original_run_once = autopilot.run_autopilot_once
    original_claim_next = autopilot._claim_next_task

    def execute_claimed_with_ci(run: Mapping[str, Any]) -> Dict[str, Any]:
        result = original_execute_claimed(run)
        if not ci_watch_enabled() or str(result.get("status") or "") != "ready_for_review":
            return result
        try:
            refreshed = autopilot.get_run(int(run.get("user_id") or 0), str(run.get("run_id") or ""))
            return _register_ci_watch(refreshed, result)
        except Exception as exc:
            code = str(getattr(exc, "code", "velia_coding_autopilot_ci_watch_failed"))[:120]
            refreshed = autopilot.get_run(int(run.get("user_id") or 0), str(run.get("run_id") or ""))
            _set_run_state(refreshed, "blocked", error_code=code, finished=True)
            autopilot._record_event(refreshed, "ci_watch_failed", {"error_code": code})
            logger.exception("VELIA_AUTOPILOT_CI_WATCH_INIT_FAILED run_id=%s code=%s", run.get("run_id"), code)
            return {**dict(result), "status": "blocked", "error_code": code}

    def claim_next_with_ci_guard(worker_id: str, *, now: Optional[datetime] = None):
        if ci_watch_enabled() and _active_ci_exists():
            return None
        return original_claim_next(worker_id, now=now)

    def run_once_with_ci() -> List[Dict[str, Any]]:
        if not autopilot.worker_enabled() or not coding_service.coding_enabled():
            return []
        if ci_watch_enabled():
            processed = process_ci_once()
            if processed is not None:
                return [processed]
        return original_run_once()

    autopilot._execute_claimed = execute_claimed_with_ci
    autopilot._claim_next_task = claim_next_with_ci_guard
    autopilot.run_autopilot_once = run_once_with_ci
    autopilot._ACTIVE_RUN_STATUSES = tuple(
        dict.fromkeys((*autopilot._ACTIVE_RUN_STATUSES, *_CI_ACTIVE_RUN_STATUSES))
    )
    autopilot._TASK_ACTIVE_STATUSES = tuple(
        dict.fromkeys((*autopilot._TASK_ACTIVE_STATUSES, *_CI_ACTIVE_RUN_STATUSES))
    )
    _PATCH_INSTALLED = True
