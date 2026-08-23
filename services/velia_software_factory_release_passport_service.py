from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from typing import Any, Dict, List, Mapping

from db.database import get_connection
from services import velia_software_factory_delivery_approval_service as approval
from services import velia_software_factory_delivery_gate_service as delivery
from services import velia_software_factory_deployment_observer_service as deployment
from services import velia_software_factory_release_completion_service as completion
from services import velia_software_factory_release_execution_service as release_execution
from services import velia_software_factory_release_post_merge_service as post_merge
from services import velia_software_factory_release_preflight_service as preflight
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_793
_MODE = "immutable_audit_passport"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def passport_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED", False)


def public_status() -> Dict[str, Any]:
    enabled = passport_enabled()
    return {
        "available": True,
        "enabled": enabled,
        "mode": _MODE,
        "complete_certificate_required": True,
        "derive_chain_from_certificate": True,
        "cross_reference_validation": True,
        "tamper_evident_chain_hash": True,
        "append_only": True,
        "network_access_supported": False,
        "github_access_supported": False,
        "railway_access_supported": False,
        "github_write_supported": False,
        "merge_supported": False,
        "deployment_supported": False,
        "revert_supported": False,
        "passport_supported": enabled,
    }


def _utcnow():
    return completion._utcnow()


def _json(value: Any, limit: int = 260000) -> str:
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


def ensure_passport_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    completion.ensure_completion_tables(execution_module)
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_release_passports (
                    passport_id TEXT PRIMARY KEY,
                    certificate_id TEXT NOT NULL REFERENCES velia_software_factory_release_completion_certificates(certificate_id) ON DELETE RESTRICT,
                    release_execution_id TEXT NOT NULL REFERENCES velia_software_factory_release_executions(execution_id) ON DELETE RESTRICT,
                    user_id BIGINT NOT NULL,
                    evidence_chain_hash TEXT NOT NULL,
                    passport_fingerprint TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id,certificate_id,evidence_chain_hash)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_release_passports_execution "
                "ON velia_software_factory_release_passports(user_id,release_execution_id,created_at DESC)"
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
    if not passport_enabled():
        raise SoftwareFactoryError("velia_factory_release_passport_disabled", status=503)
    if not completion.completion_enabled():
        raise SoftwareFactoryError("velia_factory_release_completion_disabled", status=503)
    if not rollout.intake_allowed(int(user_id)):
        raise SoftwareFactoryError("velia_factory_rollout_forbidden", status=403)


def _require_equal(code: str, left: Any, right: Any, *, detail: str = "") -> None:
    if str(left or "") != str(right or ""):
        raise SoftwareFactoryError(
            code,
            detail=(detail or f"left={str(left)[:80]} right={str(right)[:80]}")[:1000],
            status=409,
        )


