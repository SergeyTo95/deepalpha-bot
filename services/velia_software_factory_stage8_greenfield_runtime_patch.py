from __future__ import annotations

import logging
import re
from typing import Any, Dict, Mapping

from db.database import get_connection
from services import velia_developer_chat_runtime_patch as developer_chat
from services import velia_software_factory_greenfield_repository_creation_service as repository_creation
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False
_TRIGGER_REASONS = {
    "software_factory_greenfield_manifest_ready",
    "software_factory_greenfield_repositories_missing",
    "software_factory_greenfield_ready_to_attach",
}
_CONDITIONAL_NOUN_DEFERRED_APPROVAL = re.compile(
    r"\b(?:if|provided(?:\s+that)?|subject\s+to)\b[^.!?\n]{0,80}"
    r"\b(?:approval|confirmation|authorization|permission)\b[^.!?\n]{0,40}"
    r"\b(?:is|was|has\s+been|had\s+been|will\s+be)\s+"
    r"(?:given|granted|provided|issued|approved|confirmed|authorized|permitted)\b"
    r"[^.!?\n]{0,30}\bby\s+(?:me|us)\b",
    re.IGNORECASE | re.UNICODE,
)
_STOP_BLOCKER = "velia_factory_stage8_release_stop_requested"


class _RetryDeferred(RuntimeError):
    def __init__(self, state: Mapping[str, Any]):
        super().__init__("stage8_retry_deferred")
        self.state = dict(state)


def _conditional_noun_deferred_approval(objective: str) -> bool:
    return bool(_CONDITIONAL_NOUN_DEFERRED_APPROVAL.search(str(objective or "").strip()))


def _ensure_durable_stop_schema(execution_module: Any) -> None:
    from services import velia_software_factory_stage8_release_runtime_patch as release_runtime

    release_runtime.ensure_stage8_release_tables(execution_module)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "ALTER TABLE velia_software_factory_stage8_releases "
            "ADD COLUMN IF NOT EXISTS stop_requested BOOLEAN NOT NULL DEFAULT FALSE"
        )
        cursor.execute(
            "ALTER TABLE velia_software_factory_stage8_releases "
            "ADD COLUMN IF NOT EXISTS retired_release_execution_id TEXT NOT NULL DEFAULT ''"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_factory_stage8_retired_release "
            "ON velia_software_factory_stage8_releases(user_id,retired_release_execution_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _coordinator_stop_state(
    execution_module: Any,
    user_id: int,
    *,
    workspace_execution_id: str = "",
    release_execution_id: str = "",
) -> Dict[str, Any]:
    _ensure_durable_stop_schema(execution_module)
    workspace_id = str(workspace_execution_id or "").strip()
    release_id = str(release_execution_id or "").strip()
    if not workspace_id and not release_id:
        return {}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if workspace_id:
            cursor.execute(
                "SELECT workspace_execution_id,release_execution_id,retired_release_execution_id,"
                "stop_requested,status FROM velia_software_factory_stage8_releases "
                "WHERE user_id=%s AND workspace_execution_id=%s",
                (int(user_id), workspace_id),
            )
        else:
            cursor.execute(
                "SELECT workspace_execution_id,release_execution_id,retired_release_execution_id,"
                "stop_requested,status FROM velia_software_factory_stage8_releases "
                "WHERE user_id=%s AND (release_execution_id=%s OR retired_release_execution_id=%s) "
                "ORDER BY updated_at DESC LIMIT 1",
                (int(user_id), release_id, release_id),
            )
        row = cursor.fetchone()
        if not row:
            return {}
        if isinstance(row, Mapping):
            return {
                "workspace_execution_id": str(row.get("workspace_execution_id") or ""),
                "release_execution_id": str(row.get("release_execution_id") or ""),
                "retired_release_execution_id": str(row.get("retired_release_execution_id") or ""),
                "stop_requested": bool(row.get("stop_requested")),
                "status": str(row.get("status") or ""),
            }
        return {
            "workspace_execution_id": str(row[0] or ""),
            "release_execution_id": str(row[1] or ""),
            "retired_release_execution_id": str(row[2] or ""),
            "stop_requested": bool(row[3]),
            "status": str(row[4] or ""),
        }
    finally:
        cursor.close()
        conn.close()


