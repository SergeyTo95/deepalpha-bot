from __future__ import annotations

import re
from typing import Any, Dict, Optional

from services import velia_agent_chat_planner_service as agent_planner
from services import velia_agent_chat_runtime_patch as agent_patch
from services import velia_developer_coding_service as coding_service

_REPOSITORY_SCOPE_RE = re.compile(
    r"(?:\b(?:repository|repo|github|branch|commit|pull\s+request|source\s+code|"
    r"backend|frontend|android|kotlin|python|file|function|class|endpoint|database)\b|"
    r"(?:репозитор|гитхаб|ветк|коммит|пулл\s*реквест|исходник|бэкенд|фронтенд|"
    r"андроид|котлин|питон|код(?:е|а|ом)?|файл|функц|класс|эндпоинт|баз[аеы]))",
    re.IGNORECASE,
)


def _result(text: str, request_id: Optional[str], reason: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "text": str(text),
        "provider": "velia_agent",
        "model": "velyon-agent-router",
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
        "agent_context": {
            "approval_gated": True,
            "conflict_blocked": True,
        },
    }


def _text(message: str, kind: str) -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    if kind == "coding_active":
        return (
            "В этом чате уже активен план Coding Agent. Сначала выполни или отмени его, затем создай личный план Велии."
            if russian
            else "This chat already has an active Coding Agent plan. Execute or cancel it before creating a personal VELIA plan."
        )
    if kind == "agent_active":
        return (
            "В этом чате уже активен личный план Велии. Сначала выполни или отмени его, затем начинай работу с репозиторием."
            if russian
            else "This chat already has an active personal VELIA plan. Execute or cancel it before starting repository work."
        )
    return (
        "В этом чате одновременно обнаружены два активных плана. Выполнение заблокировано; отмени один из планов."
        if russian
        else "Two active plans were detected in this chat. Execution is blocked; cancel one of the plans."
    )


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_agent_chat_conflict_patch_installed", False):
        return

    original_generate = chat_module.generate_velia_chat_result

    def generate_without_plan_conflicts(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not agent_planner.chat_agent_enabled():
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
        message = agent_patch._latest_request_user_message(str(request_id or ""), int(user_id))
        if not message:
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
        agent_job = agent_planner.active_chat_job(int(user_id), str(conversation_id))
        coding_job = coding_service.active_job(int(user_id), str(conversation_id))
        if agent_job and coding_job:
            return _result(_text(message, "both"), request_id, "velia_agent_chat_plan_conflict")
        if coding_job and agent_planner.is_agent_request(message):
            return _result(_text(message, "coding_active"), request_id, "velia_agent_chat_coding_job_active")
        if not agent_job and _REPOSITORY_SCOPE_RE.search(message):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
        if (
            agent_job
            and _REPOSITORY_SCOPE_RE.search(message)
            and not (
                agent_planner.is_approval(message)
                or agent_planner.is_cancel(message)
                or agent_planner.is_status(message)
            )
        ):
            return _result(_text(message, "agent_active"), request_id, "velia_agent_chat_job_active")
        return original_generate(
            prompt,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
        )

    chat_module.generate_velia_chat_result = generate_without_plan_conflicts
    chat_module._velia_agent_chat_conflict_patch_installed = True
