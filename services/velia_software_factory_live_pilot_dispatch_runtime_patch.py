from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Mapping

from db.database import get_connection
from services import velia_developer_project_service as project_service
from services import velia_software_factory_live_pilot_guard_service as guard
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_INSTALLED = False
_INSTALL_LOCK = threading.Lock()
_CONTEXT = threading.local()


def _context() -> Dict[str, Any]:
    value = getattr(_CONTEXT, "value", None)
    return value if isinstance(value, dict) else {}


def _set_context(value: Dict[str, Any] | None) -> None:
    if value is None:
        if hasattr(_CONTEXT, "value"):
            delattr(_CONTEXT, "value")
        return
    _CONTEXT.value = value


def _guard_required_for_user(user_id: int) -> bool:
    if not guard.live_pilot_guard_enabled():
        return False
    # Import lazily so Stage 3 can install this boundary while rollout_service is
    # still being imported during process bootstrap.
    from services import velia_software_factory_rollout_service as rollout

    return rollout.eligibility_source(int(user_id)) == "admin_pilot"


def _blocked_result(task: Any, code: str) -> Dict[str, Any]:
    current = dict(getattr(task, "result", {}) or {})
    current.update(
        {
            "reason": "live_pilot_dispatch_blocked",
            "error_code": str(code),
            "max_dispatches": 1,
        }
    )
    return current


