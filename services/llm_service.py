
import os
import requests


def _safe_env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    return str(value).strip()


GEMINI_MODEL_DEFAULT = _safe_env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODEL_NEWS_DEFAULT = "gemini-2.5-flash"
GEMINI_MODEL_DECISION_DEFAULT = "gemini-2.5-flash"
try:
    LLM_TIMEOUT = int(_safe_env("LLM_TIMEOUT", "30"))
except Exception:
    LLM_TIMEOUT = 30


def _get_active_model() -> str:
    return _safe_env("GEMINI_MODEL", "gemini-2.5-flash")


def _get_providers(model: str) -> list:
    providers = []
    # Только первый ключ — платный рабочий
    key = _safe_env("GEMINI_API_KEY")
    if key:
        providers.append({"key": key, "model": model})
    return providers


def generate_text(prompt: str, model: str = "") -> str:
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
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 512,
            }
        }

        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=LLM_TIMEOUT,
            )

            print(f"LLM STATUS: {response.status_code} | model: {mdl} | key: ...{key[-6:]}")

            if response.status_code == 429:
                print(f"LLM: quota exceeded")
                return ""

            if response.status_code == 404:
                print(f"LLM: model {mdl} not available")
                return ""

            if response.status_code != 200:
                print("LLM ERROR:", response.text[:200])
                return ""

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return ""

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                return ""

            texts = [part.get("text", "") for part in parts if part.get("text")]
            if texts:
                return "\n".join(texts).strip()

        except requests.exceptions.Timeout:
            print(f"LLM TIMEOUT: model: {mdl}")
            return ""
        except Exception as e:
            print(f"LLM EXCEPTION: {str(e)}")
            return ""

    print("LLM ERROR: No providers")
    return ""


async def generate_text_async(prompt: str, model: str = "") -> str:
    import aiohttp

    chosen_model = (model or _get_active_model()).strip()
    providers = _get_providers(chosen_model)

    if not providers:
        return ""

    timeout = aiohttp.ClientTimeout(total=LLM_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for provider in providers:
            key = provider["key"]
            mdl = provider["model"]

            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{mdl}:generateContent?key={key}"
            )

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 512,
                }
            }

            headers = {"Content-Type": "application/json"}

            try:
                async with session.post(url, headers=headers, json=payload) as response:
                    print(f"LLM ASYNC: {response.status} | model: {mdl} | key: ...{key[-6:]}")

                    if response.status in (429, 404):
                        return ""

                    if response.status != 200:
                        return ""

                    data = await response.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        return ""

                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if not parts:
                        return ""

                    texts = [part.get("text", "") for part in parts if part.get("text")]
                    if texts:
                        return "\n".join(texts).strip()

            except Exception as e:
                print(f"LLM ASYNC EXCEPTION: {str(e)}")
                return ""

    return ""


async def generate_news_text_async(prompt: str) -> str:
    return await generate_text_async(prompt=prompt, model=GEMINI_MODEL_NEWS_DEFAULT)


async def generate_decision_text_async(prompt: str) -> str:
    return await generate_text_async(prompt=prompt, model=GEMINI_MODEL_DECISION_DEFAULT)


def generate_news_text(prompt: str) -> str:
    return generate_text(prompt=prompt, model=GEMINI_MODEL_NEWS_DEFAULT)


def generate_decision_text(prompt: str) -> str:
    return generate_text(prompt=prompt, model=GEMINI_MODEL_DECISION_DEFAULT)
