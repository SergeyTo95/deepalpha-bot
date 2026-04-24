import os
import time
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))

# Основная модель из env, fallback — lite версия
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODELS = ["gemini-2.5-flash-lite"]

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
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", ""), 200
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


def _call_gemini(prompt: str, max_tokens: int = 1024) -> str:
    """
    Вызывает Gemini с retry и fallback.

    Для каждой модели из списка:
    - делает попытку
    - при 503/429/0 — ждёт по RETRY_DELAYS и повторяет
    - при 404 — сразу переходит к следующей модели
    - при успехе — возвращает текст

    Если все модели исчерпаны — возвращает "".
    """
    if not GEMINI_API_KEY:
        print("LLM ERROR: GEMINI_API_KEY not set")
        return ""

    models = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]

    for model in models:
        print(f"LLM: trying model={model}")

        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            text, status = _call_model_once(prompt, model, max_tokens)

            if status == 200:
                if attempt > 1:
                    print(f"LLM: success on attempt {attempt} with model={model}")
                return text

            if status == 404:
                # Модель не существует — не retry, сразу следующая
                print(f"LLM: model={model} not available, skipping")
                break

            if status == -1:
                # API ключ не задан — нет смысла продолжать
                return ""

            # 503, 429, 0 — ждём и повторяем
            if attempt < len(RETRY_DELAYS):
                print(
                    f"LLM: model={model} attempt={attempt}/{len(RETRY_DELAYS)} "
                    f"status={status} retrying in {delay}s"
                )
                time.sleep(delay)
            else:
                print(
                    f"LLM: model={model} all {len(RETRY_DELAYS)} attempts exhausted"
                )

    print("LLM FAILED: all models exhausted, returning empty")
    return ""


def generate_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=512)


def generate_decision_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=1024)


def generate_news_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=768)
