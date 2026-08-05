from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from db.database import get_connection
from services import velia_agent_chat_planner_service as planner
from services import velia_chat_streaming_runtime_patch as streaming_patch

logger = logging.getLogger(__name__)


def _latest_request_user_message(request_id: str, user_id: int) -> str:
    if not str(request_id or "").strip():
        return ""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT content
            FROM velia_messages
            WHERE request_id=%s AND user_id=%s AND role='user'
              AND status='completed' AND deleted_at IS NULL
            ORDER BY created_at DESC, message_id DESC
            LIMIT 1
            """,
            (str(request_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return ""
        if isinstance(row, dict):
            return str(row.get("content") or "").strip()
        return str(row[0] or "").strip()
    finally:
        cursor.close()
        conn.close()


def _result(
    text: str,
    request_id: Optional[str],
    *,
    reason: str,
    usage: Optional[Dict[str, Any]] = None,
    estimated_cost_usd: float = 0.0,
    job: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "read_only": False,
        "approval_gated": True,
        "provider_neutral": True,
    }
    if job:
        context.update(
            {
                "job_id": str(job.get("job_id") or ""),
                "status": str(job.get("status") or ""),
                "actions": [
                    {
                        "action_id": str(item.get("action_id") or ""),
                        "tool_name": str(item.get("tool_name") or ""),
                        "risk": str(item.get("risk") or ""),
                        "status": str(item.get("status") or ""),
                        "requires_approval": bool(item.get("requires_approval")),
                    }
                    for item in (job.get("actions") or [])
                    if isinstance(item, dict)
                ],
            }
        )
    return {
        "ok": True,
        "text": str(text),
        "provider": "velia_agent",
        "model": "velyon-agent-core",
        "reason": str(reason),
        "request_id": str(request_id or ""),
        "finish_reason": "stop",
        "usage": usage or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
        },
        "estimated_cost_usd": float(estimated_cost_usd or 0.0),
        "agent_context": context,
    }


def _language_error(message: str, code: str) -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    mapping_ru = {
        "velia_agent_chat_plan_missing": "В этом чате нет активного плана Велии.",
        "velia_agent_chat_job_already_active": "В этом чате уже есть активный план. Выполни или отмени его перед новым запросом.",
        "velia_agent_chat_no_supported_action": "Для этого запроса пока не хватает поддерживаемого действия или обязательных данных. Уточни задачу конкретнее.",
        "velia_agent_chat_tools_unavailable": "Подходящие инструменты Велии пока не подключены.",
        "velia_agent_chat_plan_cost_limit": "План не создан: запрос превысил установленный лимит стоимости.",
        "velia_agent_chat_job_running": "План уже выполняется; отмена или повторный запуск сейчас запрещены.",
        "velia_agent_approval_required": "План ожидает подтверждения.",
    }
    mapping_en = {
        "velia_agent_chat_plan_missing": "There is no active VELIA plan in this chat.",
        "velia_agent_chat_job_already_active": "This chat already has an active plan. Execute or cancel it before creating another one.",
        "velia_agent_chat_no_supported_action": "This request is missing a supported action or required details. Please make the task more specific.",
        "velia_agent_chat_tools_unavailable": "The required VELIA tools are not connected yet.",
        "velia_agent_chat_plan_cost_limit": "The plan was not created because it exceeded the configured cost limit.",
        "velia_agent_chat_job_running": "The plan is already running and cannot be cancelled or started again.",
        "velia_agent_approval_required": "The plan is waiting for approval.",
    }
    fallback = (
        f"Не удалось обработать план Велии (`{code}`). Действия не выполнялись."
        if russian
        else f"VELIA could not process the plan (`{code}`). No actions were executed."
    )
    return (mapping_ru if russian else mapping_en).get(str(code), fallback)


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_agent_chat_patch_installed", False):
        return

    original_generate = chat_module.generate_velia_chat_result

    def generate_with_agent(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not planner.chat_agent_enabled():
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        message = _latest_request_user_message(str(request_id or ""), int(user_id))
        if not message:
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        active = planner.active_chat_job(int(user_id), str(conversation_id))
        if not planner.should_handle(message, has_active_job=bool(active)):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        on_delta = getattr(streaming_patch._STREAM_CONTEXT, "on_delta", None)
        on_reset = getattr(streaming_patch._STREAM_CONTEXT, "on_reset", None)
        progress_sent = False

        def progress(text: str) -> None:
            nonlocal progress_sent
            if not callable(on_delta):
                return
            try:
                on_delta(("\n" if progress_sent else "") + str(text))
                progress_sent = True
            except Exception:
                return

        def clear_progress() -> None:
            if progress_sent and callable(on_reset):
                try:
                    on_reset()
                except Exception:
                    return

        try:
            if planner.is_cancel(message):
                cancelled = planner.cancel_chat_plan(int(user_id), str(conversation_id))
                text = (
                    "План отменён. Велия ничего не выполнила."
                    if cancelled and re.search(r"[А-Яа-яЁё]", message)
                    else "The plan was cancelled. VELIA executed nothing."
                    if cancelled
                    else planner.format_status(None, message)
                )
                return _result(text, request_id, reason="velia_agent_chat_cancelled")

            if planner.is_status(message):
                return _result(
                    planner.format_status(active, message),
                    request_id,
                    reason="velia_agent_chat_status",
                    job=active,
                )

            if planner.is_approval(message):
                progress(
                    "Выполняю подтверждённый план Велии…"
                    if re.search(r"[А-Яа-яЁё]", message)
                    else "Executing the approved VELIA plan…"
                )
                result = planner.approve_and_execute(int(user_id), str(conversation_id))
                clear_progress()
                return _result(
                    planner.format_execution(result, message),
                    request_id,
                    reason="velia_agent_chat_completed",
                    job=result,
                )

            progress(
                "Анализирую задачу и составляю безопасный план…"
                if re.search(r"[А-Яа-яЁё]", message)
                else "Analysing the task and building a safe plan…"
            )
            job = planner.create_chat_plan(
                int(user_id),
                str(conversation_id),
                message,
            )
            clear_progress()
            return _result(
                planner.format_plan(job, message),
                request_id,
                reason="velia_agent_chat_plan_ready",
                usage=job.get("usage") if isinstance(job.get("usage"), dict) else None,
                estimated_cost_usd=float(job.get("estimated_cost_usd") or 0.0),
                job=job,
            )
        except Exception as exc:
            clear_progress()
            code = str(getattr(exc, "code", "velia_agent_chat_failed") or "velia_agent_chat_failed")[:120]
            logger.warning(
                "VELIA_AGENT_CHAT_FAILED user_id=%s conversation_id=%s code=%s",
                int(user_id),
                str(conversation_id),
                code,
            )
            return _result(
                _language_error(message, code),
                request_id,
                reason=code,
                job=active,
            )

    chat_module.generate_velia_chat_result = generate_with_agent
    chat_module._velia_agent_chat_patch_installed = True
