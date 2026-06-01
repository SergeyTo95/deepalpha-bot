"""Live Analyst screenshot extraction and local response formatting."""

import base64
import json
import os
from typing import Any, Dict


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))

REQUIRED_FIELDS = ("screen_type", "market", "visible", "takeaway", "summary")

LIVE_ANALYST_IMAGE_PROMPT = """
Ты анализируешь один скриншот, который пользователь отправил в Live Analyst.

Верни JSON only, без markdown, без пояснений вне JSON.
Обязательные поля JSON: screen_type, market, visible, takeaway, summary.

Правила извлечения:
- Определи screen_type: polymarket, chart, news, portfolio, other.
- Если это скрин Polymarket, извлеки видимое название рынка, если оно читается.
- Для Polymarket market должен включать читаемое название рынка, а не общее описание.
- Извлеки видимые лидирующие outcomes и примерные odds/prices/probabilities, если они читаются.
- Извлеки видимый volume/объём, если он читается.
- Описывай график/тренд только на высоком уровне, если он виден.
- Если точные значения читаются, используй их.
- Если точные значения не читаются, прямо скажи, что точные значения не читаются.
- Не будь чрезмерно консервативным, когда текст читается.
- Не утверждай, что title/outcomes не читаются, если они явно видны.

Для Polymarket:
- visible: компактно перечисли видимые outcomes/odds/volume/chart.
  Пример: "Лидер около 80%, второй кандидат около 19%, остальные почти 0%; виден график и объём около $35k."
- takeaway: дай быстрый визуальный insight со скриншота.
  Пример: "Рынок сильно перекошен в сторону лидера; это повод проверить, оправдана ли такая вероятность."

Ограничения:
- Не давай buy/sell instructions.
- Не рекомендуй ставить или покупать.
- Не обещай прибыль.
- Не выноси финальный EDGE / NO TRADE без ссылки Polymarket и полного анализа.
- Не добавляй фразу "Не финансовый совет."
""".strip()


def build_live_analyst_image_prompt() -> str:
    """Return the vision prompt used for Live Analyst screenshot extraction."""
    return LIVE_ANALYST_IMAGE_PROMPT


def _gemini_url(model: str) -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end >= start:
        raw = raw[start:end + 1]

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Live Analyst image response is not a JSON object")
    return data


def normalize_live_analyst_image_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    """Normalize model JSON so the formatter always has required string fields."""
    normalized: Dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        value = payload.get(field, "")
        if isinstance(value, (list, tuple)):
            value = "; ".join(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        normalized[field] = str(value or "").strip()
    return normalized


def parse_live_analyst_image_json(text: str) -> Dict[str, str]:
    """Parse and normalize the JSON-only vision model output."""
    return normalize_live_analyst_image_payload(_extract_json_object(text))


def analyze_live_analyst_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, str]:
    """Call Gemini Vision and return normalized Live Analyst screenshot data."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing")

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
    import requests

    response = requests.post(
        f"{_gemini_url(GEMINI_VISION_MODEL)}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=LLM_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = parts[0].get("text", "") if parts else ""
    return parse_live_analyst_image_json(text)


def _compact(text: str, fallback: str) -> str:
    value = " ".join((text or "").split())
    return value if value else fallback


def _is_polymarket_screen(payload: Dict[str, str]) -> bool:
    screen_type = payload.get("screen_type", "").lower()
    summary = payload.get("summary", "").lower()
    visible = payload.get("visible", "").lower()
    return "polymarket" in screen_type or "polymarket" in summary or "polymarket" in visible


def _limit_chars(text: str, limit: int = 700) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def format_live_analyst_image_response(payload: Dict[str, Any], lang: str = "ru") -> str:
    """Format a compact local card for a Live Analyst screenshot reply."""
    data = normalize_live_analyst_image_payload(payload)
    is_polymarket = _is_polymarket_screen(data)

    if lang == "en":
        if is_polymarket:
            text = (
                "🧠 Polymarket screenshot\n\n"
                "Visible:\n"
                f"• Market: {_compact(data['market'], 'readable title not detected')}\n"
                f"• {_compact(data['visible'], 'visible details are not readable enough')}\n\n"
                "Quick take:\n"
                f"• {_compact(data['takeaway'], 'Useful screenshot context, but not enough for a final call.')}\n\n"
                "Next:\n"
                "• Tap 🔍 Analyze and send the link — I’ll compare odds with AI probability and give EDGE / NO TRADE."
            )
        else:
            text = (
                "🧠 Screenshot\n\n"
                f"Visible: {_compact(data['summary'] or data['visible'], 'not enough readable data')}\n\n"
                f"Quick take: {_compact(data['takeaway'], 'Send a Polymarket link for full analysis.')}"
            )
        return _limit_chars(text)

    if is_polymarket:
        text = (
            "🧠 Polymarket-скрин\n\n"
            "Что видно:\n"
            f"• Рынок: {_compact(data['market'], 'читаемое название не определено')}\n"
            f"• {_compact(data['visible'], 'детали видны недостаточно чётко')}\n\n"
            "Быстрый вывод:\n"
            f"• {_compact(data['takeaway'], 'По скрину есть контекст, но финальный вывод нужен по ссылке.')}\n\n"
            "Что дальше:\n"
            "• Нажми 🔍 Анализ и отправь ссылку — я сравню odds с AI probability и дам вывод EDGE / NO TRADE."
        )
    else:
        text = (
            "🧠 Скрин\n\n"
            f"Что видно: {_compact(data['summary'] or data['visible'], 'недостаточно читаемых данных')}\n\n"
            f"Быстрый вывод: {_compact(data['takeaway'], 'Для полного анализа отправь ссылку Polymarket.')}"
        )
    return _limit_chars(text)


format_live_analyst_image_card = format_live_analyst_image_response
format_screenshot_reply = format_live_analyst_image_response
