from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_stage2_runtime_patch as stage2_runtime
from services import velia_software_factory_team_service as team_service
from services.velia_software_factory_core_service import ProjectSpec, SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False
_ACCEPTANCE_ACTOR_SOURCES = {"pilot_allowlist", "repository_owner"}
_DANGEROUS_ACCEPTANCE_FLAGS = (
    "VELIA_SOFTWARE_FACTORY_SUPERVISOR_ENABLED",
    "VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_ENABLED",
    "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED",
    "VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED",
    "VELIA_DEVELOPER_AUTOPILOT_ENABLED",
    "VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED",
    "VELIA_DEVELOPER_WRITE_ENABLED",
    "VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED",
    "VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _decorate_run(run: Mapping[str, Any], user_id: int) -> Dict[str, Any]:
    result = dict(run)
    result["rollout_mode"] = rollout.rollout_mode()
    result["rollout_eligible"] = rollout.user_allowed(int(user_id))
    result["dry_run"] = rollout.dry_run_enabled(int(user_id))
    result["live_execution"] = rollout.live_execution_allowed(int(user_id))
    return result


def _plan_only(factory_module: Any, user_id: int, run_id: str) -> Dict[str, Any]:
    """Build Architect/Designer/Planner artifacts and DAG without calling Coding Autopilot."""
    factory_module.ensure_software_factory_tables()
    run = factory_module.get_run(int(user_id), str(run_id))
    state = str(run.get("state") or "")
    if state in {"completed", "failed", "cancelled", "clarifying", "blocked"}:
        result = _decorate_run(run, int(user_id))
        result["execution_blocked"] = True
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

    result = _decorate_run(factory_module.get_run(int(user_id), str(run_id)), int(user_id))
    result["execution_blocked"] = True
    return result


def _install_chat_copy_patch() -> None:
    try:
        from services import velia_software_factory_chat_runtime_patch as chat_runtime
    except Exception:
        return
    if getattr(chat_runtime, "_velia_factory_rollout_copy_installed", False):
        return

    original_started = chat_runtime._started_text
    original_status = chat_runtime._status_text

    def started_text(message: str, run: Mapping[str, Any]) -> str:
        if bool(run.get("dry_run")):
            roles = run.get("team_manifest") if isinstance(run.get("team_manifest"), Mapping) else {}
            role_text = ", ".join(str(item) for item in roles.get("execution_roles") or []) or "—"
            if chat_runtime._russian(message):
                return (
                    "Dry-run готов: Architect и Planner построили архитектуру и DAG команды, "
                    f"роли: {role_text}. **GitHub не изменён, Coding Autopilot не запускался.** "
                    "После проверки план можно отдельно перевести в live-режим."
                )
            return (
                "Dry-run complete: Architect and Planner produced the architecture and team DAG, "
                f"roles: {role_text}. **GitHub was not modified and Coding Autopilot did not run.** "
                "The plan can be promoted to live execution separately after review."
            )
        return original_started(message, run)

    def status_text(message: str, run: Mapping[str, Any]) -> str:
        text = original_status(message, run)
        if bool(run.get("dry_run")):
            suffix = (
                " Dry-run: GitHub-запись заблокирована."
                if chat_runtime._russian(message)
                else " Dry-run: repository writes are blocked."
            )
            return text + suffix
        return text

    chat_runtime._started_text = started_text
    chat_runtime._status_text = status_text
    chat_runtime._velia_factory_rollout_copy_installed = True


def _acceptance_gate_requested() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ENABLED", False)


def _acceptance_actor_source() -> str:
    raw = str(os.getenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_ACTOR_SOURCE", "pilot_allowlist") or "pilot_allowlist").strip().lower()
    return raw if raw in _ACCEPTANCE_ACTOR_SOURCES else "invalid"


def _assert_acceptance_execution_gates_closed() -> None:
    if rollout.rollout_mode() != rollout.ROLLOUT_DRY_RUN:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_mode_required", status=409)
    open_flags = [name for name in _DANGEROUS_ACCEPTANCE_FLAGS if _env_bool(name, False)]
    if open_flags:
        raise SoftwareFactoryError(
            "velia_factory_dry_run_acceptance_execution_gate_open",
            detail=",".join(open_flags),
            status=409,
        )


def _repository_owner_actor_ids(repository: str) -> list[int]:
    repository = str(repository or "").strip()
    if "/" not in repository:
        return []
    owner = repository.split("/", 1)[0].strip()
    if not owner:
        return []
    project_service.ensure_developer_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT DISTINCT p.user_id
            FROM velia_developer_projects p
            JOIN velia_developer_installations i
              ON i.installation_id=p.installation_id AND i.user_id=p.user_id
            WHERE LOWER(p.repository_full_name)=LOWER(%s)
              AND p.deleted_at IS NULL
              AND p.is_archived=FALSE
              AND i.deleted_at IS NULL
              AND LOWER(i.account_login)=LOWER(%s)
            ORDER BY p.user_id
            LIMIT 3
            """,
            (repository, owner),
        )
        result = []
        for row in cursor.fetchall() or []:
            try:
                value = int(row[0])
            except (TypeError, ValueError, IndexError):
                continue
            if value > 0:
                result.append(value)
        return result
    finally:
        cursor.close()
        conn.close()


def _resolve_acceptance_actor_id(acceptance_module: Any) -> int:
    repository = str(acceptance_module.acceptance_repository() or "").strip()
    source = _acceptance_actor_source()
    if source == "invalid":
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_actor_source_invalid", status=409)

    if source == "repository_owner":
        matches = _repository_owner_actor_ids(repository)
        candidate_count = len(matches)
    else:
        candidates = sorted(int(item) for item in rollout.admin_pilot_user_ids() if int(item) > 0)
        matches = []
        if repository:
            for user_id in candidates:
                projects = project_service.list_projects(int(user_id))
                if any(
                    not bool(project.get("archived"))
                    and str(project.get("repository_full_name") or "").strip().casefold() == repository.casefold()
                    for project in projects
                ):
                    matches.append(int(user_id))
        candidate_count = len(candidates)

    if len(matches) != 1:
        code = (
            "velia_factory_dry_run_acceptance_actor_not_found"
            if not matches
            else "velia_factory_dry_run_acceptance_actor_ambiguous"
        )
        raise SoftwareFactoryError(
            code,
            detail=(
                f"actor_source={source} candidates={candidate_count} "
                f"matches={len(matches)} repository={repository}"
            ),
            status=409,
        )
    return matches[0]


def _run_dry_run_acceptance_gate(acceptance_module: Any = None, *, actor_id: int | None = None) -> Dict[str, Any]:
    """Preview-only fail-closed acceptance gate. Production defaults to a no-op."""
    if not _acceptance_gate_requested():
        return {"enabled": False, "status": "disabled", "passed": False}

    _assert_acceptance_execution_gates_closed()
    if acceptance_module is None:
        from services import velia_software_factory_dry_run_acceptance_service as acceptance_module

    actor_source = _acceptance_actor_source()
    resolved_actor = int(actor_id) if actor_id is not None else _resolve_acceptance_actor_id(acceptance_module)
    original_configured_admin_id = getattr(acceptance_module, "configured_admin_id", None)
    original_is_admin_user = getattr(acceptance_module, "is_admin_user", None)
    original_admin_pilot_user_ids = rollout.admin_pilot_user_ids
    original_admin_pilot_id_source = rollout.admin_pilot_id_source

    acceptance_module.configured_admin_id = lambda: resolved_actor
    acceptance_module.is_admin_user = lambda user_id: int(user_id) == resolved_actor
    if actor_source == "repository_owner":
        # Startup-only eligibility bridge. The normal rollout source remains unchanged
        # before and after this synchronous dry-run probe.
        rollout.admin_pilot_user_ids = lambda: {resolved_actor}
        rollout.admin_pilot_id_source = lambda: "acceptance_repository_owner"
    try:
        result = dict(acceptance_module.run_acceptance())
    except Exception as exc:
        logger.exception(
            "VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT status=failed passed=false actor_source=%s pilot_source=%s error=%s",
            actor_source,
            rollout.admin_pilot_id_source(),
            str(getattr(exc, "code", exc.__class__.__name__))[:160],
        )
        raise
    finally:
        rollout.admin_pilot_user_ids = original_admin_pilot_user_ids
        rollout.admin_pilot_id_source = original_admin_pilot_id_source
        if original_configured_admin_id is not None:
            acceptance_module.configured_admin_id = original_configured_admin_id
        if original_is_admin_user is not None:
            acceptance_module.is_admin_user = original_is_admin_user

    status = str(result.get("status") or "failed")
    passed = bool(result.get("passed")) and status == "passed"
    logger.info(
        "VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_RESULT status=%s passed=%s actor_source=%s repository=%s code_ref=%s run_id=%s dry_run=%s execution_blocked=%s missions_unchanged=%s writes=%s autopilot_task=%s merge=%s deployment=%s reused=%s blocker=%s",
        status[:40],
        str(passed).lower(),
        actor_source,
        str(result.get("repository_full_name") or "")[:240],
        str(result.get("code_ref") or "")[:40],
        str(result.get("run_id") or "")[:80],
        str(bool(result.get("dry_run"))).lower(),
        str(bool(result.get("execution_blocked"))).lower(),
        str(bool(result.get("autopilot_missions_unchanged"))).lower(),
        str(bool(result.get("repository_write_performed"))).lower(),
        str(bool(result.get("autopilot_task_dispatched"))).lower(),
        str(bool(result.get("merge_performed"))).lower(),
        str(bool(result.get("deployment_triggered"))).lower(),
        str(bool(result.get("reused"))).lower(),
        str(result.get("blocker_code") or "")[:160],
    )
    if not passed:
        raise RuntimeError("velia_factory_dry_run_acceptance_failed")
    return result


def install(factory_module: Any, autonomy_module: Any) -> bool:
    global _INSTALLED
    if _INSTALLED or getattr(factory_module, "_velia_factory_rollout_runtime_installed", False):
        _install_chat_copy_patch()
        return True

    # Stage 2 must already own Factory create/get/clarify/advance before rollout wraps it.
    if not hasattr(factory_module, "team_runtime_enabled"):
        return False

    original_get = factory_module.get_run
    original_create = factory_module.create_run
    original_clarify = factory_module.answer_clarifications
    original_advance = factory_module.advance_run
    original_supervisor_once = autonomy_module.run_supervisor_once

    def _require_user(user_id: int) -> None:
        if not rollout.intake_allowed(int(user_id)):
            raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)

    def get_run(user_id: int, run_id: str) -> Dict[str, Any]:
        return _decorate_run(original_get(int(user_id), str(run_id)), int(user_id))

    def create_run(user_id: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
        _require_user(int(user_id))
        return _decorate_run(original_create(int(user_id), payload), int(user_id))

    def answer_clarifications(user_id: int, run_id: str, answers: Mapping[str, Any]) -> Dict[str, Any]:
        _require_user(int(user_id))
        return _decorate_run(original_clarify(int(user_id), str(run_id), answers), int(user_id))

    def advance_run(user_id: int, run_id: str) -> Dict[str, Any]:
        _require_user(int(user_id))
        if rollout.dry_run_enabled(int(user_id)):
            return _plan_only(factory_module, int(user_id), str(run_id))
        if not rollout.live_execution_allowed(int(user_id)):
            raise SoftwareFactoryError("velia_factory_execution_not_allowed", status=403)
        return _decorate_run(original_advance(int(user_id), str(run_id)), int(user_id))

    def run_supervisor_once():
        # Defense in depth: even if the legacy supervisor env flag is accidentally
        # true, rollout dry-run/off can never advance repository execution.
        if not rollout.supervisor_allowed():
            return []
        return original_supervisor_once()

    factory_module.get_run = get_run
    factory_module.create_run = create_run
    factory_module.answer_clarifications = answer_clarifications
    factory_module.advance_run = advance_run
    factory_module.factory_rollout_status = rollout.public_status
    factory_module.factory_rollout_mode = rollout.rollout_mode
    autonomy_module.run_supervisor_once = run_supervisor_once
    autonomy_module.rollout_supervisor_allowed = rollout.supervisor_allowed
    factory_module._velia_factory_rollout_runtime_installed = True
    _INSTALLED = True
    _install_chat_copy_patch()
    logger.info(
        "VELIA_SOFTWARE_FACTORY_ROLLOUT_RUNTIME_INSTALLED mode=%s admin_pilot=%s pilot_source=%s actor_count=%s acceptance_actor_source=%s",
        rollout.rollout_mode(),
        str(bool(rollout.admin_pilot_enabled())).lower(),
        rollout.admin_pilot_id_source(),
        len(rollout.admin_pilot_user_ids()) if rollout.admin_pilot_enabled() else 0,
        _acceptance_actor_source(),
    )
    _run_dry_run_acceptance_gate()
    return True