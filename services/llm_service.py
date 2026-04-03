import os
import time
import requests

from db.database import get_setting


def _safe_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    return str(value).strip()


GEMINI_MODEL_DEFAULT = _safe_env("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MODEL_NEWS_DEFAULT = _safe_env("GEMINI_MODEL_NEWS", GEMINI_MODEL_DEFAULT)
GEMINI_MODEL_DECISION_DEFAULT = _safe_env("GEMINI_MODEL_DECISION", GEMINI_MODEL_DEFAULT)

try:
    LLM_TIMEOUT = int(_safe_env("LLM_TIMEOUT", "60"))
except Exception:
    LLM_TIMEOUT = 60


def _get_active_model() -> str:
    try:
        model = get_setting("active_model", "")
        return model if model else GEMINI_MODEL_DEFAULT
    except Exception:
        return GEMINI_MODEL_DEFAULT


def _get_providers(model: str) -> list:
    providers = []

    # Все 55 ключей
    for i in range(1, 56):
        if i == 1:
            key = _safe_env("GEMINI_API_KEY")
        else:
            key = _safe_env(f"GEMINI_API_KEY_{i}")
        if key:
            providers.append({"key": key, "model": model})

    # Fallback на lite модель для всех ключей
    for i in range(1, 56):
        if i == 1:
            key = _safe_env("GEMINI_API_KEY")
        else:
            key = _safe_env(f"GEMINI_API_KEY_{i}")
        if key:
            providers.append({"key": key, "model": "gemini-2.0-flash-lite"})

    return providers


def generate_text(
    prompt: str,
    model: str = "",
) -> str:
    chosen_model = (model or _get_active_model()).strip()
    providers = _get_providers(chosen_model)

    if not providers:
        print("LLM ERROR: No API keys configured")
        return ""

    for provider in providers:
        key = provider["key"]
        mdl = provider["model"]

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{mdl}:generateContent?key={key}"
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ]
        }

        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=LLM_TIMEOUT,
            )

            print(f"LLM STATUS: {response.status_code} | model: {mdl} | key: ...{key[-6:]}")

            if response.status_code == 429:
                print(f"LLM: quota exceeded for key ...{key[-6:]}, trying next")
                time.sleep(0.3)
                continue

            if response.status_code != 200:
                print("LLM RESPONSE TEXT:", response.text[:500])
                time.sleep(0.3)
                continue

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                continue

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                continue

            texts = [part.get("text", "") for part in parts if part.get("text")]
            if texts:
                return "\n".join(texts).strip()

        except Exception as e:
            print(f"LLM EXCEPTION: {str(e)} | model: {mdl}")
            time.sleep(0.3)
            continue

    print("LLM ERROR: All providers exhausted")
    return ""


def generate_news_text(prompt: str) -> str:
    model = get_setting("active_model_news", "") or GEMINI_MODEL_NEWS_DEFAULT
    return generate_text(prompt=prompt, model=model)


def generate_decision_text(prompt: str) -> str:
    model = get_setting("active_model_decision", "") or GEMINI_MODEL_DECISION_DEFAULT
    return generate_text(prompt=prompt, model=model)
