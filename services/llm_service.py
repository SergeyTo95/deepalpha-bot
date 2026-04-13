
import os
import time
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _call_gemini(prompt: str, max_tokens: int = 1024, retries: int = 3) -> str:
    """Вызывает Gemini API с retry логикой при 503."""
    if not GEMINI_API_KEY:
        print("LLM ERROR: GEMINI_API_KEY not set")
        return ""

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.7,
        },
    }

    for attempt in range(retries):
        try:
            response = requests.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                headers=headers,
                json=payload,
                timeout=LLM_TIMEOUT,
            )

            print(f"LLM STATUS: {response.status_code} | model: {GEMINI_MODEL} | key: ...{GEMINI_API_KEY[-6:]}")

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
                return ""

            elif response.status_code == 503:
                wait = (attempt + 1) * 5
                print(f"LLM 503: Gemini overloaded, retrying in {wait}s (attempt {attempt + 1}/{retries})")
                print(f"LLM ERROR: {response.text[:200]}")
                if attempt < retries - 1:
                    time.sleep(wait)
                continue

            elif response.status_code == 429:
                wait = (attempt + 1) * 10
                print(f"LLM 429: Rate limit, retrying in {wait}s (attempt {attempt + 1}/{retries})")
                if attempt < retries - 1:
                    time.sleep(wait)
                continue

            else:
                print(f"LLM ERROR: {response.text[:300]}")
                return ""

        except requests.exceptions.Timeout:
            print(f"LLM TIMEOUT: attempt {attempt + 1}/{retries}")
            if attempt < retries - 1:
                time.sleep(5)
            continue

        except Exception as e:
            print(f"LLM EXCEPTION: {e}")
            if attempt < retries - 1:
                time.sleep(3)
            continue

    print(f"LLM FAILED: all {retries} attempts exhausted")
    return ""


def generate_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=512)


def generate_decision_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=1024)


def generate_news_text(prompt: str) -> str:
    return _call_gemini(prompt, max_tokens=768)
