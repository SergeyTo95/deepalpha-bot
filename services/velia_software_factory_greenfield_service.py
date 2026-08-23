from __future__ import annotations

import json
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

from db.database import get_connection
from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_623
_MAX_REPOSITORIES = 4
_ROLE_ROOTS = {
    "fullstack": ["app", "tests", "docs"],
    "backend": ["app", "tests", "docs"],
    "frontend": ["app", "tests", "docs"],
    "android": ["android", "tests", "docs"],
}
_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def greenfield_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED", False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": greenfield_enabled(),
        "repository_creation": False,
        "repository_attach": "exact_name_after_user_continuation",
        "scope_approval": "required_before_first_write",
    }


def _json(value: Any, limit: int = 50000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


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


def ensure_greenfield_tables() -> None:
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
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_greenfield_projects (
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id) ON DELETE CASCADE,
                    repository_full_name TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    allowed_roots_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'greenfield_bootstrap',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id,project_id),
                    CHECK (profile IN ('fullstack','backend','frontend','android'))
                )
                """
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_factory_greenfield_repo "
                "ON velia_software_factory_greenfield_projects(user_id,repository_full_name)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _slug(value: str) -> str:
    text = str(value or "").strip().lower().translate(_TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    words = [word for word in text.split("-") if word and word not in {"hochu", "sozday", "sdelay", "create", "build", "make"}]
    slug = "-".join(words[:6]).strip("-")
    return (slug or "velia-project")[:60].strip("-")


def bootstrap_roles(message: str) -> List[str]:
    low = str(message or "").lower()
    has_android = any(token in low for token in ("android", "андроид", "mobile app", "мобильное приложение", "мобильн"))
    has_web = any(token in low for token in ("website", "web app", "frontend", "сайт", "веб", "магазин", "store", "shop", "marketplace", "маркетплейс"))
    explicit_backend = any(token in low for token in ("backend", "бэкенд", "бекенд", "api", "сервер"))
    explicit_frontend = any(token in low for token in ("frontend", "фронтенд"))

    if has_android and has_web:
        return ["backend", "frontend", "android"]
    if has_android:
        return ["backend", "android"]
    if explicit_backend and explicit_frontend:
        return ["backend", "frontend"]
    return ["fullstack"]


def canonical_roots(profile: str) -> List[str]:
    role = str(profile or "fullstack").lower()
    return list(_ROLE_ROOTS.get(role) or _ROLE_ROOTS["fullstack"])


def installation_options(user_id: int) -> List[Dict[str, Any]]:
    options: List[Dict[str, Any]] = []
    for raw in project_service.list_installations(int(user_id)):
        if not isinstance(raw, Mapping):
            continue
        installation_id = int(raw.get("installation_id") or 0)
        login = str(raw.get("account_login") or "").strip()
        if installation_id > 0 and login:
            options.append(
                {
                    "installation_id": installation_id,
                    "account_login": login,
                    "account_type": str(raw.get("account_type") or ""),
                }
            )
    return options[:20]


def select_installation(user_id: int, message: str = "") -> Dict[str, Any]:
    options = installation_options(int(user_id))
    if not options:
        raise SoftwareFactoryError("velia_factory_greenfield_github_installation_required", status=409)
    if len(options) == 1:
        return options[0]
    low = str(message or "").lower()
    matches = [item for item in options if str(item["account_login"]).lower() in low]
    if len(matches) == 1:
        return matches[0]
    raise SoftwareFactoryError(
        "velia_factory_greenfield_installation_choice_required",
        detail=",".join(item["account_login"] for item in options),
        status=409,
    )


def build_manifest(objective: str, installation: Mapping[str, Any]) -> Dict[str, Any]:
    roles = bootstrap_roles(objective)[:_MAX_REPOSITORIES]
    base = _slug(objective)
    account = str(installation.get("account_login") or "").strip()
    installation_id = int(installation.get("installation_id") or 0)
    if not account or installation_id <= 0:
        raise SoftwareFactoryError("velia_factory_greenfield_installation_invalid", status=409)
    repositories: List[Dict[str, Any]] = []
    multi = len(roles) > 1
    for role in roles:
        suffix = role if multi else ""
        name = f"{base}-{suffix}".strip("-")[:80].strip("-") or f"velia-{role}"
        repositories.append(
            {
                "profile": role,
                "name": name,
                "full_name": f"{account}/{name}",
                "installation_id": installation_id,
                "branch": "main",
                "recommended_roots": canonical_roots(role),
            }
        )
    return {
        "objective": str(objective or "")[:12000],
        "installation_id": installation_id,
        "account_login": account,
        "repositories": repositories,
        "requires_external_repository_creation": True,
        "auto_attach_policy": "exact_full_name_only",
        "repository_creation_performed": False,
    }


def _available_repositories(installation_id: int) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in github_service.list_installation_repositories(int(installation_id)):
        if not isinstance(raw, Mapping):
            continue
        full_name = str(raw.get("full_name") or "").strip()
        if full_name:
            result[full_name.casefold()] = dict(raw)
    return result


def missing_manifest_repositories(manifest: Mapping[str, Any]) -> List[str]:
    installation_id = int(manifest.get("installation_id") or 0)
    available = _available_repositories(installation_id)
    missing: List[str] = []
    for item in manifest.get("repositories") or []:
        if not isinstance(item, Mapping):
            continue
        full_name = str(item.get("full_name") or "").strip()
        repo = available.get(full_name.casefold())
        if not repo or bool(repo.get("archived")):
            missing.append(full_name)
    return missing


def _register_greenfield_project(user_id: int, project: Mapping[str, Any], profile: str) -> None:
    ensure_greenfield_tables()
    roots = canonical_roots(profile)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_greenfield_projects (
                user_id,project_id,repository_full_name,profile,allowed_roots_json,source,created_at
            ) VALUES (%s,%s,%s,%s,%s,'greenfield_bootstrap',NOW())
            ON CONFLICT (user_id,project_id) DO UPDATE SET
                repository_full_name=EXCLUDED.repository_full_name,
                profile=EXCLUDED.profile,
                allowed_roots_json=EXCLUDED.allowed_roots_json
            """,
            (
                int(user_id), str(project.get("id") or ""), str(project.get("repository_full_name") or ""),
                str(profile), _json(roots),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def attach_exact_repositories(user_id: int, manifest: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if not greenfield_enabled():
        raise SoftwareFactoryError("velia_factory_greenfield_disabled", status=503)
    installation_id = int(manifest.get("installation_id") or 0)
    if installation_id <= 0:
        raise SoftwareFactoryError("velia_factory_greenfield_installation_invalid", status=409)
    available = _available_repositories(installation_id)
    attached: List[Dict[str, Any]] = []
    for requirement in manifest.get("repositories") or []:
        if not isinstance(requirement, Mapping):
            continue
        full_name = str(requirement.get("full_name") or "").strip()
        profile = str(requirement.get("profile") or "fullstack").lower()
        repo = available.get(full_name.casefold())
        if not repo or bool(repo.get("archived")):
            raise SoftwareFactoryError(
                "velia_factory_greenfield_repository_missing",
                detail=full_name,
                status=409,
            )
        # Exact-name lookup is intentional: never attach a merely similar repo.
        if str(repo.get("full_name") or "").casefold() != full_name.casefold():
            raise SoftwareFactoryError("velia_factory_greenfield_repository_identity_mismatch", status=409)
        project = project_service.create_project(
            int(user_id),
            installation_id,
            repo,
            str(requirement.get("branch") or repo.get("default_branch") or "main"),
        )
        _register_greenfield_project(int(user_id), project, profile)
        project = dict(project)
        project["greenfield_profile"] = profile
        project["greenfield_roots"] = canonical_roots(profile)
        attached.append(project)
    if not attached:
        raise SoftwareFactoryError("velia_factory_greenfield_manifest_empty", status=409)
    return attached


def greenfield_roots_for_project(user_id: int, project_id: str) -> List[str]:
    ensure_greenfield_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT allowed_roots_json FROM velia_software_factory_greenfield_projects WHERE user_id=%s AND project_id=%s",
            (int(user_id), str(project_id)),
        )
        row = cursor.fetchone()
        if not row:
            return []
        roots = _loads(_value(row, "allowed_roots_json", 0, "[]"), [])
        return [str(item) for item in roots if str(item).strip()][:20]
    finally:
        cursor.close()
        conn.close()


def greenfield_project(user_id: int, project_id: str) -> Dict[str, Any]:
    ensure_greenfield_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT repository_full_name,profile,allowed_roots_json,source,created_at "
            "FROM velia_software_factory_greenfield_projects WHERE user_id=%s AND project_id=%s",
            (int(user_id), str(project_id)),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        return {
            "repository_full_name": str(_value(row, "repository_full_name", 0, "")),
            "profile": str(_value(row, "profile", 1, "")),
            "allowed_roots": _loads(_value(row, "allowed_roots_json", 2, "[]"), []),
            "source": str(_value(row, "source", 3, "")),
            "created_at": str(_value(row, "created_at", 4, "") or ""),
        }
    finally:
        cursor.close()
        conn.close()
