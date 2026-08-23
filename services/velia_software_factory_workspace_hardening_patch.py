from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from db.database import get_connection
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_stage3_hardening_patch as stage3_hardening
from services.velia_software_factory_core_service import SoftwareFactoryError

_INSTALLED = False


def _sanitize_metadata(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: Dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:40]:
        key = str(raw_key or "").strip()[:80]
        if not key:
            continue
        if raw_value is None or isinstance(raw_value, (bool, int, float)):
            result[key] = raw_value
        elif isinstance(raw_value, str):
            result[key] = raw_value[:2000]
        elif isinstance(raw_value, (list, tuple)):
            items: List[Any] = []
            for item in list(raw_value)[:40]:
                if item is None or isinstance(item, (bool, int, float)):
                    items.append(item)
                elif isinstance(item, str):
                    items.append(item[:500])
            result[key] = items
        elif isinstance(raw_value, Mapping):
            nested: Dict[str, Any] = {}
            for nested_key, nested_value in list(raw_value.items())[:20]:
                safe_key = str(nested_key or "").strip()[:80]
                if not safe_key:
                    continue
                if nested_value is None or isinstance(nested_value, (bool, int, float)):
                    nested[safe_key] = nested_value
                elif isinstance(nested_value, str):
                    nested[safe_key] = nested_value[:500]
            result[key] = nested
    return result


def _normalize_repository_role(workspace_module: Any, value: Any, project: Mapping[str, Any], *, primary: bool = False) -> str:
    # Primary is topology, not an engineering specialty. Keep the actual repo role
    # useful to the task router instead of degrading every primary repo to fullstack.
    role = str(value or "").strip().lower()[:40]
    if role == "primary" or role not in workspace_module._REPO_ROLES:
        role = workspace_module.infer_repository_role(project, primary=False)
    if role == "web":
        role = "frontend"
    return role


def _safe_allowed_paths(project: Mapping[str, Any], requested: Any) -> List[str]:
    candidates = autonomy.recommend_write_scope(project)
    safe_roots: List[str] = []
    for raw in candidates:
        root = stage3_hardening._safe_path(raw)
        if root and root not in safe_roots:
            safe_roots.append(root)
    if not safe_roots:
        raise SoftwareFactoryError("velia_factory_workspace_safe_scope_unavailable", status=409)

    source = requested if isinstance(requested, (list, tuple, set)) else ([] if requested is None else [requested])
    result: List[str] = []
    for raw in list(source)[:20]:
        candidate = stage3_hardening._safe_path(raw)
        if not candidate:
            raise SoftwareFactoryError("velia_factory_workspace_scope_path_unsafe", detail=str(raw)[:500], status=409)
        if not any(candidate == root or candidate.startswith(root + "/") for root in safe_roots):
            raise SoftwareFactoryError("velia_factory_workspace_scope_path_outside_safe_tree", detail=candidate, status=409)
        if candidate not in result:
            result.append(candidate)
    if not result:
        raise SoftwareFactoryError("velia_factory_workspace_scope_required")
    return result


def _safe_blocked_paths(requested: Any) -> List[str]:
    source = requested if isinstance(requested, (list, tuple, set)) else ([] if requested is None else [requested])
    result: List[str] = []
    for raw in list(source)[:50]:
        normalized = str(raw or "").strip().replace("\\", "/").strip("/")[:500]
        if not normalized:
            continue
        try:
            normalized = autonomy.github_service.validate_path(normalized)
        except Exception as exc:
            raise SoftwareFactoryError("velia_factory_workspace_blocked_path_invalid", detail=normalized) from exc
        if normalized not in result:
            result.append(normalized)
    return result


