from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Mapping

from db.database import get_connection
from services.velia_admin_security_service import configured_admin_id
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_811
_TERMINAL_RELEASE_STATUS = "terminal_blocked"

_RELEASE_DENY_HINTS = (
    "do not deploy",
    "don't deploy",
    "do not release",
    "don't release",
    "do not publish",
    "don't publish",
    "do not merge",
    "don't merge",
    "no merge",
    "without merge",
    "without merging",
    "stop before merge",
    "stop before merging",
    "no deploy",
    "no deployment",
    "without deploy",
    "without deployment",
    "не деплой",
    "не деплоить",
    "не выкатывай",
    "не выкатывать",
    "не публикуй",
    "не публиковать",
    "не релиз",
    "не релизить",
    "не мержи",
    "не мержить",
    "не сливай",
    "не объединяй",
    "без мержа",
    "без слияния",
    "без деплоя",
    "без публикации",
    "без релиза",
)
_RELEASE_ALLOW_HINTS = (
    "deploy",
    "deployment",
    "release",
    "publish",
    "ship to production",
    "ship it to production",
    "go live",
    "production deploy",
    "merge and deploy",
    "merge & deploy",
    "задеплой",
    "задеплоить",
    "деплой",
    "деплоить",
    "выкати",
    "выкатить",
    "опубликуй",
    "опубликовать",
    "релиз",
    "релизить",
    "в прод",
    "в production",
    "до прода",
    "до production",
    "смержи и задеплой",
    "мержи и задеплой",
)


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 100) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _protected_repositories() -> set[str]:
    raw = str(
        os.getenv(
            "VELIA_SOFTWARE_FACTORY_STAGE8_PROTECTED_REPOSITORIES",
            "SergeyTo95/deepalpha-bot,SergeyTo95/deepalpha-android,SergeyTo95/velia-media-worker",
        )
        or ""
    )
    return {item.strip().casefold() for item in raw.replace(";", ",").split(",") if item.strip()}


def _explicit_release_authorized(execution: Mapping[str, Any]) -> bool:
    """Use only the immutable workspace objective captured from the user's request.

    Planner/reviewer output is deliberately ignored so an agent cannot invent its
    own permission to merge or deploy. Explicit negative intent always wins.
    """
    plan = execution.get("plan") if isinstance(execution.get("plan"), Mapping) else {}
    objective = str(plan.get("objective") or "").strip().casefold()
    if not objective:
        return False
    if any(hint in objective for hint in _RELEASE_DENY_HINTS):
        return False
    return any(hint in objective for hint in _RELEASE_ALLOW_HINTS)


def ensure_stage8_release_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        execution_module.ensure_workspace_execution_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_stage8_releases (
                    user_id BIGINT NOT NULL,
                    workspace_execution_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL DEFAULT '',
                    plan_id TEXT NOT NULL DEFAULT '',
                    release_execution_id TEXT NOT NULL DEFAULT '',
                    verification_id TEXT NOT NULL DEFAULT '',
                    observation_id TEXT NOT NULL DEFAULT '',
                    certificate_id TEXT NOT NULL DEFAULT '',
                    passport_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ready',
                    blocker_code TEXT NOT NULL DEFAULT '',
                    blocker_detail TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id,workspace_execution_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_stage8_release_status "
                "ON velia_software_factory_stage8_releases(status,updated_at)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _state(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, str]:
    ensure_stage8_release_tables(execution_module)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT candidate_id,plan_id,release_execution_id,verification_id,observation_id,"
            "certificate_id,passport_id,status,blocker_code,blocker_detail "
            "FROM velia_software_factory_stage8_releases WHERE user_id=%s AND workspace_execution_id=%s",
            (int(user_id), str(execution_id)),
        )
        row = cursor.fetchone()
        if not row:
            return {"status": "ready"}
        keys = (
            "candidate_id", "plan_id", "release_execution_id", "verification_id",
            "observation_id", "certificate_id", "passport_id", "status",
            "blocker_code", "blocker_detail",
        )
        if isinstance(row, Mapping):
            return {key: str(row.get(key) or "") for key in keys}
        return {key: str(row[index] or "") for index, key in enumerate(keys)}
    finally:
        cursor.close()
        conn.close()


