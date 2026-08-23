from __future__ import annotations

import logging
import re
from typing import Any, Dict, Mapping, Optional

from services import velia_agent_chat_planner_service as agent_planner
from services import velia_developer_chat_runtime_patch as developer_chat
from services import velia_developer_coding_service as coding_service
from services import velia_developer_project_service as project_service
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_stage2_runtime_patch as stage2_runtime

logger = logging.getLogger(__name__)


def _russian(message: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(message or "")))


def _result(
    text: str,
    request_id: Optional[str],
    *,
    reason: str,
    run: Optional[Mapping[str, Any]] = None,
    project: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "autonomous": True,
        "stage": 3,
        "reasoning_summary": "Цель → архитектура → план задач → реализация → проверки" if _russian(text) else "Goal → architecture → task plan → implementation → checks",
    }
    if run:
        context.update(
            {
                "run_id": str(run.get("run_id") or ""),
                "state": str(run.get("state") or ""),
                "completion_scope": str(run.get("completion_scope") or "review_ready"),
                "clarification": run.get("clarification") or {},
                "team_manifest": run.get("team_manifest") or {},
            }
        )
    if project:
        context.update(
            {
                "project_id": str(project.get("id") or ""),
                "repository_full_name": str(project.get("repository_full_name") or ""),
                "selected_branch": str(project.get("selected_branch") or ""),
            }
        )
    return {
        "ok": True,
        "text": str(text),
        "provider": "velia_software_factory",
        "model": "velyon-software-factory",
        "reason": str(reason),
        "request_id": str(request_id or ""),
        "finish_reason": "stop",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
        },
        "estimated_cost_usd": 0.0,
        "software_factory_context": context,
    }


def _choose_project_text(message: str, projects: list[Dict[str, Any]]) -> str:
    names = ", ".join(str(item.get("repository_full_name") or "") for item in projects)
    if _russian(message):
        return f"Для автономной разработки нужно выбрать репозиторий. Напиши его название в следующем сообщении: {names}."
    return f"Choose the repository for the autonomous build in your next message: {names}."


def _conflict_text(message: str) -> str:
    if _russian(message):
        return "В этом чате уже выполняется другой Coding/Agent план. Заверши или отмени его, затем запускай автономную команду разработки."
    return "Another Coding/Agent plan is already active in this chat. Complete or cancel it before starting the autonomous software team."


def _scope_question(message: str, run: Mapping[str, Any], recommended: list[str]) -> str:
    paths = ", ".join(f"`{item}`" for item in recommended) if recommended else "—"
    if _russian(message):
        return (
            "Я собрал цель проекта и готов передать её команде Architect → Planner → разработчики. "
            "Перед записью в GitHub нужен один материальный допуск: какие части репозитория можно менять?\n\n"
            f"Безопасно рекомендую: {paths}.\n"
            "Можно ответить **«используй рекомендуемые пути»** или перечислить только нужные каталоги. "
            "Защищённые области (.github, auth, billing, migrations, secrets, infrastructure и т.п.) автоматически не разрешаются."
        )
    return (
        "I have the project goal and the Architect → Planner → engineering team is ready. "
        "One material permission is still required: which repository paths may the team modify?\n\n"
        f"Recommended safe scope: {paths}.\n"
        "Reply **“use the recommended paths”** or list only the directories you approve. Protected areas stay denied automatically."
    )


def _started_text(message: str, run: Mapping[str, Any]) -> str:
    state = str(run.get("state") or "")
    if _russian(message):
        return (
            "Команда разработки запущена. Велия сама ведёт проект через Architect → Planner → профильных инженеров → проверки. "
            f"Текущий статус: **{state}**. Я остановлюсь только на материальном блокере или когда изменения будут **review-ready**."
        )
    return (
        "The autonomous engineering team is running through Architect → Planner → specialist engineers → checks. "
        f"Current state: **{state}**. It will stop only for a material blocker or when the changes are **review-ready**."
    )


