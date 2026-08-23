from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_project_service as project_service
from services.velia_software_factory_core_service import (
    Clarifier,
    FactoryStateMachine,
    FactoryTask,
    ProjectBrain,
    ProjectSpec,
    SoftwareFactoryError,
    TaskDAG,
)

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_501
_RUN_COLUMNS = (
    "run_id,user_id,project_id,spec_id,spec_version,spec_fingerprint,state,state_version,"
    "spec_json,brain_json,dag_json,clarification_json,correlation_id,created_at,updated_at"
)


def software_factory_enabled() -> bool:
    value = str(os.getenv("VELIA_SOFTWARE_FACTORY_ENABLED", "false") or "false").strip().lower()
    return value in {"1", "true", "yes", "on", "enabled"}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _dict_cursor(conn):
    factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=factory) if factory else conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def ensure_software_factory_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        project_service.ensure_developer_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_runs (
                    run_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id) ON DELETE CASCADE,
                    spec_id TEXT NOT NULL,
                    spec_version INTEGER NOT NULL DEFAULT 1,
                    spec_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'draft',
                    state_version INTEGER NOT NULL DEFAULT 1,
                    spec_json TEXT NOT NULL,
                    brain_json TEXT NOT NULL DEFAULT '[]',
                    dag_json TEXT NOT NULL DEFAULT '[]',
                    clarification_json TEXT NOT NULL DEFAULT '{}',
                    correlation_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (state IN (
                        'draft','clarifying','ready','planning','executing','validating','repairing',
                        'reviewing','blocked','completed','failed','cancelled'
                    ))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_runs_project "
                "ON velia_software_factory_runs(user_id, project_id, created_at DESC)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES velia_software_factory_runs(run_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    task_id TEXT NOT NULL DEFAULT '',
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(run_id, sequence_no)
                )
                """
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_factory_event_idempotency "
                "ON velia_software_factory_events(run_id, idempotency_key) WHERE idempotency_key<>''"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_events_run "
                "ON velia_software_factory_events(run_id, sequence_no ASC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _run_from_row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "run_id": str(_value(row, "run_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "project_id": str(_value(row, "project_id", 2, "")),
        "spec_id": str(_value(row, "spec_id", 3, "")),
        "spec_version": int(_value(row, "spec_version", 4, 1) or 1),
        "spec_fingerprint": str(_value(row, "spec_fingerprint", 5, "")),
        "state": str(_value(row, "state", 6, "draft")),
        "state_version": int(_value(row, "state_version", 7, 1) or 1),
        "spec": _loads(_value(row, "spec_json", 8, "{}"), {}),
        "brain": _loads(_value(row, "brain_json", 9, "[]"), []),
        "dag": _loads(_value(row, "dag_json", 10, "[]"), []),
        "clarification": _loads(_value(row, "clarification_json", 11, "{}"), {}),
        "correlation_id": str(_value(row, "correlation_id", 12, "")),
        "created_at": _iso(_value(row, "created_at", 13)),
        "updated_at": _iso(_value(row, "updated_at", 14)),
        "completion_scope": "review_ready",
    }


def _append_event(
    cursor,
    run: Mapping[str, Any],
    event_type: str,
    actor: str,
    payload: Mapping[str, Any],
    *,
    task_id: str = "",
    causation_id: str = "",
    idempotency_key: str = "",
) -> str:
    # Lock the run row before allocating a sequence so concurrent Lead advances
    # cannot produce duplicate sequence numbers for the same event stream.
    cursor.execute(
        "SELECT run_id FROM velia_software_factory_runs WHERE run_id=%s FOR UPDATE",
        (str(run["run_id"]),),
    )
    cursor.execute(
        "SELECT COALESCE(MAX(sequence_no),0)+1 AS sequence_no "
        "FROM velia_software_factory_events WHERE run_id=%s",
        (str(run["run_id"]),),
    )
    sequence = int(_value(cursor.fetchone(), "sequence_no", 0, 1) or 1)
    event_id = str(uuid.uuid4())
    cursor.execute(
        """
        INSERT INTO velia_software_factory_events (
            event_id,run_id,user_id,project_id,sequence_no,event_type,actor,task_id,
            correlation_id,causation_id,idempotency_key,payload_json
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            event_id,
            str(run["run_id"]),
            int(run["user_id"]),
            str(run["project_id"]),
            sequence,
            str(event_type)[:120],
            str(actor)[:120],
            str(task_id)[:160],
            str(run["correlation_id"])[:160],
            str(causation_id)[:160],
            str(idempotency_key)[:240],
            _json(dict(payload)),
        ),
    )
    return event_id