def _save_state(execution_module: Any, user_id: int, execution_id: str, **fields: Any) -> Dict[str, str]:
    current = _state(execution_module, int(user_id), str(execution_id))
    keys = (
        "candidate_id", "plan_id", "release_execution_id", "verification_id",
        "observation_id", "certificate_id", "passport_id", "status",
        "blocker_code", "blocker_detail",
    )
    values = {key: str(fields.get(key, current.get(key, "")) or "") for key in keys}
    values["blocker_detail"] = values["blocker_detail"][:1000]
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_stage8_releases (
                user_id,workspace_execution_id,candidate_id,plan_id,release_execution_id,
                verification_id,observation_id,certificate_id,passport_id,status,
                blocker_code,blocker_detail,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (user_id,workspace_execution_id) DO UPDATE SET
                candidate_id=EXCLUDED.candidate_id,
                plan_id=EXCLUDED.plan_id,
                release_execution_id=EXCLUDED.release_execution_id,
                verification_id=EXCLUDED.verification_id,
                observation_id=EXCLUDED.observation_id,
                certificate_id=EXCLUDED.certificate_id,
                passport_id=EXCLUDED.passport_id,
                status=EXCLUDED.status,
                blocker_code=EXCLUDED.blocker_code,
                blocker_detail=EXCLUDED.blocker_detail,
                updated_at=NOW()
            """,
            (
                int(user_id), str(execution_id), values["candidate_id"], values["plan_id"],
                values["release_execution_id"], values["verification_id"], values["observation_id"],
                values["certificate_id"], values["passport_id"], values["status"],
                values["blocker_code"], values["blocker_detail"],
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return values


def _release_candidates(execution_module: Any, limit: int) -> list[tuple[int, str]]:
    """Select fairly without letting old blocked releases monopolize a bounded tick.

    Truly immutable policy blockers are terminal and never re-enter the queue.
    Missing release authorization preserves the existing public `blocked` state but
    is explicitly excluded. Other retryable states remain eligible; every attempt
    refreshes s.updated_at, moving that item behind untouched work for the next tick.
    """
    ensure_stage8_release_tables(execution_module)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT e.user_id,e.execution_id
            FROM velia_software_factory_workspace_executions e
            LEFT JOIN velia_software_factory_stage8_releases s
              ON s.user_id=e.user_id AND s.workspace_execution_id=e.execution_id
            WHERE e.status='review_ready'
              AND COALESCE(s.status,'ready') NOT IN ('complete','terminal_blocked')
              AND NOT (
                  COALESCE(s.status,'')='blocked'
                  AND COALESCE(s.blocker_code,'')='velia_factory_stage8_release_authorization_required'
              )
            ORDER BY COALESCE(s.updated_at,e.updated_at) ASC, e.updated_at ASC, e.execution_id ASC
            LIMIT %s
            """,
            (min(100, max(1, int(limit))),),
        )
        return [(int(row[0]), str(row[1])) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def _assert_integration_passed(execution: Mapping[str, Any]) -> None:
    validation = execution.get("integration_validation") if isinstance(execution.get("integration_validation"), Mapping) else {}
    report = validation.get("report") if isinstance(validation.get("report"), Mapping) else {}
    if str(validation.get("status") or "") != "passed" or str(report.get("status") or validation.get("status") or "") != "passed":
        raise SoftwareFactoryError("velia_factory_stage8_integration_not_passed", status=409)


def _assert_repository_scope(user_id: int, candidate: Mapping[str, Any]) -> None:
    if int(user_id) == configured_admin_id():
        return
    protected = _protected_repositories()
    for item in candidate.get("repositories") or []:
        if not isinstance(item, Mapping):
            continue
        repo = str(item.get("repository_full_name") or "").strip().casefold()
        if repo and repo in protected:
            raise SoftwareFactoryError(
                "velia_factory_stage8_protected_repository_forbidden",
                detail=repo,
                status=403,
            )


def _progress_release(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
    from services import velia_software_factory_rollout_service as rollout
    from services import velia_software_factory_stage8_full_autonomy_service as stage8

    if rollout.rollout_mode() != rollout.ROLLOUT_FULL_AUTONOMY:
        return {"status": "inactive", "execution_id": str(execution_id)}
    if not rollout.user_allowed(int(user_id)):
        return {"status": "forbidden", "execution_id": str(execution_id)}
    if not stage8.execution_allowed(int(user_id), user_eligible=True):
        return {"status": "not_ready", "execution_id": str(execution_id)}

    current = execution_module.get_execution(int(user_id), str(execution_id))
    if str(current.get("status") or "") != "review_ready":
        return {"status": "not_review_ready", "execution_id": str(execution_id)}
    _assert_integration_passed(current)

    if not _explicit_release_authorized(current):
        return _save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            status="blocked",
            blocker_code="velia_factory_stage8_release_authorization_required",
            blocker_detail="Explicit user deploy/release intent is absent from the immutable workspace objective.",
        )

    state = _state(execution_module, int(user_id), str(execution_id))
    if state.get("status") == "complete" and state.get("passport_id"):
        return {**state, "execution_id": str(execution_id)}

    # Once a release execution exists, never go back through candidate approval or
    # exact-head preflight. The PR may already be merged, so those pre-merge
    # invariants are no longer expected to validate. Resume from persisted evidence.
    if state.get("release_execution_id"):
        release = execution_module.get_release_execution(
            int(user_id), state["release_execution_id"]
        )
        release_id = str(release.get("execution_id") or state["release_execution_id"])
    else:
        candidate = (
            execution_module.get_delivery_candidate(int(user_id), state["candidate_id"])
            if state.get("candidate_id")
            else execution_module.evaluate_delivery_candidate(int(user_id), str(execution_id))
        )
        if str(candidate.get("status") or "") != "eligible" or not bool(candidate.get("release_eligible")):
            return _save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                candidate_id=str(candidate.get("candidate_id") or ""),
                status="blocked",
                blocker_code="velia_factory_stage8_candidate_not_eligible",
            )
        try:
            _assert_repository_scope(int(user_id), candidate)
        except SoftwareFactoryError as exc:
            if str(getattr(exc, "code", "")) != "velia_factory_stage8_protected_repository_forbidden":
                raise
            return _save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                candidate_id=str(candidate.get("candidate_id") or ""),
                status=_TERMINAL_RELEASE_STATUS,
                blocker_code=str(exc.code),
                blocker_detail=str(getattr(exc, "detail", "") or "")[:1000],
            )
        candidate_id = str(candidate.get("candidate_id") or "")
        state = _save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            candidate_id=candidate_id,
            blocker_code="",
            blocker_detail="",
        )

        if state.get("plan_id"):
            plan = execution_module.get_release_preflight(int(user_id), state["plan_id"])
        else:
            execution_module.record_delivery_decision(
                int(user_id),
                candidate_id,
                "approved",
                note="stage8_full_autonomy_user_request_authorized_after_reviewer",
            )
            plan = execution_module.prepare_release_preflight(int(user_id), candidate_id)
            state = _save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                plan_id=str(plan.get("plan_id") or ""),
                status="preflight_prepared",
            )

        plan_id = str(plan.get("plan_id") or state.get("plan_id") or "")
        execution_module.validate_release_preflight(int(user_id), plan_id)
        release = execution_module.create_release_execution(int(user_id), plan_id)
        release_id = str(release.get("execution_id") or "")
        state = _save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            release_execution_id=release_id,
            status="merging",
        )

    release_status = str(release.get("status") or "")
    if release_status != "completed":
        release = execution_module.execute_release(int(user_id), release_id)
        release_status = str(release.get("status") or "")
    if release_status != "completed":
        return _save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            release_execution_id=release_id,
            status="release_" + (release_status or "unknown"),
            blocker_code=str(release.get("error_code") or ""),
            blocker_detail=str(release.get("error_detail") or ""),
        )

    state = _save_state(
        execution_module,
        int(user_id),
        str(execution_id),
        release_execution_id=release_id,
        status="merged",
        blocker_code="",
        blocker_detail="",
    )

    verification = (
        execution_module.get_release_verification(int(user_id), state["verification_id"])
        if state.get("verification_id")
        else execution_module.verify_release_execution(int(user_id), release_id)
    )
    verification_id = str(verification.get("verification_id") or state.get("verification_id") or "")
    if str(verification.get("verification_status") or "") != "verified":
        return _save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            verification_id=verification_id,
            status="verification_failed",
            blocker_code="velia_factory_stage8_release_verification_failed",
        )
    state = _save_state(
        execution_module,
        int(user_id),
        str(execution_id),
        verification_id=verification_id,
        status="deployment_observing",
    )

    # Deployment observations are immutable snapshots. Re-observe until a fresh
    # exact-merge-SHA snapshot reports the configured source auto-deploy contexts.
    if state.get("observation_id"):
        previous_observation = execution_module.get_deployment_observation(
            int(user_id), state["observation_id"]
        )
    else:
        previous_observation = {}
    if (
        str(previous_observation.get("status") or "") == "success"
        and bool(previous_observation.get("deployment_complete"))
    ):
        observation = previous_observation
    else:
        observation = execution_module.observe_release_deployment(int(user_id), verification_id)
    observation_id = str(observation.get("observation_id") or state.get("observation_id") or "")
    observation_status = str(observation.get("status") or "")
    state = _save_state(
        execution_module,
        int(user_id),
        str(execution_id),
        observation_id=observation_id,
        status="deployment_" + (observation_status or "unknown"),
    )
    if observation_status != "success" or not bool(observation.get("deployment_complete")):
        return state

    if state.get("certificate_id"):
        certificate = execution_module.get_release_completion_certificate(
            int(user_id), state["certificate_id"]
        )
    else:
        certificate = execution_module.evaluate_release_completion(
            int(user_id), verification_id, observation_id
        )
    certificate_id = str(certificate.get("certificate_id") or state.get("certificate_id") or "")
    certificate_status = str(certificate.get("status") or "")
    state = _save_state(
        execution_module,
        int(user_id),
        str(execution_id),
        certificate_id=certificate_id,
        status="acceptance_" + (certificate_status or "unknown"),
    )
    if certificate_status != "complete" or not bool(certificate.get("release_complete")):
        return state

    if state.get("passport_id"):
        passport = execution_module.get_release_passport(int(user_id), state["passport_id"])
    else:
        passport = execution_module.create_release_passport(int(user_id), certificate_id)
    passport_id = str(passport.get("passport_id") or state.get("passport_id") or "")
    return _save_state(
        execution_module,
        int(user_id),
        str(execution_id),
        passport_id=passport_id,
        status="complete",
        blocker_code="",
        blocker_detail="",
    )


