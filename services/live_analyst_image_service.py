import base64
import os
from typing import Dict

import requests

from services.live_analyst_admin_service import get_max_image_size_bytes


def is_supported_image_mime(mime_type: str) -> bool:
    return (mime_type or "").lower() in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def analyze_image_bytes(image_bytes: bytes, mime_type: str, context_text: str = "") -> Dict[str, str]:
    if not image_bytes:
        return {"ok": False, "error": "empty"}
    if len(image_bytes) > get_max_image_size_bytes():
        return {"ok": False, "error": "too_large"}
    if not is_supported_image_mime(mime_type):
        return {"ok": False, "error": "unsupported_type"}

    api_key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    timeout = int(os.getenv("LLM_TIMEOUT", "30"))
    if not api_key:
        return {"ok": False, "error": "vision_unavailable"}

    prompt = (
        "Ты Live Analyst для Polymarket. Проанализируй пользовательский скриншот в контексте prediction markets. "
        "Не давай финансовых советов, не говори покупать или продавать. Ответь строго по-русски в формате:\n\n"
        "🧠 Вижу на скрине:\n\n"
        "Кратко:\n...\n\n"
        "Что важно для анализа:\n1. ...\n2. ...\n3. ...\n\n"
        "Как это может влиять на рынок:\n...\n\n"
        "Что стоит проверить дальше:\n...\n\n"
        "Не финансовый совет.\n\n"
        f"Контекст Live Analyst, если есть:\n{context_text[:3000]}"
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}},
            ]
        }],
        "generationConfig": {"maxOutputTokens": 768, "temperature": 0.35},
    }
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if response.status_code != 200:
            return {"ok": False, "error": "vision_unavailable"}
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return {"ok": False, "error": "empty_response"}
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
        if not text:
            return {"ok": False, "error": "empty_response"}
        return {"ok": True, "summary": text[:4000]}
    except Exception:
        return {"ok": False, "error": "vision_unavailable"}