def _approval_event(execution_module: Any, user_id: int, sequence_id: int) -> Dict[str, Any]:
    approval.ensure_approval_tables(execution_module)
    seq = int(sequence_id or 0)
    if seq <= 0:
        raise SoftwareFactoryError("velia_factory_release_passport_approval_sequence_missing", status=409)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT sequence_id,decision_id,candidate_id,user_id,source_id,source_fingerprint,decision,note,created_at "
            "FROM velia_software_factory_delivery_approval_events "
            "WHERE sequence_id=%s AND user_id=%s",
            (seq, int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_release_passport_approval_event_not_found", status=404)
        result = approval._event_row(row)
    finally:
        cursor.close()
        conn.close()
    if str(result.get("decision") or "") != "approved":
        raise SoftwareFactoryError(
            "velia_factory_release_passport_approval_not_approved",
            detail=str(result.get("decision") or ""),
            status=409,
        )
    return result


def _unique_by_project(items: Any, code: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in items or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        project_id = str(item.get("project_id") or "")
        if not project_id or project_id in result:
            raise SoftwareFactoryError(code, detail=project_id, status=409)
        result[project_id] = item
    if not result:
        raise SoftwareFactoryError(code, detail="empty", status=409)
    return result


def _repository_chain(
    release: Mapping[str, Any],
    plan: Mapping[str, Any],
    candidate: Mapping[str, Any],
    verification: Mapping[str, Any],
    observation: Mapping[str, Any],
    certificate: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    execution_items = _unique_by_project(
        release.get("items"), "velia_factory_release_passport_execution_items_invalid"
    )
    plan_items = _unique_by_project(
        plan.get("repositories"), "velia_factory_release_passport_plan_items_invalid"
    )
    candidate_items = _unique_by_project(
        candidate.get("repositories"), "velia_factory_release_passport_candidate_items_invalid"
    )
    verified_items = _unique_by_project(
        verification.get("verified_merges"), "velia_factory_release_passport_verification_items_invalid"
    )
    observed_items = _unique_by_project(
        observation.get("repositories"), "velia_factory_release_passport_deployment_items_invalid"
    )
    certificate_items = _unique_by_project(
        certificate.get("repositories"), "velia_factory_release_passport_completion_items_invalid"
    )
    project_sets = [
        set(execution_items),
        set(plan_items),
        set(candidate_items),
        set(verified_items),
        set(observed_items),
        set(certificate_items),
    ]
    if any(projects != project_sets[0] for projects in project_sets[1:]):
        raise SoftwareFactoryError(
            "velia_factory_release_passport_repository_set_mismatch", status=409
        )

    chain: List[Dict[str, Any]] = []
    for project_id, execution_item in sorted(
        execution_items.items(), key=lambda pair: int(pair[1].get("position") or 0)
    ):
        plan_item = plan_items[project_id]
        candidate_item = candidate_items[project_id]
        verified = verified_items[project_id]
        observed = observed_items[project_id]
        completed = certificate_items[project_id]
        if str(execution_item.get("status") or "") != "merged":
            raise SoftwareFactoryError(
                "velia_factory_release_passport_execution_item_not_merged",
                detail=project_id,
                status=409,
            )
        if str(observed.get("status") or "") != "success":
            raise SoftwareFactoryError(
                "velia_factory_release_passport_deployment_item_not_success",
                detail=project_id,
                status=409,
            )
        if str(completed.get("status") or "") != "success":
            raise SoftwareFactoryError(
                "velia_factory_release_passport_acceptance_item_not_success",
                detail=project_id,
                status=409,
            )
        repository = str(execution_item.get("repository_full_name") or "")
        run_id = str(execution_item.get("run_id") or "")
        pr_number = int(execution_item.get("pull_request_number") or 0)
        head_sha = str(execution_item.get("expected_head_sha") or "").lower()
        merge_sha = str(execution_item.get("merge_commit_sha") or "").lower()
        for item, repo_key in (
            (plan_item, "repository_full_name"),
            (candidate_item, "repository_full_name"),
            (verified, "repository_full_name"),
            (observed, "repository_full_name"),
            (completed, "repository_full_name"),
        ):
            if str(item.get(repo_key) or "").casefold() != repository.casefold():
                raise SoftwareFactoryError(
                    "velia_factory_release_passport_repository_identity_mismatch",
                    detail=project_id,
                    status=409,
                )
        _require_equal(
            "velia_factory_release_passport_run_mismatch",
            plan_item.get("run_id"),
            run_id,
            detail=project_id,
        )
        _require_equal(
            "velia_factory_release_passport_candidate_run_mismatch",
            candidate_item.get("run_id"),
            run_id,
            detail=project_id,
        )
        _require_equal(
            "velia_factory_release_passport_pr_mismatch",
            plan_item.get("pull_request_number"),
            pr_number,
            detail=project_id,
        )
        _require_equal(
            "velia_factory_release_passport_candidate_pr_mismatch",
            candidate_item.get("pull_request_number"),
            pr_number,
            detail=project_id,
        )
        _require_equal(
            "velia_factory_release_passport_verification_pr_mismatch",
            verified.get("pull_request_number"),
            pr_number,
            detail=project_id,
        )
        _require_equal(
            "velia_factory_release_passport_head_mismatch",
            str(plan_item.get("head_sha") or "").lower(),
            head_sha,
            detail=project_id,
        )
        _require_equal(
            "velia_factory_release_passport_candidate_head_mismatch",
            str(candidate_item.get("head_sha") or "").lower(),
            head_sha,
            detail=project_id,
        )
        _require_equal(
            "velia_factory_release_passport_verification_head_mismatch",
            str(verified.get("expected_head_sha") or "").lower(),
            head_sha,
            detail=project_id,
        )
        for item, field, code in (
            (verified, "merge_commit_sha", "velia_factory_release_passport_verification_merge_mismatch"),
            (observed, "merge_commit_sha", "velia_factory_release_passport_deployment_merge_mismatch"),
            (completed, "merge_commit_sha", "velia_factory_release_passport_completion_merge_mismatch"),
        ):
            _require_equal(code, str(item.get(field) or "").lower(), merge_sha, detail=project_id)
        chain.append(
            {
                "order": int(plan_item.get("order") or execution_item.get("position") or 0),
                "project_id": project_id,
                "repository_full_name": repository,
                "run_id": run_id,
                "pull_request_number": pr_number,
                "head_sha": head_sha,
                "merge_commit_sha": merge_sha,
                "base_branch": str(verified.get("base_branch") or ""),
                "deployment_profile_fingerprint": str(
                    observed.get("profile_fingerprint")
                    or completed.get("deployment_profile_fingerprint")
                    or ""
                ),
                "acceptance_profile_fingerprint": str(
                    completed.get("acceptance_profile_fingerprint") or ""
                ),
                "deployment_contexts": list(observed.get("expected_contexts") or []),
                "acceptance_contexts": list(completed.get("expected_contexts") or []),
            }
        )
    return chain


def build_passport_snapshot(
    execution_module: Any,
    user_id: int,
    certificate_id: str,
) -> Dict[str, Any]:
    _require_user(int(user_id))
    certificate = completion.get_completion_certificate(
        execution_module, int(user_id), str(certificate_id)
    )
    if str(certificate.get("status") or "") != "complete" or not bool(
        certificate.get("release_complete")
    ):
        raise SoftwareFactoryError(
            "velia_factory_release_passport_requires_complete_certificate",
            detail=str(certificate.get("status") or ""),
            status=409,
        )
    verification_id = str(certificate.get("verification_id") or "")
    observation_id = str(certificate.get("deployment_observation_id") or "")
    execution_id = str(certificate.get("release_execution_id") or "")
    verification = post_merge.get_verification(
        execution_module, int(user_id), verification_id
    )
    observation = deployment.get_observation(
        execution_module, int(user_id), observation_id
    )
    release = release_execution.get_execution(
        execution_module, int(user_id), execution_id
    )
    plan_id = str(release.get("plan_id") or "")
    candidate_id = str(release.get("candidate_id") or "")
    plan = preflight.get_plan(execution_module, int(user_id), plan_id)
    candidate = delivery.get_candidate(
        execution_module, int(user_id), candidate_id
    )
    approval_event = _approval_event(
        execution_module,
        int(user_id),
        int(release.get("approval_sequence_id") or 0),
    )

    _require_equal(
        "velia_factory_release_passport_certificate_verification_mismatch",
        certificate.get("verification_id"),
        verification.get("verification_id"),
    )
    _require_equal(
        "velia_factory_release_passport_certificate_observation_mismatch",
        certificate.get("deployment_observation_id"),
        observation.get("observation_id"),
    )
    _require_equal(
        "velia_factory_release_passport_certificate_execution_mismatch",
        certificate.get("release_execution_id"),
        release.get("execution_id"),
    )
    _require_equal(
        "velia_factory_release_passport_verification_execution_mismatch",
        verification.get("release_execution_id"),
        release.get("execution_id"),
    )
    _require_equal(
        "velia_factory_release_passport_observation_execution_mismatch",
        observation.get("release_execution_id"),
        release.get("execution_id"),
    )
    _require_equal(
        "velia_factory_release_passport_observation_verification_mismatch",
        observation.get("verification_id"),
        verification.get("verification_id"),
    )
    _require_equal(
        "velia_factory_release_passport_certificate_verification_fingerprint_mismatch",
        certificate.get("verification_fingerprint"),
        verification.get("verification_fingerprint"),
    )
    _require_equal(
        "velia_factory_release_passport_certificate_observation_fingerprint_mismatch",
        certificate.get("deployment_observation_fingerprint"),
        observation.get("observation_fingerprint"),
    )
    _require_equal(
        "velia_factory_release_passport_execution_plan_mismatch",
        release.get("plan_id"),
        plan.get("plan_id"),
    )
    _require_equal(
        "velia_factory_release_passport_execution_candidate_mismatch",
        release.get("candidate_id"),
        candidate.get("candidate_id"),
    )
    _require_equal(
        "velia_factory_release_passport_plan_candidate_mismatch",
        plan.get("candidate_id"),
        candidate.get("candidate_id"),
    )
    _require_equal(
        "velia_factory_release_passport_plan_fingerprint_mismatch",
        release.get("plan_fingerprint"),
        plan.get("plan_fingerprint"),
    )
    _require_equal(
        "velia_factory_release_passport_approval_sequence_mismatch",
        release.get("approval_sequence_id"),
        approval_event.get("sequence_id"),
    )
    _require_equal(
        "velia_factory_release_passport_plan_approval_sequence_mismatch",
        plan.get("approval_sequence_id"),
        approval_event.get("sequence_id"),
    )
    _require_equal(
        "velia_factory_release_passport_approval_candidate_mismatch",
        approval_event.get("candidate_id"),
        candidate.get("candidate_id"),
    )
    _require_equal(
        "velia_factory_release_passport_source_id_mismatch",
        plan.get("source_id"),
        candidate.get("source_id"),
    )
    _require_equal(
        "velia_factory_release_passport_approval_source_id_mismatch",
        approval_event.get("source_id"),
        candidate.get("source_id"),
    )
    _require_equal(
        "velia_factory_release_passport_source_fingerprint_mismatch",
        plan.get("source_fingerprint"),
        candidate.get("source_fingerprint"),
    )
    _require_equal(
        "velia_factory_release_passport_approval_source_fingerprint_mismatch",
        approval_event.get("source_fingerprint"),
        candidate.get("source_fingerprint"),
    )
    if str(release.get("status") or "") != "completed":
        raise SoftwareFactoryError(
            "velia_factory_release_passport_execution_not_completed",
            detail=str(release.get("status") or ""),
            status=409,
        )
    if str(verification.get("verification_status") or "") != "verified":
        raise SoftwareFactoryError(
            "velia_factory_release_passport_verification_not_verified", status=409
        )
    if str(observation.get("status") or "") != "success" or not bool(
        observation.get("deployment_complete")
    ):
        raise SoftwareFactoryError(
            "velia_factory_release_passport_deployment_not_complete", status=409
        )
    if str(candidate.get("status") or "") != "eligible" or not bool(
        candidate.get("release_eligible")
    ):
        raise SoftwareFactoryError(
            "velia_factory_release_passport_candidate_not_eligible", status=409
        )

    repositories = _repository_chain(
        release, plan, candidate, verification, observation, certificate
    )
    chain = {
        "candidate": {
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "source_id": str(candidate.get("source_id") or ""),
            "source_fingerprint": str(candidate.get("source_fingerprint") or ""),
            "created_at": str(candidate.get("created_at") or ""),
        },
        "approval": {
            "sequence_id": int(approval_event.get("sequence_id") or 0),
            "decision_id": str(approval_event.get("decision_id") or ""),
            "decision": "approved",
            "created_at": str(approval_event.get("created_at") or ""),
        },
        "preflight": {
            "plan_id": str(plan.get("plan_id") or ""),
            "plan_fingerprint": str(plan.get("plan_fingerprint") or ""),
            "created_at": str(plan.get("created_at") or ""),
        },
        "release_execution": {
            "execution_id": str(release.get("execution_id") or ""),
            "status": "completed",
            "merged_count": int(release.get("merged_count") or 0),
            "created_at": str(release.get("created_at") or ""),
            "updated_at": str(release.get("updated_at") or ""),
        },
        "post_merge_verification": {
            "verification_id": str(verification.get("verification_id") or ""),
            "verification_fingerprint": str(
                verification.get("verification_fingerprint") or ""
            ),
            "status": "verified",
            "created_at": str(verification.get("created_at") or ""),
        },
        "deployment_observation": {
            "observation_id": str(observation.get("observation_id") or ""),
            "observation_fingerprint": str(
                observation.get("observation_fingerprint") or ""
            ),
            "status": "success",
            "created_at": str(observation.get("created_at") or ""),
        },
        "completion_certificate": {
            "certificate_id": str(certificate.get("certificate_id") or ""),
            "certificate_fingerprint": str(
                certificate.get("certificate_fingerprint") or ""
            ),
            "status": "complete",
            "created_at": str(certificate.get("created_at") or ""),
        },
        "repositories": repositories,
    }
    evidence_chain_hash = _fingerprint(chain)
    snapshot = {
        "certificate_id": str(certificate_id),
        "release_execution_id": execution_id,
        "status": "complete",
        "release_complete": True,
        "evidence_chain": chain,
        "evidence_chain_hash": evidence_chain_hash,
        "repository_count": len(repositories),
        "network_accessed": False,
        "github_accessed": False,
        "railway_accessed": False,
        "merge_supported": False,
        "deployment_supported": False,
        "revert_supported": False,
        "created_at": _utcnow().isoformat() + "Z",
    }
    snapshot["passport_fingerprint"] = _fingerprint(
        {
            "certificate_id": snapshot["certificate_id"],
            "release_execution_id": execution_id,
            "evidence_chain_hash": evidence_chain_hash,
            "repository_count": len(repositories),
        }
    )
    return snapshot


def _passport_row(row: Any) -> Dict[str, Any]:
    snapshot = _loads(_value(row, "snapshot_json", 6, "{}"), {})
    result = dict(snapshot) if isinstance(snapshot, dict) else {}
    result.update(
        {
            "passport_id": str(_value(row, "passport_id", 0, "")),
            "certificate_id": str(_value(row, "certificate_id", 1, "")),
            "release_execution_id": str(
                _value(row, "release_execution_id", 2, "")
            ),
            "user_id": int(_value(row, "user_id", 3, 0) or 0),
            "evidence_chain_hash": str(
                _value(row, "evidence_chain_hash", 4, "")
            ),
            "passport_fingerprint": str(
                _value(row, "passport_fingerprint", 5, "")
            ),
            "created_at": str(_value(row, "created_at", 7, "") or ""),
        }
    )
    return result


def persist_passport(
    execution_module: Any,
    user_id: int,
    snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    ensure_passport_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        passport_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO velia_software_factory_release_passports (
                passport_id,certificate_id,release_execution_id,user_id,
                evidence_chain_hash,passport_fingerprint,snapshot_json,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,certificate_id,evidence_chain_hash) DO NOTHING
            RETURNING passport_id,certificate_id,release_execution_id,user_id,
                evidence_chain_hash,passport_fingerprint,snapshot_json,created_at
            """,
            (
                passport_id,
                str(snapshot.get("certificate_id") or ""),
                str(snapshot.get("release_execution_id") or ""),
                int(user_id),
                str(snapshot.get("evidence_chain_hash") or ""),
                str(snapshot.get("passport_fingerprint") or ""),
                _json(dict(snapshot)),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "SELECT passport_id,certificate_id,release_execution_id,user_id,"
                "evidence_chain_hash,passport_fingerprint,snapshot_json,created_at "
                "FROM velia_software_factory_release_passports "
                "WHERE user_id=%s AND certificate_id=%s AND evidence_chain_hash=%s",
                (
                    int(user_id),
                    str(snapshot.get("certificate_id") or ""),
                    str(snapshot.get("evidence_chain_hash") or ""),
                ),
            )
            row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError(
                "velia_factory_release_passport_persist_failed", status=500
            )
        conn.commit()
        return _passport_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_passport(
    execution_module: Any,
    user_id: int,
    certificate_id: str,
    *,
    persist: bool = True,
) -> Dict[str, Any]:
    snapshot = build_passport_snapshot(
        execution_module, int(user_id), str(certificate_id)
    )
    if not persist:
        return snapshot
    return persist_passport(execution_module, int(user_id), snapshot)


def get_passport(
    execution_module: Any, user_id: int, passport_id: str
) -> Dict[str, Any]:
    ensure_passport_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT passport_id,certificate_id,release_execution_id,user_id,"
            "evidence_chain_hash,passport_fingerprint,snapshot_json,created_at "
            "FROM velia_software_factory_release_passports WHERE passport_id=%s AND user_id=%s",
            (str(passport_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise SoftwareFactoryError("velia_factory_release_passport_not_found", status=404)
        return _passport_row(row)
    finally:
        cursor.close()
        conn.close()


def list_passports(
    execution_module: Any,
    user_id: int,
    release_execution_id: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    ensure_passport_tables(execution_module)
    bounded = max(1, min(100, int(limit or 20)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT passport_id,certificate_id,release_execution_id,user_id,"
            "evidence_chain_hash,passport_fingerprint,snapshot_json,created_at "
            "FROM velia_software_factory_release_passports "
            "WHERE user_id=%s AND release_execution_id=%s ORDER BY created_at DESC LIMIT %s",
            (int(user_id), str(release_execution_id), bounded),
        )
        return [_passport_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()