def _transition(cursor, run: Dict[str, Any], target: str, actor: str, reason: str) -> None:
    previous = str(run["state"])
    FactoryStateMachine.transition(previous, target)
    next_version = int(run.get("state_version") or 1) + 1
    cursor.execute(
        "UPDATE velia_software_factory_runs SET state=%s,state_version=%s,updated_at=%s "
        "WHERE run_id=%s AND state=%s AND state_version=%s",
        (target, next_version, _utcnow(), run["run_id"], previous, int(run.get("state_version") or 1)),
    )
    if cursor.rowcount != 1:
        raise SoftwareFactoryError("velia_factory_state_conflict", status=409)
    run["state"] = target
    run["state_version"] = next_version
    _append_event(cursor, run, "state.changed", actor, {"from": previous, "to": target, "reason": reason})


def get_run(user_id: int, run_id: str) -> Dict[str, Any]:
    ensure_software_factory_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_RUN_COLUMNS} FROM velia_software_factory_runs WHERE run_id=%s AND user_id=%s",
            (str(run_id), int(user_id)),
        )
        item = _run_from_row(cursor.fetchone())
        if not item:
            raise SoftwareFactoryError("velia_factory_run_not_found", status=404)
        return item
    finally:
        cursor.close()
        conn.close()


def create_run(user_id: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
    ensure_software_factory_tables()
    spec = ProjectSpec.from_payload(payload)
    project = project_service.get_project(int(user_id), spec.project_id)
    if bool(project.get("archived")):
        raise SoftwareFactoryError("velia_factory_project_archived", status=409)

    brain = ProjectBrain.from_spec(spec)
    repository = str(project.get("repository_full_name") or "").strip()
    branch = str(project.get("selected_branch") or "").strip()
    if repository:
        brain.add("repository", repository, "developer_project")
    if branch:
        brain.add("base_branch", branch, "developer_project")

    clarification = Clarifier().evaluate(spec)
    for assumption in clarification.assumptions:
        brain.add("assumption", assumption, "clarifier", confidence=0.65)
    dag = TaskDAG.from_spec(spec)

    run_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    now = _utcnow()
    run = {
        "run_id": run_id,
        "user_id": int(user_id),
        "project_id": spec.project_id,
        "spec_id": spec.spec_id,
        "spec_version": spec.version,
        "spec_fingerprint": spec.fingerprint,
        "state": "draft",
        "state_version": 1,
        "spec": spec.to_dict(),
        "brain": brain.snapshot(),
        "dag": dag.snapshot(),
        "clarification": clarification.to_dict(),
        "correlation_id": correlation_id,
    }

    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"""
            INSERT INTO velia_software_factory_runs (
                run_id,user_id,project_id,spec_id,spec_version,spec_fingerprint,state,state_version,
                spec_json,brain_json,dag_json,clarification_json,correlation_id,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,'draft',1,%s,%s,%s,%s,%s,%s,%s)
            RETURNING {_RUN_COLUMNS}
            """,
            (
                run_id,
                int(user_id),
                spec.project_id,
                spec.spec_id,
                spec.version,
                spec.fingerprint,
                _json(spec.to_dict()),
                _json(brain.snapshot()),
                _json(dag.snapshot()),
                _json(clarification.to_dict()),
                correlation_id,
                now,
                now,
            ),
        )
        stored = _run_from_row(cursor.fetchone())
        _append_event(cursor, stored, "run.created", "lead", {"spec_id": spec.spec_id, "fingerprint": spec.fingerprint})
        _append_event(cursor, stored, "project_brain.seeded", "lead", {"entry_count": len(brain.snapshot())})
        _append_event(cursor, stored, "task_dag.created", "lead", {"task_count": len(dag.tasks)})
        _transition(
            cursor,
            stored,
            "clarifying" if clarification.blocking else "ready",
            "clarifier",
            "material_gaps" if clarification.blocking else "spec_ready",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_run(user_id, run_id)


def list_events(user_id: int, run_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_software_factory_tables()
    get_run(user_id, run_id)
    safe_limit = min(500, max(1, int(limit or 200)))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT event_id,sequence_no,event_type,actor,task_id,correlation_id,causation_id,"
            "idempotency_key,payload_json,created_at FROM velia_software_factory_events "
            "WHERE run_id=%s AND user_id=%s ORDER BY sequence_no ASC LIMIT %s",
            (str(run_id), int(user_id), safe_limit),
        )
        items: List[Dict[str, Any]] = []
        for row in cursor.fetchall() or []:
            items.append(
                {
                    "event_id": str(_value(row, "event_id", 0, "")),
                    "sequence_no": int(_value(row, "sequence_no", 1, 0) or 0),
                    "event_type": str(_value(row, "event_type", 2, "")),
                    "actor": str(_value(row, "actor", 3, "")),
                    "task_id": str(_value(row, "task_id", 4, "")),
                    "correlation_id": str(_value(row, "correlation_id", 5, "")),
                    "causation_id": str(_value(row, "causation_id", 6, "")),
                    "idempotency_key": str(_value(row, "idempotency_key", 7, "")),
                    "payload": _loads(_value(row, "payload_json", 8, "{}"), {}),
                    "created_at": _iso(_value(row, "created_at", 9)),
                }
            )
        return items
    finally:
        cursor.close()
        conn.close()


def _load_dag(snapshot: List[Mapping[str, Any]]) -> TaskDAG:
    tasks: List[FactoryTask] = []
    for raw in snapshot:
        tasks.append(
            FactoryTask(
                task_id=str(raw.get("task_id") or ""),
                title=str(raw.get("title") or ""),
                goal=str(raw.get("goal") or ""),
                kind=str(raw.get("kind") or "coding"),
                depends_on=list(raw.get("depends_on") or []),
                allowed_paths=list(raw.get("allowed_paths") or []),
                status=str(raw.get("status") or "pending"),
                external_ref=str(raw.get("external_ref") or ""),
                result=dict(raw.get("result") or {}),
            )
        )
    return TaskDAG(tasks)


def answer_clarifications(user_id: int, run_id: str, answers: Mapping[str, Any]) -> Dict[str, Any]:
    run = get_run(user_id, run_id)
    current_clarification = dict(run.get("clarification") or {})
    if run["state"] != "clarifying" and not (
        run["state"] == "blocked" and bool(current_clarification.get("blocking"))
    ):
        raise SoftwareFactoryError("velia_factory_not_waiting_for_clarification", status=409)

    normalized_answers = dict(answers or {})
    spec_payload = dict(run["spec"])
    for key in ("objective", "allowed_paths", "acceptance_criteria", "constraints", "deliverables"):
        if key in normalized_answers:
            spec_payload[key] = normalized_answers[key]
    spec_payload["spec_id"] = run["spec_id"]
    spec_payload["version"] = int(run["spec_version"]) + 1
    spec = ProjectSpec.from_payload(spec_payload)
    clarification = Clarifier().evaluate(spec)
    brain = ProjectBrain(run["brain"])
    for key, value in normalized_answers.items():
        brain.add("clarification", f"{key}: {value}", "user")
    for assumption in clarification.assumptions:
        brain.add("assumption", assumption, "clarifier", confidence=0.65)
    dag = TaskDAG.from_spec(spec)

    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "UPDATE velia_software_factory_runs SET spec_version=%s,spec_fingerprint=%s,spec_json=%s,"
            "brain_json=%s,dag_json=%s,clarification_json=%s,updated_at=%s WHERE run_id=%s AND user_id=%s",
            (
                spec.version,
                spec.fingerprint,
                _json(spec.to_dict()),
                _json(brain.snapshot()),
                _json(dag.snapshot()),
                _json(clarification.to_dict()),
                _utcnow(),
                run_id,
                int(user_id),
            ),
        )
        if cursor.rowcount != 1:
            raise SoftwareFactoryError("velia_factory_run_not_found", status=404)
        run.update(
            {
                "spec_version": spec.version,
                "spec_fingerprint": spec.fingerprint,
                "spec": spec.to_dict(),
                "brain": brain.snapshot(),
                "dag": dag.snapshot(),
                "clarification": clarification.to_dict(),
            }
        )
        _append_event(
            cursor,
            run,
            "clarification.answered",
            "user",
            {"keys": sorted(normalized_answers.keys()), "spec_version": spec.version},
        )
        if not clarification.blocking:
            _transition(cursor, run, "ready", "clarifier", "material_gaps_resolved")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_run(user_id, run_id)


