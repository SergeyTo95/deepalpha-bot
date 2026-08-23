from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from typing import Any, Dict, List, Mapping, Optional

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services import velia_software_factory_team_service as team_service
from services.velia_software_factory_core_service import ProjectBrain, ProjectSpec


logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_502
_INSTALLED = False


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


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


def _fingerprint(kind: str, text: str) -> str:
    raw = f"{str(kind).strip().lower()}\n{str(text).strip().lower()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _artifact_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(dict(payload)).encode("utf-8")).hexdigest()


def _ensure_stage2_tables(original_ensure) -> None:
    global _SCHEMA_READY
    original_ensure()
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES velia_software_factory_runs(run_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(run_id, artifact_type, fingerprint)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_artifacts_run "
                "ON velia_software_factory_artifacts(run_id, artifact_type, created_at DESC)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_project_brain (
                    entry_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    text_value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence NUMERIC(6,5) NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    fingerprint TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id, project_id, fingerprint)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_project_brain "
                "ON velia_software_factory_project_brain(user_id, project_id, last_seen_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _store_artifact(cursor, run: Mapping[str, Any], artifact_type: str, actor: str, payload: Mapping[str, Any]) -> None:
    fingerprint = _artifact_fingerprint(payload)
    cursor.execute(
        """
        INSERT INTO velia_software_factory_artifacts (
            artifact_id,run_id,user_id,project_id,artifact_type,actor,fingerprint,payload_json
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (run_id, artifact_type, fingerprint) DO NOTHING
        """,
        (
            str(uuid.uuid4()),
            str(run["run_id"]),
            int(run["user_id"]),
            str(run["project_id"]),
            str(artifact_type)[:80],
            str(actor)[:80],
            fingerprint,
            _json(dict(payload)),
        ),
    )


def _latest_artifact(run_id: str, user_id: int, artifact_type: str) -> Dict[str, Any]:
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT payload_json FROM velia_software_factory_artifacts "
            "WHERE run_id=%s AND user_id=%s AND artifact_type=%s ORDER BY created_at DESC LIMIT 1",
            (str(run_id), int(user_id), str(artifact_type)),
        )
        row = cursor.fetchone()
        return _loads(_value(row, "payload_json", 0, "{}"), {}) if row else {}
    finally:
        cursor.close()
        conn.close()


