import logging
import os
from typing import Any, Dict, Optional

from services.gemini_budget_guard import can_call_gemini, record_gemini_call

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))

# Gemini remains the production default. Kimi is enabled only through explicit
# provider environment variables, so deploying this code does not switch traffic.
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LIVE_ANALYST_GEMINI_MODEL = os.getenv("LIVE_ANALYST_GEMINI_MODEL", "gemini-3.5-flash")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k3")
SUPPORTED_LLM_PROVIDERS = {"gemini", "kimi"}


def _parse_live_analyst_primary_max_attempts(raw: Optional[str] = None) -> int:
    if raw is None:
        raw = os.getenv("LIVE_ANALYST_PRIMARY_MAX_ATTEMPTS", "1")
    try:
        return max(1, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 1


def _parse_live_analyst_primary_retry_delays(raw: Optional[str] = None) -> list[float]:
    if raw is None:
        raw = os.getenv("LIVE_ANALYST_PRIMARY_RETRY_DELAYS", "")
    delays: list[float] = []
    for value in str(raw or "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            delays.append(max(0.0, float(value)))
        except ValueError:
            continue
    return delays


def _parse_fallback_models(raw: Optional[str] = None) -> list[str]:
    if raw is None:
        raw = os.getenv("GEMINI_FALLBACK_MODELS", "")
    models = [model.strip() for model in raw.split(",") if model.strip()]
    return models or ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


GEMINI_FALLBACK_MODELS = _parse_fallback_models()
LIVE_ANALYST_PRIMARY_MAX_ATTEMPTS = _parse_live_analyst_primary_max_attempts()
LIVE_ANALYST_PRIMARY_RETRY_DELAYS = _parse_live_analyst_primary_retry_delays()

# Backwards-compatible aliases for existing imports/tests.
GEMINI_MODEL = DEFAULT_GEMINI_MODEL
FALLBACK_MODELS = GEMINI_FALLBACK_MODELS
RETRY_DELAYS = [5, 15, 30]


_FEATURE_PROVIDER_ENV = {
    "news_agent": "LLM_PROVIDER_NEWS",
    "decision_agent": "LLM_PROVIDER_POLYMARKET",
    "summary_agent": "LLM_PROVIDER_POLYMARKET",
    "dynamic_driver_agent": "LLM_PROVIDER_POLYMARKET",
    "signal_generation": "LLM_PROVIDER_POLYMARKET",
    "signal_cache": "LLM_PROVIDER_POLYMARKET",
    "hot_news": "LLM_PROVIDER_NEWS",
    "channel_news": "LLM_PROVIDER_NEWS",
    "live_analyst": "LLM_PROVIDER_LIVE_ANALYST",
    "watchlist_ai_summary": "LLM_PROVIDER_POLYMARKET",
    # User-initiated Studio prompt normalization shares the global Gemini gate.
    "studio_video_prompt": "GEMINI_ENABLED",
    "studio_music_prompt": "GEMINI_ENABLED",
    "studio_music_lyrics": "GEMINI_ENABLED",
}


def _normalize_provider(value: Optional[str], default: str = "") -> str:
    provider = str(value or default).strip().lower()
    return provider if provider in SUPPORTED_LLM_PROVIDERS else ""


def resolve_text_provider(feature: str = "") -> str:
    task_env = _FEATURE_PROVIDER_ENV.get(feature or "")
    candidates = [
        os.getenv(task_env, "") if task_env else "",
        os.getenv("LLM_TEXT_PROVIDER", ""),
        os.getenv("LLM_PRIMARY_PROVIDER", "gemini"),
    ]
    for candidate in candidates:
        provider = _normalize_provider(candidate)
        if provider:
            return provider
    return ""


def resolve_fallback_provider(primary_provider: str) -> str:
    provider = _normalize_provider(os.getenv("LLM_FALLBACK_PROVIDER", "none"))
    return provider if provider and provider != primary_provider else ""


def _build_url(model: str) -> str:
    # Provider HTTP transport lives exclusively in provider gateway modules.
    return ""


def _call_model_once(prompt: str, model: str, max_tokens: int) -> tuple:
    raise RuntimeError("direct LLM HTTP is disabled; use provider gateways")


def _gemini_result(
    prompt: str,
    *,
    max_tokens: int,
    feature: str,
    user_id: Optional[int],
    chat_id: Optional[int],
    is_background: bool,
    primary_model: str,
    fallback_models: list[str],
    request_id: Optional[str],
    cycle_id: Optional[str],
    job_id: Optional[str],
    origin: str,
) -> Dict[str, Any]:
    from services.gemini_gateway import generate_content

    # Gemini 2.5 Flash can spend part of maxOutputTokens on internal reasoning.
    # The Senior Reviewer must still have enough visible budget to finish its
    # strict JSON verdict; a truncated JSON object is fail-closed as unavailable.
    if feature == "software_factory_reviewer":
        max_tokens = max(8192, int(max_tokens))

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
    }
    max_attempts = (
        int(os.getenv("LIVE_ANALYST_MAX_GEMINI_ATTEMPTS_PER_REQUEST", "2"))
        if feature == "live_analyst"
        else int(os.getenv("GEMINI_DEFAULT_MAX_ATTEMPTS", "1"))
    )
    result = generate_content(
        feature=feature,
        origin=origin,
        is_background=is_background,
        request_id=request_id,
        cycle_id=cycle_id,
        job_id=job_id,
        model=primary_model,
        payload=payload,
        max_attempts=max_attempts,
        user_id=user_id,
        chat_id=chat_id,
        fallback_models=fallback_models,
        allow_fallback_model=None,
    )
    return result if isinstance(result, dict) else {"ok": False, "text": "", "reason": "invalid_response"}


def _kimi_result(
    prompt: str,
    *,
    max_tokens: int,
    feature: str,
    user_id: Optional[int],
    chat_id: Optional[int],
    is_background: bool,
    request_id: Optional[str],
    cycle_id: Optional[str],
    job_id: Optional[str],
    origin: str,
) -> Dict[str, Any]:
    from services.kimi_gateway import call_kimi

    return call_kimi(
        prompt=prompt,
        feature=feature,
        origin=origin,
        is_background=is_background,
        request_id=request_id,
        cycle_id=cycle_id,
        job_id=job_id,
        model=os.getenv("KIMI_MODEL", KIMI_MODEL),
        max_tokens=max_tokens,
        timeout=int(os.getenv("KIMI_TIMEOUT_SECONDS", "90")),
        user_id=user_id,
        chat_id=chat_id,
    )


def _provider_result(
    provider: str,
    prompt: str,
    *,
    max_tokens: int,
    feature: str,
    user_id: Optional[int],
    chat_id: Optional[int],
    is_background: bool,
    primary_model: str,
    fallback_models: list[str],
    request_id: Optional[str],
    cycle_id: Optional[str],
    job_id: Optional[str],
    origin: str,
) -> Dict[str, Any]:
    if provider == "gemini":
        return _gemini_result(
            prompt,
            max_tokens=max_tokens,
            feature=feature,
            user_id=user_id,
            chat_id=chat_id,
            is_background=is_background,
            primary_model=primary_model,
            fallback_models=fallback_models,
            request_id=request_id,
            cycle_id=cycle_id,
            job_id=job_id,
            origin=origin,
        )
    if provider == "kimi":
        return _kimi_result(
            prompt,
            max_tokens=max_tokens,
            feature=feature,
            user_id=user_id,
            chat_id=chat_id,
            is_background=is_background,
            request_id=request_id,
            cycle_id=cycle_id,
            job_id=job_id,
            origin=origin,
        )
    return {
        "ok": False,
        "blocked": True,
        "fallback_allowed": False,
        "reason": "invalid_provider",
        "text": "",
    }


def _provider_failure_allows_fallback(result: Dict[str, Any]) -> bool:
    if result.get("blocked"):
        return False
    if "fallback_allowed" in result:
        return bool(result.get("fallback_allowed"))
    status = result.get("status_code")
    reason = str(result.get("reason") or "")
    return reason in {"timeout", "connection_error", "rate_limit", "server_error", "empty_200", "json_parse_error"} or status == 429 or (isinstance(status, int) and status >= 500)


def _call_gemini(
    prompt: str,
    max_tokens: int = 1024,
    feature: str = "news_agent",
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    is_background: bool = False,
    budget_checked: bool = False,
    admin_override: bool = False,
    primary_model: Optional[str] = None,
    fallback_models: Optional[list[str]] = None,
    request_id: Optional[str] = None,
    cycle_id: Optional[str] = None,
    job_id: Optional[str] = None,
    origin: str = "llm_service",
) -> str:
    # The historical function name remains for compatibility. Provider selection
    # is now explicit and fail-closed. Legacy bypass kwargs stay intentionally ignored.
    selected_primary_model = primary_model or DEFAULT_GEMINI_MODEL
    selected_fallback_models = fallback_models or GEMINI_FALLBACK_MODELS
    provider = resolve_text_provider(feature)
    if not provider:
        logger.error("LLM_PROVIDER_INVALID feature=%s", feature)
        return ""

    result = _provider_result(
        provider,
        prompt,
        max_tokens=max_tokens,
        feature=feature,
        user_id=user_id,
        chat_id=chat_id,
        is_background=is_background,
        primary_model=selected_primary_model,
        fallback_models=selected_fallback_models,
        request_id=request_id,
        cycle_id=cycle_id,
        job_id=job_id,
        origin=origin,
    )
    text = str(result.get("text") or "") if isinstance(result, dict) else ""
    if text:
        return text

    fallback_provider = resolve_fallback_provider(provider)
    if not fallback_provider or not _provider_failure_allows_fallback(result):
        return ""

    logger.warning(
        "LLM_PROVIDER_FALLBACK feature=%s primary=%s fallback=%s reason=%s",
        feature,
        provider,
        fallback_provider,
        result.get("reason"),
    )
    fallback_result = _provider_result(
        fallback_provider,
        prompt,
        max_tokens=max_tokens,
        feature=feature,
        user_id=user_id,
        chat_id=chat_id,
        is_background=is_background,
        primary_model=selected_primary_model,
        fallback_models=selected_fallback_models,
        request_id=request_id,
        cycle_id=cycle_id,
        job_id=job_id,
        origin=f"{origin}:fallback_from_{provider}",
    )
    return str(fallback_result.get("text") or "") if isinstance(fallback_result, dict) else ""


def generate_text(prompt: str, feature: str = "signal_generation", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    return _call_gemini(prompt, max_tokens=512, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)


def generate_music_text(prompt: str, feature: str, user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    max_tokens = max(256, min(4096, int(os.getenv("VELIA_STUDIO_MUSIC_LLM_MAX_OUTPUT_TOKENS", "2500"))))
    return _call_gemini(prompt, max_tokens=max_tokens, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)


def generate_decision_text(prompt: str, feature: str = "signal_generation", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    return _call_gemini(prompt, max_tokens=1024, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)


def generate_live_analyst_text(prompt: str, feature: str = "live_analyst", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    max_tokens = int(os.getenv("LIVE_ANALYST_MAX_OUTPUT_TOKENS", "2200"))
    return _call_gemini(prompt, max_tokens=max_tokens, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=LIVE_ANALYST_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)


def generate_news_text(prompt: str, feature: str = "news_agent", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    return _call_gemini(prompt, max_tokens=768, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)