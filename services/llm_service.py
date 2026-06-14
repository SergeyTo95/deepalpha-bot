import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
GEMINI_PROVIDER_COOLDOWN_SECONDS = int(os.getenv("GEMINI_PROVIDER_COOLDOWN_SECONDS", "300"))

# Основная модель из env, fallback — lite версия
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODELS = ["gemini-2.5-flash-lite"]

# Задержки между retry попытками (секунды)
RETRY_DELAYS = [5, 15, 30]
HARD_PROVIDER_ERRORS = {"permission_denied", "quota_exceeded"}
TEMP_PROVIDER_ERRORS = {"rate_limited", "overloaded"}

logger.info(
    "llm_config provider=gemini api_key_present=%s text_model=%s vision_model=%s",
    bool(GEMINI_API_KEY),
    GEMINI_MODEL,
    os.getenv("GEMINI_VISION_MODEL", GEMINI_MODEL),
)


@dataclass
class _ProviderCooldown:
    error_type: str = ""
    until: float = 0.0
    logged: bool = False


_gemini_cooldown = _ProviderCooldown()


class ProviderUnavailableText(str):
    """String fallback carrying provider_error metadata for existing call sites."""

    def __new__(cls, value: str = "", provider_error: Optional[str] = None):
        obj = str.__new__(cls, value)
        obj.provider_error = provider_error
        return obj


def classify_gemini_error(status: int, body: str) -> str:
    body_l = (body or "").lower()
    if status == 403 and (
        "permission_denied" in body_l
        or "denied access" in body_l
        or "project has been denied access" in body_l
    ):
        return "permission_denied"
    if status == 429 and ("quota" in body_l or "exceeded your current quota" in body_l):
        return "quota_exceeded"
    if status == 429:
        return "rate_limited"
    if status == 503 or "overloaded" in body_l:
        return "overloaded"
    if status >= 500:
        return "retryable"
    return "unknown"


def _body_preview(body: str, limit: int = 160) -> str:
    return " ".join((body or "").split())[:limit]


def _set_gemini_cooldown(error_type: str) -> None:
    _gemini_cooldown.error_type = error_type
    _gemini_cooldown.until = time.time() + GEMINI_PROVIDER_COOLDOWN_SECONDS
    _gemini_cooldown.logged = False


def clear_gemini_provider_cooldown() -> None:
    _gemini_cooldown.error_type = ""
    _gemini_cooldown.until = 0.0
    _gemini_cooldown.logged = False


def get_gemini_provider_cooldown_error() -> Optional[str]:
    if _gemini_cooldown.error_type and time.time() < _gemini_cooldown.until:
        return _gemini_cooldown.error_type
    if _gemini_cooldown.error_type:
        clear_gemini_provider_cooldown()
    return None


def is_gemini_provider_unavailable() -> bool:
    return get_gemini_provider_cooldown_error() is not None


def _cooldown_fallback() -> Optional[ProviderUnavailableText]:
    error_type = get_gemini_provider_cooldown_error()
    if not error_type:
        return None
    if not _gemini_cooldown.logged:
        logger.warning(
            "llm_provider_cooldown_active provider=gemini error_type=%s",
            error_type,
        )
        _gemini_cooldown.logged = True
    return ProviderUnavailableText("", provider_error=error_type)


def _build_url(model: str) -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


def _call_model_once(prompt: str, model: str, max_tokens: int) -> Tuple[str, int, str]:
    if not GEMINI_API_KEY:
        logger.error("LLM ERROR: GEMINI_API_KEY not set")
        return "", -1, "unknown"

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
    }

    try:
        response = requests.post(
            f"{_build_url(model)}?key={GEMINI_API_KEY}",
            headers=headers,
            json=payload,
            timeout=LLM_TIMEOUT,
        )
        status = response.status_code
        logger.info("LLM STATUS: %s | model: %s", status, model)

        if status == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", ""), 200, ""
            return "", 200, ""

        body = response.text or ""
        error_type = classify_gemini_error(status, body)
        if error_type in HARD_PROVIDER_ERRORS:
            logger.error(
                "llm_provider_failure provider=gemini model=%s error_type=%s stop_retry=true body_preview=%s",
                model,
                error_type,
                _body_preview(body),
            )
        elif status == 404:
            logger.warning("LLM 404: model=%s not found — skipping", model)
        else:
            logger.warning(
                "llm_provider_error provider=gemini model=%s status=%s error_type=%s body_preview=%s",
                model,
                status,
                error_type,
                _body_preview(body),
            )
        return "", status, error_type

    except requests.exceptions.Timeout:
        logger.warning("LLM TIMEOUT: model=%s", model)
        return "", 0, "retryable"
    except Exception as e:
        logger.warning("LLM EXCEPTION: model=%s error=%s", model, e)
        return "", 0, "retryable"


def _call_gemini(prompt: str, max_tokens: int = 1024) -> str:
    cooldown = _cooldown_fallback()
    if cooldown is not None:
        return cooldown
    if not GEMINI_API_KEY:
        logger.error("LLM ERROR: GEMINI_API_KEY not set")
        return ""

    models = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]
    for model in models:
        logger.info("LLM: trying model=%s", model)
        max_attempts = len(RETRY_DELAYS)
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            text, status, error_type = _call_model_once(prompt, model, max_tokens)
            if status == 200:
                if attempt > 1:
                    logger.info("LLM: success on attempt %s with model=%s", attempt, model)
                return text
            if error_type in HARD_PROVIDER_ERRORS:
                _set_gemini_cooldown(error_type)
                return ProviderUnavailableText("", provider_error=error_type)
            if error_type in TEMP_PROVIDER_ERRORS:
                max_attempts = min(max_attempts, 2)
            if status == 404:
                break
            if status == -1:
                return ""
            if attempt >= max_attempts:
                logger.warning("LLM: model=%s attempts exhausted error_type=%s", model, error_type)
                break
            logger.info(
                "LLM: model=%s attempt=%s/%s status=%s retrying in %ss",
                model,
                attempt,
                max_attempts,
                status,
                delay,
            )
            time.sleep(delay)
        if error_type in TEMP_PROVIDER_ERRORS:
            logger.warning(
                "llm_provider_failure provider=gemini model=%s error_type=%s stop_retry=true",
                model,
                error_type,
            )
            return ProviderUnavailableText("", provider_error=error_type)

    logger.warning("LLM FAILED: all models exhausted, returning empty")
    return ""


def generate_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=512)


def generate_decision_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=1024)


def generate_news_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=768)