def _mission_for_run(user_id: int, run: Mapping[str, Any], spec: ProjectSpec, dag: TaskDAG) -> Dict[str, Any]:
    mission_name = f"VELIA Factory · {str(run['run_id'])[:8]} · {spec.title or spec.project_id}"[:200]
    for mission in autopilot.list_missions(int(user_id)):
        if (
            str(mission.get("project_id") or "") == spec.project_id
            and str(mission.get("name") or "") == mission_name
            and str(mission.get("status") or "") in {"paused", "active"}
        ):
            if str(mission.get("status") or "") != "active":
                return autopilot.set_mission_status(int(user_id), str(mission["mission_id"]), "active")
            return mission

    allowed_paths = list(spec.allowed_paths)
    if not allowed_paths:
        for task in dag.tasks.values():
            for path in task.allowed_paths:
                if path not in allowed_paths:
                    allowed_paths.append(path)
    if not allowed_paths:
        raise SoftwareFactoryError("velia_factory_write_scope_required", status=409)
    mission = autopilot.create_mission(
        int(user_id),
        spec.project_id,
        mission_name,
        allowed_paths=allowed_paths,
        blocked_paths=list(spec.blocked_paths),
        max_steps=4,
        max_files=8,
    )
    return autopilot.set_mission_status(int(user_id), str(mission["mission_id"]), "active")


