from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping

from db.database import get_connection
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_project_service as project_service
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_rollout_service as rollout
from services.velia_admin_security_service import configured_admin_id, is_admin_user
from services.velia_software_factory_core_service import SoftwareFactoryError, canonical_json

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_711
_DEFAULT_PROMPT = "Хочу интернет-магазин цветов"
_ACCEPTANCE_STATUSES = {"passed", "blocked", "failed"}
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def acceptance_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", False)


def acceptance_repository() -> str:
    return str(os.getenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_REPOSITORY", "") or "").strip()[:240]


def acceptance_prompt() -> str:
    return str(os.getenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_PROMPT", _DEFAULT_PROMPT) or _DEFAULT_PROMPT).strip()[:4000]


def code_ref() -> str:
    value = str(os.getenv("RAILWAY_GIT_COMMIT_SHA", "") or "").strip().lower()
    return value if _SHA40.fullmatch(value) else ""


def _utcnow() -> datetime:
    return datetime.utcnow()


def _json(value: Any, limit: int = 160000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _dict_cursor(conn):
    try:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        return conn.cursor()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": acceptance_enabled(),
        "mode": "startup_dry_run_probe",
        "dry_run_required": True,
        "admin_pilot_required": True,
        "exact_repository_required": True,
        "exact_code_ref_required": True,
        "safe_scope_only": True,
        "autopilot_mission_immutability_required": True,
        "repository_write_supported": False,
        "autopilot_execution_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
    }


def ensure_acceptance_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_dry_run_acceptance_runs (
                    acceptance_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    repository_full_name TEXT NOT NULL,
                    code_ref TEXT NOT NULL,
                    prompt_fingerprint TEXT NOT NULL,
                    probe_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('passed','blocked','failed')),
                    UNIQUE(user_id,probe_fingerprint)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_dry_run_acceptance_time "
                "ON velia_software_factory_dry_run_acceptance_runs(created_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _row(row: Any) -> Dict[str, Any]:
    result = _loads(_value(row, "result_json", 7, "{}"), {})
    if not isinstance(result, dict):
        result = {}
    result.update(
        {
            "acceptance_id": str(_value(row, "acceptance_id", 0, "")),
            "repository_full_name": str(_value(row, "repository_full_name", 2, "")),
            "code_ref": str(_value(row, "code_ref", 3, "")),
            "prompt_fingerprint": str(_value(row, "prompt_fingerprint", 4, "")),
            "probe_fingerprint": str(_value(row, "probe_fingerprint", 5, "")),
            "status": str(_value(row, "status", 6, result.get("status") or "failed")),
            "created_at": str(_value(row, "created_at", 8, "") or ""),
        }
    )
    return result


def _probe_identity(repository: str, prompt: str, commit_sha: str) -> Dict[str, str]:
    prompt_fp = _fingerprint({"prompt": str(prompt)})
    probe_fp = _fingerprint(
        {
            "version": "stage6.1",
            "repository_full_name": str(repository).casefold(),
            "prompt_fingerprint": prompt_fp,
            "code_ref": str(commit_sha),
        }
    )
    return {"prompt_fingerprint": prompt_fp, "probe_fingerprint": probe_fp}


def _existing(user_id: int, probe_fingerprint: str) -> Dict[str, Any] | None:
    ensure_acceptance_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT acceptance_id,user_id,repository_full_name,code_ref,prompt_fingerprint,probe_fingerprint,"
            "status,result_json,created_at FROM velia_software_factory_dry_run_acceptance_runs "
            "WHERE user_id=%s AND probe_fingerprint=%s ORDER BY created_at DESC LIMIT 1",
            (int(user_id), str(probe_fingerprint)),
        )
        raw = cursor.fetchone()
        return _row(raw) if raw else None
    finally:
        cursor.close()
        conn.close()


def _persist(user_id: int, repository: str, commit_sha: str, identity: Mapping[str, str], result: Mapping[str, Any]) -> Dict[str, Any]:
    ensure_acceptance_tables()
    status = str(result.get("status") or "failed")
    if status not in _ACCEPTANCE_STATUSES:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_status_invalid", status=500)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_dry_run_acceptance_runs (
                acceptance_id,user_id,repository_full_name,code_ref,prompt_fingerprint,
                probe_fingerprint,status,result_json,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,probe_fingerprint) DO NOTHING
            RETURNING acceptance_id,user_id,repository_full_name,code_ref,prompt_fingerprint,
                probe_fingerprint,status,result_json,created_at
            """,
            (
                str(uuid.uuid4()),
                int(user_id),
                str(repository),
                str(commit_sha),
                str(identity.get("prompt_fingerprint") or ""),
                str(identity.get("probe_fingerprint") or ""),
                status,
                _json(dict(result)),
                _utcnow(),
            ),
        )
        raw = cursor.fetchone()
        if not raw:
            cursor.execute(
                "SELECT acceptance_id,user_id,repository_full_name,code_ref,prompt_fingerprint,probe_fingerprint,"
                "status,result_json,created_at FROM velia_software_factory_dry_run_acceptance_runs "
                "WHERE user_id=%s AND probe_fingerprint=%s",
                (int(user_id), str(identity.get("probe_fingerprint") or "")),
            )
            raw = cursor.fetchone()
        if not raw:
            raise SoftwareFactoryError("velia_factory_dry_run_acceptance_persist_failed", status=500)
        conn.commit()
        return _row(raw)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _blocked(code: str, *, detail: str = "", **extra: Any) -> Dict[str, Any]:
    result = {
        "status": "blocked",
        "passed": False,
        "blocker_code": str(code)[:160],
        "blocker_detail": str(detail or "")[:1000],
        "dry_run": True,
        "execution_blocked": True,
        "repository_write_performed": False,
        "autopilot_task_dispatched": False,
        "merge_performed": False,
        "deployment_triggered": False,
    }
    result.update(extra)
    return result


def _require_runtime() -> tuple[int, str, str, str]:
    if not acceptance_enabled():
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_disabled", status=503)
    repository = acceptance_repository()
    prompt = acceptance_prompt()
    commit_sha = code_ref()
    if not repository or "/" not in repository:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_repository_required", status=409)
    if not prompt:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_prompt_required", status=409)
    if not commit_sha:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_code_ref_required", status=409)
    admin_id = configured_admin_id()
    if admin_id <= 0 or not is_admin_user(admin_id):
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_admin_required", status=409)
    if not rollout.admin_pilot_enabled():
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_admin_pilot_required", status=409)
    if rollout.rollout_mode() != rollout.ROLLOUT_DRY_RUN:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_mode_required", status=409)
    if not rollout.dry_run_enabled(admin_id):
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_not_eligible", status=403)
    if rollout.live_execution_allowed(admin_id) or rollout.supervisor_allowed():
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_execution_must_be_blocked", status=409)
    readiness = rollout.pilot_readiness(admin_id)
    if not bool((readiness.get("plan") or {}).get("ready")):
        missing = ",".join((readiness.get("plan") or {}).get("missing_flags") or [])
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_plan_not_ready", detail=missing, status=409)
    return admin_id, repository, prompt, commit_sha


def _project(user_id: int, repository: str) -> Dict[str, Any]:
    matches = [
        item
        for item in project_service.list_projects(int(user_id))
        if str(item.get("repository_full_name") or "").strip().casefold() == str(repository).strip().casefold()
    ]
    if len(matches) != 1:
        raise SoftwareFactoryError(
            "velia_factory_dry_run_acceptance_project_not_found" if not matches else "velia_factory_dry_run_acceptance_project_ambiguous",
            detail=str(repository),
            status=409,
        )
    project = dict(matches[0])
    if bool(project.get("archived")):
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_project_archived", status=409)
    return project


def _mission_ids(user_id: int) -> List[str]:
    return sorted(
        str(item.get("mission_id") or "")
        for item in autopilot.list_missions(int(user_id))
        if str(item.get("mission_id") or "")
    )


def _question_reasons(run: Mapping[str, Any]) -> List[str]:
    clarification = run.get("clarification") if isinstance(run.get("clarification"), Mapping) else {}
    return [
        str(item.get("reason") or "")
        for item in clarification.get("questions") or []
        if isinstance(item, Mapping)
    ]


def run_acceptance() -> Dict[str, Any]:
    admin_id, repository, prompt, commit_sha = _require_runtime()
    identity = _probe_identity(repository, prompt, commit_sha)
    previous = _existing(admin_id, identity["probe_fingerprint"])
    if previous:
        result = dict(previous)
        result["reused"] = True
        return result

    project = _project(admin_id, repository)
    before_missions = _mission_ids(admin_id)
    recommended = autonomy.recommend_write_scope(project)
    if not recommended:
        return _persist(
            admin_id,
            repository,
            commit_sha,
            identity,
            _blocked("velia_factory_dry_run_acceptance_safe_scope_missing"),
        )

    request_id = f"dry-run-acceptance:{commit_sha[:12]}"
    spec = autonomy.build_project_spec_from_message(
        prompt,
        project,
        recommended,
        user_id=admin_id,
        request_id=request_id,
    )
    run = factory.create_run(admin_id, spec)
    initial_state = str(run.get("state") or "")
    reasons = _question_reasons(run)
    scope_auto_approved = False

    if initial_state == "clarifying":
        if not reasons or any(reason != "write_scope_required" for reason in reasons):
            return _persist(
                admin_id,
                repository,
                commit_sha,
                identity,
                _blocked(
                    "velia_factory_dry_run_acceptance_unexpected_clarification",
                    detail=",".join(reasons),
                    run_id=str(run.get("run_id") or ""),
                    initial_clarification_reasons=reasons,
                ),
            )
        run = factory.answer_clarifications(
            admin_id,
            str(run.get("run_id") or ""),
            {"allowed_paths": list(recommended)},
        )
        scope_auto_approved = True

    if str(run.get("state") or "") != "ready":
        return _persist(
            admin_id,
            repository,
            commit_sha,
            identity,
            _blocked(
                "velia_factory_dry_run_acceptance_not_ready_after_scope",
                detail=str(run.get("state") or ""),
                run_id=str(run.get("run_id") or ""),
            ),
        )

    planned = factory.advance_run(admin_id, str(run.get("run_id") or ""))
    after_missions = _mission_ids(admin_id)
    missions_unchanged = before_missions == after_missions
    team_plan = planned.get("team_plan") if isinstance(planned.get("team_plan"), Mapping) else {}
    manifest = planned.get("team_manifest") if isinstance(planned.get("team_manifest"), Mapping) else {}
    architecture = planned.get("architecture") if isinstance(planned.get("architecture"), Mapping) else {}
    tasks = [item for item in team_plan.get("tasks") or [] if isinstance(item, Mapping)]
    roles = [str(item) for item in manifest.get("execution_roles") or [] if str(item)]

    passed = bool(
        planned.get("dry_run")
        and planned.get("execution_blocked")
        and str(planned.get("state") or "") == "planning"
        and tasks
        and roles
        and architecture
        and missions_unchanged
    )
    result: Dict[str, Any] = {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "repository_full_name": str(repository),
        "code_ref": commit_sha,
        "prompt_fingerprint": identity["prompt_fingerprint"],
        "run_id": str(planned.get("run_id") or ""),
        "initial_state": initial_state,
        "initial_clarification_reasons": reasons,
        "safe_scope_auto_approved": scope_auto_approved,
        "recommended_scope_count": len(recommended),
        "final_state": str(planned.get("state") or ""),
        "dry_run": bool(planned.get("dry_run")),
        "execution_blocked": bool(planned.get("execution_blocked")),
        "architecture_mode": str(architecture.get("mode") or ""),
        "team_plan_task_count": len(tasks),
        "team_roles": roles,
        "autopilot_missions_unchanged": missions_unchanged,
        "autopilot_mission_count_before": len(before_missions),
        "autopilot_mission_count_after": len(after_missions),
        "repository_write_performed": False,
        "autopilot_task_dispatched": False,
        "merge_performed": False,
        "deployment_triggered": False,
    }
    if not passed:
        failures: List[str] = []
        if not bool(planned.get("dry_run")):
            failures.append("dry_run_false")
        if not bool(planned.get("execution_blocked")):
            failures.append("execution_not_blocked")
        if str(planned.get("state") or "") != "planning":
            failures.append("unexpected_state")
        if not tasks:
            failures.append("team_plan_empty")
        if not roles:
            failures.append("team_roles_empty")
        if not architecture:
            failures.append("architecture_missing")
        if not missions_unchanged:
            failures.append("autopilot_missions_changed")
        result["failure_reasons"] = failures
    return _persist(admin_id, repository, commit_sha, identity, result)
