import logging
from typing import Any

logger = logging.getLogger(__name__)


def install_news_agent_runtime_safety(news_module: Any) -> None:
    """Install defensive defaults without changing the public NewsAgent API."""
    for name, default in (
        ("is_background", False),
        ("cycle_id", None),
        ("job_id", None),
        ("request_id", None),
    ):
        if not hasattr(news_module, name):
            setattr(news_module, name, default)

    original_score = getattr(news_module, "_score_source", None)
    if not callable(original_score) or getattr(original_score, "_deepalpha_runtime_safe", False):
        return

    def safe_score_source(item, entities, question, deadline="", event_drivers=None):
        safe_drivers = dict(event_drivers) if isinstance(event_drivers, dict) else {}
        if not isinstance(safe_drivers.get("must_find"), list):
            safe_drivers["must_find"] = []
        return original_score(item, entities, question, deadline, safe_drivers)

    safe_score_source._deepalpha_runtime_safe = True
    news_module._score_source = safe_score_source


def install_llm_provider_diagnostics() -> None:
    """Log the exact provider failure before llm_service converts it to an empty string."""
    from services import llm_service

    original_provider_result = getattr(llm_service, "_provider_result", None)
    if not callable(original_provider_result) or getattr(
        original_provider_result, "_deepalpha_diagnostics", False
    ):
        return

    def provider_result_with_diagnostics(provider, prompt, **kwargs):
        result = original_provider_result(provider, prompt, **kwargs)
        if isinstance(result, dict) and not str(result.get("text") or "").strip():
            logger.warning(
                "LLM_PROVIDER_EMPTY feature=%s provider=%s model=%s reason=%s "
                "blocked=%s fallback_allowed=%s status=%s",
                kwargs.get("feature") or "unknown",
                provider or "unknown",
                result.get("model") or "unknown",
                result.get("reason") or "unknown",
                bool(result.get("blocked")),
                bool(result.get("fallback_allowed")),
                result.get("status_code"),
            )
        return result

    provider_result_with_diagnostics._deepalpha_diagnostics = True
    llm_service._provider_result = provider_result_with_diagnostics