def _persist_dag(cursor, run: Mapping[str, Any], dag: TaskDAG) -> None:
    cursor.execute(
        "UPDATE velia_software_factory_runs SET dag_json=%s,updated_at=%s WHERE run_id=%s AND user_id=%s",
        (_json(dag.snapshot()), _utcnow(), str(run["run_id"]), int(run["user_id"])),
    )
    if cursor.rowcount != 1:
        raise SoftwareFactoryError("velia_factory_run_not_found", status=404)


def advance_run(user_id: int, run_id: str) -> Dict[str, Any]:
    run = get_run(user_id, run_id)
    if run["state"] in {"completed", "failed", "cancelled", "clarifying", "blocked"}:
        return run

    spec = ProjectSpec.from_payload(run["spec"])
    dag = _load_dag(run["dag"])
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        if run["state"] == "ready":
            _transition(cursor, run, "planning", "lead", "build_execution_plan")
        if run["state"] == "planning":
            _transition(cursor, run, "executing", "lead", "dag_ready")

        # Coding Autopilot stays the sole owner of repository writes, CI repair,
        # review loop and merge policy. Lead only reconciles its leaf-task state.
        for task in dag.tasks.values():
            if task.status not in {"dispatched", "running"} or not task.external_ref:
                continue
            external = autopilot.get_task(int(user_id), task.external_ref)
            external_status = str(external.get("status") or "")
            if external_status in {"queued", "claimed", "planning", "executing"}:
                dag.set_status(
                    task.task_id,
                    "running",
                    external_ref=task.external_ref,
                    result={**task.result, "autopilot_status": external_status},
                )
            elif external_status == "ready_for_review":
                dag.set_status(
                    task.task_id,
                    "succeeded",
                    external_ref=task.external_ref,
                    result={
                        **task.result,
                        "autopilot_status": external_status,
                        "autopilot_result": external.get("result") or {},
                    },
                )
                _append_event(
                    cursor,
                    run,
                    "task.completed",
                    "lead",
                    {"autopilot_task_id": task.external_ref, "autopilot_status": external_status},
                    task_id=task.task_id,
                    idempotency_key=f"complete:{task.task_id}",
                )
            elif external_status in {"failed", "blocked", "cancelled"}:
                mapped = "failed" if external_status == "failed" else external_status
                dag.set_status(
                    task.task_id,
                    mapped,
                    external_ref=task.external_ref,
                    result={
                        **task.result,
                        "autopilot_status": external_status,
                        "autopilot_result": external.get("result") or {},
                        "error_code": external.get("error_code"),
                    },
                )
                _append_event(
                    cursor,
                    run,
                    "task.blocked" if mapped == "blocked" else "task.failed",
                    "lead",
                    {
                        "autopilot_task_id": task.external_ref,
                        "autopilot_status": external_status,
                        "error_code": external.get("error_code"),
                    },
                    task_id=task.task_id,
                    idempotency_key=f"terminal:{task.task_id}",
                )

        if any(task.status in {"failed", "blocked", "cancelled"} for task in dag.tasks.values()):
            if run["state"] == "executing":
                _transition(cursor, run, "blocked", "lead", "leaf_task_blocked")
            _persist_dag(cursor, run, dag)
            conn.commit()
            return get_run(user_id, run_id)

        if dag.complete():
            if run["state"] == "executing":
                _transition(cursor, run, "reviewing", "lead", "all_leaf_tasks_ready_for_review")
            if run["state"] == "reviewing":
                _transition(cursor, run, "completed", "lead", "task_dag_completed_review_ready")
            _persist_dag(cursor, run, dag)
            conn.commit()
            return get_run(user_id, run_id)

        ready = dag.ready_tasks()
        mission: Optional[Dict[str, Any]] = None
        for task in ready:
            if task.kind != "coding":
                dag.set_status(
                    task.task_id,
                    "blocked",
                    result={"reason": "execution_adapter_unavailable", "kind": task.kind},
                )
                _append_event(
                    cursor,
                    run,
                    "task.blocked",
                    "lead",
                    {"reason": "execution_adapter_unavailable", "kind": task.kind},
                    task_id=task.task_id,
                    idempotency_key=f"unsupported:{task.task_id}",
                )
                continue

            if mission is None:
                mission = _mission_for_run(int(user_id), run, spec, dag)
            instruction = task.goal
            if spec.constraints:
                instruction += "\n\nConstraints:\n- " + "\n- ".join(spec.constraints)
            if spec.acceptance_criteria:
                instruction += "\n\nAcceptance criteria:\n- " + "\n- ".join(spec.acceptance_criteria)
            queued = autopilot.enqueue_task(
                int(user_id),
                str(mission["mission_id"]),
                instruction,
                priority=50,
                client_request_id=f"factory:{run_id}:{task.task_id}",
            )
            external_ref = str(queued.get("task_id") or "")
            if not external_ref:
                raise SoftwareFactoryError("velia_factory_autopilot_task_missing", status=502)
            dag.set_status(
                task.task_id,
                "dispatched",
                external_ref=external_ref,
                result={"mission_id": mission.get("mission_id")},
            )
            _append_event(
                cursor,
                run,
                "task.dispatched",
                "lead",
                {"autopilot_task_id": external_ref, "mission_id": mission.get("mission_id")},
                task_id=task.task_id,
                idempotency_key=f"dispatch:{task.task_id}",
            )

        if any(task.status == "blocked" for task in dag.tasks.values()) and run["state"] == "executing":
            _transition(cursor, run, "blocked", "lead", "execution_adapter_unavailable")
        _persist_dag(cursor, run, dag)
        _append_event(
            cursor,
            run,
            "lead.advance",
            "lead",
            {
                "ready_count": len(ready),
                "dispatched_count": sum(1 for task in dag.tasks.values() if task.status == "dispatched"),
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_run(user_id, run_id)
