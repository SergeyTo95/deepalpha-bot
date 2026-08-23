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


def install(chat_module: Any, service: Any, runtime_module: Any) -> None:
    global _INSTALLED
    if getattr(chat_module, "_velia_software_factory_greenfield_hardening_installed", False):
        return

    original_enabled = service.greenfield_enabled
    original_attach = service.attach_exact_repositories
    original_recommend_scope = autonomy.recommend_write_scope
    original_generate = chat_module.generate_velia_chat_result

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
        return original_attach(int(user_id), manifest)

    def recommend_write_scope(project: Mapping[str, Any], *args: Any, **kwargs: Any) -> List[str]:
        normal = list(original_recommend_scope(project, *args, **kwargs) or [])
        if normal:
            return normal
        project_id = str(project.get("id") or project.get("project_id") or "")
        if not project_id or not greenfield_enabled():
            return []
        return _registered_roots(service, project_id)

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
            "Не удалось продолжить greenfield bootstrap. Проверь, что нужные репозитории созданы и доступны установленному VELIA GitHub App, затем повтори «продолжай». Репозитории Velia этим шагом не создаёт."
            if russian
            else "The greenfield bootstrap could not continue. Verify that the required repositories exist and are visible to the installed VELIA GitHub App, then retry “continue”. VELIA does not create repositories in this step."
        )
        return safe

    service.greenfield_enabled = greenfield_enabled
    service.attach_exact_repositories = attach_exact_repositories
    autonomy.recommend_write_scope = recommend_write_scope
    chat_module.generate_velia_chat_result = generate_hardened
    chat_module._velia_software_factory_greenfield_hardening_installed = True
    _INSTALLED = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_GREENFIELD_HARDENING_INSTALLED enabled=%s repository_creation=false",
        str(greenfield_enabled()).lower(),
    )
