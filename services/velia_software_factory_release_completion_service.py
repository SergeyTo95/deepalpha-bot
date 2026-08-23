from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from typing import Any, Dict, List, Mapping, Sequence

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services import velia_software_factory_deployment_observer_service as deployment
from services import velia_software_factory_deployment_status_github_service as status_github
from services import velia_software_factory_release_post_merge_service as post_merge
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_761
_MODE = "evidence_certificate"
_CERTIFICATE_STATES = {"complete", "pending", "failed", "blocked"}
_MAX_CONTEXTS = 24


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def completion_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED", False)


def public_status() -> Dict[str, Any]:
    enabled = completion_enabled()
    return {
        "available": True,
        "enabled": enabled,
        "mode": _MODE,
        "full_verified_release_required": True,
        "deployment_observation_success_required": True,
        "acceptance_profile_required": True,
        "exact_acceptance_context_match": True,
        "deployment_contexts_cannot_count_as_acceptance": True,
        "railway_status_cannot_count_as_acceptance": True,
        "exact_merge_commit_required": True,
        "append_only_certificate": True,
        "arbitrary_http_probe_supported": False,
        "github_write_supported": False,
        "deployment_trigger_supported": False,
        "revert_supported": False,
        "release_complete_supported": enabled,
        "deployment_supported": False,
    }


def _utcnow():
    return post_merge._utcnow()