def list_project_brain(user_id: int, project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    project_service.get_project(int(user_id), str(project_id))
    safe_limit = min(300, max(1, int(limit or 100)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT entry_id,kind,text_value,source,confidence,metadata_json,created_at,last_seen_at "
            "FROM velia_software_factory_project_brain WHERE user_id=%s AND project_id=%s "
            "ORDER BY last_seen_at DESC LIMIT %s",
            (int(user_id), str(project_id), safe_limit),
        )
        result: List[Dict[str, Any]] = []
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


def _remember_brain_entries(run: Mapping[str, Any], entries: List[Mapping[str, Any]], utcnow) -> None:
    if not entries:
        return
    conn = get_connection()
    cursor = conn.cursor()
    now = utcnow()
    try:
        for raw in entries[:120]:
            kind = str(raw.get("kind") or "fact")[:80]
            text = str(raw.get("text") or "").strip()[:8000]
            source = str(raw.get("source") or "software_factory")[:120]
            if not text:
                continue
            try:
                confidence = min(1.0, max(0.0, float(raw.get("confidence", 1.0) or 0.0)))
            except (TypeError, ValueError):
                confidence = 1.0
            fingerprint = _fingerprint(kind, text)
            cursor.execute(
                """
                INSERT INTO velia_software_factory_project_brain (
                    entry_id,user_id,project_id,kind,text_value,source,confidence,metadata_json,
                    fingerprint,created_at,last_seen_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id, project_id, fingerprint) DO UPDATE SET
                    source=EXCLUDED.source,
                    confidence=GREATEST(velia_software_factory_project_brain.confidence, EXCLUDED.confidence),
                    metadata_json=EXCLUDED.metadata_json,
                    last_seen_at=EXCLUDED.last_seen_at
                """,
                (
                    str(uuid.uuid4()),
                    int(run["user_id"]),
                    str(run["project_id"]),
                    kind,
                    text,
                    source,
                    confidence,
                    _json(dict(raw.get("metadata") or {})),
                    fingerprint,
                    now,
                    now,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _hydrate_project_brain(factory_module, run: Mapping[str, Any]) -> Dict[str, Any]:
    persistent = list_project_brain(int(run["user_id"]), str(run["project_id"]), 100)
    if not persistent:
        return dict(run)
    brain = ProjectBrain(run.get("brain") or [])
    before = len(brain.snapshot())
    for item in reversed(persistent):
        brain.add(
            item.get("kind"),
            item.get("text"),
            item.get("source"),
            confidence=item.get("confidence", 1.0),
            metadata=item.get("metadata") or {},
        )
    snapshot = brain.snapshot()
    if len(snapshot) == before:
        return dict(run)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_runs SET brain_json=%s,updated_at=%s WHERE run_id=%s AND user_id=%s",
            (factory_module._json(snapshot), factory_module._utcnow(), str(run["run_id"]), int(run["user_id"])),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    result = dict(run)
    result["brain"] = snapshot
    return result


def _transition_ready_to_planning(factory_module, run: Dict[str, Any]) -> None:
    conn = get_connection()
    cursor = factory_module._dict_cursor(conn)
    try:
        factory_module._transition(cursor, run, "planning", "lead", "stage2_architecture_planning")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _persist_team_bundle(factory_module, run: Dict[str, Any], bundle: Mapping[str, Any]) -> None:
    plan = dict(bundle.get("plan") or {})
    tasks = plan.get("tasks") or []
    if not isinstance(tasks, list) or not tasks:
        raise factory_module.SoftwareFactoryError("velia_factory_team_plan_empty", status=502)

    brain = ProjectBrain(run.get("brain") or [])
    for item in bundle.get("brain_entries") or []:
        if isinstance(item, Mapping):
            brain.add(
                item.get("kind"),
                item.get("text"),
                item.get("source"),
                confidence=item.get("confidence", 1.0),
                metadata=item.get("metadata") or {},
            )
    brain_snapshot = brain.snapshot()

    conn = get_connection()
    cursor = factory_module._dict_cursor(conn)
    try:
        cursor.execute(
            "UPDATE velia_software_factory_runs SET brain_json=%s,dag_json=%s,updated_at=%s "
            "WHERE run_id=%s AND user_id=%s AND state='planning'",
            (
                factory_module._json(brain_snapshot),
                factory_module._json(tasks),
                factory_module._utcnow(),
                str(run["run_id"]),
                int(run["user_id"]),
            ),
        )
        if cursor.rowcount != 1:
            raise factory_module.SoftwareFactoryError("velia_factory_state_conflict", status=409)
        run["brain"] = brain_snapshot
        run["dag"] = tasks

        architecture = dict(bundle.get("architecture") or {})
        design = dict(bundle.get("design") or {})
        manifest = dict(bundle.get("manifest") or {})
        _store_artifact(cursor, run, "architecture", "architect", architecture)
        _store_artifact(cursor, run, "design_brief", "designer", design)
        _store_artifact(cursor, run, "team_plan", "planner", plan)
        _store_artifact(cursor, run, "team_manifest", "lead", manifest)
        factory_module._append_event(
            cursor,
            run,
            "architect.completed",
            "architect",
            {"mode": architecture.get("mode"), "component_count": len(architecture.get("components") or [])},
            idempotency_key="stage2:architect",
        )
        if bool(design.get("required")):
            factory_module._append_event(
                cursor,
                run,
                "designer.completed",
                "designer",
                {"mode": design.get("mode"), "screen_count": len(design.get("screens") or [])},
                idempotency_key="stage2:designer",
            )
        factory_module._append_event(
            cursor,
            run,
            "planner.completed",
            "planner",
            {
                "mode": plan.get("mode"),
                "task_count": len(tasks),
                "parallelism": plan.get("parallelism"),
            },
            idempotency_key="stage2:planner",
        )
        factory_module._append_event(
            cursor,
            run,
            "team.assigned",
            "lead",
            manifest,
            idempotency_key="stage2:team-assigned",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    try:
        _remember_brain_entries(
            run,
            [item for item in bundle.get("brain_entries") or [] if isinstance(item, Mapping)],
            factory_module._utcnow,
        )
    except Exception:
        logger.exception("VELIA_SOFTWARE_FACTORY_PROJECT_BRAIN_PERSIST_FAILED run_id=%s", run.get("run_id"))


def _configure_llm_features() -> None:
    features = {
        "software_factory_architect": "GEMINI_ENABLED",
        "software_factory_designer": "GEMINI_ENABLED",
        "software_factory_planner": "GEMINI_ENABLED",
    }
    try:
        from services import gemini_budget_guard, llm_service

        gemini_budget_guard.FEATURE_FLAGS.update(features)
        llm_service._FEATURE_PROVIDER_ENV.update(features)
    except Exception:
        logger.exception("VELIA_SOFTWARE_FACTORY_LLM_FEATURE_PATCH_FAILED")


def install(factory_module) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _configure_llm_features()

    original_ensure = factory_module.ensure_software_factory_tables
    original_get = factory_module.get_run
    original_create = factory_module.create_run
    original_clarify = factory_module.answer_clarifications
    original_advance = factory_module.advance_run

    def ensure_tables() -> None:
        _ensure_stage2_tables(original_ensure)

    def get_run(user_id: int, run_id: str) -> Dict[str, Any]:
        ensure_tables()
        run = original_get(int(user_id), str(run_id))
        run["stage"] = 2
        run["team_enabled"] = team_service.team_enabled()
        run["architecture"] = _latest_artifact(str(run_id), int(user_id), "architecture")
        run["design_brief"] = _latest_artifact(str(run_id), int(user_id), "design_brief")
        run["team_plan"] = _latest_artifact(str(run_id), int(user_id), "team_plan")
        run["team_manifest"] = _latest_artifact(str(run_id), int(user_id), "team_manifest")
        return run

    def create_run(user_id: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
        ensure_tables()
        run = original_create(int(user_id), payload)
        try:
            run = _hydrate_project_brain(factory_module, run)
            _remember_brain_entries(
                run,
                [
                    item
                    for item in run.get("brain") or []
                    if isinstance(item, Mapping) and str(item.get("source") or "") in {"project_spec", "user", "clarifier"}
                ],
                factory_module._utcnow,
            )
        except Exception:
            logger.exception("VELIA_SOFTWARE_FACTORY_PROJECT_BRAIN_SEED_FAILED run_id=%s", run.get("run_id"))
        return get_run(int(user_id), str(run["run_id"]))

    def answer_clarifications(user_id: int, run_id: str, answers: Mapping[str, Any]) -> Dict[str, Any]:
        ensure_tables()
        run = original_clarify(int(user_id), str(run_id), answers)
        try:
            _remember_brain_entries(
                run,
                [
                    item
                    for item in run.get("brain") or []
                    if isinstance(item, Mapping) and str(item.get("source") or "") in {"user", "clarifier"}
                ],
                factory_module._utcnow,
            )
        except Exception:
            logger.exception("VELIA_SOFTWARE_FACTORY_PROJECT_BRAIN_CLARIFICATION_FAILED run_id=%s", run_id)
        return get_run(int(user_id), str(run_id))

    def advance_run(user_id: int, run_id: str) -> Dict[str, Any]:
        ensure_tables()
        if not team_service.team_enabled():
            return original_advance(int(user_id), str(run_id))

        run = original_get(int(user_id), str(run_id))
        if run.get("state") in {"completed", "failed", "cancelled", "clarifying", "blocked"}:
            return get_run(int(user_id), str(run_id))

        if run.get("state") == "ready":
            _transition_ready_to_planning(factory_module, run)
            run = original_get(int(user_id), str(run_id))

        if run.get("state") == "planning" and not _latest_artifact(str(run_id), int(user_id), "team_plan"):
            try:
                spec = ProjectSpec.from_payload(run.get("spec") or {})
                project = project_service.get_project(int(user_id), str(run["project_id"]))
                bundle = team_service.build_team_bundle(
                    spec,
                    run.get("brain") or [],
                    repository=str(project.get("repository_full_name") or ""),
                    branch=str(project.get("selected_branch") or ""),
                    user_id=int(user_id),
                    run_id=str(run_id),
                )
                _persist_team_bundle(factory_module, run, bundle)
                logger.info(
                    "VELIA_SOFTWARE_FACTORY_STAGE2_PLANNED run_id=%s tasks=%s roles=%s",
                    str(run_id)[:80],
                    len((bundle.get("plan") or {}).get("tasks") or []),
                    ",".join((bundle.get("manifest") or {}).get("execution_roles") or []),
                )
            except Exception:
                logger.exception("VELIA_SOFTWARE_FACTORY_STAGE2_PLAN_FAILED run_id=%s", str(run_id)[:80])
                # Stage 2 is an enhancement layer. If its model/runtime is unavailable,
                # the existing Stage 1 DAG remains the safe fallback and still routes
                # every repository write through Coding Autopilot.

        return original_advance(int(user_id), str(run_id))

    factory_module.ensure_software_factory_tables = ensure_tables
    factory_module.get_run = get_run
    factory_module.create_run = create_run
    factory_module.answer_clarifications = answer_clarifications
    factory_module.advance_run = advance_run
    factory_module.team_runtime_enabled = team_service.team_enabled
    factory_module.team_role_catalog = team_service.role_catalog
    factory_module.list_project_brain = list_project_brain
    logger.info("VELIA_SOFTWARE_FACTORY_STAGE2_RUNTIME_INSTALLED")
