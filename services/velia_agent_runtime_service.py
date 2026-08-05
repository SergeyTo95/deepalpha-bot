from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping

from services import velia_agent_job_service as jobs
from services import velia_agent_permission_service as permissions
from services import velia_agent_tool_registry_service as tools
from services.velia_agent_protocol_service import ActionRisk, ActionStatus, AgentProtocolError, JobStatus, build_action_request

logger = logging.getLogger(__name__)
_BUILTINS_READY = False


class AgentRuntimeError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:300]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def agent_core_enabled() -> bool:
    return _env_bool("VELIA_AGENT_CORE_ENABLED", False)


def _echo(user_id: int, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    return {"text": str(arguments.get("text") or "")[:4000], "processed_on": "velyon_core", "user_id": int(user_id)}


def _create_task_draft(user_id: int, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    return jobs.create_task_draft(int(user_id), str(arguments.get("title") or ""), str(arguments.get("notes") or ""))


def _list_task_drafts(user_id: int, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    return {"items": jobs.list_task_drafts(int(user_id), int(arguments.get("limit") or 50))}


def ensure_builtin_tools() -> None:
    global _BUILTINS_READY
    if _BUILTINS_READY:
        return
    definitions = (
        tools.ToolDefinition("velia.echo", "Return bounded text without side effects.", ActionRisk.READ, _echo),
        tools.ToolDefinition("velia.tasks.create_draft", "Create a reversible task draft owned by the user.", ActionRisk.WRITE_REVERSIBLE, _create_task_draft),
        tools.ToolDefinition("velia.tasks.list", "List the user's VELIA task drafts.", ActionRisk.READ, _list_task_drafts),
    )
    for definition in definitions:
        try:
            tools.register_tool(definition)
        except AgentProtocolError as exc:
            if exc.code != "velia_agent_tool_duplicate":
                raise
    _BUILTINS_READY = True


def public_tools() -> List[Dict[str, Any]]:
    ensure_builtin_tools()
    return tools.list_tools()


def plan_job(user_id: int, goal: str, raw_actions: Any, *, mode: str = "interactive") -> Dict[str, Any]:
    ensure_builtin_tools()
    if not isinstance(raw_actions, list) or not raw_actions:
        raise AgentRuntimeError("velia_agent_actions_empty")
    if len(raw_actions) > 8:
        raise AgentRuntimeError("velia_agent_actions_too_many")
    try:
        permission_mode = permissions.PermissionMode(mode)
    except ValueError as exc:
        raise AgentRuntimeError("velia_agent_mode_invalid") from exc
    actions = []
    for raw in raw_actions:
        if not isinstance(raw, dict):
            raise AgentRuntimeError("velia_agent_action_invalid")
        definition = tools.get_tool(str(raw.get("tool_name") or ""))
        decision = permissions.evaluate_action(definition.risk, mode=permission_mode)
        if decision.decision is permissions.PermissionDecisionType.DENY:
            raise AgentRuntimeError("velia_agent_action_denied", status=403, detail=decision.reason)
        actions.append(
            build_action_request(
                tool_name=definition.name,
                arguments=raw.get("arguments") or {},
                risk=definition.risk,
                requires_approval=decision.requires_approval,
                idempotency_key=raw.get("idempotency_key") or "",
            )
        )
    return jobs.create_job(int(user_id), str(goal or "")[:4000], permission_mode.value, actions)


def approve_action(user_id: int, job_id: str, action_id: str) -> Dict[str, Any]:
    return jobs.decide_action(int(user_id), job_id, action_id, "approved")


def reject_action(user_id: int, job_id: str, action_id: str) -> Dict[str, Any]:
    return jobs.decide_action(int(user_id), job_id, action_id, "rejected")


def _safe_audit(user_id: int, event_type: str, *, job_id: str, action_id: str = "", payload: Any = None) -> None:
    try:
        jobs.audit(int(user_id), event_type, job_id=job_id, action_id=action_id, payload=payload)
    except Exception:
        logger.exception(
            "VELIA_AGENT_AUDIT_WRITE_FAILED user_id=%s job_id=%s action_id=%s event_type=%s",
            int(user_id),
            str(job_id),
            str(action_id),
            str(event_type),
        )


def execute_job(user_id: int, job_id: str) -> Dict[str, Any]:
    ensure_builtin_tools()
    normalized_user_id = int(user_id)
    jobs.claim_job_for_execution(normalized_user_id, job_id)
    job = jobs.get_job(normalized_user_id, job_id)
    active_action_id = ""
    try:
        for action in job["actions"]:
            status = str(action.get("status") or "")
            if status == ActionStatus.COMPLETED.value:
                continue
            if status not in {ActionStatus.PROPOSED.value, ActionStatus.APPROVED.value}:
                raise AgentRuntimeError("velia_agent_action_not_executable", status=409, detail=status)
            active_action_id = str(action["action_id"])
            jobs.update_action(normalized_user_id, job_id, active_action_id, ActionStatus.RUNNING)
            definition = tools.get_tool(str(action["tool_name"]))
            handler_arguments = dict(action.get("arguments") or {})
            handler_arguments.update(
                {
                    "_velia_action_id": active_action_id,
                    "_velia_job_id": str(job_id),
                    "_velia_idempotency_key": str(action.get("idempotency_key") or ""),
                }
            )
            result = definition.handler(normalized_user_id, handler_arguments)
            jobs.update_action(
                normalized_user_id,
                job_id,
                active_action_id,
                ActionStatus.COMPLETED,
                result=result,
            )
            _safe_audit(
                normalized_user_id,
                "action_completed",
                job_id=job_id,
                action_id=active_action_id,
                payload={"tool": definition.name},
            )
            active_action_id = ""
        jobs.set_job_status(normalized_user_id, job_id, JobStatus.COMPLETED)
    except Exception as exc:
        code = str(getattr(exc, "code", "velia_agent_execution_failed"))[:120]
        if active_action_id:
            try:
                jobs.update_action(
                    normalized_user_id,
                    job_id,
                    active_action_id,
                    ActionStatus.FAILED,
                    error_code=code,
                )
            except Exception:
                logger.exception(
                    "VELIA_AGENT_ACTION_FAILURE_PERSIST_FAILED user_id=%s job_id=%s action_id=%s",
                    normalized_user_id,
                    str(job_id),
                    active_action_id,
                )
        jobs.set_job_status(normalized_user_id, job_id, JobStatus.FAILED)
        _safe_audit(normalized_user_id, "job_failed", job_id=job_id, payload={"error": code})
        raise
    return jobs.get_job(normalized_user_id, job_id)
