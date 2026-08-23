from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_MAX_REPOSITORIES = 8
_MAX_TASKS = 64
_REPO_ROLES = {"primary", "backend", "frontend", "android", "web", "infra", "shared", "other"}
_TASK_ROLES = {"backend", "frontend", "android", "qa", "security", "devops", "fullstack"}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _dict_cursor(conn):
    try:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        return conn.cursor()


def _str_list(value: Any, *, limit: int = 100, item_limit: int = 500) -> List[str]:
    source = value if isinstance(value, (list, tuple, set)) else ([] if value is None else [value])
    result: List[str] = []
    for raw in source:
        item = _text(raw, item_limit)
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _slug(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", _text(value, 120).lower()).strip("-_")
    return (normalized or fallback)[:100]


def infer_repository_role(project: Mapping[str, Any], *, primary: bool = False) -> str:
    if primary:
        return "primary"
    name = str(project.get("repository_full_name") or "").rsplit("/", 1)[-1].lower()
    if "android" in name or "mobile" in name:
        return "android"
    if any(token in name for token in ("frontend", "webapp", "web-ui", "website")):
        return "frontend"
    if any(token in name for token in ("backend", "server", "api", "-bot", "bot")):
        return "backend"
    if any(token in name for token in ("infra", "deploy", "terraform")):
        return "infra"
    return "shared"


def _normalize_repo_role(value: Any, project: Mapping[str, Any], *, primary: bool = False) -> str:
    role = _text(value, 40).lower()
    if role not in _REPO_ROLES:
        role = infer_repository_role(project, primary=primary)
    if primary:
        role = "primary"
    return role


def ensure_workspace_tables() -> None:
    global _SCHEMA_READY
    project_service.ensure_developer_tables()
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL DEFAULT '',
                    primary_project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id),
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('active','archived'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_workspaces_user "
                "ON velia_software_factory_workspaces(user_id,status,updated_at DESC)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_projects (
                    workspace_id TEXT NOT NULL REFERENCES velia_software_factory_workspaces(workspace_id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id),
                    user_id BIGINT NOT NULL,
                    repo_role TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
                    blocked_paths_json TEXT NOT NULL DEFAULT '[]',
                    scope_approved BOOLEAN NOT NULL DEFAULT FALSE,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (workspace_id,project_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_workspace_projects_user "
                "ON velia_software_factory_workspace_projects(user_id,workspace_id,sort_order)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_brain (
                    entry_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES velia_software_factory_workspaces(workspace_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    kind TEXT NOT NULL,
                    text_value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence NUMERIC(6,5) NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    fingerprint TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(workspace_id,fingerprint)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_workspace_brain "
                "ON velia_software_factory_workspace_brain(user_id,workspace_id,last_seen_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _workspace_row(row: Any) -> Dict[str, Any]:
    return {
        "workspace_id": str(_value(row, "workspace_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "title": str(_value(row, "title", 2, "")),
        "objective": str(_value(row, "objective", 3, "")),
        "primary_project_id": str(_value(row, "primary_project_id", 4, "")),
        "status": str(_value(row, "status", 5, "active")),
        "metadata": _loads(_value(row, "metadata_json", 6, "{}"), {}),
        "created_at": str(_value(row, "created_at", 7, "") or ""),
        "updated_at": str(_value(row, "updated_at", 8, "") or ""),
    }


def _member_row(row: Any, project: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "project_id": str(_value(row, "project_id", 0, "")),
        "repo_role": str(_value(row, "repo_role", 1, "shared")),
        "sort_order": int(_value(row, "sort_order", 2, 0) or 0),
        "allowed_paths": _loads(_value(row, "allowed_paths_json", 3, "[]"), []),
        "blocked_paths": _loads(_value(row, "blocked_paths_json", 4, "[]"), []),
        "scope_approved": bool(_value(row, "scope_approved", 5, False)),
        "metadata": _loads(_value(row, "metadata_json", 6, "{}"), {}),
        "repository_full_name": str(project.get("repository_full_name") or ""),
        "selected_branch": str(project.get("selected_branch") or ""),
        "default_branch": str(project.get("default_branch") or ""),
        "is_private": bool(project.get("is_private", True)),
    }


def _brain_fingerprint(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind.strip().lower()}\n{text.strip().lower()}".encode("utf-8")).hexdigest()


def _remember_cursor(cursor: Any, workspace_id: str, user_id: int, kind: str, text: str, source: str, metadata: Optional[Mapping[str, Any]] = None) -> None:
    normalized = _text(text, 8000)
    if not normalized:
        return
    now = _utcnow()
    fingerprint = _brain_fingerprint(kind, normalized)
    cursor.execute(
        """
        INSERT INTO velia_software_factory_workspace_brain (
            entry_id,workspace_id,user_id,kind,text_value,source,confidence,metadata_json,fingerprint,created_at,last_seen_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (workspace_id,fingerprint) DO UPDATE SET
            source=EXCLUDED.source,metadata_json=EXCLUDED.metadata_json,last_seen_at=EXCLUDED.last_seen_at
        """,
        (
            str(uuid.uuid4()), str(workspace_id), int(user_id), _text(kind, 80) or "fact", normalized,
            _text(source, 120) or "workspace", 1.0, _json(dict(metadata or {})), fingerprint, now, now,
        ),
    )


def create_workspace(user_id: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
    ensure_workspace_tables()
    title = _text(payload.get("title") or payload.get("name"), 200)
    objective = _text(payload.get("objective") or payload.get("goal"), 12000)
    raw_members = payload.get("repositories") or payload.get("projects") or []
    if not isinstance(raw_members, (list, tuple)) or not raw_members:
        raise SoftwareFactoryError("velia_factory_workspace_projects_required")
    if len(raw_members) > _MAX_REPOSITORIES:
        raise SoftwareFactoryError("velia_factory_workspace_projects_exceeded", detail=str(len(raw_members)))

    normalized: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for index, raw in enumerate(raw_members):
        item = dict(raw) if isinstance(raw, Mapping) else {"project_id": raw}
        project_id = _text(item.get("project_id") or item.get("id"), 160)
        if not project_id or project_id in seen:
            raise SoftwareFactoryError("velia_factory_workspace_project_invalid", detail=project_id)
        if item.get("allowed_paths") or item.get("blocked_paths") or item.get("scope_approved"):
            raise SoftwareFactoryError("velia_factory_workspace_scope_requires_separate_approval", status=409)
        project = project_service.get_project(int(user_id), project_id)
        seen.add(project_id)
        normalized.append({"project": project, "project_id": project_id, "requested_role": item.get("role"), "sort_order": index})

    primary_project_id = _text(payload.get("primary_project_id"), 160) or normalized[0]["project_id"]
    if primary_project_id not in seen:
        raise SoftwareFactoryError("velia_factory_workspace_primary_project_invalid")
    workspace_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_workspaces (
                workspace_id,user_id,title,objective,primary_project_id,status,metadata_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s)
            """,
            (workspace_id, int(user_id), title or "VELIA workspace", objective, primary_project_id, _json(dict(payload.get("metadata") or {})), now, now),
        )
        for member in normalized:
            project = member["project"]
            project_id = member["project_id"]
            role = _normalize_repo_role(
                member.get("requested_role"),
                project,
                primary=project_id == primary_project_id,
            )
            cursor.execute(
                """
                INSERT INTO velia_software_factory_workspace_projects (
                    workspace_id,project_id,user_id,repo_role,sort_order,allowed_paths_json,blocked_paths_json,
                    scope_approved,metadata_json,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,'[]','[]',FALSE,'{}',%s,%s)
                """,
                (workspace_id, project_id, int(user_id), role, int(member["sort_order"]), now, now),
            )
            _remember_cursor(
                cursor,
                workspace_id,
                int(user_id),
                "repository",
                f"{role}: {project.get('repository_full_name')} @ {project.get('selected_branch')}",
                "workspace_topology",
                {"project_id": project_id, "repo_role": role},
            )
        if objective:
            _remember_cursor(cursor, workspace_id, int(user_id), "objective", objective, "workspace_spec")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_workspace(int(user_id), workspace_id)


def get_workspace(user_id: int, workspace_id: str) -> Dict[str, Any]:
    ensure_workspace_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT workspace_id,user_id,title,objective,primary_project_id,status,metadata_json,created_at,updated_at "
            "FROM velia_software_factory_workspaces WHERE workspace_id=%s AND user_id=%s",
            (str(workspace_id), int(user_id)),
        )
        raw = cursor.fetchone()
        if not raw:
            raise SoftwareFactoryError("velia_factory_workspace_not_found", status=404)
        workspace = _workspace_row(raw)
        cursor.execute(
            "SELECT project_id,repo_role,sort_order,allowed_paths_json,blocked_paths_json,scope_approved,metadata_json "
            "FROM velia_software_factory_workspace_projects WHERE workspace_id=%s AND user_id=%s ORDER BY sort_order,project_id",
            (str(workspace_id), int(user_id)),
        )
        members = []
        for row in cursor.fetchall() or []:
            project_id = str(_value(row, "project_id", 0, ""))
            project = project_service.get_project(int(user_id), project_id)
            members.append(_member_row(row, project))
        workspace["repositories"] = members
        workspace["multi_repo"] = len(members) > 1
        workspace["all_scopes_approved"] = bool(members) and all(bool(item.get("scope_approved")) for item in members)
        return workspace
    finally:
        cursor.close()
        conn.close()


def list_workspaces(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    ensure_workspace_tables()
    safe_limit = min(100, max(1, int(limit or 50)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT workspace_id FROM velia_software_factory_workspaces WHERE user_id=%s AND status='active' "
            "ORDER BY updated_at DESC LIMIT %s",
            (int(user_id), safe_limit),
        )
        ids = [str(_value(row, "workspace_id", 0, "")) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()
    return [get_workspace(int(user_id), workspace_id) for workspace_id in ids]


def list_workspace_brain(user_id: int, workspace_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    get_workspace(int(user_id), str(workspace_id))
    safe_limit = min(300, max(1, int(limit or 100)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT entry_id,kind,text_value,source,confidence,metadata_json,created_at,last_seen_at "
            "FROM velia_software_factory_workspace_brain WHERE workspace_id=%s AND user_id=%s "
            "ORDER BY last_seen_at DESC LIMIT %s",
            (str(workspace_id), int(user_id), safe_limit),
        )
        result = []
        for row in cursor.fetchall() or []:
            result.append(
                {
                    "entry_id": str(_value(row, "entry_id", 0, "")),
                    "kind": str(_value(row, "kind", 1, "")),
                    "text": str(_value(row, "text_value", 2, "")),
                    "source": str(_value(row, "source", 3, "")),
                    "confidence": float(_value(row, "confidence", 4, 1.0) or 0.0),
                    "metadata": _loads(_value(row, "metadata_json", 5, "{}"), {}),
                    "created_at": str(_value(row, "created_at", 6, "") or ""),
                    "last_seen_at": str(_value(row, "last_seen_at", 7, "") or ""),
                }
            )
        return result
    finally:
        cursor.close()
        conn.close()


def normalize_workspace_plan(raw: Mapping[str, Any], workspace: Mapping[str, Any]) -> Dict[str, Any]:
    members = {
        str(item.get("project_id") or ""): item
        for item in workspace.get("repositories") or []
        if isinstance(item, Mapping) and str(item.get("project_id") or "")
    }
    if not members:
        raise SoftwareFactoryError("velia_factory_workspace_empty")
    tasks_raw = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    if not tasks_raw:
        raise SoftwareFactoryError("velia_factory_workspace_plan_empty")
    if len(tasks_raw) > _MAX_TASKS:
        raise SoftwareFactoryError("velia_factory_workspace_plan_tasks_exceeded", detail=str(len(tasks_raw)))

    tasks: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for index, raw_task in enumerate(tasks_raw):
        if not isinstance(raw_task, Mapping):
            raise SoftwareFactoryError("velia_factory_workspace_task_invalid")
        task_id = _slug(raw_task.get("id"), f"task-{index + 1}")
        if task_id in seen:
            raise SoftwareFactoryError("velia_factory_workspace_task_duplicate", detail=task_id)
        project_id = _text(raw_task.get("project_id"), 160)
        if project_id not in members:
            raise SoftwareFactoryError("velia_factory_workspace_task_project_invalid", detail=project_id)
        member = members[project_id]
        role = _text(raw_task.get("role"), 40).lower()
        if role not in _TASK_ROLES:
            repo_role = str(member.get("repo_role") or "shared")
            role = repo_role if repo_role in _TASK_ROLES else "fullstack"
        requested_paths = _str_list(raw_task.get("allowed_paths"), limit=50, item_limit=500)
        approved_paths = _str_list(member.get("allowed_paths"), limit=100, item_limit=500) if bool(member.get("scope_approved")) else []
        allowed_paths = [path for path in requested_paths if path in approved_paths] if requested_paths else list(approved_paths)
        tasks.append(
            {
                "id": task_id,
                "title": _text(raw_task.get("title") or task_id, 240),
                "goal": _text(raw_task.get("goal") or raw_task.get("objective") or task_id, 8000),
                "project_id": project_id,
                "repository_full_name": str(member.get("repository_full_name") or ""),
                "selected_branch": str(member.get("selected_branch") or ""),
                "role": role,
                "depends_on": _str_list(raw_task.get("depends_on"), limit=50, item_limit=100),
                "allowed_paths": allowed_paths,
                "scope_approved": bool(member.get("scope_approved")),
            }
        )
        seen.add(task_id)

    by_id = {item["id"]: item for item in tasks}
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def walk(task_id: str) -> None:
        if task_id in visiting:
            raise SoftwareFactoryError("velia_factory_workspace_dependency_cycle", detail=task_id)
        if task_id in visited:
            return
        visiting.add(task_id)
        task = by_id[task_id]
        for dependency in task["depends_on"]:
            if dependency not in by_id:
                raise SoftwareFactoryError("velia_factory_workspace_dependency_missing", detail=f"{task_id}:{dependency}")
            if dependency == task_id:
                raise SoftwareFactoryError("velia_factory_workspace_dependency_cycle", detail=task_id)
            walk(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        walk(task_id)

    return {
        "workspace_id": str(workspace.get("workspace_id") or ""),
        "objective": _text(raw.get("objective") or workspace.get("objective"), 12000),
        "tasks": tasks,
        "repositories": sorted({item["project_id"] for item in tasks}),
        "execution_ready": all(bool(item.get("scope_approved")) and bool(item.get("allowed_paths")) for item in tasks),
    }
