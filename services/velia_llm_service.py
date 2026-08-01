import os
import uuid
from typing import Any, Dict, Optional

from services import llm_service


_SUPPORTED_PROVIDERS = {"gemini", "kimi"}


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 32768) -> int:
    try:
        parsed = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def resolve_velia_provider() -> str:
    candidates = (
        os.getenv("LLM_PROVIDER_VELIA_CHAT", ""),
        os.getenv("LLM_TEXT_PROVIDER", ""),
        os.getenv("LLM_PRIMARY_PROVIDER", ""),
    )
    for candidate in candidates:
        provider = str(candidate or "").strip().lower()
        if provider in _SUPPORTED_PROVIDERS:
            return provider
    return ""


def _call_provider(
    provider: str,
    prompt: str,
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
) -> Dict[str, Any]:
    return llm_service._provider_result(
        provider,
        prompt,
        max_tokens=_env_int("VELIA_CHAT_MAX_OUTPUT_TOKENS", 1536, 128, 8192),
        feature="velia_chat",
        user_id=int(user_id),
        chat_id=None,
        is_background=False,
        primary_model=llm_service.DEFAULT_GEMINI_MODEL,
        fallback_models=llm_service.GEMINI_FALLBACK_MODELS,
        request_id=request_id,
        cycle_id=conversation_id,
        job_id=None,
        origin="velia_mobile_chat",
    )


def generate_velia_chat_result(
    prompt: str,
    *,
    user_id: int,
    conversation_id: str,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    request_id = str(request_id or uuid.uuid4())
    provider = resolve_velia_provider()
    if not provider:
        return {
            "ok": False,
            "blocked": True,
            "reason": "provider_not_configured",
            "text": "",
            "request_id": request_id,
        }

    result = _call_provider(
        provider,
        prompt,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
    )
    if not isinstance(result, dict):
        result = {"ok": False, "reason": "invalid_provider_response", "text": ""}
    result["request_id"] = request_id
    if str(result.get("text") or "").strip():
        result["ok"] = True
        return result

    fallback_provider = llm_service.resolve_fallback_provider(provider)
    if not fallback_provider or not llm_service._provider_failure_allows_fallback(result):
        return result

    fallback_result = _call_provider(
        fallback_provider,
        prompt,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
    )
    if not isinstance(fallback_result, dict):
        fallback_result = {
            "ok": False,
            "reason": "invalid_fallback_response",
            "text": "",
        }
    fallback_result["request_id"] = request_id
    fallback_result["fallback_used"] = True
    fallback_result["primary_failure_reason"] = str(result.get("reason") or "")
    if str(fallback_result.get("text") or "").strip():
        fallback_result["ok"] = True
    return fallback_result


def public_generation_metadata(result: Dict[str, Any], *, debug_usage: bool = False) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "request_id": str(result.get("request_id") or ""),
        "finish_reason": str(result.get("finish_reason") or ""),
        "fallback_used": bool(result.get("fallback_used")),
    }
    if debug_usage:
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        metadata["usage"] = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
            "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
        }
    return metadata
