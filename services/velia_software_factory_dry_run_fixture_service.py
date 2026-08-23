from __future__ import annotations

import hashlib
import os
from typing import Any, Dict

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services.velia_software_factory_core_service import SoftwareFactoryError

_FIXTURE_ACTOR_BASE = 8_100_000_000_000_000_000
_FIXTURE_INSTALLATION_BASE = 8_200_000_000_000_000_000
_FIXTURE_REPOSITORY_BASE = 8_300_000_000_000_000_000
_FIXTURE_SPAN = 1_000_000_000_000


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def fixture_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_FIXTURE_ENABLED", False)


def _environment_name() -> str:
    return str(os.getenv("RAILWAY_ENVIRONMENT_NAME", "") or "").strip().lower()


def _environment_id() -> str:
    return str(os.getenv("RAILWAY_ENVIRONMENT_ID", "") or "").strip()


def _assert_preview_only() -> None:
    if not fixture_enabled():
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_fixture_disabled", status=503)
    name = _environment_name()
    if not name or "-pr-" not in name:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_fixture_preview_required", status=409)
    if not _environment_id():
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_fixture_environment_required", status=409)


def _stable_id(base: int, label: str, repository: str) -> int:
    seed = f"stage6.1:{_environment_id()}:{repository.casefold()}:{label}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    offset = int.from_bytes(digest[:8], "big") % _FIXTURE_SPAN
    return int(base + offset)


def _fixture_project_id(repository: str) -> str:
    seed = f"stage6.1:{_environment_id()}:{repository.casefold()}:project"
    return "velia-stage61-fixture-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _branch() -> str:
    return str(
        os.getenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_BRANCH")
        or os.getenv("API_COMMERCIAL_PRODUCTION_BRANCH")
        or "main"
    ).strip()[:200] or "main"


def ensure_fixture(repository: str) -> Dict[str, Any]:
    _assert_preview_only()
    repository = str(repository or "").strip()[:240]
    if "/" not in repository:
        raise SoftwareFactoryError("velia_factory_dry_run_acceptance_repository_required", status=409)

    actor_id = _stable_id(_FIXTURE_ACTOR_BASE, "actor", repository)
    installation_id = _stable_id(_FIXTURE_INSTALLATION_BASE, "installation", repository)
    repository_id = _stable_id(_FIXTURE_REPOSITORY_BASE, "repository", repository)
    project_id = _fixture_project_id(repository)
    branch = _branch()

    project_service.ensure_developer_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT user_id,account_login,deleted_at FROM velia_developer_installations WHERE installation_id=%s",
            (installation_id,),
        )
        installation = cursor.fetchone()
        if installation:
            if int(installation[0] or 0) != actor_id or installation[2] is not None:
                raise SoftwareFactoryError("velia_factory_dry_run_acceptance_fixture_id_collision", status=409)
        else:
            cursor.execute(
                """
                INSERT INTO velia_developer_installations (
                    installation_id,user_id,account_login,account_type,repository_selection,
                    created_at,updated_at,deleted_at
                ) VALUES (%s,%s,%s,'User','acceptance_fixture',NOW(),NOW(),NULL)
                """,
                (installation_id, actor_id, "velia-stage61-preview"),
            )

        cursor.execute(
            """
            SELECT project_id,user_id,installation_id,repository_id,repository_full_name,
                   selected_branch,is_archived,deleted_at
            FROM velia_developer_projects
            WHERE project_id=%s
            """,
            (project_id,),
        )
        project = cursor.fetchone()
        if project:
            if (
                int(project[1] or 0) != actor_id
                or int(project[2] or 0) != installation_id
                or int(project[3] or 0) != repository_id
                or str(project[4] or "").casefold() != repository.casefold()
                or bool(project[6])
                or project[7] is not None
            ):
                raise SoftwareFactoryError("velia_factory_dry_run_acceptance_fixture_id_collision", status=409)
        else:
            cursor.execute(
                """
                SELECT project_id,repository_full_name
                FROM velia_developer_projects
                WHERE user_id=%s AND repository_id=%s AND deleted_at IS NULL
                """,
                (actor_id, repository_id),
            )
            conflicting = cursor.fetchone()
            if conflicting:
                raise SoftwareFactoryError("velia_factory_dry_run_acceptance_fixture_id_collision", status=409)
            cursor.execute(
                """
                INSERT INTO velia_developer_projects (
                    project_id,user_id,installation_id,repository_id,repository_full_name,
                    default_branch,selected_branch,is_private,is_archived,created_at,updated_at,deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE,FALSE,NOW(),NOW(),NULL)
                """,
                (
                    project_id,
                    actor_id,
                    installation_id,
                    repository_id,
                    repository,
                    branch,
                    branch,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return {
        "actor_id": actor_id,
        "project_id": project_id,
        "installation_id": installation_id,
        "repository_id": repository_id,
        "repository_full_name": repository,
        "selected_branch": branch,
        "fixture": True,
    }


def tree_loader(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Deterministic read-only tree used only by Stage 6.1 preview fixture scope discovery."""
    return {
        "entries": [
            {"path": "services/stage61_acceptance.py", "type": "blob"},
            {"path": "tests/test_stage61_acceptance.py", "type": "blob"},
            {"path": "docs/stage61_acceptance.md", "type": "blob"},
        ]
    }
