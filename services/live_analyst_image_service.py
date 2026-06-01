import base64
import json
import os
import re
from typing import Any

import urllib.error
import urllib.request


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LIVE_ANALYST_IMAGE_MODEL = os.getenv("LIVE_ANALYST_IMAGE_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
LIVE_ANALYST_IMAGE_TIMEOUT = int(os.getenv("LIVE_ANALYST_IMAGE_TIMEOUT", os.getenv("LLM_TIMEOUT", "30")))
LIVE_ANALYST_MAX_CHARS = 700


LIVE_ANALYST_IMAGE_PROMPT = """
Ты — vision-анализатор DeepAlpha для скриншотов prediction markets.

Задача: извлечь только то, что ВИДНО на изображении, и вернуть JSON only.

Если это скрин Polymarket:
- Extract visible market title if readable.
- Extract visible leading outcomes and approximate odds/prices if readable.
- Extract visible volume if readable.
- Extract chart/trend only at a high level if visible.
- If exact values are readable, use them.
- If exact values are not readable, say so.
- Do not be overly conservative when text is readable.
- Do not claim “not readable” if the title/outcomes are clearly visible.
- Можно дать quick visual insight по скриншоту.
- Нельзя давать buy/sell instructions.
- Нельзя рекомендовать ставку или обещать прибыль.
- Нельзя выдавать final EDGE / NO TRADE без ссылки на рынок и полного анализа.

Required JSON fields:
{
  "screen_type": "polymarket|chart|news|unknown",
  "market": "readable market title or empty string",
  "visible": "compact visible data: outcomes/odds/volume/chart if readable",
  "takeaway": "short visual insight from screenshot only, not a trade recommendation",
  "summary": "one compact Russian sentence"
}

Для Polymarket-скрина market должен включать читаемое название рынка.
Пример visible: "Лидер около 80%, второй кандидат около 19%, остальные почти 0%; виден график и объём около $35,136."
Пример takeaway: "Рынок сильно перекошен в сторону лидера; это повод проверить, оправдана ли такая вероятность."

Return JSON only. No markdown. No extra text.
""".strip()


def _build_gemini_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _safe_text(value: Any, limit: int = 260) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].rstrip()


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_live_analyst_image_json(raw: str) -> dict[str, str]:
    """Parse model JSON and normalize the required Live Analyst fields."""
    text = _strip_json_fence(raw)
    data: dict[str, Any] = {}

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                data = {}

    return {
        "screen_type": _safe_text(data.get("screen_type"), 40).lower() or "unknown",
        "market": _safe_text(data.get("market"), 220),
        "visible": _safe_text(data.get("visible"), 320),
        "takeaway": _safe_text(data.get("takeaway"), 260),
        "summary": _safe_text(data.get("summary"), 260),
    }


def analyze_live_analyst_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict[str, str]:
    """Call Gemini vision for a screenshot and return normalized extraction fields."""
    if not GEMINI_API_KEY or not image_bytes:
        return {
            "screen_type": "unknown",
            "market": "",
            "visible": "Не удалось прочитать изображение.",
            "takeaway": "Отправь ссылку на рынок для полноценного анализа.",
            "summary": "Скрин не распознан.",
        }

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": LIVE_ANALYST_IMAGE_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 512,
            "responseMimeType": "application/json",
        },
    }

    try:
        request = urllib.request.Request(
            f"{_build_gemini_url(LIVE_ANALYST_IMAGE_MODEL)}?key={GEMINI_API_KEY}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=LIVE_ANALYST_IMAGE_TIMEOUT) as response:
            status_code = response.getcode()
            response_body = response.read().decode("utf-8")

        if status_code != 200:
            return {
                "screen_type": "unknown",
                "market": "",
                "visible": "Не удалось получить vision-анализ скрина.",
                "takeaway": "Отправь ссылку на рынок для полноценного анализа.",
                "summary": "Vision-анализ временно недоступен.",
            }

        candidates = json.loads(response_body).get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        raw = parts[0].get("text", "") if parts else ""
        return parse_live_analyst_image_json(raw)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {
            "screen_type": "unknown",
            "market": "",
            "visible": "Не удалось получить vision-анализ скрина.",
            "takeaway": "Отправь ссылку на рынок для полноценного анализа.",
            "summary": "Vision-анализ временно недоступен.",
        }


def _is_polymarket_screen(result: dict[str, str]) -> bool:
    screen_type = (result.get("screen_type") or "").lower()
    text = " ".join([result.get("market", ""), result.get("visible", ""), result.get("summary", "")]).lower()
    return "polymarket" in screen_type or "polymarket" in text


def format_live_analyst_image_card(result: dict[str, str]) -> str:
    """Format a compact local reply for Live Analyst screenshot analysis."""
    screen_type = (result.get("screen_type") or "unknown").lower()
    market = _safe_text(result.get("market"), 180)
    visible = _safe_text(result.get("visible"), 260)
    takeaway = _safe_text(result.get("takeaway") or result.get("summary"), 220)

    if _is_polymarket_screen(result):
        lines = ["🧠 Polymarket-скрин", "", "Что видно:"]
        if market:
            lines.append(f"• Рынок: {market}")
        lines.append(f"• {visible or 'Видны элементы рынка, но точные значения не читаются.'}")
        lines.extend(
            [
                "",
                "Быстрый вывод:",
                f"• {takeaway or 'Скрин даёт только визуальную подсказку; нужна ссылка для проверки вероятностей.'}",
                "",
                "Что дальше:",
                "• Нажми 🔍 Анализ и отправь ссылку — я сравню odds с AI probability и дам вывод EDGE / NO TRADE.",
            ]
        )
    else:
        title = "🧠 Скрин" if screen_type == "unknown" else f"🧠 Скрин: {screen_type}"
        lines = [
            title,
            "",
            "Что видно:",
            f"• {visible or 'Точные данные на скрине не читаются.'}",
            "",
            "Быстрый вывод:",
            f"• {takeaway or 'Нужен контекст или ссылка для полноценного анализа.'}",
        ]

    text = "\n".join(lines).strip()
    if len(text) <= LIVE_ANALYST_MAX_CHARS:
        return text

    cta = "\n\nЧто дальше:\n• Нажми 🔍 Анализ и отправь ссылку — я сравню odds с AI probability и дам вывод EDGE / NO TRADE."
    budget = LIVE_ANALYST_MAX_CHARS - len(cta) - 1
    return text[:budget].rstrip() + "…" + cta
