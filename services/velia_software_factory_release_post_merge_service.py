from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Mapping

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services import velia_software_factory_release_execution_service as release_execution
from services import velia_software_factory_release_verification_github_service as verification_github
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_701
_VERIFIABLE_EXECUTION_STATES = {"completed", "partial_release"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def verification_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED", False)


def public_status() -> Dict[str, Any]:
    return {
        "available": True,
        "enabled": verification_enabled(),
        "mode": "post_merge_read_only",
        "exact_planned_head_required": True,
        "exact_recorded_merge_commit_required": True,
        "merge_commit_must_be_reachable_from_base": True,
        "append_only_evidence": True,
        "partial_release_recovery_artifact": True,
        "revert_supported": False,
        "github_write_supported": False,
        "execution_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
    }


def _utcnow():
    return release_execution._utcnow()


def _json(value: Any, limit: int = 180000) -> str:
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


def _valid_sha(value: Any) -> bool:
    return bool(_SHA_RE.fullmatch(str(value or "").strip().lower()))


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


def ensure_post_merge_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    release_execution.ensure_execution_tables(execution_module)
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_verifications (
                    verification_id TEXT PRIMARY KEY,
                    release_execution_id TEXT NOT NULL REFERENCES velia_software_factory_release_executions(execution_id) ON DELETE RESTRICT,
                    user_id BIGINT NOT NULL,
                    verification_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('verified','partial_verified','failed')),
                    UNIQUE(user_id,release_execution_id,verification_fingerprint)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_recovery_artifacts (
                    recovery_id TEXT PRIMARY KEY,
                    release_execution_id TEXT NOT NULL REFERENCES velia_software_factory_release_executions(execution_id) ON DELETE RESTRICT,
                    verification_id TEXT NOT NULL REFERENCES velia_software_factory_release_verifications(verification_id) ON DELETE RESTRICT,
                    user_id BIGINT NOT NULL,
                    recovery_fingerprint TEXT NOT NULL,
                    artifact_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id,release_execution_id,recovery_fingerprint)
                )
                """
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
    if not verification_enabled():
        raise SoftwareFactoryError("velia_factory_release_verification_disabled", status=503)
    if not rollout.intake_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)


def _project_for_item(user_id: int, item: Mapping[str, Any]) -> Dict[str, Any]:
    project = project_service.get_project(int(user_id), str(item.get("project_id") or ""))
    actual = str(project.get("repository_full_name") or "").strip().casefold()
    expected = str(item.get("repository_full_name") or "").strip().casefold()
    if not actual or actual != expected:
        raise SoftwareFactoryError(
            "velia_factory_release_verification_repository_identity_changed",
            detail=str(item.get("project_id") or ""),
            status=409,
        )
    return project


def build_verification_snapshot(execution_module: Any, user_id: int, release_execution_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    execution = release_execution.get_execution(
        execution_module, int(user_id), str(release_execution_id)
    )
    execution_status = str(execution.get("status") or "")
    if execution_status not in _VERIFIABLE_EXECUTION_STATES:
        raise SoftwareFactoryError(
            "velia_factory_release_verification_execution_not_terminal",
            detail=execution_status,
            status=409,
        )
    items = [dict(item) for item in execution.get("items") or [] if isinstance(item, Mapping)]
    if not items:
        raise SoftwareFactoryError("velia_factory_release_verification_items_missing", status=409)

    merged: List[Dict[str, Any]] = []
    unmerged: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    for item in items:
        item_status = str(item.get("status") or "")
        if item_status != "merged":
            unmerged.append(
                {
                    "position": int(item.get("position") or 0),
                    "project_id": str(item.get("project_id") or ""),
                    "repository_full_name": str(item.get("repository_full_name") or ""),
                    "run_id": str(item.get("run_id") or ""),
                    "pull_request_number": int(item.get("pull_request_number") or 0),
                    "expected_head_sha": str(item.get("expected_head_sha") or ""),
                    "status": item_status,
                    "error_code": str(item.get("error_code") or ""),
                    "error_detail": str(item.get("error_detail") or "")[:1000],
                }
            )
            continue
        expected_merge = str(item.get("merge_commit_sha") or "").strip().lower()
        if not _valid_sha(expected_merge):
            failures.append(
                {
                    "position": str(int(item.get("position") or 0)),
                    "project_id": str(item.get("project_id") or ""),
                    "repository_full_name": str(item.get("repository_full_name") or ""),
                    "code": "velia_factory_release_verification_recorded_merge_commit_missing",
                    "detail": expected_merge[:80],
                }
            )
            continue
        try:
            project = _project_for_item(int(user_id), item)
            evidence = verification_github.verify_merged_pull(
                project,
                pull_number=int(item.get("pull_request_number") or 0),
                expected_head_sha=str(item.get("expected_head_sha") or ""),
                expected_merge_commit_sha=expected_merge,
            )
            merged.append(
                {
                    "position": int(item.get("position") or 0),
                    "project_id": str(item.get("project_id") or ""),
                    "run_id": str(item.get("run_id") or ""),
                    **evidence,
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "position": str(int(item.get("position") or 0)),
                    "project_id": str(item.get("project_id") or ""),
                    "repository_full_name": str(item.get("repository_full_name") or ""),
                    "code": str(getattr(exc, "code", exc.__class__.__name__))[:160],
                    "detail": str(getattr(exc, "detail", str(exc)) or "")[:1000],
                }
            )

    expected_merged = int(execution.get("merged_count") or 0)
    item_merged_count = sum(1 for item in items if str(item.get("status") or "") == "merged")
    if expected_merged != item_merged_count:
        failures.append(
            {
                "position": "",
                "project_id": "",
                "repository_full_name": "",
                "code": "velia_factory_release_verification_merged_count_mismatch",
                "detail": f"execution={expected_merged} items={item_merged_count}",
            }
        )
    if execution_status == "completed" and unmerged:
        failures.append(
            {
                "position": "",
                "project_id": "",
                "repository_full_name": "",
                "code": "velia_factory_release_verification_completed_has_unmerged_items",
                "detail": str(len(unmerged)),
            }
        )
    if execution_status == "partial_release" and not merged:
        failures.append(
            {
                "position": "",
                "project_id": "",
                "repository_full_name": "",
                "code": "velia_factory_release_verification_partial_without_verified_merge",
                "detail": "",
            }
        )

    if failures:
        status = "failed"
    elif execution_status == "completed":
        status = "verified"
    else:
        status = "partial_verified"

    snapshot = {
        "release_execution_id": str(release_execution_id),
        "plan_id": str(execution.get("plan_id") or ""),
        "candidate_id": str(execution.get("candidate_id") or ""),
        "plan_fingerprint": str(execution.get("plan_fingerprint") or ""),
        "approval_sequence_id": int(execution.get("approval_sequence_id") or 0),
        "release_execution_status": execution_status,
        "verification_status": status,
        "merged_count": len(merged),
        "unmerged_count": len(unmerged),
        "verified_merges": merged,
        "unmerged_items": unmerged,
        "failures": failures,
        "recovery_required": execution_status == "partial_release",
        "github_write_supported": False,
        "revert_supported": False,
        "deployment_supported": False,
        "verified_at": _utcnow().isoformat() + "Z",
    }
    snapshot["verification_fingerprint"] = _fingerprint(
        {
            "release_execution_id": snapshot["release_execution_id"],
            "plan_fingerprint": snapshot["plan_fingerprint"],
            "approval_sequence_id": snapshot["approval_sequence_id"],
            "release_execution_status": execution_status,
            "verified_merges": merged,
            "unmerged_items": unmerged,
            "failures": failures,
        }
    )
    return snapshot


def _verification_row(row: Any) -> Dict[str, Any]:
    snapshot = _loads(_value(row, "snapshot_json", 5, "{}"), {})
    result = dict(snapshot) if isinstance(snapshot, dict) else {}
    result.update(
        {
            "verification_id": str(_value(row, "verification_id", 0, "")),
            "release_execution_id": str(_value(row, "release_execution_id", 1, "")),
            "user_id": int(_value(row, "user_id", 2, 0) or 0),
            "verification_fingerprint": str(_value(row, "verification_fingerprint", 3, "")),
            "verification_status": str(_value(row, "status", 4, result.get("verification_status") or "failed")),
            "created_at": str(_value(row, "created_at", 6, "") or ""),
        }
    )
    return result


def persist_verification(execution_module: Any, user_id: int, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    ensure_post_merge_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        verification_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_release_verifications (
                verification_id,release_execution_id,user_id,verification_fingerprint,status,snapshot_json,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,release_execution_id,verification_fingerprint) DO NOTHING
            RETURNING verification_id,release_execution_id,user_id,verification_fingerprint,status,snapshot_json,created_at
            """,
            (
                verification_id,
                str(snapshot.get("release_execution_id") or ""),
                int(user_id),
                str(snapshot.get("verification_fingerprint") or ""),
                str(snapshot.get("verification_status") or "failed"),
                _json(dict(snapshot)),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "SELECT verification_id,release_execution_id,user_id,verification_fingerprint,status,snapshot_json,created_at "
                "FROM velia_software_factory_release_verifications "
                "WHERE user_id=%s AND release_execution_id=%s AND verification_fingerprint=%s",
                (
                    int(user_id),
                    str(snapshot.get("release_execution_id") or ""),
                    str(snapshot.get("verification_fingerprint") or ""),
                ),
            )
            row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_release_verification_persist_failed", status=500)
        conn.commit()
        return _verification_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def verify_release(execution_module: Any, user_id: int, release_execution_id: str, *, persist: bool = True) -> Dict[str, Any]:
    snapshot = build_verification_snapshot(execution_module, int(user_id), str(release_execution_id))
    if not persist:
        return snapshot
    return persist_verification(execution_module, int(user_id), snapshot)