def _status_text(message: str, run: Mapping[str, Any]) -> str:
    state = str(run.get("state") or "unknown")
    dag = [item for item in run.get("dag") or [] if isinstance(item, Mapping)]
    counts: Dict[str, int] = {}
    for item in dag:
        status = str(item.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    progress = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items())) or "no tasks yet"
    manifest = run.get("team_manifest") if isinstance(run.get("team_manifest"), Mapping) else {}
    roles = ", ".join(str(item) for item in manifest.get("execution_roles") or [])
    if _russian(message):
        roles_text = roles or "формируются"
        return f"Factory run **{str(run.get('run_id') or '')[:8]}** — статус **{state}**. Задачи: {progress}. Команда: {roles_text}."
    return f"Factory run **{str(run.get('run_id') or '')[:8]}** — state **{state}**. Tasks: {progress}. Team: {roles or 'being formed'}."


def _stop_text(message: str, stop: Mapping[str, Any]) -> str:
    pending = list(stop.get("pending") or [])
    if str(stop.get("state") or "") == "cancelled":
        return "Автономная разработка безопасно остановлена." if _russian(message) else "The autonomous build was stopped safely."
    if _russian(message):
        return (
            "Новые задачи остановлены и миссия поставлена на паузу. "
            f"Но {len(pending)} задача уже находится внутри защищённого execution-сегмента; статус **stop_pending**. "
            "Велия завершит остановку в первой безопасной точке, не обрывая запись в GitHub посередине."
        )
    return (
        "New work is paused, but " + str(len(pending)) + " task(s) are already inside a protected execution segment. "
        "State: **stop_pending**. VELIA will finalize the stop at the first safe boundary instead of interrupting a repository write."
    )