def _json(value: Any, limit: int = 220000) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _dict_cursor(conn):
    try:
        import psycopg2.extras

        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        return conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _normalize_contexts(values: Sequence[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for raw in values or []:
        text = str(raw or "").replace("\x00", "").strip()
        if not text:
            continue
        if len(text) > 240:
            raise SoftwareFactoryError(
                "velia_factory_acceptance_context_too_long", status=400
            )
        if any(token in text for token in ("*", "?", "[", "]")):
            raise SoftwareFactoryError(
                "velia_factory_acceptance_context_must_be_exact",
                detail=text[:240],
                status=400,
            )
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    if not result:
        raise SoftwareFactoryError(
            "velia_factory_acceptance_contexts_required", status=400
        )
    if len(result) > _MAX_CONTEXTS:
        raise SoftwareFactoryError(
            "velia_factory_acceptance_context_limit_exceeded",
            detail=str(len(result)),
            status=400,
        )
    return sorted(result, key=str.casefold)


def ensure_completion_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    post_merge.ensure_post_merge_tables(execution_module)
    deployment.ensure_deployment_observer_tables(execution_module)
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_acceptance_profiles (
                    profile_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id) ON DELETE RESTRICT,
                    repository_full_name TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    expected_contexts_json TEXT NOT NULL DEFAULT '[]',
                    profile_fingerprint TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id,project_id,branch)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_completion_certificates (
                    certificate_id TEXT PRIMARY KEY,
                    verification_id TEXT NOT NULL REFERENCES velia_software_factory_release_verifications(verification_id) ON DELETE RESTRICT,
                    deployment_observation_id TEXT NOT NULL REFERENCES velia_software_factory_deployment_observations(observation_id) ON DELETE RESTRICT,
                    release_execution_id TEXT NOT NULL REFERENCES velia_software_factory_release_executions(execution_id) ON DELETE RESTRICT,
                    user_id BIGINT NOT NULL,
                    certificate_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id,verification_id,deployment_observation_id,certificate_fingerprint),
                    CHECK (status IN ('complete','pending','failed','blocked'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_release_completion_execution "
                "ON velia_software_factory_release_completion_certificates(user_id,release_execution_id,created_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _require_user(user_id: int) -> None:
    if not completion_enabled():
        raise SoftwareFactoryError(
            "velia_factory_release_completion_disabled", status=503
        )
    if not post_merge.verification_enabled():
        raise SoftwareFactoryError(
            "velia_factory_release_verification_disabled", status=503
        )
    if not deployment.deployment_observer_enabled():
        raise SoftwareFactoryError(
            "velia_factory_deployment_observer_disabled", status=503
        )
    if not rollout.intake_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)


def _profile_row(row: Any) -> Dict[str, Any]:
    contexts = _loads(_value(row, "expected_contexts_json", 5, "[]"), [])
    if not isinstance(contexts, list):
        contexts = []
    return {
        "profile_id": str(_value(row, "profile_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "project_id": str(_value(row, "project_id", 2, "")),
        "repository_full_name": str(_value(row, "repository_full_name", 3, "")),
        "branch": str(_value(row, "branch", 4, "")),
        "expected_contexts": [
            str(item) for item in contexts if str(item or "").strip()
        ],
        "profile_fingerprint": str(_value(row, "profile_fingerprint", 6, "")),
        "enabled": bool(_value(row, "enabled", 7, False)),
        "created_at": str(_value(row, "created_at", 8, "") or ""),
        "updated_at": str(_value(row, "updated_at", 9, "") or ""),
    }


def _project_identity(user_id: int, project_id: str) -> Dict[str, Any]:
    project = project_service.get_project(int(user_id), str(project_id))
    repository = str(project.get("repository_full_name") or "").strip()
    branch = str(project.get("selected_branch") or "").strip()
    if not repository or not branch:
        raise SoftwareFactoryError(
            "velia_factory_acceptance_project_identity_missing", status=409
        )
    return project


def configure_acceptance_profile(
    execution_module: Any,
    user_id: int,
    project_id: str,
    *,
    branch: str,
    expected_contexts: Sequence[Any],
    enabled: bool = True,
) -> Dict[str, Any]:
    _require_user(int(user_id))
    ensure_completion_tables(execution_module)
    project = _project_identity(int(user_id), str(project_id))
    repository = str(project.get("repository_full_name") or "").strip()
    selected_branch = str(project.get("selected_branch") or "").strip()
    target_branch = str(branch or "").strip()
    if target_branch != selected_branch:
        raise SoftwareFactoryError(
            "velia_factory_acceptance_profile_branch_mismatch",
            detail=f"selected={selected_branch} requested={target_branch}",
            status=409,
        )
    contexts = _normalize_contexts(expected_contexts)
    deployment_profile = deployment.get_profile(
        execution_module,
        int(user_id),
        str(project_id),
        target_branch,
        require_enabled=True,
    )
    deployment_contexts = {
        str(item).casefold()
        for item in deployment_profile.get("expected_contexts") or []
        if str(item or "").strip()
    }
    overlap = [item for item in contexts if item.casefold() in deployment_contexts]
    if overlap:
        raise SoftwareFactoryError(
            "velia_factory_acceptance_context_overlaps_deployment",
            detail=",".join(overlap)[:500],
            status=409,
        )
    fingerprint = _fingerprint(
        {
            "project_id": str(project_id),
            "repository_full_name": repository,
            "branch": target_branch,
            "expected_contexts": contexts,
            "deployment_profile_fingerprint": str(
                deployment_profile.get("profile_fingerprint") or ""
            ),
            "enabled": bool(enabled),
        }
    )
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        profile_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_acceptance_profiles (
                profile_id,user_id,project_id,repository_full_name,branch,
                expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,project_id,branch) DO UPDATE SET
                repository_full_name=EXCLUDED.repository_full_name,
                expected_contexts_json=EXCLUDED.expected_contexts_json,
                profile_fingerprint=EXCLUDED.profile_fingerprint,
                enabled=EXCLUDED.enabled,
                updated_at=EXCLUDED.updated_at
            RETURNING profile_id,user_id,project_id,repository_full_name,branch,
                expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at
            """,
            (
                profile_id,
                int(user_id),
                str(project_id),
                repository,
                target_branch,
                _json(contexts, 12000),
                fingerprint,
                bool(enabled),
                _utcnow(),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _profile_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_acceptance_profile(
    execution_module: Any,
    user_id: int,
    project_id: str,
    branch: str,
    *,
    require_enabled: bool = False,
) -> Dict[str, Any]:
    ensure_completion_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT profile_id,user_id,project_id,repository_full_name,branch,"
            "expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at "
            "FROM velia_software_factory_acceptance_profiles "
            "WHERE user_id=%s AND project_id=%s AND branch=%s",
            (int(user_id), str(project_id), str(branch)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError(
                "velia_factory_acceptance_profile_not_found", status=404
            )
        result = _profile_row(row)
        if require_enabled and not bool(result.get("enabled")):
            raise SoftwareFactoryError(
                "velia_factory_acceptance_profile_disabled", status=409
            )
        _normalize_contexts(result.get("expected_contexts") or [])
        return result
    finally:
        cursor.close()
        conn.close()


def list_acceptance_profiles(
    execution_module: Any, user_id: int
) -> List[Dict[str, Any]]:
    ensure_completion_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT profile_id,user_id,project_id,repository_full_name,branch,"
            "expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at "
            "FROM velia_software_factory_acceptance_profiles "
            "WHERE user_id=%s ORDER BY updated_at DESC",
            (int(user_id),),
        )
        return [_profile_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def _evaluate_acceptance_contexts(
    profile: Mapping[str, Any], status_snapshot: Mapping[str, Any]
) -> Dict[str, Any]:
    expected = _normalize_contexts(profile.get("expected_contexts") or [])
    statuses = {
        str(item.get("context") or ""): dict(item)
        for item in status_snapshot.get("statuses") or []
        if isinstance(item, Mapping) and str(item.get("context") or "").strip()
    }
    matched: List[Dict[str, Any]] = []
    missing: List[str] = []
    failing: List[str] = []
    waiting: List[str] = []
    railway_targets: List[str] = []
    allowed_railway_hosts = set(status_github._RAILWAY_HOSTS)
    for context in expected:
        item = statuses.get(context)
        if item is None:
            missing.append(context)
            continue
        state = str(item.get("state") or "").strip().lower()
        target_url = str(item.get("target_url") or "")[:1000]
        target_host = status_github._target_host(target_url)
        matched.append(
            {
                "context": context,
                "state": state,
                "description": str(item.get("description") or "")[:500],
                "target_url": target_url,
                "target_host": target_host,
                "updated_at": str(item.get("updated_at") or "")[:80],
            }
        )
        if target_host in allowed_railway_hosts:
            railway_targets.append(context)
            continue
        if state in {"failure", "error"}:
            failing.append(context)
        elif state != "success":
            waiting.append(context)
    if railway_targets:
        status = "failed"
    elif failing:
        status = "failed"
    elif missing or waiting:
        status = "pending"
    else:
        status = "success"
    return {
        "status": status,
        "expected_contexts": expected,
        "matched_contexts": matched,
        "missing_contexts": missing,
        "failing_contexts": failing,
        "waiting_contexts": waiting,
        "railway_target_contexts": railway_targets,
    }


def _verified_item_project(
    user_id: int, item: Mapping[str, Any]
) -> Dict[str, Any]:
    project_id = str(item.get("project_id") or "")
    project = _project_identity(int(user_id), project_id)
    actual_repo = str(project.get("repository_full_name") or "").strip().casefold()
    actual_branch = str(project.get("selected_branch") or "").strip()
    expected_repo = str(item.get("repository_full_name") or "").strip().casefold()
    expected_branch = str(item.get("base_branch") or "").strip()
    if actual_repo != expected_repo:
        raise SoftwareFactoryError(
            "velia_factory_acceptance_repository_identity_changed",
            detail=project_id,
            status=409,
        )
    if actual_branch != expected_branch:
        raise SoftwareFactoryError(
            "velia_factory_acceptance_branch_identity_changed",
            detail=f"expected={expected_branch} actual={actual_branch}",
            status=409,
        )
    return project


def discover_acceptance_contexts(
    execution_module: Any,
    user_id: int,
    verification_id: str,
) -> Dict[str, Any]:
    _require_user(int(user_id))
    verification = post_merge.get_verification(
        execution_module, int(user_id), str(verification_id)
    )
    if str(verification.get("verification_status") or "") != "verified":
        raise SoftwareFactoryError(
            "velia_factory_release_completion_requires_full_verified_release",
            detail=str(verification.get("verification_status") or ""),
            status=409,
        )
    verified_merges = [
        dict(item)
        for item in verification.get("verified_merges") or []
        if isinstance(item, Mapping)
    ]
    if not verified_merges:
        raise SoftwareFactoryError(
            "velia_factory_release_completion_verified_merges_missing", status=409
        )
    repositories: List[Dict[str, Any]] = []
    for item in verified_merges:
        project = _verified_item_project(int(user_id), item)
        branch = str(item.get("base_branch") or "")
        deploy_profile = deployment.get_profile(
            execution_module,
            int(user_id),
            str(item.get("project_id") or ""),
            branch,
            require_enabled=True,
        )
        deploy_contexts = {
            str(context).casefold()
            for context in deploy_profile.get("expected_contexts") or []
        }
        status_snapshot = status_github.commit_status_snapshot(
            project, str(item.get("merge_commit_sha") or "")
        )
        candidates = []
        for status in status_snapshot.get("statuses") or []:
            if not isinstance(status, Mapping):
                continue
            context = str(status.get("context") or "").strip()
            if not context or context.casefold() in deploy_contexts:
                continue
            if status_github._target_host(status.get("target_url")) in status_github._RAILWAY_HOSTS:
                continue
            candidates.append(
                {
                    "context": context,
                    "state": str(status.get("state") or ""),
                    "target_url": str(status.get("target_url") or ""),
                }
            )
        repositories.append(
            {
                "project_id": str(item.get("project_id") or ""),
                "repository_full_name": str(item.get("repository_full_name") or ""),
                "branch": branch,
                "merge_commit_sha": str(item.get("merge_commit_sha") or ""),
                "acceptance_candidates": candidates,
            }
        )
    return {
        "verification_id": str(verification_id),
        "suggested_only": True,
        "explicit_profile_confirmation_required": True,
        "arbitrary_http_probe_supported": False,
        "repositories": repositories,
    }


def build_completion_snapshot(
    execution_module: Any,
    user_id: int,
    verification_id: str,
    deployment_observation_id: str,
) -> Dict[str, Any]:
    _require_user(int(user_id))
    verification = post_merge.get_verification(
        execution_module, int(user_id), str(verification_id)
    )
    verification_status = str(verification.get("verification_status") or "")
    if verification_status != "verified":
        raise SoftwareFactoryError(
            "velia_factory_release_completion_requires_full_verified_release",
            detail=verification_status,
            status=409,
        )
    verified_merges = [
        dict(item)
        for item in verification.get("verified_merges") or []
        if isinstance(item, Mapping)
    ]
    if not verified_merges:
        raise SoftwareFactoryError(
            "velia_factory_release_completion_verified_merges_missing", status=409
        )

    observation = deployment.get_observation(
        execution_module, int(user_id), str(deployment_observation_id)
    )
    if str(observation.get("verification_id") or "") != str(verification_id):
        raise SoftwareFactoryError(
            "velia_factory_release_completion_deployment_verification_mismatch",
            status=409,
        )
    release_execution_id = str(verification.get("release_execution_id") or "")
    if str(observation.get("release_execution_id") or "") != release_execution_id:
        raise SoftwareFactoryError(
            "velia_factory_release_completion_execution_mismatch", status=409
        )
    if (
        str(observation.get("status") or "") != "success"
        or not bool(observation.get("deployment_complete"))
    ):
        raise SoftwareFactoryError(
            "velia_factory_release_completion_deployment_not_complete",
            detail=str(observation.get("status") or ""),
            status=409,
        )

    repositories: List[Dict[str, Any]] = []
    blockers: List[Dict[str, str]] = []
    for item in verified_merges:
        project_id = str(item.get("project_id") or "")
        repository = str(item.get("repository_full_name") or "")
        branch = str(item.get("base_branch") or "")
        merge_sha = str(item.get("merge_commit_sha") or "")
        try:
            project = _verified_item_project(int(user_id), item)
            acceptance_profile = get_acceptance_profile(
                execution_module,
                int(user_id),
                project_id,
                branch,
                require_enabled=True,
            )
            deployment_profile = deployment.get_profile(
                execution_module,
                int(user_id),
                project_id,
                branch,
                require_enabled=True,
            )
            deployment_contexts = {
                str(context).casefold()
                for context in deployment_profile.get("expected_contexts") or []
            }
            acceptance_contexts = _normalize_contexts(
                acceptance_profile.get("expected_contexts") or []
            )
            overlap = [
                context
                for context in acceptance_contexts
                if context.casefold() in deployment_contexts
            ]
            if overlap:
                raise SoftwareFactoryError(
                    "velia_factory_acceptance_context_overlaps_deployment",
                    detail=",".join(overlap)[:500],
                    status=409,
                )
            status_snapshot = status_github.commit_status_snapshot(project, merge_sha)
            evaluation = _evaluate_acceptance_contexts(
                acceptance_profile, status_snapshot
            )
            repositories.append(
                {
                    "project_id": project_id,
                    "repository_full_name": repository,
                    "branch": branch,
                    "merge_commit_sha": merge_sha,
                    "acceptance_profile_id": str(
                        acceptance_profile.get("profile_id") or ""
                    ),
                    "acceptance_profile_fingerprint": str(
                        acceptance_profile.get("profile_fingerprint") or ""
                    ),
                    "deployment_profile_fingerprint": str(
                        deployment_profile.get("profile_fingerprint") or ""
                    ),
                    "combined_state": str(
                        status_snapshot.get("combined_state") or ""
                    ),
                    **evaluation,
                }
            )
        except Exception as exc:
            blockers.append(
                {
                    "project_id": project_id,
                    "repository_full_name": repository,
                    "code": str(
                        getattr(exc, "code", exc.__class__.__name__)
                    )[:160],
                    "detail": str(
                        getattr(exc, "detail", str(exc)) or ""
                    )[:1000],
                }
            )

    if blockers:
        status = "blocked"
    elif len(repositories) != len(verified_merges):
        status = "blocked"
    elif any(str(item.get("status") or "") == "failed" for item in repositories):
        status = "failed"
    elif any(str(item.get("status") or "") != "success" for item in repositories):
        status = "pending"
    else:
        status = "complete"

    snapshot = {
        "verification_id": str(verification_id),
        "deployment_observation_id": str(deployment_observation_id),
        "release_execution_id": release_execution_id,
        "verification_fingerprint": str(
            verification.get("verification_fingerprint") or ""
        ),
        "deployment_observation_fingerprint": str(
            observation.get("observation_fingerprint") or ""
        ),
        "status": status,
        "release_complete": status == "complete",
        "repositories": repositories,
        "blockers": blockers,
        "expected_repository_count": len(verified_merges),
        "observed_repository_count": len(repositories),
        "arbitrary_http_probe_supported": False,
        "github_write_supported": False,
        "deployment_triggered": False,
        "deployment_supported": False,
        "revert_supported": False,
        "evaluated_at": _utcnow().isoformat() + "Z",
    }
    snapshot["certificate_fingerprint"] = _fingerprint(
        {
            "verification_id": snapshot["verification_id"],
            "verification_fingerprint": snapshot["verification_fingerprint"],
            "deployment_observation_id": snapshot["deployment_observation_id"],
            "deployment_observation_fingerprint": snapshot[
                "deployment_observation_fingerprint"
            ],
            "status": status,
            "repositories": repositories,
            "blockers": blockers,
        }
    )
    return snapshot


def _certificate_row(row: Any) -> Dict[str, Any]:
    snapshot = _loads(_value(row, "snapshot_json", 7, "{}"), {})
    result = dict(snapshot) if isinstance(snapshot, dict) else {}
    result.update(
        {
            "certificate_id": str(_value(row, "certificate_id", 0, "")),
            "verification_id": str(_value(row, "verification_id", 1, "")),
            "deployment_observation_id": str(
                _value(row, "deployment_observation_id", 2, "")
            ),
            "release_execution_id": str(
                _value(row, "release_execution_id", 3, "")
            ),
            "user_id": int(_value(row, "user_id", 4, 0) or 0),
            "certificate_fingerprint": str(
                _value(row, "certificate_fingerprint", 5, "")
            ),
            "status": str(
                _value(row, "status", 6, result.get("status") or "blocked")
            ),
            "created_at": str(_value(row, "created_at", 8, "") or ""),
        }
    )
    return result


def persist_completion_certificate(
    execution_module: Any,
    user_id: int,
    snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    ensure_completion_tables(execution_module)
    status = str(snapshot.get("status") or "")
    if status not in _CERTIFICATE_STATES:
        raise SoftwareFactoryError(
            "velia_factory_release_completion_status_invalid", status=500
        )
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        certificate_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_release_completion_certificates (
                certificate_id,verification_id,deployment_observation_id,
                release_execution_id,user_id,certificate_fingerprint,status,
                snapshot_json,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,verification_id,deployment_observation_id,certificate_fingerprint) DO NOTHING
            RETURNING certificate_id,verification_id,deployment_observation_id,
                release_execution_id,user_id,certificate_fingerprint,status,
                snapshot_json,created_at
            """,
            (
                certificate_id,
                str(snapshot.get("verification_id") or ""),
                str(snapshot.get("deployment_observation_id") or ""),
                str(snapshot.get("release_execution_id") or ""),
                int(user_id),
                str(snapshot.get("certificate_fingerprint") or ""),
                status,
                _json(dict(snapshot)),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "SELECT certificate_id,verification_id,deployment_observation_id,"
                "release_execution_id,user_id,certificate_fingerprint,status,snapshot_json,created_at "
                "FROM velia_software_factory_release_completion_certificates "
                "WHERE user_id=%s AND verification_id=%s AND deployment_observation_id=%s "
                "AND certificate_fingerprint=%s",
                (
                    int(user_id),
                    str(snapshot.get("verification_id") or ""),
                    str(snapshot.get("deployment_observation_id") or ""),
                    str(snapshot.get("certificate_fingerprint") or ""),
                ),
            )
            row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError(
                "velia_factory_release_completion_persist_failed", status=500
            )
        conn.commit()
        return _certificate_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def evaluate_release_completion(
    execution_module: Any,
    user_id: int,
    verification_id: str,
    deployment_observation_id: str,
    *,
    persist: bool = True,
) -> Dict[str, Any]:
    snapshot = build_completion_snapshot(
        execution_module,
        int(user_id),
        str(verification_id),
        str(deployment_observation_id),
    )
    if not persist:
        return snapshot
    return persist_completion_certificate(execution_module, int(user_id), snapshot)


def get_completion_certificate(
    execution_module: Any,
    user_id: int,
    certificate_id: str,
) -> Dict[str, Any]:
    ensure_completion_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT certificate_id,verification_id,deployment_observation_id,"
            "release_execution_id,user_id,certificate_fingerprint,status,snapshot_json,created_at "
            "FROM velia_software_factory_release_completion_certificates "
            "WHERE certificate_id=%s AND user_id=%s",
            (str(certificate_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError(
                "velia_factory_release_completion_not_found", status=404
            )
        return _certificate_row(row)
    finally:
        cursor.close()
        conn.close()


def list_completion_certificates(
    execution_module: Any,
    user_id: int,
    release_execution_id: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    ensure_completion_tables(execution_module)
    bounded = max(1, min(100, int(limit or 20)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT certificate_id,verification_id,deployment_observation_id,"
            "release_execution_id,user_id,certificate_fingerprint,status,snapshot_json,created_at "
            "FROM velia_software_factory_release_completion_certificates "
            "WHERE user_id=%s AND release_execution_id=%s "
            "ORDER BY created_at DESC LIMIT %s",
            (int(user_id), str(release_execution_id), bounded),
        )
        return [_certificate_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()
