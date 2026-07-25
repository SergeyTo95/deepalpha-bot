import os
from typing import Any, Dict, List

from services.llm_service import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_FALLBACK_MODELS,
    KIMI_MODEL,
    LIVE_ANALYST_GEMINI_MODEL,
    resolve_fallback_provider,
    resolve_text_provider,
)


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def get_provider_status() -> Dict[str, Any]:
    primary = resolve_text_provider("") or "gemini"
    fallback = resolve_fallback_provider(primary)
    gemini_enabled = _env_enabled("GEMINI_ENABLED", False)
    kimi_enabled = _env_enabled("KIMI_ENABLED", False) and bool((os.getenv("KIMI_API_KEY", "") or "").strip())
    return {
        "gemini": {
            "enabled": gemini_enabled,
            "role": "primary" if primary == "gemini" else ("fallback" if fallback == "gemini" else "available"),
            "model": DEFAULT_GEMINI_MODEL,
            "default_model": DEFAULT_GEMINI_MODEL,
            "live_analyst_model": LIVE_ANALYST_GEMINI_MODEL,
            "fallback_models": list(GEMINI_FALLBACK_MODELS),
        },
        "kimi": {
            "enabled": kimi_enabled,
            "configured": bool((os.getenv("KIMI_API_KEY", "") or "").strip()),
            "role": "primary" if primary == "kimi" else ("fallback" if fallback == "kimi" else "available"),
            "model": os.getenv("KIMI_MODEL", KIMI_MODEL),
            "reasoning_effort": os.getenv("KIMI_REASONING_EFFORT", "max"),
            "base_url_configured": bool((os.getenv("KIMI_BASE_URL", "") or "").strip()),
        },
        "openai": {"enabled": _env_enabled("OPENAI_ENABLED", False), "role": "future"},
        "anthropic": {"enabled": _env_enabled("ANTHROPIC_ENABLED", False), "role": "future"},
    }


def get_enabled_ai_providers() -> List[str]:
    return [name for name, info in get_provider_status().items() if info.get("enabled")]


def choose_provider_for_task(task_type: str, mode: str, quality_need: str = "normal", cost_sensitivity: str = "normal") -> Dict[str, Any]:
    provider = resolve_text_provider(task_type) or "gemini"
    status = get_provider_status()
    selected = status.get(provider) or {}
    return {
        "provider": provider,
        "model": selected.get("model") or (KIMI_MODEL if provider == "kimi" else DEFAULT_GEMINI_MODEL),
        "enabled": bool(selected.get("enabled")),
        "reason": "Selected by task/text/primary provider configuration",
        "fallback_provider": resolve_fallback_provider(provider) or None,
        "task_type": task_type,
        "mode": mode,
        "quality_need": quality_need,
        "cost_sensitivity": cost_sensitivity,
    }