def _persist_guard_block(factory: Any, original_ready_tasks: Any, user_id: int, run_id: str, code: str) -> Dict[str, Any]:
    run = factory.get_run(int(user_id), str(run_id))
    if str(run.get("state") or "") in {"blocked", "completed", "failed", "cancelled"}:
        return run
    dag = factory._load_dag(run.get("dag") or [])
    ctx = _context()
    task_id = str(ctx.get("factory_task_id") or "")
    target = dag.tasks.get(task_id) if task_id else None
    if target is None:
        ready = list(original_ready_tasks(dag) or [])
        target = ready[0] if ready else None
    if target is None:
        raise SoftwareFactoryError(str(code), status=409)

    dag.set_status(
        target.task_id,
        "blocked",
        external_ref=getattr(target, "external_ref", ""),
        result=_blocked_result(target, code),
    )
    conn = get_connection()
    cursor = factory._dict_cursor(conn)
    try:
        state = str(run.get("state") or "")
        if state == "ready":
            factory._transition(cursor, run, "planning", "lead", "live_pilot_dispatch_guard")
            state = "planning"
        if state == "planning":
            factory._transition(cursor, run, "executing", "lead", "live_pilot_dispatch_guard")
            state = "executing"
        if state == "executing":
            factory._transition(cursor, run, "blocked", "lead", "live_pilot_dispatch_guard")
        factory._persist_dag(cursor, run, dag)
        factory._append_event(
            cursor,
            run,
            "task.blocked",
            "lead",
            {
                "reason": "live_pilot_dispatch_guard",
                "error_code": str(code),
                "max_dispatches": 1,
            },
            task_id=str(target.task_id),
            idempotency_key=f"live-pilot-block:{target.task_id}:{str(code)[:120]}",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    logger.warning(
        "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_DISPATCH_BLOCKED run_id=%s task_id=%s error=%s",
        str(run_id)[:80],
        str(target.task_id)[:120],
        str(code)[:160],
    )
    return factory.get_run(int(user_id), str(run_id))


def install(factory_module: Any = None) -> bool:
    """Install before Stage 2 captures Lead.advance_run; inert unless the guard flag is on."""
    global _INSTALLED
    with _INSTALL_LOCK:
        if _INSTALLED:
            return True
        if factory_module is None:
            from services import velia_software_factory_lead_service as factory_module

        if getattr(factory_module, "_velia_factory_live_pilot_dispatch_guard_installed", False):
            _INSTALLED = True
            return True

        original_advance = factory_module.advance_run
        original_ready_tasks = factory_module.TaskDAG.ready_tasks
        original_mission_for_run = factory_module._mission_for_run
        original_enqueue_task = factory_module.autopilot.enqueue_task

        def guarded_ready_tasks(dag_self):
            ready = list(original_ready_tasks(dag_self) or [])
            ctx = _context()
            if not ctx.get("active"):
                return ready
            # While the one authorized task is still in flight, reconcile it but
            # never dispatch a parallel second task.
            if any(
                str(getattr(task, "status", "")) in {"dispatched", "running"}
                and bool(str(getattr(task, "external_ref", "") or "").strip())
                for task in dag_self.tasks.values()
            ):
                return []
            if not ready:
                return []
            selected = ready[0]
            ctx["factory_task_id"] = str(selected.task_id)
            ctx["client_request_id"] = f"factory:{ctx['run_id']}:{selected.task_id}"[:160]
            return [selected]

        def guarded_mission_for_run(user_id, run, spec, dag):
            ctx = _context()
            if ctx.get("active"):
                task_id = str(ctx.get("factory_task_id") or "")
                request_id = str(ctx.get("client_request_id") or "")
                if not task_id or not request_id:
                    raise SoftwareFactoryError("velia_factory_live_pilot_dispatch_identity_required", status=409)
                claimed = guard.claim_dispatch(
                    int(user_id),
                    run,
                    ctx["project"],
                    factory_task_id=task_id,
                    client_request_id=request_id,
                )
                ctx["grant_id"] = str(claimed.get("grant_id") or "")
                return original_mission_for_run(user_id, run, spec, dag)
            return original_mission_for_run(user_id, run, spec, dag)

        def guarded_enqueue_task(user_id, mission_id, instruction, *, priority=0, client_request_id=""):
            ctx = _context()
            if not ctx.get("active"):
                return original_enqueue_task(
                    user_id,
                    mission_id,
                    instruction,
                    priority=priority,
                    client_request_id=client_request_id,
                )
            expected = str(ctx.get("client_request_id") or "")
            task_id = str(ctx.get("factory_task_id") or "")
            if str(client_request_id or "") != expected or not task_id:
                raise SoftwareFactoryError("velia_factory_live_pilot_dispatch_identity_mismatch", status=409)
            # Reclaim is idempotent for the exact same Factory task/request pair.
            guard.claim_dispatch(
                int(user_id),
                ctx["run"],
                ctx["project"],
                factory_task_id=task_id,
                client_request_id=expected,
            )
            queued = original_enqueue_task(
                user_id,
                mission_id,
                instruction,
                priority=priority,
                client_request_id=client_request_id,
            )
            external_id = str((queued or {}).get("task_id") or "")
            if not external_id:
                return queued
            guard.confirm_dispatch(
                int(user_id),
                str(ctx["run_id"]),
                factory_task_id=task_id,
                client_request_id=expected,
                autopilot_task_id=external_id,
            )
            ctx["autopilot_task_id"] = external_id
            logger.info(
                "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_DISPATCH_CONSUMED run_id=%s task_id=%s autopilot_task_id=%s",
                str(ctx["run_id"])[:80],
                task_id[:120],
                external_id[:120],
            )
            return queued

        def guarded_advance(user_id: int, run_id: str):
            if not _guard_required_for_user(int(user_id)):
                return original_advance(int(user_id), str(run_id))
            run = factory_module.get_run(int(user_id), str(run_id))
            project = project_service.get_project(int(user_id), str(run.get("project_id") or ""))
            _set_context(
                {
                    "active": True,
                    "user_id": int(user_id),
                    "run_id": str(run_id),
                    "run": run,
                    "project": project,
                }
            )
            try:
                return original_advance(int(user_id), str(run_id))
            except SoftwareFactoryError as exc:
                code = str(getattr(exc, "code", "") or "")
                if code.startswith("velia_factory_live_pilot_"):
                    return _persist_guard_block(
                        factory_module,
                        original_ready_tasks,
                        int(user_id),
                        str(run_id),
                        code,
                    )
                raise
            finally:
                _set_context(None)

        factory_module.TaskDAG.ready_tasks = guarded_ready_tasks
        factory_module._mission_for_run = guarded_mission_for_run
        # autopilot is a module singleton. This wrapper is context-inert for every
        # non-Factory caller and therefore does not alter Developer Autopilot APIs.
        factory_module.autopilot.enqueue_task = guarded_enqueue_task
        factory_module.advance_run = guarded_advance
        factory_module._velia_factory_live_pilot_dispatch_guard_installed = True
        _INSTALLED = True
        logger.info(
            "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_INSTALLED enabled=%s max_dispatches=1",
            str(bool(guard.live_pilot_guard_enabled())).lower(),
        )
        return True
