
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
    LLM_TIMEOUT = int(_safe_env("LLM_TIMEOUT", "30"))
except Exception:
    LLM_TIMEOUT = 30


def _get_active_model() -> str:
    try:
        model = get_setting("active_model", "")
        return model if model else GEMINI_MODEL_DEFAULT
    except Exception:
        return GEMINI_MODEL_DEFAULT


def _get_providers(model: str) -> list:
    providers = []

    # Основные ключи с основной моделью
    for i in range(1, 56):
        key = _safe_env("GEMINI_API_KEY") if i == 1 else _safe_env(f"GEMINI_API_KEY_{i}")
        if key:
            providers.append({"key": key, "model": model})

    # Fallback на lite модель
    for i in range(1, 56):
        key = _safe_env("GEMINI_API_KEY") if i == 1 else _safe_env(f"GEMINI_API_KEY_{i}")
        if key:
            providers.append({"key": key, "model": "gemini-2.0-flash-lite"})

    return providers


def generate_text(prompt: str, model: str = "") -> str:
    chosen_model = (model or _get_active_model()).strip()
    providers = _get_providers(chosen_model)

    if not providers:
        print("LLM ERROR: No API keys configured")
        return ""

    consecutive_429 = 0

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
                "maxOutputTokens": 1024,
            }
        }

        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=LLM_TIMEOUT,
            )

            print(f"LLM STATUS: {response.status_code} | model: {mdl} | key: ...{key[-6:]}")

            if response.status_code == 429:
                consecutive_429 += 1
                print(f"LLM: quota exceeded for key ...{key[-6:]}, trying next")
                # Чем больше подряд 429 — тем дольше ждём
                wait = min(consecutive_429 * 2, 10)
                time.sleep(wait)
                continue

            consecutive_429 = 0

            if response.status_code != 200:
                print("LLM RESPONSE TEXT:", response.text[:300])
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

        except requests.exceptions.Timeout:
            print(f"LLM TIMEOUT: model: {mdl} | key: ...{key[-6:]}")
            time.sleep(1)
            continue
        except Exception as e:
            print(f"LLM EXCEPTION: {str(e)} | model: {mdl}")
            time.sleep(1)
            continue

    print("LLM ERROR: All providers exhausted")
    return ""


async def generate_text_async(prompt: str, model: str = "") -> str:
    """Async версия для opportunity_agent."""
    import asyncio
    import aiohttp

    chosen_model = (model or _get_active_model()).strip()
    providers = _get_providers(chosen_model)

    if not providers:
        return ""

    timeout = aiohttp.ClientTimeout(total=LLM_TIMEOUT)
    consecutive_429 = 0

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
                    "maxOutputTokens": 1024,
                }
            }

            headers = {"Content-Type": "application/json"}

            try:
                async with session.post(url, headers=headers, json=payload) as response:
                    print(f"LLM ASYNC STATUS: {response.status} | model: {mdl} | key: ...{key[-6:]}")

                    if response.status == 429:
                        consecutive_429 += 1
                        wait = min(consecutive_429 * 2, 10)
                        print(f"LLM ASYNC: quota exceeded, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue

                    consecutive_429 = 0

                    if response.status != 200:
                        text = await response.text()
                        print("LLM ASYNC ERROR:", text[:200])
                        await asyncio.sleep(1)
                        continue

                    data = await response.json()
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
                print(f"LLM ASYNC EXCEPTION: {str(e)} | model: {mdl}")
                await asyncio.sleep(1)
                continue

    print("LLM ASYNC ERROR: All providers exhausted")
    return ""


async def generate_news_text_async(prompt: str) -> str:
    model = get_setting("active_model_news", "") or GEMINI_MODEL_NEWS_DEFAULT
    return await generate_text_async(prompt=prompt, model=model)


async def generate_decision_text_async(prompt: str) -> str:
    model = get_setting("active_model_decision", "") or GEMINI_MODEL_DECISION_DEFAULT
    return await generate_text_async(prompt=prompt, model=model)


def generate_news_text(prompt: str) -> str:
    model = get_setting("active_model_news", "") or GEMINI_MODEL_NEWS_DEFAULT
    return generate_text(prompt=prompt, model=model)


def generate_decision_text(prompt: str) -> str:
    model = get_setting("active_model_decision", "") or GEMINI_MODEL_DECISION_DEFAULT
    return generate_text(prompt=prompt, model=model)
