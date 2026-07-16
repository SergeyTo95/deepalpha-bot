import os
from typing import Optional
from services.gemini_budget_guard import can_call_gemini, record_gemini_call

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))

# Default model for general/background tasks. Live Analyst can use a stronger
# model without changing the cost profile for other features.
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LIVE_ANALYST_GEMINI_MODEL = os.getenv("LIVE_ANALYST_GEMINI_MODEL", "gemini-3.5-flash")


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

# Задержки между retry попытками (секунды)
RETRY_DELAYS = [5, 15, 30]


def _build_url(model: str) -> str:
    # Gemini HTTP transport lives exclusively in services.gemini_gateway.
    return ""


def _call_model_once(prompt: str, model: str, max_tokens: int) -> tuple:
    raise RuntimeError("direct Gemini HTTP is disabled; use services.gemini_gateway")


def _call_gemini(prompt: str, max_tokens: int = 1024, feature: str = "news_agent", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, primary_model: Optional[str] = None, fallback_models: Optional[list[str]] = None, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None, origin: str = "llm_service") -> str:
    # budget_checked/admin_override are intentionally ignored: legacy kwargs must not bypass gateway checks.
    from services.gemini_gateway import generate_content
    selected_primary_model = primary_model or DEFAULT_GEMINI_MODEL
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
    }
    max_attempts = int(os.getenv("LIVE_ANALYST_MAX_GEMINI_ATTEMPTS_PER_REQUEST", "2")) if feature == "live_analyst" else int(os.getenv("GEMINI_DEFAULT_MAX_ATTEMPTS", "1"))
    result = generate_content(
        feature=feature, origin=origin, is_background=is_background, request_id=request_id, cycle_id=cycle_id, job_id=job_id,
        model=selected_primary_model, payload=payload, max_attempts=max_attempts, user_id=user_id, chat_id=chat_id,
        fallback_models=fallback_models or GEMINI_FALLBACK_MODELS, allow_fallback_model=None,
    )
    return (result.get("text") or "") if isinstance(result, dict) else ""

def generate_text(prompt: str, feature: str = "signal_generation", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    return _call_gemini(prompt, max_tokens=512, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)


def generate_decision_text(prompt: str, feature: str = "signal_generation", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    return _call_gemini(prompt, max_tokens=1024, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)


def generate_live_analyst_text(prompt: str, feature: str = "live_analyst", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    max_tokens = int(os.getenv("LIVE_ANALYST_MAX_OUTPUT_TOKENS", "2200"))
    return _call_gemini(prompt, max_tokens=max_tokens, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=LIVE_ANALYST_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)


def generate_news_text(prompt: str, feature: str = "news_agent", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, request_id: Optional[str] = None, cycle_id: Optional[str] = None, job_id: Optional[str] = None) -> str:
    return _call_gemini(prompt, max_tokens=768, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS, request_id=request_id, cycle_id=cycle_id, job_id=job_id)
