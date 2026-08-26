from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping
from urllib.parse import quote

from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service
from services.velia_software_factory_core_service import SoftwareFactoryError

_FLAG = "VELIA_SOFTWARE_FACTORY_GREENFIELD_REPOSITORY_CREATION_ENABLED"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return _env_bool(_FLAG, False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": enabled(),
        "provider": "github_app_organization",
        "private_by_default": True,
        "initial_commit": "auto_init_readme",
        "organization_installation_required": True,
        "administration_write_required": True,
        "configured": bool(enabled() and github_service.github_app_configured()),
    }


def _validated_installation(user_id: int, manifest: Mapping[str, Any]) -> Dict[str, Any]:
    installation_id = int(manifest.get("installation_id") or 0)
    if installation_id <= 0:
        raise SoftwareFactoryError("velia_factory_greenfield_installation_invalid", status=409)
    stored = project_service.get_installation(int(user_id), installation_id)
    declared = str(manifest.get("account_login") or "").strip()
    stored_login = str(stored.get("account_login") or "").strip()
    if not declared or declared.casefold() != stored_login.casefold():
        raise SoftwareFactoryError("velia_factory_greenfield_installation_owner_mismatch", status=409)

    try:
        raw = github_service._request(
            "GET",
            f"/app/installations/{installation_id}",
            token=github_service._app_jwt(),
        )
    except Exception as exc:
        raise SoftwareFactoryError(
            "velia_factory_greenfield_installation_capability_unavailable",
            detail=str(getattr(exc, "code", exc.__class__.__name__)),
            status=503,
        ) from exc
    account = raw.get("account") if isinstance(raw, Mapping) else {}
    permissions = raw.get("permissions") if isinstance(raw, Mapping) else {}
    account_login = str((account or {}).get("login") or "").strip()
    account_type = str((account or {}).get("type") or "").strip().lower()
    administration = str((permissions or {}).get("administration") or "").strip().lower()
    if account_login.casefold() != declared.casefold():
        raise SoftwareFactoryError("velia_factory_greenfield_installation_identity_changed", status=409)
    if account_type != "organization":
        raise SoftwareFactoryError(
            "velia_factory_greenfield_organization_installation_required",
            detail=account_login,
            status=409,
        )
    if administration != "write":
        raise SoftwareFactoryError(
            "velia_factory_greenfield_administration_write_required",
            detail=account_login,
            status=403,
        )
    return {
        "installation_id": installation_id,
        "account_login": account_login,
        "account_type": account_type,
    }


def create_missing_repositories(
    user_id: int,
    manifest: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    if not enabled():
        raise SoftwareFactoryError("velia_factory_greenfield_repository_creation_disabled", status=503)
    installation = _validated_installation(int(user_id), manifest)
    installation_id = int(installation["installation_id"])
    owner = str(installation["account_login"])
    existing = {
        str(item.get("full_name") or "").casefold(): dict(item)
        for item in github_service.list_installation_repositories(installation_id)
        if isinstance(item, Mapping)
    }
    token = github_service._installation_token(installation_id)
    created: List[Dict[str, Any]] = []
    for raw in manifest.get("repositories") or []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        full_name = str(raw.get("full_name") or "").strip()
        if not name or full_name.casefold() != f"{owner}/{name}".casefold():
            raise SoftwareFactoryError(
                "velia_factory_greenfield_repository_identity_mismatch",
                detail=full_name,
                status=409,
            )
        current = existing.get(full_name.casefold())
        if current and not bool(current.get("archived")):
            created.append({**current, "created_now": False})
            continue
        try:
            data = github_service._request(
                "POST",
                f"/orgs/{quote(owner)}/repos",
                token=token,
                body={
                    "name": name,
                    "description": "Created by VELIA Software Factory Stage 8",
                    "private": True,
                    "auto_init": True,
                    "has_issues": True,
                    "has_projects": False,
                    "has_wiki": False,
                },
                expected=(201,),
            )
        except Exception as exc:
            raise SoftwareFactoryError(
                "velia_factory_greenfield_repository_creation_failed",
                detail=f"{full_name}:{getattr(exc, 'code', exc.__class__.__name__)}",
                status=int(getattr(exc, "status", 503) or 503),
            ) from exc
        actual_full_name = str(data.get("full_name") or "") if isinstance(data, Mapping) else ""
        if actual_full_name.casefold() != full_name.casefold():
            raise SoftwareFactoryError(
                "velia_factory_greenfield_created_repository_identity_mismatch",
                detail=actual_full_name,
                status=409,
            )
        created.append(
            {
                "id": int(data.get("id") or 0),
                "full_name": actual_full_name,
                "name": str(data.get("name") or name),
                "owner": owner,
                "private": bool(data.get("private", True)),
                "default_branch": str(data.get("default_branch") or "main"),
                "archived": bool(data.get("archived")),
                "created_now": True,
            }
        )
    if not created:
        raise SoftwareFactoryError("velia_factory_greenfield_manifest_empty", status=409)
    return created