def _mark_coordinator_stop(
    execution_module: Any,
    user_id: int,
    release_execution_id: str,
) -> Dict[str, Any]:
    """Persist workspace stop intent before waiting on the release advisory lock.

    PostgreSQL re-checks UPDATE predicates after a conflicting row update commits,
    so a stop racing a rotation still matches the newly persisted retired release id.
    """
    _ensure_durable_stop_schema(execution_module)
    release_id = str(release_execution_id or "").strip()
    if not release_id:
        return {}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_stage8_releases SET "
            "stop_requested=TRUE,updated_at=NOW() "
            "WHERE user_id=%s AND (release_execution_id=%s OR retired_release_execution_id=%s) "
            "RETURNING workspace_execution_id,release_execution_id,retired_release_execution_id,status",
            (int(user_id), release_id, release_id),
        )
        row = cursor.fetchone()
        conn.commit()
        if not row:
            return {}
        if isinstance(row, Mapping):
            return {
                "workspace_execution_id": str(row.get("workspace_execution_id") or ""),
                "release_execution_id": str(row.get("release_execution_id") or ""),
                "retired_release_execution_id": str(row.get("retired_release_execution_id") or ""),
                "stop_requested": True,
                "status": str(row.get("status") or ""),
            }
        return {
            "workspace_execution_id": str(row[0] or ""),
            "release_execution_id": str(row[1] or ""),
            "retired_release_execution_id": str(row[2] or ""),
            "stop_requested": True,
            "status": str(row[3] or ""),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _terminalize_coordinator_stop(
    execution_module: Any,
    user_id: int,
    workspace_execution_id: str,
) -> Dict[str, Any]:
    from services import velia_software_factory_stage8_release_runtime_patch as release_runtime

    workspace_id = str(workspace_execution_id or "").strip()
    if not workspace_id:
        return {"status": "terminal_blocked", "blocker_code": _STOP_BLOCKER}
    return release_runtime._save_state(
        execution_module,
        int(user_id),
        workspace_id,
        status="terminal_blocked",
        blocker_code=_STOP_BLOCKER,
        blocker_detail="Explicit stop intent is durably attached to this Stage 8 workspace release.",
    )


def _atomic_zero_merge_rotate(
    execution_module: Any,
    user_id: int,
    state: Mapping[str, Any],
    release: Mapping[str, Any],
) -> None:
    """Retire a zero-merge attempt while preserving late stop intent durably."""
    from services import velia_software_factory_stage8_final_hardening_patch as final_hardening

    _ensure_durable_stop_schema(execution_module)
    release_id = str(
        release.get("execution_id") or state.get("release_execution_id") or ""
    ).strip()
    if not release_id:
        raise SoftwareFactoryError(
            "velia_factory_stage8_zero_merge_release_recheck_unavailable", status=503
        )

    with final_hardening._release_operation_lock(release_id) as (conn, cursor):
        current = execution_module.get_release_execution(int(user_id), release_id)
        if not isinstance(current, Mapping) or not final_hardening._zero_merge_terminal_release(current):
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_release_no_longer_retryable", status=409
            )

        cursor.execute(
            "SELECT workspace_execution_id,plan_id,release_execution_id,stop_requested "
            "FROM velia_software_factory_stage8_releases "
            "WHERE user_id=%s AND release_execution_id=%s FOR UPDATE",
            (int(user_id), release_id),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_coordinator_binding_missing", status=409
            )
        if isinstance(row, Mapping):
            workspace_execution_id = str(row.get("workspace_execution_id") or "")
            persisted_plan_id = str(row.get("plan_id") or "")
            persisted_release_id = str(row.get("release_execution_id") or "")
            stop_requested = bool(row.get("stop_requested"))
        else:
            workspace_execution_id = str(row[0] or "")
            persisted_plan_id = str(row[1] or "")
            persisted_release_id = str(row[2] or "")
            stop_requested = bool(row[3])
        if not workspace_execution_id or persisted_release_id != release_id:
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_coordinator_binding_changed", status=409
            )
        if stop_requested:
            raise SoftwareFactoryError(_STOP_BLOCKER, status=409)

        cancel = getattr(execution_module, "cancel_release_preflight", None)
        if not callable(cancel):
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_preflight_rotation_unavailable", status=503
            )
        plan_id = str(
            persisted_plan_id
            or state.get("plan_id")
            or current.get("plan_id")
            or release.get("plan_id")
            or ""
        ).strip()
        if not plan_id:
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_preflight_missing", status=409
            )
        rotated = cancel(int(user_id), plan_id)
        rotated_status = (
            str((rotated or {}).get("status") or "")
            if isinstance(rotated, Mapping)
            else ""
        )
        if rotated_status not in {"cancelled", "stale"}:
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_preflight_not_rotated",
                detail=rotated_status or plan_id,
                status=409,
            )

        cursor.execute(
            "UPDATE velia_software_factory_stage8_releases SET "
            "candidate_id='',plan_id='',retired_release_execution_id=%s,release_execution_id='',"
            "verification_id='',observation_id='',certificate_id='',passport_id='',"
            "status='retrying_candidate',blocker_code='',blocker_detail='',updated_at=NOW() "
            "WHERE user_id=%s AND workspace_execution_id=%s AND release_execution_id=%s "
            "AND stop_requested=FALSE",
            (release_id, int(user_id), workspace_execution_id, release_id),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            raise SoftwareFactoryError(
                "velia_factory_stage8_zero_merge_coordinator_rotation_failed", status=409
            )
        conn.commit()


def _install_release_atomicity_hardening(execution_module: Any) -> None:
    if getattr(execution_module, "_workspace_stage8_release_atomicity_installed", False):
        return
    from services import velia_software_factory_release_execution_service as release_execution
    from services import velia_software_factory_stage8_final_hardening_patch as final_hardening
    from services import velia_software_factory_stage8_release_runtime_patch as release_runtime

    if not getattr(execution_module, "_workspace_stage8_final_hardening_installed", False):
        raise RuntimeError("stage8_release_atomicity_requires_final_hardening")

    _ensure_durable_stop_schema(execution_module)
    original_authorizer = final_hardening._strict_release_authorized
    original_refresh = final_hardening._refresh_retryable_evidence
    original_progress = release_runtime._progress_release
    original_request_stop = release_execution.request_stop
    original_execute_release = execution_module.execute_release

    def strict_release_authorized(execution: Mapping[str, Any]) -> bool:
        plan = execution.get("plan") if isinstance(execution.get("plan"), Mapping) else {}
        objective = str(plan.get("objective") or "").strip()
        if _conditional_noun_deferred_approval(objective):
            return False
        return bool(original_authorizer(execution))

    def refresh_retryable_evidence(execution_module_arg: Any, user_id: int, execution_id: str) -> None:
        before = release_runtime._state(execution_module_arg, int(user_id), str(execution_id))
        old_release_id = str(before.get("release_execution_id") or "")
        original_refresh(execution_module_arg, int(user_id), str(execution_id))
        after = release_runtime._state(execution_module_arg, int(user_id), str(execution_id))
        if (
            old_release_id
            and not str(after.get("release_execution_id") or "")
            and str(after.get("status") or "") == "retrying_candidate"
        ):
            raise _RetryDeferred(after)

    def progress_release(execution_module_arg: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
        stop_state = _coordinator_stop_state(
            execution_module_arg,
            int(user_id),
            workspace_execution_id=str(execution_id),
        )
        if stop_state.get("stop_requested"):
            active_release_id = str(stop_state.get("release_execution_id") or "")
            if active_release_id:
                try:
                    original_request_stop(execution_module_arg, int(user_id), active_release_id)
                except Exception:
                    logger.exception(
                        "VELIA_STAGE8_DURABLE_STOP_PROPAGATION_FAILED release_execution_id=%s",
                        active_release_id,
                    )
            return _terminalize_coordinator_stop(
                execution_module_arg, int(user_id), str(execution_id)
            )
        try:
            return original_progress(execution_module_arg, int(user_id), str(execution_id))
        except _RetryDeferred as deferred:
            return {**deferred.state, "execution_id": str(execution_id)}

    def request_stop(
        execution_module_arg: Any,
        user_id: int,
        release_execution_id: str,
    ) -> Dict[str, Any]:
        release_id = str(release_execution_id or "").strip()
        marker = _mark_coordinator_stop(
            execution_module_arg, int(user_id), release_id
        )
        result = original_request_stop(
            execution_module_arg, int(user_id), release_id
        )
        if marker:
            safe = dict(result or {})
            safe["stage8_workspace_stop_requested"] = True
            safe["workspace_execution_id"] = str(marker.get("workspace_execution_id") or "")
            return safe
        return result

    def execute_release(user_id: int, release_execution_id: str) -> Dict[str, Any]:
        release_id = str(release_execution_id or "").strip()
        marker = _coordinator_stop_state(
            execution_module,
            int(user_id),
            release_execution_id=release_id,
        )
        if marker.get("stop_requested"):
            original_request_stop(execution_module, int(user_id), release_id)
            _terminalize_coordinator_stop(
                execution_module,
                int(user_id),
                str(marker.get("workspace_execution_id") or ""),
            )
            return execution_module.get_release_execution(int(user_id), release_id)
        return original_execute_release(int(user_id), release_id)

    final_hardening._strict_release_authorized = strict_release_authorized
    release_runtime._explicit_release_authorized = strict_release_authorized
    final_hardening._rotate_zero_merge_preflight = _atomic_zero_merge_rotate
    final_hardening._refresh_retryable_evidence = refresh_retryable_evidence
    release_runtime._progress_release = progress_release
    release_execution.request_stop = request_stop
    execution_module.execute_release = execute_release
    execution_module._workspace_stage8_release_atomicity_installed = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_STAGE8_RELEASE_ATOMICITY_INSTALLED durable_stop=true"
    )


def _stage8_single_workspace_delegate(
    runtime_module: Any,
    *,
    objective: str,
    user_id: int,
    conversation_id: str,
    request_id: str,
    project: Mapping[str, Any],
) -> Dict[str, Any]:
    """Create a one-project workspace only for Stage 8 greenfield delivery.

    Stage 4.4 remains multi-repository by default. This explicit Stage 8 path is
    needed so a one-repository greenfield project reaches workspace review_ready
    and therefore the Stage 8 release/deployment/passport coordinator.
    """
    workspace_runtime = runtime_module.workspace_runtime
    project_dict = dict(project)
    project_id = str(project_dict.get("id") or "")
    if not project_id:
        raise runtime_module.SoftwareFactoryError(
            "velia_factory_greenfield_projects_missing", status=409
        )
    payload = {
        "title": str(objective or "VELIA greenfield product")[:200],
        "objective": str(objective or "")[:12000],
        "primary_project_id": project_id,
        "repositories": [
            {
                "project_id": project_id,
                "role": workspace_runtime.workspace_service.infer_repository_role(
                    project_dict, primary=False
                ),
            }
        ],
        "metadata": {
            "source": "stage8_greenfield_single_workspace",
            "conversation_id": str(conversation_id)[:200],
        },
    }
    workspace = workspace_runtime.workspace_service.create_workspace(int(user_id), payload)
    plan = workspace_runtime.workspace_chat.build_workspace_plan(
        str(objective),
        workspace,
        user_id=int(user_id),
        request_id=str(request_id or conversation_id),
    )
    context = workspace_runtime._save_context(
        int(user_id),
        str(conversation_id),
        status="collecting_scopes",
        workspace_id=str(workspace.get("workspace_id") or ""),
        objective=str(objective),
        plan=plan,
        selection={"project_ids": [project_id], "stage8_single_project": True},
    )
    pending = workspace_runtime._next_scope(workspace, plan)
    if pending is None:
        return workspace_runtime._execute_or_plan(
            message=str(objective),
            request_id=str(request_id),
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            context=context,
            workspace=workspace,
        )
    selected_project = runtime_module.project_service.get_project(
        int(user_id), str(pending.get("project_id") or "")
    )
    recommended = runtime_module.autonomy.recommend_write_scope(selected_project)
    if not recommended:
        raise runtime_module.SoftwareFactoryError(
            "velia_factory_greenfield_safe_scope_unavailable", status=409
        )
    return runtime_module._result(
        workspace_runtime._scope_question(str(objective), pending, recommended),
        request_id,
        reason="software_factory_greenfield_delegated_workspace",
    )


def install(chat_module: Any, greenfield_service: Any, runtime_module: Any) -> None:
    global _INSTALLED
    if getattr(chat_module, "_velia_software_factory_stage8_greenfield_installed", False):
        return

    # Stage 8 release hardening must be installed after the base release runtime
    # and before full-autonomy readiness can become true.
    from services import velia_software_factory_stage8_final_hardening_patch as final_hardening
    from services import velia_software_factory_workspace_execution_service as execution_module

    final_hardening.install(execution_module)
    _install_release_atomicity_hardening(execution_module)

    original_generate = chat_module.generate_velia_chat_result
    original_delegate_single = runtime_module._delegate_single

    def delegate_single_stage8(
        *,
        objective: str,
        user_id: int,
        conversation_id: str,
        request_id: str,
        project: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if rollout.rollout_mode() != rollout.ROLLOUT_FULL_AUTONOMY:
            return original_delegate_single(
                objective=str(objective),
                user_id=int(user_id),
                conversation_id=str(conversation_id),
                request_id=str(request_id),
                project=project,
            )
        return _stage8_single_workspace_delegate(
            runtime_module,
            objective=str(objective),
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=str(request_id),
            project=project,
        )

    runtime_module._delegate_single = delegate_single_stage8

    def generate_stage8_greenfield(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: str | None = None,
    ) -> Dict[str, Any]:
        result = original_generate(
            prompt,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=request_id,
        )
        if not isinstance(result, dict):
            return result
        if rollout.rollout_mode() != rollout.ROLLOUT_FULL_AUTONOMY:
            return result
        if not rollout.user_allowed(int(user_id)) or not repository_creation.enabled():
            return result
        if str(result.get("reason") or "") not in _TRIGGER_REASONS:
            return result

        context = runtime_module.get_greenfield_context(int(user_id), str(conversation_id))
        manifest = context.get("manifest") if isinstance(context.get("manifest"), Mapping) else {}
        if not manifest:
            return result
        message = developer_chat._latest_request_user_message(
            str(request_id or ""), int(user_id)
        ) or str(context.get("objective") or "")
        try:
            created = repository_creation.create_missing_repositories(int(user_id), manifest)
            missing = greenfield_service.missing_manifest_repositories(manifest)
            if missing:
                safe = dict(result)
                safe["reason"] = "software_factory_greenfield_repository_creation_pending_visibility"
                safe["software_factory_context"] = dict(safe.get("software_factory_context") or {})
                safe["software_factory_context"].update(
                    {
                        "repository_creation": True,
                        "repository_creation_performed": any(bool(item.get("created_now")) for item in created),
                        "missing_repositories": list(missing),
                    }
                )
                return safe

            enriched = dict(manifest)
            enriched["requires_external_repository_creation"] = False
            enriched["repository_creation_performed"] = any(
                bool(item.get("created_now")) for item in created
            )
            enriched["repository_creation_provider"] = "github_app_organization"
            context = runtime_module._save_context(
                int(user_id),
                str(conversation_id),
                status="waiting_repositories",
                objective=str(context.get("objective") or ""),
                roles=[str(item) for item in context.get("roles") or []],
                existing_project_ids=[str(item) for item in context.get("existing_project_ids") or []],
                manifest=enriched,
            )
            delegated = runtime_module._attach_and_delegate(
                message,
                user_id=int(user_id),
                conversation_id=str(conversation_id),
                request_id=str(request_id or ""),
                context=context,
            )
            if isinstance(delegated, dict):
                delegated = dict(delegated)
                delegated["software_factory_context"] = dict(
                    delegated.get("software_factory_context") or {}
                )
                delegated["software_factory_context"].update(
                    {
                        "stage": "8",
                        "mode": "full_autonomy_greenfield",
                        "repository_creation": True,
                        "repository_creation_performed": bool(
                            enriched["repository_creation_performed"]
                        ),
                        "repository_creation_provider": "github_app_organization",
                        "workspace_release_path": True,
                    }
                )
            return delegated
        except Exception as exc:
            logger.exception(
                "VELIA_STAGE8_GREENFIELD_CREATION_FAILED user_id=%s conversation_id=%s",
                int(user_id),
                str(conversation_id),
            )
            safe = dict(result)
            safe["reason"] = "software_factory_stage8_greenfield_creation_blocked"
            safe["software_factory_context"] = dict(safe.get("software_factory_context") or {})
            safe["software_factory_context"].update(
                {
                    "stage": "8",
                    "repository_creation": True,
                    "repository_creation_blocker": str(
                        getattr(exc, "code", exc.__class__.__name__)
                    )[:160],
                }
            )
            safe["text"] = (
                "Не удалось безопасно создать GitHub repository автоматически: "
                + str(getattr(exc, "code", exc.__class__.__name__))
                + ". Никакой похожий или чужой repository не подключался."
                if runtime_module._russian(message)
                else "VELIA could not safely create the GitHub repository automatically: "
                + str(getattr(exc, "code", exc.__class__.__name__))
                + ". No similar or foreign repository was attached."
            )
            return safe

    chat_module.generate_velia_chat_result = generate_stage8_greenfield
    chat_module._velia_software_factory_stage8_greenfield_installed = True
    _INSTALLED = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_STAGE8_GREENFIELD_RUNTIME_INSTALLED enabled=%s final_hardening=true atomic_retry=true durable_stop=true",
        str(repository_creation.enabled()).lower(),
    )