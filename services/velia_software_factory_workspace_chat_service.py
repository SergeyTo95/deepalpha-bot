from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from services import velia_software_factory_team_service as team_service
from services import velia_software_factory_workspace_service as workspace_service
from services.velia_software_factory_core_service import SoftwareFactoryError

_EXECUTION_ROLES = {"backend", "frontend", "android", "qa", "security", "devops", "fullstack"}
_ROLE_ORDER = ("backend", "frontend", "android")
_WEB_HINTS = {
    "web", "website", "frontend", "site", "store", "shop", "ecommerce", "e-commerce",
    "сайт", "веб", "магазин", "интернет-магазин", "интернет магазин", "витрина",
}
_ANDROID_HINTS = {
    "android", "kotlin", "compose", "mobile", "мобиль", "андроид",
}
_BACKEND_HINTS = {
    "backend", "server", "api", "database", "postgres", "бэкенд", "бекенд", "сервер", "апи",
}
_BROAD_PRODUCT_HINTS = {
    "под ключ", "полностью", "весь продукт", "весь проект", "full product", "end-to-end", "end to end",
    "с нуля", "целое приложение", "полное приложение",
}
_ALL_PROJECTS_HINTS = {
    "все доступные", "все репозитории", "используй все", "all available", "all repositories", "use all",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def workspace_chat_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_ENABLED", False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": workspace_chat_enabled(),
        "write_owner": "coding_autopilot",
        "scope_model": "per_repository_explicit_approval",
        "completion_gate": "cross_repo_integration_validation",
    }


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _compact(value: Any, limit: int = 24000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _slug(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", _text(value, 120).lower()).strip("-_")
    return (normalized or fallback)[:80]


def _contains(text: str, hints: Sequence[str] | set[str]) -> bool:
    low = str(text or "").lower()
    return any(hint in low for hint in hints)


def _project_role(project: Mapping[str, Any]) -> str:
    return workspace_service.infer_repository_role(project, primary=False)


def _project_name(project: Mapping[str, Any]) -> str:
    return str(project.get("repository_full_name") or "")


def explicit_projects(message: str, projects: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    low = str(message or "").lower()
    matches: List[Dict[str, Any]] = []
    for raw in projects:
        project = dict(raw)
        full = _project_name(project).lower()
        short = full.rsplit("/", 1)[-1]
        if full and full in low:
            matches.append(project)
            continue
        if len(short) >= 4 and short in low:
            matches.append(project)
    return matches


def required_roles(message: str) -> List[str]:
    roles: List[str] = []
    if _contains(message, _WEB_HINTS):
        roles.extend(["backend", "frontend"])
    if _contains(message, _ANDROID_HINTS):
        if "backend" not in roles:
            roles.append("backend")
        roles.append("android")
    if _contains(message, _BACKEND_HINTS) and "backend" not in roles:
        roles.append("backend")
    return [role for role in _ROLE_ORDER if role in roles]


def select_workspace_projects(message: str, projects: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    available = [dict(item) for item in projects if isinstance(item, Mapping) and not bool(item.get("archived"))]
    if len(available) < 2:
        return {"status": "single", "projects": available[:1], "required_roles": []}

    explicit = explicit_projects(message, available)
    if len(explicit) >= 2:
        return {"status": "selected", "projects": explicit[:8], "required_roles": []}
    if len(explicit) == 1:
        return {"status": "single", "projects": explicit, "required_roles": []}

    roles = required_roles(message)
    if not roles and _contains(message, _BROAD_PRODUCT_HINTS):
        present = {_project_role(item) for item in available}
        roles = [role for role in _ROLE_ORDER if role in present]

    if not roles:
        return {"status": "single", "projects": [], "required_roles": []}

    by_role: Dict[str, List[Dict[str, Any]]] = {role: [] for role in roles}
    for project in available:
        role = _project_role(project)
        if role in by_role:
            by_role[role].append(project)

    missing = [role for role, items in by_role.items() if not items]
    if missing:
        return {
            "status": "missing_roles",
            "projects": [],
            "required_roles": roles,
            "missing_roles": missing,
            "candidates": available[:8],
        }
    ambiguous = {role: items for role, items in by_role.items() if len(items) > 1}
    if ambiguous:
        return {
            "status": "ambiguous",
            "projects": [],
            "required_roles": roles,
            "ambiguous_roles": {role: [_project_name(item) for item in items[:8]] for role, items in ambiguous.items()},
            "candidates": available[:8],
        }
    selected = [by_role[role][0] for role in roles]
    if len(selected) < 2:
        return {"status": "single", "projects": selected, "required_roles": roles}
    return {"status": "selected", "projects": selected[:8], "required_roles": roles}


def resolve_repository_choice(message: str, projects: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    available = [dict(item) for item in projects if isinstance(item, Mapping) and not bool(item.get("archived"))]
    explicit = explicit_projects(message, available)
    if len(explicit) >= 2:
        return explicit[:8]
    if _contains(message, _ALL_PROJECTS_HINTS):
        return available[:8]

    low = str(message or "").lower()
    selected: List[Dict[str, Any]] = []
    role_words = {
        "backend": ("backend", "бэкенд", "бекенд", "server", "сервер"),
        "frontend": ("frontend", "web", "веб", "сайт", "website"),
        "android": ("android", "андроид", "mobile", "мобиль"),
    }
    for role, words in role_words.items():
        if not any(word in low for word in words):
            continue
        matches = [item for item in available if _project_role(item) == role]
        if len(matches) == 1 and matches[0] not in selected:
            selected.append(matches[0])
    return selected[:8] if len(selected) >= 2 else []


def is_workspace_candidate(message: str, projects: Sequence[Mapping[str, Any]]) -> bool:
    result = select_workspace_projects(message, projects)
    return str(result.get("status") or "") in {"selected", "missing_roles", "ambiguous"}


def _fallback_plan(objective: str, workspace: Mapping[str, Any], reason: str = "") -> Dict[str, Any]:
    members = [dict(item) for item in workspace.get("repositories") or [] if isinstance(item, Mapping)]
    tasks: List[Dict[str, Any]] = []
    provider_id = ""
    for index, member in enumerate(members, start=1):
        role = str(member.get("repo_role") or "fullstack")
        if role not in _EXECUTION_ROLES:
            role = "fullstack"
        task_id = _slug(f"{role}-{index}", f"task-{index}")
        if not provider_id and role == "backend":
            provider_id = task_id
        tasks.append(
            {
                "id": task_id,
                "title": f"Implement {role} part of the product",
                "goal": objective,
                "project_id": str(member.get("project_id") or ""),
                "role": role,
                "depends_on": [],
            }
        )
    if not provider_id and tasks:
        provider_id = str(tasks[0]["id"])
    provider_project = next((str(item.get("project_id") or "") for item in tasks if str(item.get("id") or "") == provider_id), "")
    for task in tasks:
        if str(task.get("id") or "") == provider_id:
            continue
        if str(task.get("project_id") or "") != provider_project:
            task["depends_on"] = [provider_id]
    return {
        "objective": objective,
        "tasks": tasks,
        "acceptance_criteria": [
            "Each repository change is review-ready and its exact-head CI is green.",
            "Cross-repository interfaces are compatible before workspace completion.",
        ],
        "planner_mode": "deterministic_fallback",
        "planner_reason": reason,
    }


def _has_cycle(tasks: Sequence[Mapping[str, Any]]) -> bool:
    by_id = {str(item.get("id") or ""): item for item in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        for dependency in by_id.get(task_id, {}).get("depends_on") or []:
            if str(dependency) in by_id and walk(str(dependency)):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    return any(walk(task_id) for task_id in by_id)


def _normalize_plan(raw: Mapping[str, Any], objective: str, workspace: Mapping[str, Any]) -> Dict[str, Any]:
    members = {
        str(item.get("project_id") or ""): dict(item)
        for item in workspace.get("repositories") or []
        if isinstance(item, Mapping) and str(item.get("project_id") or "")
    }
    if len(members) < 2:
        raise SoftwareFactoryError("velia_factory_workspace_chat_requires_multi_repo", status=409)

    staged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("tasks") or []):
        if not isinstance(item, Mapping) or len(staged) >= 16:
            continue
        project_id = _text(item.get("project_id"), 160)
        if project_id not in members:
            continue
        task_id = _slug(item.get("id"), f"task-{index + 1}")
        if task_id in seen:
            continue
        seen.add(task_id)
        member = members[project_id]
        role = _text(item.get("role"), 40).lower()
        if role not in _EXECUTION_ROLES:
            repo_role = str(member.get("repo_role") or "")
            role = repo_role if repo_role in _EXECUTION_ROLES else "fullstack"
        staged.append(
            {
                "id": task_id,
                "title": _text(item.get("title") or task_id, 240),
                "goal": _text(item.get("goal") or objective, 7000),
                "project_id": project_id,
                "role": role,
                "depends_on": [str(dep) for dep in item.get("depends_on") or [] if str(dep).strip()][:30],
            }
        )

    existing_projects = {str(item.get("project_id") or "") for item in staged}
    for project_id, member in members.items():
        if project_id in existing_projects:
            continue
        role = str(member.get("repo_role") or "fullstack")
        if role not in _EXECUTION_ROLES:
            role = "fullstack"
        task_id = _slug(f"{role}-{len(staged) + 1}", f"task-{len(staged) + 1}")
        while task_id in seen:
            task_id = _slug(f"{task_id}-x", f"task-{len(staged) + 1}")
        seen.add(task_id)
        staged.append(
            {
                "id": task_id,
                "title": f"Implement {role} part of the product",
                "goal": objective,
                "project_id": project_id,
                "role": role,
                "depends_on": [],
            }
        )

    valid_ids = {str(item["id"]) for item in staged}
    for item in staged:
        item["depends_on"] = [
            dep for dep in item.get("depends_on") or []
            if dep in valid_ids and dep != item["id"]
        ]

    backend = next((item for item in staged if str(item.get("role") or "") == "backend"), None)
    provider = backend or (staged[0] if staged else None)
    if provider:
        provider_id = str(provider["id"])
        provider_project = str(provider["project_id"])
        for project_id in members:
            if project_id == provider_project:
                continue
            first = next((item for item in staged if str(item.get("project_id") or "") == project_id), None)
            if first and provider_id not in first["depends_on"]:
                first["depends_on"].append(provider_id)

    if not staged or _has_cycle(staged):
        raise SoftwareFactoryError("velia_factory_workspace_chat_plan_invalid", status=502)

    acceptance = []
    for raw_item in raw.get("acceptance_criteria") or []:
        item = _text(raw_item, 1000)
        if item and item not in acceptance:
            acceptance.append(item)
        if len(acceptance) >= 20:
            break
    for required in (
        "Each repository change is review-ready and its exact-head CI is green.",
        "Cross-repository interfaces are compatible before workspace completion.",
    ):
        if required not in acceptance:
            acceptance.append(required)

    return {
        "objective": objective,
        "tasks": staged,
        "acceptance_criteria": acceptance,
        "planner_mode": "llm",
    }


def build_workspace_plan(
    objective: str,
    workspace: Mapping[str, Any],
    *,
    user_id: int,
    request_id: str,
    generator: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    members = [
        {
            "project_id": str(item.get("project_id") or ""),
            "repository_full_name": str(item.get("repository_full_name") or ""),
            "selected_branch": str(item.get("selected_branch") or ""),
            "repo_role": str(item.get("repo_role") or "shared"),
        }
        for item in workspace.get("repositories") or []
        if isinstance(item, Mapping)
    ]
    prompt = (
        "You are VELIA Software Factory's cross-repository Technical Planner. Return ONLY one valid JSON object. "
        "Split the product objective across the supplied repositories. Use only supplied project_id values. "
        "Never propose merge, deploy, credentials, repository creation, write scopes, allowed_paths or blocked_paths. "
        "Do not invent repositories. Prefer the smallest dependency-safe plan and make cross-repository interface dependencies explicit. "
        "Use only roles backend, frontend, android, qa, security, devops, fullstack.\n\n"
        "JSON schema:\n"
        '{"tasks":[{"id":"...","title":"...","goal":"...","project_id":"...","role":"backend|frontend|android|qa|security|devops|fullstack","depends_on":["..."]}],"acceptance_criteria":["..."]}\n\n'
        f"Objective: {_text(objective, 12000)}\nRepositories: {_compact(members, 12000)}"
    )
    generate = generator or team_service._default_generator(
        "software_factory_planner", int(user_id), str(request_id or "workspace-chat"), 2600
    )
    try:
        raw = team_service._extract_json_object(generate(prompt))
        return _normalize_plan(raw, _text(objective, 12000), workspace)
    except Exception as exc:
        return _fallback_plan(_text(objective, 12000), workspace, exc.__class__.__name__)
