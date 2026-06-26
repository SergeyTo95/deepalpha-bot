import os
import time
import requests
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
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


def _call_model_once(prompt: str, model: str, max_tokens: int) -> tuple:
    """
    Один вызов к модели.
    Возвращает (text, status_code):
    - (text, 200) — успех
    - ("", 503)   — перегружена, можно retry
    - ("", 429)   — rate limit, можно retry
    - ("", 404)   — модель не найдена, не retry
    - ("", 0)     — сетевая ошибка / таймаут, можно retry
    """
    if not GEMINI_API_KEY:
        print("LLM ERROR: GEMINI_API_KEY not set")
        return "", -1

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.7,
        },
    }

    try:
        response = requests.post(
            f"{_build_url(model)}?key={GEMINI_API_KEY}",
            headers=headers,
            json=payload,
            timeout=LLM_TIMEOUT,
        )
        status = response.status_code
        print(f"LLM STATUS: {status} | model: {model}")

        if status == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            print(f"LLM RESPONSE: model={model} candidates={len(candidates)}")
            if candidates:
                candidate = candidates[0] or {}
                finish_reason = candidate.get("finishReason")
                parts = candidate.get("content", {}).get("parts", []) or []
                text_parts = []
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        text_parts.append(str(part.get("text")))
                text = "\n".join(text_parts).strip()
                print(f"LLM FINISH: model={model} finishReason={finish_reason} parts={len(parts)} chars={len(text)}")
                if str(finish_reason or "").upper() in {"MAX_TOKENS", "LENGTH"}:
                    print(f"LLM OUTPUT TRUNCATED_BY_MODEL model={model} max_tokens={max_tokens}")
                return text, 200
            return "", 200

        elif status in (503, 429):
            print(f"LLM {status}: model={model} overloaded/rate-limit")
            return "", status

        elif status == 404:
            print(f"LLM 404: model={model} not found — skipping")
            return "", 404

        else:
            print(f"LLM ERROR {status}: {response.text[:200]}")
            return "", status

    except requests.exceptions.Timeout:
        print(f"LLM TIMEOUT: model={model}")
        return "", 0

    except Exception as e:
        print(f"LLM EXCEPTION: model={model} error={e}")
        return "", 0


def _call_gemini(prompt: str, max_tokens: int = 1024, feature: str = "news_agent", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False, primary_model: Optional[str] = None, fallback_models: Optional[list[str]] = None) -> str:
    """
    Вызывает Gemini с retry и fallback.

    Для каждой модели из списка:
    - делает попытку
    - при 503/429/0 — ждёт по RETRY_DELAYS и повторяет
    - при 404 — сразу переходит к следующей модели
    - при успехе — возвращает текст

    Если все модели исчерпаны — возвращает "".
    """
    if not budget_checked:
        guard = can_call_gemini(feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, admin_override=admin_override)
        if not guard.get("allowed"):
            print(f"gemini_call_blocked_by_budget_guard feature={feature} reason={guard.get('reason')} user_id={user_id} chat_id={chat_id} is_background={is_background}")
            return ""

    if not GEMINI_API_KEY:
        print("LLM ERROR: GEMINI_API_KEY not set")
        return ""

    selected_primary_model = primary_model or DEFAULT_GEMINI_MODEL
    selected_fallback_models = fallback_models if fallback_models is not None else GEMINI_FALLBACK_MODELS
    models = [selected_primary_model] + [m for m in selected_fallback_models if m != selected_primary_model]

    for index, model in enumerate(models):
        is_live_primary = feature == "live_analyst" and index == 0 and model == selected_primary_model
        if is_live_primary:
            print(f"LLM_PRIMARY_SELECTED feature={feature} model={model}")
            max_attempts = LIVE_ANALYST_PRIMARY_MAX_ATTEMPTS
            retry_delays = LIVE_ANALYST_PRIMARY_RETRY_DELAYS
        else:
            if feature == "live_analyst":
                print(f"LLM_FALLBACK_SELECTED feature={feature} model={model}")
            max_attempts = len(RETRY_DELAYS)
            retry_delays = RETRY_DELAYS

        print(f"LLM: trying model={model} feature={feature}")

        for attempt in range(1, max_attempts + 1):
            text, status = _call_model_once(prompt, model, max_tokens)

            if status == 200 and (text or not is_live_primary):
                if attempt > 1:
                    print(f"LLM: success on attempt {attempt} with model={model}")
                print(f"LLM_SUCCESS feature={feature} model={model}")
                try:
                    record_gemini_call(feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background)
                except Exception as exc:
                    print(f"gemini_usage_record_failed feature={feature} error={exc}")
                return text

            if status == 200:
                print(f"LLM EMPTY: model={model} feature={feature}")

            if status == 404:
                # Модель не существует — не retry, сразу следующая
                print(f"LLM: model={model} not available, skipping")
                break

            if status == -1:
                # API ключ не задан — нет смысла продолжать
                return ""

            if attempt < max_attempts:
                delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)] if retry_delays else 0
                print(
                    f"LLM: model={model} attempt={attempt}/{max_attempts} "
                    f"status={status} retrying in {delay}s"
                )
                if delay > 0:
                    time.sleep(delay)
            else:
                print(
                    f"LLM: model={model} all {max_attempts} attempts exhausted"
                )

    print("LLM FAILED: all models exhausted, returning empty")
    return ""


def generate_text(prompt: str, feature: str = "signal_generation", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False) -> str:
    return _call_gemini(prompt, max_tokens=512, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS)


def generate_decision_text(prompt: str, feature: str = "signal_generation", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False) -> str:
    return _call_gemini(prompt, max_tokens=1024, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS)


def generate_live_analyst_text(prompt: str, feature: str = "live_analyst", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False) -> str:
    max_tokens = int(os.getenv("LIVE_ANALYST_MAX_OUTPUT_TOKENS", "2200"))
    return _call_gemini(prompt, max_tokens=max_tokens, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=LIVE_ANALYST_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS)


def generate_news_text(prompt: str, feature: str = "news_agent", user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, budget_checked: bool = False, admin_override: bool = False) -> str:
    return _call_gemini(prompt, max_tokens=768, feature=feature, user_id=user_id, chat_id=chat_id, is_background=is_background, budget_checked=budget_checked, admin_override=admin_override, primary_model=DEFAULT_GEMINI_MODEL, fallback_models=GEMINI_FALLBACK_MODELS)
