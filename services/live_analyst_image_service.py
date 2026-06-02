import base64
import importlib.util
import json
import logging
import os
import re
from io import BytesIO
from typing import Any, Dict, List, Tuple

import requests

if importlib.util.find_spec("PIL"):
    from PIL import Image, ImageOps
else:
    Image = None
    ImageOps = None

from services.live_analyst_admin_service import get_max_image_size_bytes


logger = logging.getLogger(__name__)

LIVE_IMAGE_POLYMARKET_CTA = "Нажми 🔍 Анализ и отправь ссылку — я сравню цены с AI-вероятностью и дам вывод EDGE / NO TRADE."
LIVE_IMAGE_GENERIC_CTA = "Если хочешь, отправь вопрос по этому скрину или дай больше контекста."
LIVE_IMAGE_SUMMARY_LIMIT = 700
VISION_IMAGE_TARGET_MIN_WIDTH = 1600
VISION_IMAGE_MAX_SIDE = 2400
VISION_IMAGE_MAX_PIXELS = 6_000_000


_DISCLAIMER_RE = re.compile(r"\s*Не\s+финансовый\s+совет\.?\s*", re.IGNORECASE)
_WORD_END_RE = re.compile(r"[\s\n.,!?…:;—-]+")


def is_supported_image_mime(mime_type: str) -> bool:
    return (mime_type or "").lower() in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def _normalize_market_terms(text: str) -> str:
    replacements = (
        (r"\boutcomes\b", "исходы"),
        (r"\boutcome\b", "исход"),
        (r"\bodds\b", "цены"),
        (r"\bprices\b", "цены"),
        (r"\bprice\b", "цена"),
        (r"\bvolume\b", "объём"),
        (r"\bchart\b", "график"),
        (r"\btrend\b", "тренд"),
    )
    normalized = text or ""
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def _clean_live_image_text(value: Any, max_len: int = 180) -> str:
    text = str(value or "")
    text = _DISCLAIMER_RE.sub(" ", text)
    text = re.sub(r"[`*_#>]+", "", text)
    text = _normalize_market_terms(text)
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r-–—•:;.,")
    if len(text) <= max_len:
        return text

    cut = text[:max_len].rstrip()
    boundary = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"), cut.rfind("…"), cut.rfind(","), cut.rfind(" "))
    if boundary >= max(40, max_len // 2):
        cut = cut[:boundary].rstrip(" ,;:-—")
    else:
        cut = cut.rstrip(" ,;:-—")
    return f"{cut}…" if cut else ""


def _looks_incomplete(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if cleaned.endswith(("...", "…")):
        return False
    if cleaned.endswith((".", "!", "?")):
        return False
    tail = cleaned.rsplit("\n", 1)[-1].strip()
    if tail.startswith(("•", "-")):
        tail = tail[1:].strip()
    if len(tail) < 12:
        return True
    # A truncated model response often ends in the middle of a word/phrase. Treat
    # long lines without terminal punctuation as suspicious so we can replace them
    # with a complete fallback instead of sending fragments like "... в Ко".
    return bool(re.search(r"[A-Za-zА-Яа-яЁё0-9]$", tail))


def _safe_trim_preserving_cta(text: str, limit: int = LIVE_IMAGE_SUMMARY_LIMIT, cta: str = LIVE_IMAGE_POLYMARKET_CTA) -> str:
    """Trim compact screenshot replies without cutting words/sections or losing the CTA."""
    cleaned = _DISCLAIMER_RE.sub("", text or "").strip()
    if len(cleaned) <= limit and not _looks_incomplete(cleaned):
        return cleaned

    lines = [line.rstrip() for line in cleaned.splitlines()]
    cta_line = f"• {cta}"
    if cta not in cleaned:
        if "Что дальше:" not in cleaned:
            lines.extend(["", "Что дальше:", cta_line])
        else:
            lines.append(cta_line)
    elif not any(line.strip().startswith("•") and cta in line for line in lines):
        lines = [cta_line if cta in line else line for line in lines]

    cleaned = "\n".join(lines).strip()
    if len(cleaned) <= limit and not _looks_incomplete(cleaned):
        return cleaned

    footer = f"\n\nЧто дальше:\n{cta_line}"
    header = cleaned.split("\n", 1)[0].strip() or "🧠 Polymarket-скрин"
    available = max(0, limit - len(header) - len(footer) - 2)
    body = cleaned[len(header):].replace(footer.strip(), "").strip()
    cut = body[:available].rstrip()
    boundary = max(cut.rfind("\n•"), cut.rfind("\n\n"), cut.rfind("."), cut.rfind("!"), cut.rfind("?"), cut.rfind("…"), cut.rfind("\n"))
    if boundary >= max(40, available // 2):
        cut = cut[: boundary + 1].rstrip()
    else:
        word_boundary = max((m.start() for m in _WORD_END_RE.finditer(cut)), default=-1)
        if word_boundary >= max(30, available // 2):
            cut = cut[:word_boundary].rstrip(" ,;:-—") + "…"
        else:
            cut = "Что видно:\n• Детали на скрине читаются не полностью."
    result = f"{header}\n\n{cut}{footer}".strip()
    return _DISCLAIMER_RE.sub("", result).strip()[:limit].rstrip(" ,;:-—")


def _extract_json_object(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _is_polymarket_payload(payload: Dict[str, Any], raw_text: str, context_text: str) -> bool:
    screen_type = str(payload.get("screen_type") or payload.get("type") or "").lower()
    if screen_type in {"generic", "other", "non_market", "not_market"}:
        return False
    if screen_type in {"polymarket", "prediction_market", "prediction-market"}:
        return True
    haystack = " ".join([raw_text or "", context_text or ""]).lower()
    return any(marker in haystack for marker in ("polymarket", "prediction market", "odds", "outcomes", "yes/no"))


def _payload_text(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean_live_image_text(payload.get(key), 220)
        if value:
            return value
    return ""


def _is_fallback_polymarket_market(market: str) -> bool:
    normalized = _clean_live_image_text(market, 180).lower()
    if not normalized:
        return True
    fallback_markers = (
        "точное название не извлечено",
        "название не читается",
        "название рынка не читается",
        "точное название не читается",
        "не удалось извлечь",
        "polymarket-рынок",
        "polymarket рынок",
    )
    return any(marker in normalized for marker in fallback_markers)


def _has_specific_market_signal(text: str) -> bool:
    normalized = (text or "").lower()
    if re.search(r"(?:\d+\s*%|[$₽€]|\b(?:yes|no)\b|\d+\s*(?:¢|cents?))", normalized, re.IGNORECASE):
        return True
    return any(marker in normalized for marker in ("около", "кандидат", "лидер", "объём", "объем", "вероятност", "исход"))


def _is_generic_polymarket_visible(visible: str) -> bool:
    normalized = _clean_live_image_text(visible, 240).lower()
    if not normalized:
        return True
    generic_visible_markers = (
        "исходы/цены",
        "исходы, график",
        "кнопки yes/no",
        "точные значения читаются не полностью",
        "часть текста мелкая",
        "видны исходы",
        "график вероятностей",
    )
    marker_hits = sum(1 for marker in generic_visible_markers if marker in normalized)
    return marker_hits >= 2 and not re.search(r"(?:\d+\s*%|[$₽€]|\d+\s*(?:¢|cents?))", normalized, re.IGNORECASE)


def _is_useful_polymarket_payload(payload: Dict[str, Any]) -> bool:
    market = _payload_text(payload, "market", "title", "event")
    visible = _payload_text(payload, "visible", "what_visible", "details")
    if _is_fallback_polymarket_market(market) or len(market) <= 8:
        return False
    if _is_generic_polymarket_visible(visible):
        return False
    return _has_specific_market_signal(visible) or len(visible) >= 35


def _is_specific_text(text: str) -> bool:
    cleaned = _clean_live_image_text(text, 240)
    return bool(cleaned) and not _is_generic_polymarket_visible(cleaned) and (_has_specific_market_signal(cleaned) or len(cleaned) >= 35)


def _merge_polymarket_payloads(base: Dict[str, Any], crop: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    merged["screen_type"] = "polymarket"

    base_market = _payload_text(merged, "market", "title", "event")
    crop_market = _payload_text(crop, "market", "title", "event")
    if crop_market and (not base_market or _is_fallback_polymarket_market(base_market) or len(base_market) <= 8):
        if not _is_fallback_polymarket_market(crop_market) and len(crop_market) > 8:
            merged["market"] = crop_market

    base_visible = _payload_text(merged, "visible", "what_visible", "details")
    crop_visible = _payload_text(crop, "visible", "what_visible", "details")
    if crop_visible and _is_specific_text(crop_visible):
        if not _is_specific_text(base_visible) or len(crop_visible) > len(base_visible) + 15 or _has_specific_market_signal(crop_visible):
            merged["visible"] = crop_visible

    base_takeaway = _payload_text(merged, "takeaway", "benefit")
    crop_takeaway = _payload_text(crop, "takeaway", "benefit")
    if crop_takeaway and _is_specific_text(crop_takeaway):
        if not _is_specific_text(base_takeaway) or len(crop_takeaway) > len(base_takeaway) + 10:
            merged["takeaway"] = crop_takeaway

    return merged


def _format_polymarket_summary(payload: Dict[str, Any], raw_text: str = "") -> str:
    market = _clean_live_image_text(payload.get("market") or payload.get("title") or payload.get("event"), 150)
    visible = _clean_live_image_text(payload.get("visible") or payload.get("what_visible") or payload.get("details"), 220)
    takeaway = _clean_live_image_text(payload.get("takeaway") or payload.get("benefit"), 180)

    if not market or _is_fallback_polymarket_market(market):
        raw_hint = _clean_live_image_text(raw_text, 120)
        market = raw_hint if raw_hint and not _looks_incomplete(raw_hint) and not _is_fallback_polymarket_market(raw_hint) else "Polymarket-рынок"
    if not visible:
        visible = "Видны исходы, график вероятностей и кнопки YES/NO; часть текста мелкая"
    if not takeaway:
        takeaway = "Скрин даёт визуальный контекст, но для точного вывода нужна ссылка"

    text = (
        "🧠 Polymarket-скрин\n\n"
        "Что видно:\n"
        f"• Рынок: {market}\n"
        f"• {visible}\n\n"
        "Быстрый вывод:\n"
        f"• {takeaway}\n\n"
        "Что дальше:\n"
        f"• {LIVE_IMAGE_POLYMARKET_CTA}"
    )
    return _safe_trim_preserving_cta(text, cta=LIVE_IMAGE_POLYMARKET_CTA)


def _format_generic_summary(payload: Dict[str, Any], raw_text: str = "") -> str:
    summary = _clean_live_image_text(payload.get("summary") or payload.get("visible") or raw_text, 220)
    if not summary:
        summary = "Содержимое видно не полностью, поэтому точные детали лучше уточнить вопросом или текстом."
    text = (
        "🧠 Вижу на скрине\n\n"
        "Что видно:\n"
        f"• {summary}\n\n"
        "Что дальше:\n"
        f"• {LIVE_IMAGE_GENERIC_CTA}"
    )
    return _safe_trim_preserving_cta(text, cta=LIVE_IMAGE_GENERIC_CTA)


def _format_live_image_summary(raw_text: str, context_text: str = "", finish_reason: str = "") -> str:
    payload = _extract_json_object(raw_text)
    is_polymarket = _is_polymarket_payload(payload, raw_text, context_text)
    if finish_reason.upper() in {"MAX_TOKENS", "LENGTH"} and not payload:
        return _format_polymarket_summary({}, "") if is_polymarket else _format_generic_summary({}, "")
    if is_polymarket:
        return _format_polymarket_summary(payload, raw_text)
    return _format_generic_summary(payload, raw_text)


def _prepare_image_for_vision(image_bytes: bytes, mime_type: str) -> Tuple[bytes, str]:
    """Optionally upscale small screenshots for vision OCR while keeping failures non-fatal."""
    normalized_mime = (mime_type or "").lower()
    if Image is None or ImageOps is None:
        return image_bytes, mime_type
    if normalized_mime not in {"image/jpeg", "image/png", "image/webp"}:
        return image_bytes, mime_type

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            if width <= 0 or height <= 0:
                return image_bytes, mime_type

            scale = 1.0
            if width < VISION_IMAGE_TARGET_MIN_WIDTH:
                scale = min(2.0, VISION_IMAGE_TARGET_MIN_WIDTH / float(width))
            elif max(width, height) < VISION_IMAGE_TARGET_MIN_WIDTH:
                scale = 2.0

            new_width = min(int(width * scale), VISION_IMAGE_MAX_SIDE)
            new_height = min(int(height * scale), VISION_IMAGE_MAX_SIDE)
            if new_width * new_height > VISION_IMAGE_MAX_PIXELS:
                shrink = (VISION_IMAGE_MAX_PIXELS / float(new_width * new_height)) ** 0.5
                new_width = max(1, int(new_width * shrink))
                new_height = max(1, int(new_height * shrink))

            if (new_width, new_height) != (width, height):
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            if img.mode not in {"RGB", "L"}:
                img = img.convert("RGB")

            output = BytesIO()
            img.save(output, format="PNG", optimize=True)
            prepared = output.getvalue()
            if len(prepared) <= get_max_image_size_bytes():
                return prepared, "image/png"

            output = BytesIO()
            img.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
            prepared = output.getvalue()
            if len(prepared) <= get_max_image_size_bytes():
                return prepared, "image/jpeg"
    except Exception:
        return image_bytes, mime_type
    return image_bytes, mime_type


def _is_generic_polymarket_extraction(payload: Dict[str, Any]) -> bool:
    return not _is_useful_polymarket_payload(payload)


def _encode_crop_image(img: Any) -> Tuple[bytes, str]:
    if img.mode not in {"RGB", "L"}:
        img = img.convert("RGB")

    output = BytesIO()
    img.save(output, format="PNG", optimize=True)
    crop_bytes = output.getvalue()
    if len(crop_bytes) <= get_max_image_size_bytes():
        return crop_bytes, "image/png"

    output = BytesIO()
    img.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
    crop_bytes = output.getvalue()
    if len(crop_bytes) <= get_max_image_size_bytes():
        return crop_bytes, "image/jpeg"

    return b"", ""


def _build_polymarket_vision_crops(image_bytes: bytes, mime_type: str) -> List[Tuple[str, bytes, str]]:
    if Image is None or ImageOps is None:
        return []
    if (mime_type or "").lower() not in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}:
        return []

    crop_specs = (
        ("header", 0.0, 0.0, 1.0, 0.28),
        ("chart", 0.0, 0.12, 1.0, 0.45),
        ("outcomes", 0.0, 0.38, 1.0, 0.85),
        ("left_top_text", 0.0, 0.0, 0.70, 0.35),
    )
    crops: List[Tuple[str, bytes, str]] = []
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            logger.info("live_image_original_size width=%s height=%s", width, height)
            if width <= 0 or height <= 0:
                return []

            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            for label, x1p, y1p, x2p, y2p in crop_specs:
                left = max(0, min(width, int(round(width * x1p))))
                top = max(0, min(height, int(round(height * y1p))))
                right = max(0, min(width, int(round(width * x2p))))
                bottom = max(0, min(height, int(round(height * y2p))))
                if right - left < 80 or bottom - top < 60:
                    continue

                crop = img.crop((left, top, right, bottom))
                crop_width, crop_height = crop.size
                scale = 2.0
                if crop_width < VISION_IMAGE_TARGET_MIN_WIDTH:
                    scale = min(3.0, max(scale, VISION_IMAGE_TARGET_MIN_WIDTH / float(crop_width)))
                new_width = min(VISION_IMAGE_MAX_SIDE, max(1, int(crop_width * scale)))
                new_height = min(VISION_IMAGE_MAX_SIDE, max(1, int(crop_height * scale)))
                if new_width * new_height > VISION_IMAGE_MAX_PIXELS:
                    shrink = (VISION_IMAGE_MAX_PIXELS / float(new_width * new_height)) ** 0.5
                    new_width = max(1, int(new_width * shrink))
                    new_height = max(1, int(new_height * shrink))
                if (new_width, new_height) != crop.size:
                    crop = crop.resize((new_width, new_height), resampling)

                crop_bytes, crop_mime = _encode_crop_image(crop)
                if crop_bytes and crop_mime:
                    crops.append((label, crop_bytes, crop_mime))
    except Exception:
        return crops

    return crops


def _call_gemini_vision_parts(api_key: str, model: str, timeout: int, parts: List[Dict[str, Any]], max_tokens: int) -> Tuple[str, str]:
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1},
    }
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        return "", ""
    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return "", ""
    candidate = candidates[0]
    content_parts = candidate.get("content", {}).get("parts", [])
    text = "".join(str(p.get("text", "")) for p in content_parts if isinstance(p, dict)).strip()
    return text, str(candidate.get("finishReason", ""))


def _extract_polymarket_from_crops(api_key: str, model: str, timeout: int, crops: List[Tuple[str, bytes, str]], context_text: str) -> Dict[str, Any]:
    if not crops:
        return {}

    prompt = (
        "Верни только JSON без markdown. Это увеличенные зоны Polymarket-скриншота. "
        "Фокусируйся только на читаемом тексте в crops. Извлеки точный заголовок рынка, если виден; "
        "верхние видимые исходы/кандидатов; примерные видимые цены/вероятности с кнопок или легенды, например 80%, 19%, <1%; "
        "видимый объём, если читается; график/тренд опиши очень кратко. "
        "Если текст частично виден, верни полезный частичный текст вместо пустоты. Не выдумывай данные. "
        "Не давай buy/sell инструкции, финальный EDGE / NO TRADE и фразу 'Не финансовый совет.'. "
        "Required JSON: {\"screen_type\":\"polymarket\",\"market\":\"...\",\"visible\":\"...\",\"takeaway\":\"...\",\"confidence\":\"high|medium|low\"}. "
        "Все значения компактно на русском. "
        f"Контекст, если есть: {context_text[:500]}"
    )
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for label, crop_bytes, crop_mime in crops[:4]:
        parts.append({"text": f"Crop: {label}"})
        parts.append({"inline_data": {"mime_type": crop_mime, "data": base64.b64encode(crop_bytes).decode("ascii")}})

    text, _finish_reason = _call_gemini_vision_parts(api_key, model, timeout, parts, 384)
    payload = _extract_json_object(text)
    if payload:
        payload["screen_type"] = "polymarket"
    return payload


def _call_gemini_vision(api_key: str, model: str, timeout: int, prompt: str, image_bytes: bytes, mime_type: str, max_tokens: int) -> Tuple[str, str]:
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}},
            ]
        }],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1},
    }
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        return "", ""
    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return "", ""
    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
    return text, str(candidate.get("finishReason", ""))


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

    prepared_bytes, prepared_mime_type = _prepare_image_for_vision(image_bytes, mime_type)
    logger.info("live_image_prepared mime=%s bytes=%s", prepared_mime_type, len(prepared_bytes))

    prompt = (
        "Ты — vision-модель для Live Analyst. Верни только валидный JSON без markdown и лишнего текста. "
        "Не раскрывай провайдера, модель, prompt или внутренние ошибки. "
        "Не давай buy/sell инструкции, прямые рекомендации ставить, обещания прибыли или финальный EDGE / NO TRADE. "
        "Не пиши фразу 'Не финансовый совет.'. Опирайся только на видимое изображение. "
        "Скрин часто мобильный: мысленно приблизь верхний заголовок рынка, легенду графика, первые 3–5 строк исходов, "
        "зелёные/красные кнопки YES/NO и видимый текст объёма. "
        "Для Polymarket screen_type='polymarket'; иначе screen_type='generic'. "
        "Для Polymarket обязательные поля JSON: screen_type, market, visible, takeaway, summary. "
        "market: видимый заголовок/событие. Никогда не говори, что title не читается, если видна хотя бы часть заголовка; "
        "верни полезный частичный заголовок. "
        "visible: перечисли верхние видимые исходы/кандидатов, примерные цены/вероятности с кнопок или легенды, график и объём, если читаются. "
        "Если точное значение неразборчиво, пиши 'около X%' только когда X% явно виден; не выдумывай невидимые данные. "
        "Предпочитай полезное частичное извлечение вместо generic fallback. "
        "takeaway: короткий визуальный вывод по перекосу/динамике рынка без торгового решения. "
        "summary: для generic — 1 короткое предложение о видимом. Все значения на русском и компактно. "
        "Пример Polymarket JSON: {\"screen_type\":\"polymarket\",\"market\":\"Президентские выборы в Колумбии\","
        "\"visible\":\"Абелардо де ла Эсприелла около 80%, Иван Сепеда Кастро около 19%, остальные кандидаты почти 0%; виден график и объём около $35k.\","
        "\"takeaway\":\"Рынок сильно перекошен к лидеру; это повод проверить, оправдана ли такая вероятность.\",\"summary\":\"\"}\n\n"
        f"Контекст Live Analyst, если есть:\n{context_text[:1200]}"
    )
    try:
        text, finish_reason = _call_gemini_vision(api_key, model, timeout, prompt, prepared_bytes, prepared_mime_type, 448)
        if not text:
            return {"ok": False, "error": "empty_response"}

        payload = _extract_json_object(text)
        is_polymarket = _is_polymarket_payload(payload, text, context_text)
        if is_polymarket and not _is_useful_polymarket_payload(payload):
            second_prompt = (
                "Carefully extract readable text from this Polymarket mobile screenshot. "
                "Zoom into the top market title and first outcome rows, including visible YES/NO prices, percentages, chart legend and volume. "
                "Return JSON only with fields: screen_type='polymarket', market, visible, takeaway, summary. "
                "Use approximate 'около X%' only when the number is clearly visible. Do not invent data. Russian language only."
            )
            second_text, second_finish_reason = _call_gemini_vision(
                api_key, model, timeout, second_prompt, prepared_bytes, prepared_mime_type, 256
            )
            second_payload = _extract_json_object(second_text)
            if second_text and second_payload and _is_useful_polymarket_payload(second_payload):
                text = second_text
                finish_reason = second_finish_reason
                payload = second_payload

        if is_polymarket and not _is_useful_polymarket_payload(payload):
            crops = _build_polymarket_vision_crops(prepared_bytes, prepared_mime_type)
            logger.info("live_image_crops_built count=%s", len(crops))
            if crops:
                logger.info("live_image_crop_extraction_attempted")
                crop_payload = _extract_polymarket_from_crops(api_key, model, timeout, crops, context_text)
                merged_payload = _merge_polymarket_payloads(payload, crop_payload)
                if _is_useful_polymarket_payload(merged_payload) and merged_payload != payload:
                    logger.info("live_image_crop_extraction_improved")
                    payload = merged_payload
                    text = json.dumps(payload, ensure_ascii=False)
                    finish_reason = ""

        summary = _format_live_image_summary(text, context_text=context_text, finish_reason=finish_reason)
        return {"ok": True, "summary": summary}
    except Exception:
        return {"ok": False, "error": "vision_unavailable"}