def _resolve_project(message: str, user_id: int, conversation_id: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    projects = project_service.list_projects(int(user_id))
    if not projects:
        return None, "missing"
    explicit = developer_chat._explicit_projects(message, projects)
    if len(explicit) == 1:
        project = explicit[0]
        developer_chat._bind_project(int(user_id), str(conversation_id), str(project["id"]))
        return project, None
    if len(explicit) > 1:
        return None, "ambiguous"
    bound = developer_chat._bound_project(int(user_id), str(conversation_id))
    if bound:
        return bound, None
    if len(projects) == 1:
        project = projects[0]
        developer_chat._bind_project(int(user_id), str(conversation_id), str(project["id"]))
        return project, None
    return None, "choose"


def _recommended_from_run(run: Mapping[str, Any]) -> list[str]:
    spec = run.get("spec") if isinstance(run.get("spec"), Mapping) else {}
    metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
    return [str(item) for item in metadata.get("recommended_write_scope") or [] if str(item).strip()][:20]


def _handle_clarification(
    message: str,
    *,
    user_id: int,
    run: Mapping[str, Any],
    project: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    clarification = run.get("clarification") if isinstance(run.get("clarification"), Mapping) else {}
    questions = [item for item in clarification.get("questions") or [] if isinstance(item, Mapping)]
    if not questions:
        return None
    answers: Dict[str, Any] = {}
    for question in questions:
        key = str(question.get("key") or "")
        if key == "allowed_paths":
            recommended = _recommended_from_run(run)
            selected = autonomy.parse_scope_answer(message, recommended)
            if not selected:
                return _result(
                    _scope_question(message, run, recommended),
                    None,
                    reason="software_factory_write_scope_required",
                    run=run,
                    project=project,
                )
            answers["allowed_paths"] = selected
        elif key == "objective":
            answers["objective"] = message
    if not answers:
        return None
    updated = factory.answer_clarifications(int(user_id), str(run["run_id"]), answers)
    if str(updated.get("state") or "") == "ready":
        updated = factory.advance_run(int(user_id), str(run["run_id"]))
    return _result(
        _started_text(message, updated),
        None,
        reason="software_factory_started",
        run=updated,
        project=project,
    )


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_software_factory_chat_patch_installed", False):
        return
    stage2_runtime.install(factory)
    original_generate = chat_module.generate_velia_chat_result

    def generate_with_factory(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not (
            factory.software_factory_enabled()
            and factory.team_runtime_enabled()
            and autonomy.autonomy_enabled()
        ):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        message = developer_chat._latest_request_user_message(str(request_id or ""), int(user_id))
        if not message:
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        try:
            context = autonomy.get_chat_run(int(user_id), str(conversation_id))
            if context:
                run = context["run"]
                project = project_service.get_project(int(user_id), str(context["project_id"]))
                state = str(run.get("state") or "")
                if state in {"completed", "failed", "cancelled"}:
                    context = None
                else:
                    if autonomy.is_cancel_request(message):
                        stop = autonomy.request_stop(int(user_id), str(run["run_id"]))
                        return _result(
                            _stop_text(message, stop),
                            request_id,
                            reason="software_factory_stop_requested",
                            run=stop.get("run") or run,
                            project=project,
                        )
                    if autonomy.is_status_request(message):
                        return _result(
                            _status_text(message, factory.get_run(int(user_id), str(run["run_id"]))),
                            request_id,
                            reason="software_factory_status",
                            run=run,
                            project=project,
                        )
                    if state == "clarifying":
                        handled = _handle_clarification(
                            message,
                            user_id=int(user_id),
                            run=run,
                            project=project,
                        )
                        if handled:
                            handled["request_id"] = str(request_id or "")
                            return handled
                    return _result(
                        _status_text(message, factory.get_run(int(user_id), str(run["run_id"]))),
                        request_id,
                        reason="software_factory_active",
                        run=run,
                        project=project,
                    )

            if not autonomy.is_build_intent(message):
                return original_generate(
                    prompt,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                )

            if agent_planner.active_chat_job(int(user_id), str(conversation_id)) or coding_service.active_job(int(user_id), str(conversation_id)):
                return _result(_conflict_text(message), request_id, reason="software_factory_plan_conflict")

            project, issue = _resolve_project(message, int(user_id), str(conversation_id))
            if not project:
                projects = project_service.list_projects(int(user_id))
                if issue == "missing":
                    text = "Сначала подключи Developer-проект с GitHub-репозиторием." if _russian(message) else "Connect a Developer project backed by a GitHub repository first."
                    return _result(text, request_id, reason="software_factory_project_missing")
                return _result(
                    _choose_project_text(message, projects),
                    request_id,
                    reason="software_factory_project_required",
                )

            recommended = autonomy.recommend_write_scope(project)
            spec = autonomy.build_project_spec_from_message(
                message,
                project,
                recommended,
                user_id=int(user_id),
                request_id=str(request_id or ""),
            )
            run = factory.create_run(int(user_id), spec)
            autonomy.bind_chat_run(
                int(user_id),
                str(conversation_id),
                str(project["id"]),
                str(run["run_id"]),
            )
            if str(run.get("state") or "") == "clarifying":
                return _result(
                    _scope_question(message, run, recommended),
                    request_id,
                    reason="software_factory_write_scope_required",
                    run=run,
                    project=project,
                )
            run = factory.advance_run(int(user_id), str(run["run_id"]))
            return _result(
                _started_text(message, run),
                request_id,
                reason="software_factory_started",
                run=run,
                project=project,
            )
        except Exception as exc:
            logger.exception(
                "VELIA_SOFTWARE_FACTORY_CHAT_FAILED user_id=%s conversation_id=%s error=%s",
                int(user_id),
                str(conversation_id),
                exc.__class__.__name__,
            )
            if autonomy.is_build_intent(message):
                text = (
                    "Автономная команда не была запущена из-за внутренней ошибки. GitHub не изменён."
                    if _russian(message)
                    else "The autonomous team was not started because of an internal error. GitHub was not changed."
                )
                return _result(text, request_id, reason="software_factory_unavailable")
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

    chat_module.generate_velia_chat_result = generate_with_factory
    chat_module._velia_software_factory_chat_patch_installed = True
    logger.info("VELIA_SOFTWARE_FACTORY_CHAT_RUNTIME_INSTALLED")
