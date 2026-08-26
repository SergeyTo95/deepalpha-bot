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


def install(chat_module: Any, greenfield_service: Any, runtime_module: Any) -> None:
    global _INSTALLED
    if getattr(chat_module, "_velia_software_factory_stage8_greenfield_installed", False):
        return
    original_generate = chat_module.generate_velia_chat_result

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
        "VELIA_SOFTWARE_FACTORY_STAGE8_GREENFIELD_RUNTIME_INSTALLED enabled=%s",
        str(repository_creation.enabled()).lower(),
    )