def install(execution_module: Any) -> None:
    global _INSTALLED
    if getattr(execution_module, "_workspace_stage8_release_runtime_installed", False):
        return
    if not getattr(execution_module, "_workspace_delivery_gate_installed", False):
        raise RuntimeError("stage8_release_requires_delivery_gate_runtime")
    if not getattr(execution_module, "_workspace_integration_repair_installed", False):
        raise RuntimeError("stage8_release_requires_integration_repair_runtime")

    ensure_stage8_release_tables(execution_module)
    original_supervisor_once = execution_module.run_workspace_supervisor_once

    def run_workspace_supervisor_once() -> list[Dict[str, Any]]:
        results = list(original_supervisor_once() or [])
        from services import velia_software_factory_rollout_service as rollout
        from services import velia_software_factory_stage8_full_autonomy_service as stage8

        if rollout.rollout_mode() != rollout.ROLLOUT_FULL_AUTONOMY or not stage8.enabled():
            return results
        limit = _env_int("VELIA_SOFTWARE_FACTORY_STAGE8_RELEASE_MAX_RUNS_PER_TICK", 5, 1, 20)
        for user_id, execution_id in _release_candidates(execution_module, limit):
            if not rollout.user_allowed(int(user_id)):
                continue
            try:
                results.append(_progress_release(execution_module, int(user_id), str(execution_id)))
            except Exception as exc:
                code = str(getattr(exc, "code", exc.__class__.__name__))[:160]
                detail = str(getattr(exc, "detail", str(exc)) or "")[:1000]
                try:
                    _save_state(
                        execution_module,
                        int(user_id),
                        str(execution_id),
                        status="blocked",
                        blocker_code=code,
                        blocker_detail=detail,
                    )
                except Exception:
                    logger.exception("VELIA_STAGE8_RELEASE_STATE_SAVE_FAILED execution_id=%s", execution_id)
                logger.exception(
                    "VELIA_STAGE8_RELEASE_COORDINATOR_FAILED execution_id=%s code=%s",
                    execution_id,
                    code,
                )
        return results

    execution_module.run_workspace_supervisor_once = run_workspace_supervisor_once
    execution_module.progress_stage8_release = lambda user_id, execution_id: _progress_release(
        execution_module, int(user_id), str(execution_id)
    )
    execution_module.stage8_release_state = lambda user_id, execution_id: _state(
        execution_module, int(user_id), str(execution_id)
    )
    execution_module._workspace_stage8_release_runtime_installed = True
    _INSTALLED = True
    logger.info("VELIA_SOFTWARE_FACTORY_STAGE8_RELEASE_RUNTIME_INSTALLED")