def _assert_scope_mutable(workspace_id: str, user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT to_regclass('public.velia_software_factory_workspace_executions')")
        row = cursor.fetchone()
        table = row[0] if row and not isinstance(row, dict) else (next(iter(row.values())) if row else None)
        if not table:
            return
        cursor.execute(
            "SELECT 1 FROM velia_software_factory_workspace_executions "
            "WHERE workspace_id=%s AND user_id=%s AND status IN ('created','running','blocked') LIMIT 1",
            (str(workspace_id), int(user_id)),
        )
        if cursor.fetchone():
            raise SoftwareFactoryError("velia_factory_workspace_scope_locked_by_execution", status=409)
    finally:
        cursor.close()
        conn.close()


def approve_workspace_scope(
    workspace_module: Any,
    user_id: int,
    workspace_id: str,
    project_id: str,
    *,
    allowed_paths: Any,
    blocked_paths: Any = None,
) -> Dict[str, Any]:
    workspace_module.ensure_workspace_tables()
    workspace = workspace_module.get_workspace(int(user_id), str(workspace_id))
    _assert_scope_mutable(str(workspace_id), int(user_id))
    member = next(
        (item for item in workspace.get("repositories") or [] if str(item.get("project_id") or "") == str(project_id)),
        None,
    )
    if not member:
        raise SoftwareFactoryError("velia_factory_workspace_project_not_found", detail=str(project_id), status=404)
    project = workspace_module.project_service.get_project(int(user_id), str(project_id))
    safe_allowed = _safe_allowed_paths(project, allowed_paths)
    safe_blocked = _safe_blocked_paths(blocked_paths)
    now = workspace_module._utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_workspace_projects "
            "SET allowed_paths_json=%s,blocked_paths_json=%s,scope_approved=TRUE,updated_at=%s "
            "WHERE workspace_id=%s AND project_id=%s AND user_id=%s",
            (
                workspace_module._json(safe_allowed),
                workspace_module._json(safe_blocked),
                now,
                str(workspace_id),
                str(project_id),
                int(user_id),
            ),
        )
        if cursor.rowcount != 1:
            raise SoftwareFactoryError("velia_factory_workspace_project_not_found", status=404)
        workspace_module._remember_cursor(
            cursor,
            str(workspace_id),
            int(user_id),
            "write_scope",
            f"Approved {project.get('repository_full_name')}: {', '.join(safe_allowed)}",
            "user_scope_approval",
            {"project_id": str(project_id), "allowed_paths": safe_allowed, "blocked_paths": safe_blocked},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return workspace_module.get_workspace(int(user_id), str(workspace_id))


def revoke_workspace_scope(workspace_module: Any, user_id: int, workspace_id: str, project_id: str) -> Dict[str, Any]:
    workspace_module.ensure_workspace_tables()
    workspace_module.get_workspace(int(user_id), str(workspace_id))
    _assert_scope_mutable(str(workspace_id), int(user_id))
    now = workspace_module._utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_workspace_projects "
            "SET allowed_paths_json='[]',blocked_paths_json='[]',scope_approved=FALSE,updated_at=%s "
            "WHERE workspace_id=%s AND project_id=%s AND user_id=%s",
            (now, str(workspace_id), str(project_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            raise SoftwareFactoryError("velia_factory_workspace_project_not_found", status=404)
        workspace_module._remember_cursor(
            cursor,
            str(workspace_id),
            int(user_id),
            "write_scope",
            f"Revoked write scope for project {project_id}",
            "user_scope_revocation",
            {"project_id": str(project_id)},
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return workspace_module.get_workspace(int(user_id), str(workspace_id))


def install(workspace_module: Any) -> None:
    global _INSTALLED
    if getattr(workspace_module, "_workspace_hardening_installed", False):
        return
    original_create = workspace_module.create_workspace
    original_get = workspace_module.get_workspace

    def create_workspace(user_id: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
        safe_payload = dict(payload or {})
        safe_payload["metadata"] = _sanitize_metadata(safe_payload.get("metadata"))
        return original_create(int(user_id), safe_payload)

    def get_workspace(user_id: int, workspace_id: str) -> Dict[str, Any]:
        result = original_get(int(user_id), str(workspace_id))
        primary_id = str(result.get("primary_project_id") or "")
        for member in result.get("repositories") or []:
            if not isinstance(member, dict):
                continue
            member["is_primary"] = str(member.get("project_id") or "") == primary_id
            if str(member.get("repo_role") or "") == "primary":
                project = workspace_module.project_service.get_project(int(user_id), str(member.get("project_id") or ""))
                member["repo_role"] = workspace_module.infer_repository_role(project, primary=False)
        return result

    def approve_scope(user_id: int, workspace_id: str, project_id: str, *, allowed_paths: Any, blocked_paths: Any = None):
        return approve_workspace_scope(
            workspace_module,
            int(user_id),
            str(workspace_id),
            str(project_id),
            allowed_paths=allowed_paths,
            blocked_paths=blocked_paths,
        )

    def revoke_scope(user_id: int, workspace_id: str, project_id: str):
        return revoke_workspace_scope(workspace_module, int(user_id), str(workspace_id), str(project_id))

    workspace_module._normalize_repo_role = lambda value, project, primary=False: _normalize_repository_role(
        workspace_module, value, project, primary=primary
    )
    workspace_module.create_workspace = create_workspace
    workspace_module.get_workspace = get_workspace
    workspace_module.approve_workspace_scope = approve_scope
    workspace_module.revoke_workspace_scope = revoke_scope

    # The route setup calls workspace hardening before it starts the workspace
    # execution supervisor. Install execution hardening here so mission-conflict
    # preflight and emergency-stop reconciliation are mandatory runtime policy.
    from services import velia_software_factory_workspace_execution_hardening_patch as execution_hardening
    from services import velia_software_factory_workspace_execution_service as execution_module

    execution_hardening.install(execution_module)
    workspace_module._workspace_hardening_installed = True
    _INSTALLED = True
