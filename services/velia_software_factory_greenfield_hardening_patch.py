from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping

from services import velia_developer_project_service as project_service
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_stage3_hardening_patch as stage3_hardening
from services import velia_software_factory_team_service as team_service
from services import velia_software_factory_workspace_chat_service as workspace_chat
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False
_CANONICAL_GREENFIELD_ROOTS = {"app", "android", "tests", "docs"}
_ALLOWED_PROFILES = {"fullstack", "backend", "frontend", "android"}


def _registered_roots(service: Any, project_id: str) -> List[str]:
    service.ensure_greenfield_tables()
    conn = service.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT allowed_roots_json FROM velia_software_factory_greenfield_projects WHERE project_id=%s LIMIT 2",
            (str(project_id),),
        )
        rows = cursor.fetchall() or []
        if len(rows) != 1:
            return []
        row = rows[0]
        raw = row[0] if not isinstance(row, dict) else row.get("allowed_roots_json")
        roots = service._loads(raw, [])
        result: List[str] = []
        for item in roots if isinstance(roots, list) else []:
            safe = stage3_hardening._safe_path(str(item or ""))
            if safe in _CANONICAL_GREENFIELD_ROOTS and safe not in result:
                result.append(safe)
        return result
    finally:
        cursor.close()
        conn.close()


def _validate_manifest_owner(service: Any, user_id: int, manifest: Mapping[str, Any]) -> None:
    installation_id = int(manifest.get("installation_id") or 0)
    if installation_id <= 0:
        raise SoftwareFactoryError("velia_factory_greenfield_installation_invalid", status=409)
    installation = project_service.get_installation(int(user_id), installation_id)
    account = str(installation.get("account_login") or "").strip()
    declared_account = str(manifest.get("account_login") or "").strip()
    if not account or not declared_account or account.casefold() != declared_account.casefold():
        raise SoftwareFactoryError("velia_factory_greenfield_installation_owner_mismatch", status=409)

    repositories = manifest.get("repositories") if isinstance(manifest.get("repositories"), list) else []
    if not repositories or len(repositories) > 4:
        raise SoftwareFactoryError("velia_factory_greenfield_manifest_invalid", status=409)
    prefix = account.casefold() + "/"
    seen = set()
    for raw in repositories:
        if not isinstance(raw, Mapping):
            raise SoftwareFactoryError("velia_factory_greenfield_manifest_invalid", status=409)
        full_name = str(raw.get("full_name") or "").strip()
        profile = str(raw.get("profile") or "").strip().lower()
        if not full_name or not full_name.casefold().startswith(prefix):
            raise SoftwareFactoryError("velia_factory_greenfield_repository_owner_mismatch", detail=full_name, status=409)
        if full_name.casefold() in seen:
            raise SoftwareFactoryError("velia_factory_greenfield_repository_duplicate", detail=full_name, status=409)
        seen.add(full_name.casefold())
        if profile not in _ALLOWED_PROFILES:
            raise SoftwareFactoryError("velia_factory_greenfield_profile_invalid", detail=profile, status=409)
        declared_roots = [str(item or "").strip("/") for item in raw.get("recommended_roots") or []]
        canonical = service.canonical_roots(profile)
        if declared_roots != canonical:
            raise SoftwareFactoryError("velia_factory_greenfield_scope_manifest_invalid", detail=full_name, status=409)


def _require_initialized_repositories(service: Any, manifest: Mapping[str, Any]) -> None:
    installation_id = int(manifest.get("installation_id") or 0)
    available = service._available_repositories(installation_id)
    for raw in manifest.get("repositories") or []:
        if not isinstance(raw, Mapping):
            continue
        full_name = str(raw.get("full_name") or "").strip()
        repo = available.get(full_name.casefold())
        if not repo or bool(repo.get("archived")):
            raise SoftwareFactoryError("velia_factory_greenfield_repository_missing", detail=full_name, status=409)
        if str(repo.get("full_name") or "").casefold() != full_name.casefold():
            raise SoftwareFactoryError("velia_factory_greenfield_repository_identity_mismatch", detail=full_name, status=409)
        branch = str(repo.get("default_branch") or "main")
        try:
            tree = service.github_service.list_tree(
                installation_id,
                int(repo.get("id") or 0),
                full_name,
                branch,
                prefix="",
            )
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            detail = str(getattr(exc, "detail", "")).lower()
            if code in {"github_not_found", "github_invalid_request", "github_api_error"} and "empty" in detail:
                raise SoftwareFactoryError(
                    "velia_factory_greenfield_initial_commit_required", detail=full_name, status=409
                ) from exc
            raise
        entries = tree.get("entries") if isinstance(tree, Mapping) else []
        if not isinstance(entries, list) or not entries:
            raise SoftwareFactoryError("velia_factory_greenfield_initial_commit_required", detail=full_name, status=409)


