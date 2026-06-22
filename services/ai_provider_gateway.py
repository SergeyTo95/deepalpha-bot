import os
from typing import Any, Dict, List

from services.llm_service import GEMINI_MODEL


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def get_provider_status() -> Dict[str, Any]:
    return {
        "gemini": {"enabled": _env_enabled("GEMINI_ENABLED", True), "role": "primary", "model": GEMINI_MODEL},
        "openai": {"enabled": _env_enabled("OPENAI_ENABLED", False), "role": "future"},
        "anthropic": {"enabled": _env_enabled("ANTHROPIC_ENABLED", False), "role": "future"},
    }


def get_enabled_ai_providers() -> List[str]:
    return [name for name, info in get_provider_status().items() if info.get("enabled")]


def choose_provider_for_task(task_type: str, mode: str, quality_need: str = "normal", cost_sensitivity: str = "normal") -> Dict[str, Any]:
    status = get_provider_status()
    gemini = status.get("gemini") or {}
    return {
        "provider": "gemini",
        "model": gemini.get("model") or GEMINI_MODEL,
        "enabled": bool(gemini.get("enabled")),
        "reason": "Gemini is the only enabled provider",
        "task_type": task_type,
        "mode": mode,
        "quality_need": quality_need,
        "cost_sensitivity": cost_sensitivity,
    }
