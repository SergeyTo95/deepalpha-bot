import os
import time
import requests

 
def _safe_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    return str(value).strip()


GEMINI_API_KEY = _safe_env("GEMINI_API_KEY", "")
GEMINI_MODEL = _safe_env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODEL_NEWS = _safe_env("GEMINI_MODEL_NEWS", GEMINI_MODEL)
GEMINI_MODEL_DECISION = _safe_env("GEMINI_MODEL_DECISION", GEMINI_MODEL)

try:
    LLM_TIMEOUT = int(_safe_env("LLM_TIMEOUT", "60"))
except Exception:
    LLM_TIMEOUT = 60


def generate_text(
    prompt: str,
    model: str = "",
    max_retries: int = 3,
) -> str:
    if not GEMINI_API_KEY:
        print("LLM ERROR: GEMINI_API_KEY is missing")
        return ""

    chosen_model = (model or GEMINI_MODEL).strip()
    if not chosen_model:
        print("LLM ERROR: GEMINI_MODEL is missing")
        return ""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{chosen_model}:generateContent?key={GEMINI_API_KEY}"
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

    headers = {
        "Content-Type": "application/json"
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=LLM_TIMEOUT,
            )

            print("LLM STATUS:", response.status_code)

            if response.status_code != 200:
                print("LLM RESPONSE TEXT:", response.text[:2000])
                time.sleep(1)
                continue

            data = response.json()
            print("LLM RESPONSE JSON:", data)

            candidates = data.get("candidates", [])
            if not candidates:
                return ""

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                return ""

            texts = []
            for part in parts:
                text = part.get("text", "")
                if text:
                    texts.append(text)

            return "\n".join(texts).strip()

        except Exception as e:
            print("LLM EXCEPTION:", str(e))
            time.sleep(1)

    return ""


def generate_news_text(prompt: str) -> str:
    return generate_text(prompt=prompt, model=GEMINI_MODEL_NEWS)


def generate_decision_text(prompt: str) -> str:
    return generate_text(prompt=prompt, model=GEMINI_MODEL_DECISION)