def get_verification(execution_module: Any, user_id: int, verification_id: str) -> Dict[str, Any]:
    ensure_post_merge_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT verification_id,release_execution_id,user_id,verification_fingerprint,status,snapshot_json,created_at "
            "FROM velia_software_factory_release_verifications WHERE verification_id=%s AND user_id=%s",
            (str(verification_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_release_verification_not_found", status=404)
        return _verification_row(row)
    finally:
        cursor.close()
        conn.close()


def _recovery_snapshot(verification: Mapping[str, Any]) -> Dict[str, Any]:
    if str(verification.get("verification_status") or "") != "partial_verified":
        raise SoftwareFactoryError(
            "velia_factory_release_recovery_requires_verified_partial_release",
            detail=str(verification.get("verification_status") or ""),
            status=409,
        )
    merged = [dict(item) for item in verification.get("verified_merges") or [] if isinstance(item, Mapping)]
    unmerged = [dict(item) for item in verification.get("unmerged_items") or [] if isinstance(item, Mapping)]
    artifact = {
        "release_execution_id": str(verification.get("release_execution_id") or ""),
        "verification_id": str(verification.get("verification_id") or ""),
        "verification_fingerprint": str(verification.get("verification_fingerprint") or ""),
        "state": "recovery_required",
        "already_merged": merged,
        "not_merged": unmerged,
        "next_action_policy": "fix_blocker_then_build_new_release_candidate_for_remaining_work",
        "automatic_revert": False,
        "automatic_merge": False,
        "deployment_started": False,
        "created_at": _utcnow().isoformat() + "Z",
    }
    artifact["recovery_fingerprint"] = _fingerprint(
        {
            "release_execution_id": artifact["release_execution_id"],
            "verification_fingerprint": artifact["verification_fingerprint"],
            "already_merged": merged,
            "not_merged": unmerged,
            "next_action_policy": artifact["next_action_policy"],
        }
    )
    return artifact


def build_recovery_artifact(execution_module: Any, user_id: int, verification_id: str) -> Dict[str, Any]:
    _require_user(int(user_id))
    verification = get_verification(execution_module, int(user_id), str(verification_id))
    artifact = _recovery_snapshot(verification)
    ensure_post_merge_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        recovery_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_release_recovery_artifacts (
                recovery_id,release_execution_id,verification_id,user_id,recovery_fingerprint,artifact_json,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,release_execution_id,recovery_fingerprint) DO NOTHING
            RETURNING recovery_id,release_execution_id,verification_id,user_id,recovery_fingerprint,artifact_json,created_at
            """,
            (
                recovery_id,
                str(artifact.get("release_execution_id") or ""),
                str(verification_id),
                int(user_id),
                str(artifact.get("recovery_fingerprint") or ""),
                _json(artifact),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "SELECT recovery_id,release_execution_id,verification_id,user_id,recovery_fingerprint,artifact_json,created_at "
                "FROM velia_software_factory_release_recovery_artifacts "
                "WHERE user_id=%s AND release_execution_id=%s AND recovery_fingerprint=%s",
                (
                    int(user_id),
                    str(artifact.get("release_execution_id") or ""),
                    str(artifact.get("recovery_fingerprint") or ""),
                ),
            )
            row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_release_recovery_persist_failed", status=500)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    result = _loads(_value(row, "artifact_json", 5, "{}"), {})
    result = dict(result) if isinstance(result, dict) else {}
    result.update(
        {
            "recovery_id": str(_value(row, "recovery_id", 0, "")),
            "release_execution_id": str(_value(row, "release_execution_id", 1, "")),
            "verification_id": str(_value(row, "verification_id", 2, "")),
            "user_id": int(_value(row, "user_id", 3, 0) or 0),
            "recovery_fingerprint": str(_value(row, "recovery_fingerprint", 4, "")),
            "created_at": str(_value(row, "created_at", 6, "") or ""),
        }
    )
    return result
