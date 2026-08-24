from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from services import velia_agent_chat_planner_service as agent_planner
from services import velia_agent_chat_runtime_patch as agent_patch
from services import velia_developer_chat_presentation_service as developer_presentation
from services import velia_developer_coding_quote_service as coding_quote
from services import velia_developer_coding_service as coding_service

logger = logging.getLogger(__name__)

_REPOSITORY_SCOPE_RE = re.compile(
    r"(?:\b(?:repository|repo|github|branch|commit|pull\s+request|source\s+code|"
    r"backend|frontend|android|kotlin|python|file|function|class|endpoint|database)\b|"
    r"(?:репозитор|гитхаб|ветк|коммит|пулл\s*реквест|исходник|бэкенд|фронтенд|"
    r"андроид|котлин|питон|код(?:е|а|ом)?|файл|функц|класс|эндпоинт|баз[аеы]))",
    re.IGNORECASE,
)
_MOBILE_APPROVAL_RE = re.compile(
    r"^\s*(?:"
    r"выполни(?:\s+план)?|выполнить(?:\s+план)?|запускай(?:\s+план)?|"
    r"подтверждаю(?:\s+план)?|"
    r"execute(?:\s+the\s+plan|\s+plan)?|run(?:\s+the\s+plan|\s+plan)?|"
    r"approve(?:\s+the\s+plan|\s+plan)?|"
    r"uygula|planı\s+uygula|onaylıyorum|planı\s+onaylıyorum"
    r")\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _install_mobile_approval_aliases() -> None:
    if getattr(agent_planner, "_velia_mobile_approval_aliases_installed", False):
        return
    original_is_approval = agent_planner.is_approval

    def is_approval_with_mobile_aliases(message: str) -> bool:
        normalized = str(message or "").strip()
        return bool(original_is_approval(normalized) or _MOBILE_APPROVAL_RE.fullmatch(normalized))

    agent_planner.is_approval = is_approval_with_mobile_aliases
    agent_planner._velia_mobile_approval_aliases_installed = True


def _coding_approval(message: str) -> bool:
    normalized = str(message or "").strip()
    return bool(coding_service.is_approval(normalized) or _MOBILE_APPROVAL_RE.fullmatch(normalized))


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


def _coding_result(
    *,
    text: str,
    request_id: Optional[str],
    reason: str,
    job: Dict[str, Any],
    quote: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "ok": True,
        "text": str(text),
        "provider": "velia_coding_agent",
        "model": "coding-quote-v1",
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
        "estimated_cost_usd": float(job.get("estimated_cost_usd") or 0.0),
        "developer_context": {
            "project_id": str(job.get("project_id") or ""),
            "coding_job_id": str(job.get("job_id") or ""),
            "read_only": True,
            "write_pending_approval": False,
            "quoted_tokens": int(quote.get("quoted_tokens") or 0),
            "balance_tokens": int(quote.get("balance_tokens") or 0),
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
    if kind == "agent_request_active":
        return (
            "Сначала заверши или отмени текущий план Велии. После этого можно создать новый план."
            if russian
            else "Complete or cancel the current VELIA plan before creating another one."
        )
    if kind == "plan_missing":
        return (
            "Активного плана Велии нет. Сначала опиши действие, которое нужно выполнить."
            if russian
            else "There is no active VELIA plan. Describe the action first."
        )
    return (
        "В этом чате одновременно обнаружены два активных плана. Выполнение заблокировано; отмени один из планов."
        if russian
        else "Two active plans were detected in this chat. Execution is blocked; cancel one of the plans."
    )


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_agent_chat_conflict_patch_installed", False):
        return

    _install_mobile_approval_aliases()
    original_generate = chat_module.generate_velia_chat_result

    def call_inner_with_developer_presentation(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str],
        message: str,
    ) -> Dict[str, Any]:
        stream_context = agent_patch.streaming_patch._STREAM_CONTEXT
        had_delta = hasattr(stream_context, "on_delta")
        had_reset = hasattr(stream_context, "on_reset")
        original_delta = getattr(stream_context, "on_delta", None)
        original_reset = getattr(stream_context, "on_reset", None)
        coding_scope = bool(
            _REPOSITORY_SCOPE_RE.search(message)
            or coding_service.is_coding_request(message)
            or _coding_approval(message)
            or coding_service.is_cancel(message)
            or coding_service.is_status_request(message)
        )
        coding_job_before = coding_service.active_job(int(user_id), str(conversation_id))
        charged_quote: Dict[str, Any] = {}

        if coding_job_before and _coding_approval(message):
            try:
                quote = coding_quote.quote_for_job(str(coding_job_before.get("job_id") or ""))
                if not quote:
                    quote = coding_quote.create_quote(
                        user_id=int(user_id),
                        conversation_id=str(conversation_id),
                        job=coding_job_before,
                    )
                charged_quote = coding_quote.charge_quote(
                    user_id=int(user_id),
                    job_id=str(coding_job_before.get("job_id") or ""),
                )
            except coding_quote.CodingQuoteError as exc:
                quote = dict(exc.quote or {})
                if exc.code == "developer_coding_insufficient_tokens" or quote.get("status") == "insufficient":
                    coding_service._update_job(
                        str(coding_job_before.get("job_id") or ""),
                        status="cancelled",
                        error_code="developer_coding_insufficient_tokens",
                    )
                    raw = _coding_result(
                        text=coding_quote.insufficient_text(message, quote),
                        request_id=request_id,
                        reason="developer_coding_insufficient_tokens",
                        job=coding_job_before,
                        quote=quote,
                    )
                    enriched = developer_presentation.enrich_result_best_effort(
                        raw,
                        user_id=int(user_id),
                        conversation_id=str(conversation_id),
                        request_id=str(request_id or ""),
                        message=str(message or ""),
                    )
                    return coding_quote.enrich_result_with_quote(
                        enriched,
                        user_id=int(user_id),
                        conversation_id=str(conversation_id),
                        request_id=str(request_id or ""),
                        message=message,
                        quote=quote,
                    )
                raise

        if coding_scope and callable(original_delta):
            def compact_delta(value: str) -> None:
                compact = developer_presentation.compact_progress_text(value)
                if callable(original_reset):
                    try:
                        original_reset()
                    except Exception:
                        pass
                if compact:
                    original_delta(compact)

            stream_context.on_delta = compact_delta

        try:
            result = original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
        except Exception:
            if charged_quote and coding_job_before:
                try:
                    coding_quote.refund_quote(
                        user_id=int(user_id),
                        job_id=str(coding_job_before.get("job_id") or ""),
                    )
                except Exception:
                    logger.exception(
                        "VELIA_CODING_QUOTE_REFUND_FAILED job_id=%s user_id=%s",
                        str(coding_job_before.get("job_id") or ""),
                        int(user_id),
                    )
            raise
        finally:
            if had_delta:
                stream_context.on_delta = original_delta
            elif hasattr(stream_context, "on_delta"):
                delattr(stream_context, "on_delta")
            if had_reset:
                stream_context.on_reset = original_reset
            elif hasattr(stream_context, "on_reset"):
                delattr(stream_context, "on_reset")

        reason = str(result.get("reason") or "")
        quote: Dict[str, Any] = {}
        job_after = coding_service.active_job(int(user_id), str(conversation_id))

        if reason == "developer_coding_plan_ready" and job_after:
            try:
                quote = coding_quote.create_quote(
                    user_id=int(user_id),
                    conversation_id=str(conversation_id),
                    job=job_after,
                )
            except Exception:
                coding_service._update_job(
                    str(job_after.get("job_id") or ""),
                    status="cancelled",
                    error_code="developer_coding_quote_unavailable",
                )
                logger.exception(
                    "VELIA_CODING_QUOTE_CREATE_FAILED job_id=%s user_id=%s",
                    str(job_after.get("job_id") or ""),
                    int(user_id),
                )
                result = _coding_result(
                    text=(
                        "Не удалось безопасно рассчитать стоимость задачи. Выполнение отменено; GitHub не изменён."
                        if re.search(r"[А-Яа-яЁё]", message)
                        else "The coding price could not be calculated safely. Execution was cancelled and GitHub was not changed."
                    ),
                    request_id=request_id,
                    reason="developer_coding_quote_unavailable",
                    job=job_after,
                    quote={},
                )
                job_after = None
            else:
                if quote.get("status") == "insufficient":
                    coding_service._update_job(
                        str(job_after.get("job_id") or ""),
                        status="cancelled",
                        error_code="developer_coding_insufficient_tokens",
                    )
                    result["reason"] = "developer_coding_insufficient_tokens"
                    result["text"] = coding_quote.insufficient_text(message, quote)
                    developer_context = result.get("developer_context") if isinstance(result.get("developer_context"), dict) else {}
                    developer_context = dict(developer_context)
                    developer_context["write_pending_approval"] = False
                    result["developer_context"] = developer_context
                else:
                    result["text"] = str(result.get("text") or "").rstrip() + "\n\n" + coding_quote.quote_text(message, quote)

        if coding_job_before and reason == "developer_coding_cancelled":
            try:
                coding_quote.cancel_quote(str(coding_job_before.get("job_id") or ""))
            except Exception:
                logger.exception(
                    "VELIA_CODING_QUOTE_CANCEL_FAILED job_id=%s",
                    str(coding_job_before.get("job_id") or ""),
                )

        if charged_quote and coding_job_before:
            if reason == "developer_coding_completed":
                coding_quote.consume_quote(str(coding_job_before.get("job_id") or ""))
                quote = coding_quote.quote_for_job(str(coding_job_before.get("job_id") or ""))
            else:
                try:
                    quote = coding_quote.refund_quote(
                        user_id=int(user_id),
                        job_id=str(coding_job_before.get("job_id") or ""),
                    )
                except Exception:
                    logger.exception(
                        "VELIA_CODING_QUOTE_REFUND_FAILED job_id=%s user_id=%s",
                        str(coding_job_before.get("job_id") or ""),
                        int(user_id),
                    )
                    quote = charged_quote

        enriched = developer_presentation.enrich_result_best_effort(
            result,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=str(request_id or ""),
            message=str(message or ""),
        )
        return coding_quote.enrich_result_with_quote(
            enriched,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=str(request_id or ""),
            message=message,
            quote=quote,
        )

    def generate_without_plan_conflicts(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        message = agent_patch._latest_request_user_message(str(request_id or ""), int(user_id))
        if not message:
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )
        if not agent_planner.chat_agent_enabled():
            return call_inner_with_developer_presentation(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
                message=message,
            )

        agent_job = agent_planner.active_chat_job(int(user_id), str(conversation_id))
        coding_job = coding_service.active_job(int(user_id), str(conversation_id))
        is_approval = agent_planner.is_approval(message)
        is_cancel = agent_planner.is_cancel(message)
        is_status = agent_planner.is_status(message)

        if agent_job and coding_job:
            return _result(_text(message, "both"), request_id, "velia_agent_chat_plan_conflict")
        if coding_job and agent_planner.is_agent_request(message):
            return _result(_text(message, "coding_active"), request_id, "velia_agent_chat_coding_job_active")
        if not agent_job and _REPOSITORY_SCOPE_RE.search(message):
            return call_inner_with_developer_presentation(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
                message=message,
            )
        if not agent_job and not coding_job and (is_approval or is_cancel or is_status):
            return agent_patch._result(
                _text(message, "plan_missing"),
                request_id,
                reason="velia_agent_chat_plan_missing",
                user_id=int(user_id),
                conversation_id=str(conversation_id),
            )
        if (
            agent_job
            and _REPOSITORY_SCOPE_RE.search(message)
            and not (is_approval or is_cancel or is_status)
        ):
            return _result(_text(message, "agent_active"), request_id, "velia_agent_chat_job_active")
        if (
            agent_job
            and agent_planner.is_agent_request(message)
            and not (is_approval or is_cancel or is_status)
        ):
            display_job = dict(agent_job)
            reminder = _text(message, "agent_request_active")
            display_job["planner_summary"] = reminder
            return agent_patch._result(
                reminder,
                request_id,
                reason="velia_agent_chat_plan_ready",
                user_id=int(user_id),
                conversation_id=str(conversation_id),
                job=display_job,
            )
        return call_inner_with_developer_presentation(
            prompt,
            user_id=user_id,
            conversation_id=conversation_id,
            request_id=request_id,
            message=message,
        )

    chat_module.generate_velia_chat_result = generate_without_plan_conflicts
    chat_module._velia_agent_chat_conflict_patch_installed = True