def _delegation_slot_busy(runtime_module: Any, user_id: int, conversation_id: str) -> bool:
    if autonomy.get_chat_run(int(user_id), str(conversation_id)):
        return True
    workspace_context = runtime_module.workspace_runtime.get_workspace_chat_context(
        int(user_id), str(conversation_id)
    )
    if str(workspace_context.get("status") or "") in runtime_module.workspace_runtime._ACTIVE_CONTEXT_STATES:
        return True
    if runtime_module.agent_planner.active_chat_job(int(user_id), str(conversation_id)):
        return True
    if runtime_module.coding_service.active_job(int(user_id), str(conversation_id)):
        return True
    return False


def install(chat_module: Any, service: Any, runtime_module: Any) -> None:
    global _INSTALLED
    if getattr(chat_module, "_velia_software_factory_greenfield_hardening_installed", False):
        return

    original_enabled = service.greenfield_enabled
    original_attach = service.attach_exact_repositories
    original_recommend_scope = autonomy.recommend_write_scope
    original_generate = chat_module.generate_velia_chat_result
    original_attach_and_delegate = runtime_module._attach_and_delegate

    def greenfield_enabled() -> bool:
        return (
            bool(original_enabled())
            and bool(project_service.developer_enabled())
            and bool(factory.software_factory_enabled())
            and bool(team_service.team_enabled())
            and bool(autonomy.autonomy_enabled())
            and bool(workspace_chat.workspace_chat_enabled())
        )

    def attach_exact_repositories(user_id: int, manifest: Mapping[str, Any]):
        if not greenfield_enabled():
            raise SoftwareFactoryError("velia_factory_greenfield_disabled", status=503)
        if not rollout.intake_allowed(int(user_id)):
            raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)
        _validate_manifest_owner(service, int(user_id), manifest)
        _require_initialized_repositories(service, manifest)
        return original_attach(int(user_id), manifest)

    def recommend_write_scope(project: Mapping[str, Any], *args: Any, **kwargs: Any) -> List[str]:
        normal = list(original_recommend_scope(project, *args, **kwargs) or [])
        if normal:
            return normal
        project_id = str(project.get("id") or project.get("project_id") or "")
        if not project_id or not greenfield_enabled():
            return []
        return _registered_roots(service, project_id)

    def attach_and_delegate(
        message: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: str,
        context: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if _delegation_slot_busy(runtime_module, int(user_id), str(conversation_id)):
            text = (
                "Greenfield bootstrap готов к подключению репозиториев, но в этом чате уже запущена другая разработка. Заверши или отмени её, затем снова напиши «продолжай». Репозитории этим шагом не прикреплялись и код не запускался."
                if runtime_module._russian(message)
                else "The greenfield bootstrap is ready to attach repositories, but another development run is active in this chat. Complete or cancel it, then reply “continue” again. No repositories were attached and no code execution was started by this step."
            )
            return runtime_module._result(
                text,
                request_id,
                reason="software_factory_greenfield_plan_conflict",
                context=context,
            )
        return original_attach_and_delegate(
            message,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=str(request_id),
            context=context,
        )

    def generate_hardened(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = original_generate(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        if str(result.get("reason") or "") != "software_factory_greenfield_internal_error":
            return result
        safe = dict(result)
        text = str(result.get("text") or "")
        russian = runtime_module._russian(text)
        safe["text"] = (
            "Не удалось продолжить greenfield bootstrap. Проверь, что нужные репозитории созданы, имеют первый commit и доступны установленному VELIA GitHub App, затем повтори «продолжай». Репозитории Velia этим шагом не создаёт."
            if russian
            else "The greenfield bootstrap could not continue. Verify that the required repositories exist, have an initial commit, and are visible to the installed VELIA GitHub App, then retry “continue”. VELIA does not create repositories in this step."
        )
        return safe

    service.greenfield_enabled = greenfield_enabled
    service.attach_exact_repositories = attach_exact_repositories
    autonomy.recommend_write_scope = recommend_write_scope
    runtime_module._attach_and_delegate = attach_and_delegate
    chat_module.generate_velia_chat_result = generate_hardened
    chat_module._velia_software_factory_greenfield_hardening_installed = True
    _INSTALLED = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_GREENFIELD_HARDENING_INSTALLED enabled=%s repository_creation=false",
        str(greenfield_enabled()).lower(),
    )
