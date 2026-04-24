import os
import time
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))

# Fallback модели при перегрузке основной
FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def _build_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _call_gemini_model(prompt: str, model: str, max_tokens: int) -> str:
    """Один вызов к конкретной модели. Возвращает текст или '' при ошибке."""
    if not GEMINI_API_KEY:
        return ""

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

        print(f"LLM STATUS: {response.status_code} | model: {model}")

        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return ""

        elif response.status_code in (503, 429, 500):
            print(f"LLM {response.status_code}: model={model}, overloaded/rate-limit")
            return None  # None = сигнал попробовать другую модель

        else:
            print(f"LLM ERROR {response.status_code}: {response.text[:200]}")
            return ""

    except requests.exceptions.Timeout:
        print(f"LLM TIMEOUT: model={model}")
        return None

    except Exception as e:
        print(f"LLM EXCEPTION: model={model}, error={e}")
        return None


def _call_gemini(prompt: str, max_tokens: int = 1024, retries: int = 3) -> str:
    """
    Вызывает Gemini с retry и fallback на другие модели при 503/429.
    Порядок:
    1. Основная модель (GEMINI_MODEL) — 3 попытки
    2. Fallback модели — по 1 попытке
    """
    if not GEMINI_API_KEY:
        print("LLM ERROR: GEMINI_API_KEY not set")
        return ""

    # Пробуем основную модель
    for attempt in range(retries):
        result = _call_gemini_model(prompt, GEMINI_MODEL, max_tokens)

        if result is None:
            # 503/429 — ждём и повторяем
            wait = (attempt + 1) * 5
            print(f"LLM: retrying {GEMINI_MODEL} in {wait}s (attempt {attempt + 1}/{retries})")
            if attempt < retries - 1:
                time.sleep(wait)
            continue

        if result != "":
            return result

        # Пустой ответ — что-то не так, не ретраим
        break

    # Основная модель не ответила — пробуем fallback
    print(f"LLM: {GEMINI_MODEL} exhausted, trying fallback models...")
    for fallback_model in FALLBACK_MODELS:
        if fallback_model == GEMINI_MODEL:
            continue
        print(f"LLM: trying fallback model={fallback_model}")
        result = _call_gemini_model(prompt, fallback_model, max_tokens)
        if result is not None and result != "":
            print(f"LLM: fallback succeeded with model={fallback_model}")
            return result
        time.sleep(3)

    print("LLM FAILED: all models exhausted")
    return ""


def generate_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=512)


def generate_decision_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=1024)


def generate_news_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=768)
