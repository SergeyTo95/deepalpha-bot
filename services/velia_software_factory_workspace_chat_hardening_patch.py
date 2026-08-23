from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Sequence

from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_team_service as team_service

logger = logging.getLogger(__name__)
_INSTALLED = False


def _explicit_multi_repo_intent(message: str, workspace_chat_service: Any, projects: Sequence[Mapping[str, Any]]) -> bool:
    explicit = workspace_chat_service.explicit_projects(str(message or ""), projects)
    if len(explicit) >= 2:
        return True
    low = str(message or "").lower()
    cross_platform_pairs = (
        (("android", "андроид", "mobile", "мобиль"), ("web", "сайт", "frontend", "веб", "магазин")),
        (("backend", "бэкенд", "бекенд", "server", "сервер"), ("android", "андроид", "mobile", "мобиль")),
    )
    if any(any(left in low for left in lefts) and any(right in low for right in rights) for lefts, rights in cross_platform_pairs):
        return True
    return workspace_chat_service._contains(str(message or ""), workspace_chat_service._BROAD_PRODUCT_HINTS)


def install(chat_module: Any, workspace_chat_service: Any, runtime_module: Any) -> None:
    global _INSTALLED
    if getattr(chat_module, "_velia_software_factory_workspace_chat_hardening_installed", False):
        return

    original_feature_enabled = workspace_chat_service.workspace_chat_enabled
    original_select_projects = workspace_chat_service.select_workspace_projects
    original_live_ready = runtime_module._live_workspace_ready
    original_generate = chat_module.generate_velia_chat_result

    def workspace_chat_enabled() -> bool:
        return (
            bool(original_feature_enabled())
            and bool(factory.software_factory_enabled())
            and bool(team_service.team_enabled())
            and bool(autonomy.autonomy_enabled())
        )

    def select_workspace_projects(message: str, projects: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        result = dict(original_select_projects(str(message or ""), projects) or {})
        if str(result.get("status") or "") != "missing_roles":
            return result
        if _explicit_multi_repo_intent(str(message or ""), workspace_chat_service, projects):
            return result
        # A normal product request must not force an artificial second repo. If
        # the user's connected topology cannot satisfy the inferred split, defer
        # to the established single-repository Factory path instead of asking for
        # a repository that may not be needed at all.
        return {"status": "single", "projects": [], "required_roles": []}

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
    workspace_chat_service.select_workspace_projects = select_workspace_projects
    runtime_module._live_workspace_ready = live_workspace_ready
    chat_module.generate_velia_chat_result = generate_hardened
    chat_module._velia_software_factory_workspace_chat_hardening_installed = True
    _INSTALLED = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_HARDENING_INSTALLED enabled=%s",
        str(workspace_chat_enabled()).lower(),
    )
