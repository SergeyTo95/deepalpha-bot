from __future__ import annotations

import logging
from typing import Any, Dict, Mapping

from services import velia_developer_chat_runtime_patch as developer_chat
from services import velia_software_factory_greenfield_repository_creation_service as repository_creation
from services import velia_software_factory_rollout_service as rollout

logger = logging.getLogger(__name__)
_INSTALLED = False
_TRIGGER_REASONS = {
    "software_factory_greenfield_manifest_ready",
    "software_factory_greenfield_repositories_missing",
    "software_factory_greenfield_ready_to_attach",
}


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
        "VELIA_SOFTWARE_FACTORY_STAGE8_GREENFIELD_RUNTIME_INSTALLED enabled=%s final_hardening=true",
        str(repository_creation.enabled()).lower(),
    )
