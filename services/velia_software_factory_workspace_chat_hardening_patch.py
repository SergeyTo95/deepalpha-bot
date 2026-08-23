from __future__ import annotations

from typing import Any, Dict

from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_team_service as team_service

_INSTALLED = False


def install(chat_module: Any, workspace_chat_service: Any, runtime_module: Any) -> None:
    global _INSTALLED
    if getattr(chat_module, "_velia_software_factory_workspace_chat_hardening_installed", False):
        return

    original_feature_enabled = workspace_chat_service.workspace_chat_enabled
    original_live_ready = runtime_module._live_workspace_ready
    original_generate = chat_module.generate_velia_chat_result

    def workspace_chat_enabled() -> bool:
        return (
            bool(original_feature_enabled())
            and bool(factory.software_factory_enabled())
            and bool(team_service.team_enabled())
            and bool(autonomy.autonomy_enabled())
        )

    def live_workspace_ready(user_id: int) -> bool:
        return (
            workspace_chat_enabled()
            and bool(runtime_module.workspace_execution.workspace_supervisor_enabled())
            and bool(original_live_ready(int(user_id)))
        )

    def generate_hardened(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = original_generate(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        if str(result.get("reason") or "") != "software_factory_workspace_internal_error":
            return result
        safe = dict(result)
        text = str(result.get("text") or "")
        russian = runtime_module._russian(text)
        safe["text"] = (
            "Не удалось продолжить multi-repo операцию. Проверь статус workspace перед повторным запуском: предыдущий шаг мог остановиться на безопасной границе."
            if russian
            else "The multi-repo operation could not continue. Check workspace status before retrying because the previous step may have stopped at a safe boundary."
        )
        return safe

    workspace_chat_service.workspace_chat_enabled = workspace_chat_enabled
    runtime_module._live_workspace_ready = live_workspace_ready
    chat_module.generate_velia_chat_result = generate_hardened
    chat_module._velia_software_factory_workspace_chat_hardening_installed = True
    _INSTALLED = True
