from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Mapping, Optional, Sequence

from db.database import get_connection
from services import velia_agent_chat_planner_service as agent_planner
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_chat_runtime_patch as developer_chat
from services import velia_developer_coding_service as coding_service
from services import velia_developer_project_service as project_service
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_workspace_chat_service as workspace_chat
from services import velia_software_factory_workspace_execution_service as workspace_execution
from services import velia_software_factory_workspace_hardening_patch as workspace_hardening
from services import velia_software_factory_workspace_service as workspace_service
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_ACTIVE_CONTEXT_STATES = {"selecting_repositories", "collecting_scopes", "planned", "running"}
_TERMINAL_EXECUTION_STATES = {"review_ready", "cancelled", "failed"}


def _utcnow():
    return workspace_service._utcnow()


def _json(value: Any, limit: int = 120000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


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


def ensure_workspace_chat_tables() -> None:
    global _SCHEMA_READY
    workspace_service.ensure_workspace_tables()
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_chat_contexts (
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT '',
                    execution_id TEXT NOT NULL DEFAULT '',
                    objective TEXT NOT NULL DEFAULT '',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    selection_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id,conversation_id),
                    CHECK (status IN ('selecting_repositories','collecting_scopes','planned','running','terminal','cancelled'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_workspace_chat_active "
                "ON velia_software_factory_workspace_chat_contexts(user_id,status,updated_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _context_from_row(row: Any) -> Dict[str, Any]:
    return {
        "user_id": int(_value(row, "user_id", 0, 0) or 0),
        "conversation_id": str(_value(row, "conversation_id", 1, "")),
        "status": str(_value(row, "status", 2, "")),
        "workspace_id": str(_value(row, "workspace_id", 3, "")),
        "execution_id": str(_value(row, "execution_id", 4, "")),
        "objective": str(_value(row, "objective", 5, "")),
        "plan": _loads(_value(row, "plan_json", 6, "{}"), {}),
        "selection": _loads(_value(row, "selection_json", 7, "{}"), {}),
        "created_at": str(_value(row, "created_at", 8, "") or ""),
        "updated_at": str(_value(row, "updated_at", 9, "") or ""),
    }


def get_workspace_chat_context(user_id: int, conversation_id: str) -> Dict[str, Any]:
    ensure_workspace_chat_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT user_id,conversation_id,status,workspace_id,execution_id,objective,plan_json,selection_json,created_at,updated_at "
            "FROM velia_software_factory_workspace_chat_contexts WHERE user_id=%s AND conversation_id=%s",
            (int(user_id), str(conversation_id)),
        )
        row = cursor.fetchone()
        return _context_from_row(row) if row else {}
    finally:
        cursor.close()
        conn.close()


def _save_context(
    user_id: int,
    conversation_id: str,
    *,
    status: str,
    workspace_id: str = "",
    execution_id: str = "",
    objective: str = "",
    plan: Optional[Mapping[str, Any]] = None,
    selection: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_chat_tables()
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_workspace_chat_contexts (
                user_id,conversation_id,status,workspace_id,execution_id,objective,plan_json,selection_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,conversation_id) DO UPDATE SET
                status=EXCLUDED.status,
                workspace_id=EXCLUDED.workspace_id,
                execution_id=EXCLUDED.execution_id,
                objective=EXCLUDED.objective,
                plan_json=EXCLUDED.plan_json,
                selection_json=EXCLUDED.selection_json,
                updated_at=EXCLUDED.updated_at
            """,
            (
                int(user_id), str(conversation_id), str(status), str(workspace_id), str(execution_id),
                str(objective)[:12000], _json(dict(plan or {})), _json(dict(selection or {})), now, now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_workspace_chat_context(int(user_id), str(conversation_id))


def _russian(message: str) -> bool:
    import re
    return bool(re.search(r"[А-Яа-яЁё]", str(message or "")))


def _result(
    text: str,
    request_id: Optional[str],
    *,
    reason: str,
    context: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Mapping[str, Any]] = None,
    execution: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    software_context: Dict[str, Any] = {
        "autonomous": True,
        "stage": "4.4",
        "mode": "multi_repo_workspace",
        "reasoning_summary": (
            "Цель → топология репозиториев → отдельные write scopes → cross-repo план → реализация → integration checks"
            if _russian(text)
            else "Goal → repository topology → per-repo write scopes → cross-repo plan → implementation → integration checks"
        ),
    }
    if context:
        software_context.update(
            {
                "workspace_chat_state": str(context.get("status") or ""),
                "workspace_id": str(context.get("workspace_id") or ""),
                "execution_id": str(context.get("execution_id") or ""),
            }
        )
    if workspace:
        software_context["repositories"] = [
            {
                "project_id": str(item.get("project_id") or ""),
                "repository_full_name": str(item.get("repository_full_name") or ""),
                "repo_role": str(item.get("repo_role") or ""),
                "scope_approved": bool(item.get("scope_approved")),
            }
            for item in workspace.get("repositories") or []
            if isinstance(item, Mapping)
        ]
    if execution:
        software_context.update(
            {
                "execution_state": str(execution.get("status") or ""),
                "completion_scope": str(execution.get("completion_scope") or "review_ready"),
                "integration_validation": execution.get("integration_validation") or {},
                "integration_repair": execution.get("integration_repair") or {},
            }
        )
    return {
        "ok": True,
        "text": str(text),
        "provider": "velia_software_factory",
        "model": "velyon-software-factory-workspace",
        "reason": str(reason),
        "request_id": str(request_id or ""),
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_input_tokens": 0, "reasoning_tokens": 0},
        "estimated_cost_usd": 0.0,
        "software_factory_context": software_context,
    }


def _selection_question(message: str, selection: Mapping[str, Any], projects: Sequence[Mapping[str, Any]]) -> str:
    names = ", ".join(str(item.get("repository_full_name") or "") for item in projects[:8]) or "—"
    missing = ", ".join(str(item) for item in selection.get("missing_roles") or [])
    ambiguous = selection.get("ambiguous_roles") if isinstance(selection.get("ambiguous_roles"), Mapping) else {}
    if _russian(message):
        if missing:
            return (
                "Для этого продукта нужна multi-repo команда, но не хватает подключённого репозитория роли "
                f"**{missing}**. Сейчас доступны: {names}. Подключи недостающий Developer-проект или явно назови минимум два существующих репозитория, которые использовать."
            )
        if ambiguous:
            return (
                "Я вижу несколько подходящих репозиториев одной роли и не буду угадывать. "
                f"Назови минимум два репозитория для workspace. Доступны: {names}."
            )
        return f"Назови минимум два репозитория для общего workspace. Доступны: {names}."
    if missing:
        return f"This product needs a multi-repo team, but the **{missing}** repository role is missing. Available: {names}. Connect it or explicitly name at least two existing repositories to use."
    return f"Choose at least two repositories for the workspace. Available: {names}."


def _scope_question(message: str, repository: Mapping[str, Any], recommended: Sequence[str]) -> str:
    name = str(repository.get("repository_full_name") or repository.get("project_id") or "repository")
    paths = ", ".join(f"`{item}`" for item in recommended) if recommended else "—"
    if _russian(message):
        return (
            f"Топология готова. Для **{name}** нужен отдельный write scope перед любыми изменениями GitHub. "
            f"Безопасно рекомендую: {paths}. Ответь **«используй рекомендуемые пути»** или перечисли разрешённые каталоги. "
            "Разрешение действует только на этот репозиторий и не переносится на остальные."
        )
    return (
        f"The topology is ready. **{name}** needs its own write scope before any GitHub changes. "
        f"Recommended safe paths: {paths}. Reply **“use the recommended paths”** or list approved directories. "
        "This permission applies only to this repository."
    )


def _plan_ready_text(message: str, workspace: Mapping[str, Any], *, dry_run: bool) -> str:
    names = ", ".join(str(item.get("repository_full_name") or "") for item in workspace.get("repositories") or [])
    if _russian(message):
        suffix = "Это dry-run: GitHub не изменён, Coding Autopilot не запускался." if dry_run else "Live execution пока выключен; GitHub не изменён."
        return f"Cross-repo план готов для: {names}. Все write scopes подтверждены. {suffix}"
    suffix = "This is a dry run: GitHub was not modified and Coding Autopilot was not started." if dry_run else "Live execution is currently disabled; GitHub was not modified."
    return f"The cross-repo plan is ready for: {names}. All write scopes are approved. {suffix}"


def _started_text(message: str, execution: Mapping[str, Any]) -> str:
    state = str(execution.get("status") or "running")
    if _russian(message):
        return (
            "Multi-repo команда запущена: отдельная Coding Autopilot mission на каждый репозиторий, зависимости между ними контролирует workspace scheduler. "
            f"Статус: **{state}**. Финал возможен только после review-ready PR и cross-repo Integration Validation."
        )
    return f"The multi-repo team is running with one Coding Autopilot mission per repository and workspace dependency scheduling. State: **{state}**. Completion requires review-ready PRs and cross-repo Integration Validation."


def _status_text(message: str, context: Mapping[str, Any], workspace: Optional[Mapping[str, Any]], execution: Optional[Mapping[str, Any]]) -> str:
    if execution:
        state = str(execution.get("status") or "unknown")
        counts = execution.get("progress") if isinstance(execution.get("progress"), Mapping) else {}
        progress = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items())) or "—"
        if _russian(message):
            return f"Workspace execution **{str(execution.get('execution_id') or '')[:8]}** — **{state}**. Задачи: {progress}."
        return f"Workspace execution **{str(execution.get('execution_id') or '')[:8]}** — **{state}**. Tasks: {progress}."
    state = str(context.get("status") or "unknown")
    names = ", ".join(str(item.get("repository_full_name") or "") for item in (workspace or {}).get("repositories") or [])
    if _russian(message):
        return f"Multi-repo workspace — статус **{state}**. Репозитории: {names or 'ещё не выбраны'}."
    return f"Multi-repo workspace — **{state}**. Repositories: {names or 'not selected yet'}."


def _continue_request(message: str) -> bool:
    low = str(message or "").lower()
    return any(token in low for token in ("продолж", "запуск", "запусти", "поехали", "start", "continue", "run it", "go"))


def _used_project_ids(plan: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("project_id") or "")
        for item in plan.get("tasks") or []
        if isinstance(item, Mapping) and str(item.get("project_id") or "")
    }


def _next_scope(workspace: Mapping[str, Any], plan: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    used = _used_project_ids(plan)
    for raw in workspace.get("repositories") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if used and str(item.get("project_id") or "") not in used:
            continue
        if not bool(item.get("scope_approved")):
            return item
    return None


def _live_workspace_ready(user_id: int) -> bool:
    return (
        rollout.live_execution_allowed(int(user_id))
        and workspace_execution.workspace_execution_enabled()
        and callable(getattr(workspace_execution, "integration_validator_enabled", None))
        and bool(workspace_execution.integration_validator_enabled())
        and autopilot.autopilot_enabled()
        and autopilot.worker_enabled()
    )


def _create_workspace_and_plan(
    *,
    message: str,
    user_id: int,
    conversation_id: str,
    request_id: str,
    selected_projects: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    projects = [dict(item) for item in selected_projects[:8]]
    if len(projects) < 2:
        raise SoftwareFactoryError("velia_factory_workspace_chat_requires_multi_repo", status=409)
    backend = next((item for item in projects if workspace_service.infer_repository_role(item, primary=False) == "backend"), None)
    primary = backend or projects[0]
    payload = {
        "title": str(message or "VELIA multi-repo product")[:200],
        "objective": str(message or "")[:12000],
        "primary_project_id": str(primary.get("id") or ""),
        "repositories": [
            {
                "project_id": str(item.get("id") or ""),
                "role": workspace_service.infer_repository_role(item, primary=False),
            }
            for item in projects
        ],
        "metadata": {"source": "workspace_chat", "conversation_id": str(conversation_id)[:200]},
    }
    workspace = workspace_service.create_workspace(int(user_id), payload)
    plan = workspace_chat.build_workspace_plan(
        str(message), workspace, user_id=int(user_id), request_id=str(request_id or conversation_id)
    )
    context = _save_context(
        int(user_id), str(conversation_id), status="collecting_scopes",
        workspace_id=str(workspace.get("workspace_id") or ""), objective=str(message), plan=plan,
        selection={"project_ids": [str(item.get("id") or "") for item in projects]},
    )
    return context, workspace, plan


def _execute_or_plan(
    *,
    message: str,
    request_id: str,
    user_id: int,
    conversation_id: str,
    context: Mapping[str, Any],
    workspace: Mapping[str, Any],
) -> Dict[str, Any]:
    plan = context.get("plan") if isinstance(context.get("plan"), Mapping) else {}
    normalized = workspace_service.normalize_workspace_plan(plan, workspace)
    if not bool(normalized.get("execution_ready")):
        raise SoftwareFactoryError("velia_factory_workspace_scopes_not_approved", status=409)

    if not _live_workspace_ready(int(user_id)):
        updated = _save_context(
            int(user_id), str(conversation_id), status="planned",
            workspace_id=str(workspace.get("workspace_id") or ""), objective=str(context.get("objective") or ""),
            plan=plan, selection=context.get("selection") if isinstance(context.get("selection"), Mapping) else {},
        )
        return _result(
            _plan_ready_text(message, workspace, dry_run=rollout.dry_run_enabled(int(user_id))),
            request_id,
            reason="software_factory_workspace_plan_ready",
            context=updated,
            workspace=workspace,
        )

    execution = workspace_execution.create_execution(
        int(user_id), str(workspace.get("workspace_id") or ""), plan
    )
    updated = _save_context(
        int(user_id), str(conversation_id), status="running",
        workspace_id=str(workspace.get("workspace_id") or ""), execution_id=str(execution.get("execution_id") or ""),
        objective=str(context.get("objective") or ""), plan=plan,
        selection=context.get("selection") if isinstance(context.get("selection"), Mapping) else {},
    )
    return _result(
        _started_text(message, execution), request_id,
        reason="software_factory_workspace_started", context=updated, workspace=workspace, execution=execution,
    )


def _handle_context(
    message: str,
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
    context: Mapping[str, Any],
) -> Dict[str, Any]:
    status = str(context.get("status") or "")
    if autonomy.is_cancel_request(message):
        execution_id = str(context.get("execution_id") or "")
        execution = None
        if execution_id:
            execution = workspace_execution.request_stop(int(user_id), execution_id)
        updated = _save_context(
            int(user_id), str(conversation_id), status="cancelled",
            workspace_id=str(context.get("workspace_id") or ""), execution_id=execution_id,
            objective=str(context.get("objective") or ""),
            plan=context.get("plan") if isinstance(context.get("plan"), Mapping) else {},
            selection=context.get("selection") if isinstance(context.get("selection"), Mapping) else {},
        )
        text = "Multi-repo разработка остановлена безопасно." if _russian(message) else "The multi-repo build was stopped safely."
        return _result(text, request_id, reason="software_factory_workspace_stop_requested", context=updated, execution=execution)

    if status == "selecting_repositories":
        projects = project_service.list_projects(int(user_id))
        chosen = workspace_chat.resolve_repository_choice(message, projects)
        if len(chosen) < 2:
            selection = context.get("selection") if isinstance(context.get("selection"), Mapping) else {}
            return _result(
                _selection_question(message, selection, projects), request_id,
                reason="software_factory_workspace_repositories_required", context=context,
            )
        new_context, workspace, plan = _create_workspace_and_plan(
            message=str(context.get("objective") or message), user_id=int(user_id), conversation_id=str(conversation_id),
            request_id=str(request_id), selected_projects=chosen,
        )
        pending = _next_scope(workspace, plan)
        if pending is None:
            return _execute_or_plan(
                message=message, request_id=request_id, user_id=int(user_id), conversation_id=str(conversation_id),
                context=new_context, workspace=workspace,
            )
        project = project_service.get_project(int(user_id), str(pending.get("project_id") or ""))
        recommended = autonomy.recommend_write_scope(project)
        return _result(
            _scope_question(message, pending, recommended), request_id,
            reason="software_factory_workspace_write_scope_required", context=new_context, workspace=workspace,
        )

    workspace = workspace_service.get_workspace(int(user_id), str(context.get("workspace_id") or "")) if context.get("workspace_id") else None

    if status == "collecting_scopes" and workspace:
        plan = context.get("plan") if isinstance(context.get("plan"), Mapping) else {}
        pending = _next_scope(workspace, plan)
        if pending is None:
            return _execute_or_plan(
                message=message, request_id=request_id, user_id=int(user_id), conversation_id=str(conversation_id), context=context, workspace=workspace,
            )
        project = project_service.get_project(int(user_id), str(pending.get("project_id") or ""))
        recommended = autonomy.recommend_write_scope(project)
        selected = autonomy.parse_scope_answer(message, recommended)
        if not selected:
            return _result(
                _scope_question(message, pending, recommended), request_id,
                reason="software_factory_workspace_write_scope_required", context=context, workspace=workspace,
            )
        workspace = workspace_service.approve_workspace_scope(
            int(user_id), str(workspace.get("workspace_id") or ""), str(pending.get("project_id") or ""),
            allowed_paths=selected, blocked_paths=[],
        )
        next_pending = _next_scope(workspace, plan)
        if next_pending:
            next_project = project_service.get_project(int(user_id), str(next_pending.get("project_id") or ""))
            next_recommended = autonomy.recommend_write_scope(next_project)
            return _result(
                _scope_question(message, next_pending, next_recommended), request_id,
                reason="software_factory_workspace_write_scope_required", context=context, workspace=workspace,
            )
        return _execute_or_plan(
            message=message, request_id=request_id, user_id=int(user_id), conversation_id=str(conversation_id), context=context, workspace=workspace,
        )

    if status == "planned" and workspace:
        if _continue_request(message) and _live_workspace_ready(int(user_id)):
            return _execute_or_plan(
                message=message, request_id=request_id, user_id=int(user_id), conversation_id=str(conversation_id), context=context, workspace=workspace,
            )
        return _result(
            _status_text(message, context, workspace, None), request_id,
            reason="software_factory_workspace_planned", context=context, workspace=workspace,
        )

    if status == "running" and context.get("execution_id"):
        execution = workspace_execution.get_execution(int(user_id), str(context.get("execution_id") or ""))
        execution_status = str(execution.get("status") or "")
        if execution_status in _TERMINAL_EXECUTION_STATES:
            updated = _save_context(
                int(user_id), str(conversation_id), status="terminal",
                workspace_id=str(context.get("workspace_id") or ""), execution_id=str(context.get("execution_id") or ""),
                objective=str(context.get("objective") or ""), plan=context.get("plan") if isinstance(context.get("plan"), Mapping) else {},
                selection=context.get("selection") if isinstance(context.get("selection"), Mapping) else {},
            )
            return _result(
                _status_text(message, updated, workspace, execution), request_id,
                reason="software_factory_workspace_terminal", context=updated, workspace=workspace, execution=execution,
            )
        if execution_status == "blocked" and _continue_request(message):
            execution = workspace_execution.resume_execution(int(user_id), str(execution.get("execution_id") or ""))
        return _result(
            _status_text(message, context, workspace, execution), request_id,
            reason="software_factory_workspace_status", context=context, workspace=workspace, execution=execution,
        )

    return _result(
        _status_text(message, context, workspace, None), request_id,
        reason="software_factory_workspace_context", context=context, workspace=workspace,
    )


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_software_factory_workspace_chat_installed", False):
        return
    workspace_hardening.install(workspace_service)
    original_generate = chat_module.generate_velia_chat_result

    def generate_with_workspace_factory(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not workspace_chat.workspace_chat_enabled():
            return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
        message = developer_chat._latest_request_user_message(str(request_id or ""), int(user_id))
        if not message:
            return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
        try:
            context = get_workspace_chat_context(int(user_id), str(conversation_id))
            if context and str(context.get("status") or "") in _ACTIVE_CONTEXT_STATES:
                return _handle_context(
                    message, user_id=int(user_id), conversation_id=str(conversation_id), request_id=str(request_id or ""), context=context,
                )

            if not autonomy.is_build_intent(message):
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
            if not rollout.intake_allowed(int(user_id)):
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
            if autonomy.get_chat_run(int(user_id), str(conversation_id)):
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
            if agent_planner.active_chat_job(int(user_id), str(conversation_id)) or coding_service.active_job(int(user_id), str(conversation_id)):
                text = "В этом чате уже выполняется другой Coding/Agent план. Заверши или отмени его перед multi-repo workspace." if _russian(message) else "Another Coding/Agent plan is already active in this chat. Complete or cancel it before starting a multi-repo workspace."
                return _result(text, request_id, reason="software_factory_workspace_plan_conflict")

            projects = project_service.list_projects(int(user_id))
            selection = workspace_chat.select_workspace_projects(message, projects)
            selection_status = str(selection.get("status") or "")
            if selection_status == "single":
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
            if selection_status in {"missing_roles", "ambiguous"}:
                context = _save_context(
                    int(user_id), str(conversation_id), status="selecting_repositories", objective=message,
                    selection={
                        "status": selection_status,
                        "required_roles": list(selection.get("required_roles") or []),
                        "missing_roles": list(selection.get("missing_roles") or []),
                        "ambiguous_roles": dict(selection.get("ambiguous_roles") or {}),
                    },
                )
                return _result(
                    _selection_question(message, selection, projects), request_id,
                    reason="software_factory_workspace_repositories_required", context=context,
                )
            selected = [dict(item) for item in selection.get("projects") or [] if isinstance(item, Mapping)]
            if len(selected) < 2:
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
            context, workspace, plan = _create_workspace_and_plan(
                message=message, user_id=int(user_id), conversation_id=str(conversation_id), request_id=str(request_id or ""), selected_projects=selected,
            )
            pending = _next_scope(workspace, plan)
            if pending is None:
                return _execute_or_plan(
                    message=message, request_id=str(request_id or ""), user_id=int(user_id), conversation_id=str(conversation_id), context=context, workspace=workspace,
                )
            project = project_service.get_project(int(user_id), str(pending.get("project_id") or ""))
            recommended = autonomy.recommend_write_scope(project)
            return _result(
                _scope_question(message, pending, recommended), request_id,
                reason="software_factory_workspace_write_scope_required", context=context, workspace=workspace,
            )
        except Exception as exc:
            logger.exception(
                "VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_FAILED user_id=%s conversation_id=%s error=%s",
                int(user_id), str(conversation_id), exc.__class__.__name__,
            )
            text = (
                "Multi-repo команда не была продолжена из-за внутренней ошибки. Новые GitHub-изменения не запускались этим шагом."
                if _russian(message)
                else "The multi-repo team could not continue because of an internal error. This step did not start new GitHub changes."
            )
            return _result(text, request_id, reason="software_factory_workspace_internal_error")

    chat_module.generate_velia_chat_result = generate_with_workspace_factory
    chat_module._velia_software_factory_workspace_chat_installed = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_INSTALLED enabled=%s",
        str(workspace_chat.workspace_chat_enabled()).lower(),
    )
