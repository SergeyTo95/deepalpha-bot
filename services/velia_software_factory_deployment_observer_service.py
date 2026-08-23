from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from typing import Any, Dict, List, Mapping, Sequence

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services import velia_software_factory_deployment_status_github_service as status_github
from services import velia_software_factory_release_post_merge_service as post_merge
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_733
_PROVIDER = "github_commit_status"
_VERIFICATION_STATES = {"verified", "partial_verified"}
_OBSERVATION_STATES = {"success", "partial_success", "pending", "failed", "blocked"}
_MAX_CONTEXTS = 24


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def deployment_observer_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED", False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": deployment_observer_enabled(),
        "mode": "github_commit_status_observer",
        "profile_required": True,
        "exact_context_match": True,
        "post_merge_verification_required": True,
        "exact_merge_commit_required": True,
        "railway_credentials_required": False,
        "deployment_trigger_supported": False,
        "deployment_verification_supported": deployment_observer_enabled(),
        "deployment_supported": False,
        "revert_supported": False,
    }


def _utcnow():
    return post_merge._utcnow()


def _json(value: Any, limit: int = 200000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


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
    for raw in values:
        text = str(raw or "").replace("\x00", "").strip()
        if not text:
            continue
        if len(text) > 240:
            raise SoftwareFactoryError("velia_factory_deployment_context_too_long", status=400)
        if any(token in text for token in ("*", "?", "[", "]")):
            raise SoftwareFactoryError(
                "velia_factory_deployment_context_must_be_exact",
                detail=text[:240],
                status=400,
            )
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    if not result:
        raise SoftwareFactoryError("velia_factory_deployment_contexts_required", status=400)
    if len(result) > _MAX_CONTEXTS:
        raise SoftwareFactoryError(
            "velia_factory_deployment_context_limit_exceeded",
            detail=str(len(result)),
            status=400,
        )
    return result


def ensure_deployment_observer_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    post_merge.ensure_post_merge_tables(execution_module)
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_deployment_profiles (
                    profile_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id) ON DELETE RESTRICT,
                    repository_full_name TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'github_commit_status',
                    expected_contexts_json TEXT NOT NULL DEFAULT '[]',
                    profile_fingerprint TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id,project_id,branch),
                    CHECK (provider IN ('github_commit_status'))
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_deployment_observations (
                    observation_id TEXT PRIMARY KEY,
                    verification_id TEXT NOT NULL REFERENCES velia_software_factory_release_verifications(verification_id) ON DELETE RESTRICT,
                    release_execution_id TEXT NOT NULL REFERENCES velia_software_factory_release_executions(execution_id) ON DELETE RESTRICT,
                    user_id BIGINT NOT NULL,
                    observation_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id,verification_id,observation_fingerprint),
                    CHECK (status IN ('success','partial_success','pending','failed','blocked'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_deployment_observations_release "
                "ON velia_software_factory_deployment_observations(user_id,release_execution_id,created_at DESC)"
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
    if not deployment_observer_enabled():
        raise SoftwareFactoryError("velia_factory_deployment_observer_disabled", status=503)
    if not post_merge.verification_enabled():
        raise SoftwareFactoryError("velia_factory_release_verification_disabled", status=503)
    if not rollout.intake_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)


def _profile_row(row: Any) -> Dict[str, Any]:
    contexts = _loads(_value(row, "expected_contexts_json", 6, "[]"), [])
    if not isinstance(contexts, list):
        contexts = []
    return {
        "profile_id": str(_value(row, "profile_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "project_id": str(_value(row, "project_id", 2, "")),
        "repository_full_name": str(_value(row, "repository_full_name", 3, "")),
        "branch": str(_value(row, "branch", 4, "")),
        "provider": str(_value(row, "provider", 5, _PROVIDER) or _PROVIDER),
        "expected_contexts": [str(item) for item in contexts if str(item or "").strip()],
        "profile_fingerprint": str(_value(row, "profile_fingerprint", 7, "")),
        "enabled": bool(_value(row, "enabled", 8, False)),
        "created_at": str(_value(row, "created_at", 9, "") or ""),
        "updated_at": str(_value(row, "updated_at", 10, "") or ""),
    }


def configure_profile(
    execution_module: Any,
    user_id: int,
    project_id: str,
    *,
    branch: str,
    expected_contexts: Sequence[Any],
    enabled: bool = True,
) -> Dict[str, Any]:
    _require_user(int(user_id))
    ensure_deployment_observer_tables(execution_module)
    project = project_service.get_project(int(user_id), str(project_id))
    selected_branch = str(project.get("selected_branch") or "").strip()
    target_branch = str(branch or "").strip()
    if not selected_branch or target_branch != selected_branch:
        raise SoftwareFactoryError(
            "velia_factory_deployment_profile_branch_mismatch",
            detail=f"selected={selected_branch} requested={target_branch}",
            status=409,
        )
    repository = str(project.get("repository_full_name") or "").strip()
    if not repository:
        raise SoftwareFactoryError("velia_factory_deployment_profile_repository_missing", status=409)
    contexts = _normalize_contexts(expected_contexts)
    fingerprint = _fingerprint(
        {
            "project_id": str(project_id),
            "repository_full_name": repository,
            "branch": target_branch,
            "provider": _PROVIDER,
            "expected_contexts": contexts,
            "enabled": bool(enabled),
        }
    )
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        profile_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_deployment_profiles (
                profile_id,user_id,project_id,repository_full_name,branch,provider,
                expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,project_id,branch) DO UPDATE SET
                repository_full_name=EXCLUDED.repository_full_name,
                provider=EXCLUDED.provider,
                expected_contexts_json=EXCLUDED.expected_contexts_json,
                profile_fingerprint=EXCLUDED.profile_fingerprint,
                enabled=EXCLUDED.enabled,
                updated_at=EXCLUDED.updated_at
            RETURNING profile_id,user_id,project_id,repository_full_name,branch,provider,
                expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at
            """,
            (
                profile_id,
                int(user_id),
                str(project_id),
                repository,
                target_branch,
                _PROVIDER,
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


def get_profile(
    execution_module: Any,
    user_id: int,
    project_id: str,
    branch: str,
    *,
    require_enabled: bool = False,
) -> Dict[str, Any]:
    ensure_deployment_observer_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT profile_id,user_id,project_id,repository_full_name,branch,provider,"
            "expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at "
            "FROM velia_software_factory_deployment_profiles "
            "WHERE user_id=%s AND project_id=%s AND branch=%s",
            (int(user_id), str(project_id), str(branch)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_deployment_profile_not_found", status=404)
        result = _profile_row(row)
        if require_enabled and not bool(result.get("enabled")):
            raise SoftwareFactoryError("velia_factory_deployment_profile_disabled", status=409)
        return result
    finally:
        cursor.close()
        conn.close()


def list_profiles(execution_module: Any, user_id: int) -> List[Dict[str, Any]]:
    ensure_deployment_observer_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT profile_id,user_id,project_id,repository_full_name,branch,provider,"
            "expected_contexts_json,profile_fingerprint,enabled,created_at,updated_at "
            "FROM velia_software_factory_deployment_profiles WHERE user_id=%s ORDER BY updated_at DESC",
            (int(user_id),),
        )
        return [_profile_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def _project_for_verified_merge(user_id: int, item: Mapping[str, Any]) -> Dict[str, Any]:
    project = project_service.get_project(int(user_id), str(item.get("project_id") or ""))
    actual_repo = str(project.get("repository_full_name") or "").strip().casefold()
    expected_repo = str(item.get("repository_full_name") or "").strip().casefold()
    actual_branch = str(project.get("selected_branch") or "").strip()
    expected_branch = str(item.get("base_branch") or "").strip()
    if not actual_repo or actual_repo != expected_repo:
        raise SoftwareFactoryError(
            "velia_factory_deployment_repository_identity_changed",
            detail=str(item.get("project_id") or ""),
            status=409,
        )
    if not actual_branch or actual_branch != expected_branch:
        raise SoftwareFactoryError(
            "velia_factory_deployment_branch_identity_changed",
            detail=f"expected={expected_branch} actual={actual_branch}",
            status=409,
        )
    return project


def _evaluate_expected_contexts(
    profile: Mapping[str, Any],
    status_snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = [str(item) for item in profile.get("expected_contexts") or []]
    statuses = {
        str(item.get("context") or ""): dict(item)
        for item in status_snapshot.get("statuses") or []
        if isinstance(item, Mapping) and str(item.get("context") or "").strip()
    }
    matched: List[Dict[str, Any]] = []
    missing: List[str] = []
    failing: List[str] = []
    waiting: List[str] = []
    for context in expected:
        item = statuses.get(context)
        if item is None:
            missing.append(context)
            continue
        state = str(item.get("state") or "").strip().lower()
        matched.append(
            {
                "context": context,
                "state": state,
                "description": str(item.get("description") or "")[:500],
                "target_url": str(item.get("target_url") or "")[:1000],
                "updated_at": str(item.get("updated_at") or "")[:80],
            }
        )
        if state in {"failure", "error"}:
            failing.append(context)
        elif state != "success":
            waiting.append(context)
    if failing:
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
    }


def discover_context_candidates(
    execution_module: Any,
    user_id: int,
    verification_id: str,
) -> Dict[str, Any]:
    _require_user(int(user_id))
    verification = post_merge.get_verification(
        execution_module, int(user_id), str(verification_id)
    )
    verification_status = str(verification.get("verification_status") or "")
    if verification_status not in _VERIFICATION_STATES:
        raise SoftwareFactoryError(
            "velia_factory_deployment_requires_verified_release",
            detail=verification_status,
            status=409,
        )
    result: List[Dict[str, Any]] = []
    for item in verification.get("verified_merges") or []:
        if not isinstance(item, Mapping):
            continue
        project = _project_for_verified_merge(int(user_id), item)
        commit_sha = str(item.get("merge_commit_sha") or "")
        snapshot = status_github.commit_status_snapshot(project, commit_sha)
        result.append(
            {
                "project_id": str(item.get("project_id") or ""),
                "repository_full_name": str(item.get("repository_full_name") or ""),
                "branch": str(item.get("base_branch") or ""),
                "merge_commit_sha": commit_sha,
                "railway_candidates": status_github.railway_context_candidates(snapshot),
                "all_status_contexts": [
                    {
                        "context": str(status.get("context") or ""),
                        "state": str(status.get("state") or ""),
                        "target_url": str(status.get("target_url") or ""),
                    }
                    for status in snapshot.get("statuses") or []
                    if isinstance(status, Mapping)
                ],
            }
        )
    return {
        "verification_id": str(verification_id),
        "verification_status": verification_status,
        "suggested_only": True,
        "explicit_profile_confirmation_required": True,
        "repositories": result,
        "deployment_triggered": False,
    }


def build_observation_snapshot(
    execution_module: Any,
    user_id: int,
    verification_id: str,
) -> Dict[str, Any]:
    _require_user(int(user_id))
    verification = post_merge.get_verification(
        execution_module, int(user_id), str(verification_id)
    )
    verification_status = str(verification.get("verification_status") or "")
    if verification_status not in _VERIFICATION_STATES:
        raise SoftwareFactoryError(
            "velia_factory_deployment_requires_verified_release",
            detail=verification_status,
            status=409,
        )

    repositories: List[Dict[str, Any]] = []
    blockers: List[Dict[str, str]] = []
    for item in verification.get("verified_merges") or []:
        if not isinstance(item, Mapping):
            continue
        project_id = str(item.get("project_id") or "")
        repository = str(item.get("repository_full_name") or "")
        branch = str(item.get("base_branch") or "")
        commit_sha = str(item.get("merge_commit_sha") or "")
        try:
            project = _project_for_verified_merge(int(user_id), item)
            profile = get_profile(
                execution_module,
                int(user_id),
                project_id,
                branch,
                require_enabled=True,
            )
            if str(profile.get("repository_full_name") or "").casefold() != repository.casefold():
                raise SoftwareFactoryError(
                    "velia_factory_deployment_profile_repository_mismatch",
                    detail=repository,
                    status=409,
                )
            status_snapshot = status_github.commit_status_snapshot(project, commit_sha)
            evaluation = _evaluate_expected_contexts(profile, status_snapshot)
            repositories.append(
                {
                    "project_id": project_id,
                    "repository_full_name": repository,
                    "branch": branch,
                    "merge_commit_sha": commit_sha,
                    "profile_id": str(profile.get("profile_id") or ""),
                    "profile_fingerprint": str(profile.get("profile_fingerprint") or ""),
                    "provider": _PROVIDER,
                    "combined_state": str(status_snapshot.get("combined_state") or ""),
                    **evaluation,
                }
            )
        except Exception as exc:
            blockers.append(
                {
                    "project_id": project_id,
                    "repository_full_name": repository,
                    "code": str(getattr(exc, "code", exc.__class__.__name__))[:160],
                    "detail": str(getattr(exc, "detail", str(exc)) or "")[:1000],
                }
            )

    if blockers:
        status = "blocked"
    elif any(str(item.get("status") or "") == "failed" for item in repositories):
        status = "failed"
    elif any(str(item.get("status") or "") != "success" for item in repositories):
        status = "pending"
    elif verification_status == "partial_verified":
        status = "partial_success"
    else:
        status = "success"

    snapshot = {
        "verification_id": str(verification_id),
        "release_execution_id": str(verification.get("release_execution_id") or ""),
        "verification_fingerprint": str(verification.get("verification_fingerprint") or ""),
        "verification_status": verification_status,
        "status": status,
        "repositories": repositories,
        "blockers": blockers,
        "repository_count": len(repositories),
        "deployment_complete": status == "success",
        "partial_release_recovery_required": verification_status == "partial_verified",
        "deployment_triggered": False,
        "deployment_trigger_supported": False,
        "deployment_supported": False,
        "observed_at": _utcnow().isoformat() + "Z",
    }
    snapshot["observation_fingerprint"] = _fingerprint(
        {
            "verification_id": snapshot["verification_id"],
            "verification_fingerprint": snapshot["verification_fingerprint"],
            "verification_status": verification_status,
            "status": status,
            "repositories": repositories,
            "blockers": blockers,
        }
    )
    return snapshot


def _observation_row(row: Any) -> Dict[str, Any]:
    snapshot = _loads(_value(row, "snapshot_json", 5, "{}"), {})
    result = dict(snapshot) if isinstance(snapshot, dict) else {}
    result.update(
        {
            "observation_id": str(_value(row, "observation_id", 0, "")),
            "verification_id": str(_value(row, "verification_id", 1, "")),
            "release_execution_id": str(_value(row, "release_execution_id", 2, "")),
            "user_id": int(_value(row, "user_id", 3, 0) or 0),
            "observation_fingerprint": str(_value(row, "observation_fingerprint", 4, "")),
            "status": str(_value(row, "status", 5, result.get("status") or "blocked")),
            "created_at": str(_value(row, "created_at", 7, "") or ""),
        }
    )
    return result


def persist_observation(
    execution_module: Any,
    user_id: int,
    snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    ensure_deployment_observer_tables(execution_module)
    status = str(snapshot.get("status") or "")
    if status not in _OBSERVATION_STATES:
        raise SoftwareFactoryError("velia_factory_deployment_observation_status_invalid", status=500)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        observation_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_deployment_observations (
                observation_id,verification_id,release_execution_id,user_id,
                observation_fingerprint,status,snapshot_json,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,verification_id,observation_fingerprint) DO NOTHING
            RETURNING observation_id,verification_id,release_execution_id,user_id,
                observation_fingerprint,status,snapshot_json,created_at
            """,
            (
                observation_id,
                str(snapshot.get("verification_id") or ""),
                str(snapshot.get("release_execution_id") or ""),
                int(user_id),
                str(snapshot.get("observation_fingerprint") or ""),
                status,
                _json(dict(snapshot)),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "SELECT observation_id,verification_id,release_execution_id,user_id,"
                "observation_fingerprint,status,snapshot_json,created_at "
                "FROM velia_software_factory_deployment_observations "
                "WHERE user_id=%s AND verification_id=%s AND observation_fingerprint=%s",
                (
                    int(user_id),
                    str(snapshot.get("verification_id") or ""),
                    str(snapshot.get("observation_fingerprint") or ""),
                ),
            )
            row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_deployment_observation_persist_failed", status=500)
        conn.commit()
        return _observation_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def observe_release_deployment(
    execution_module: Any,
    user_id: int,
    verification_id: str,
    *,
    persist: bool = True,
) -> Dict[str, Any]:
    snapshot = build_observation_snapshot(
        execution_module, int(user_id), str(verification_id)
    )
    if not persist:
        return snapshot
    return persist_observation(execution_module, int(user_id), snapshot)


def get_observation(
    execution_module: Any,
    user_id: int,
    observation_id: str,
) -> Dict[str, Any]:
    ensure_deployment_observer_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT observation_id,verification_id,release_execution_id,user_id,"
            "observation_fingerprint,status,snapshot_json,created_at "
            "FROM velia_software_factory_deployment_observations "
            "WHERE observation_id=%s AND user_id=%s",
            (str(observation_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_deployment_observation_not_found", status=404)
        return _observation_row(row)
    finally:
        cursor.close()
        conn.close()


def list_observations(
    execution_module: Any,
    user_id: int,
    release_execution_id: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    ensure_deployment_observer_tables(execution_module)
    bounded = max(1, min(100, int(limit or 20)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT observation_id,verification_id,release_execution_id,user_id,"
            "observation_fingerprint,status,snapshot_json,created_at "
            "FROM velia_software_factory_deployment_observations "
            "WHERE user_id=%s AND release_execution_id=%s ORDER BY created_at DESC LIMIT %s",
            (int(user_id), str(release_execution_id), bounded),
        )
        return [_observation_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()
