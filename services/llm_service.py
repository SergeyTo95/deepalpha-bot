
import os
import time
import requests


def _safe_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    return str(value).strip()


GEMINI_MODEL = _safe_env("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MODEL_NEWS = _safe_env("GEMINI_MODEL_NEWS", GEMINI_MODEL)
GEMINI_MODEL_DECISION = _safe_env("GEMINI_MODEL_DECISION", GEMINI_MODEL)

try:
    LLM_TIMEOUT = int(_safe_env("LLM_TIMEOUT", "60"))
except Exception:
    LLM_TIMEOUT = 60


def _get_providers(model: str) -> list:
    """Собирает список провайдеров из env переменных."""
    providers = []
    keys_and_models = [
        (_safe_env("GEMINI_API_KEY"), model),
        (_safe_env("GEMINI_API_KEY_2"), model),
        (_safe_env("GEMINI_API_KEY_3"), model),
        (_safe_env("GEMINI_API_KEY_4"), model),
        (_safe_env("GEMINI_API_KEY_5"), model),
        # Fallback на более лёгкую модель
        (_safe_env("GEMINI_API_KEY"), "gemini-2.0-flash-lite"),
        (_safe_env("GEMINI_API_KEY_2"), "gemini-2.0-flash-lite"),
        (_safe_env("GEMINI_API_KEY_3"), "gemini-2.0-flash-lite"),
        (_safe_env("GEMINI_API_KEY_4"), "gemini-2.0-flash-lite"),
        (_safe_env("GEMINI_API_KEY_5"), "gemini-2.0-flash-lite"),
    ]
    for key, mdl in keys_and_models:
        if key:
            providers.append({"key": key, "model": mdl})
    return providers


def generate_text(
    prompt: str,
    model: str = "",
) -> str:
    chosen_model = (model or GEMINI_MODEL).strip()
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
                time.sleep(1)
                continue

            if response.status_code != 200:
                print("LLM RESPONSE TEXT:", response.text[:500])
                time.sleep(1)
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
            time.sleep(1)
            continue

    print("LLM ERROR: All providers exhausted")
    return ""


def generate_news_text(prompt: str) -> str:
    return generate_text(prompt=prompt, model=GEMINI_MODEL_NEWS)


def generate_decision_text(prompt: str) -> str:
    return generate_text(prompt=prompt, model=GEMINI_MODEL_DECISION)
