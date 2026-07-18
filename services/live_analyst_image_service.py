import base64
import copy
import importlib.util
import json
import logging
import os
import re
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

if importlib.util.find_spec("PIL"):
    from PIL import Image, ImageOps
else:
    Image = None
    ImageOps = None

from services.live_analyst_admin_service import get_max_image_size_bytes
from services.skill_loader_service import get_live_screenshot_skill_context
from services.polymarket_localized_normalizer import normalize_polymarket_screenshot_payload


class _NoDirectRequests:
    def post(self, *args, **kwargs):
        raise RuntimeError("direct Gemini HTTP is disabled; use services.gemini_gateway")

requests = _NoDirectRequests()  # compatibility for tests; not used for Gemini transport

logger = logging.getLogger(__name__)

class _VisionAttemptBudget:
    def __init__(self, limit: Optional[int] = None):
        if limit is None:
            try:
                limit = int(os.getenv("LIVE_ANALYST_VISION_TOTAL_ATTEMPTS_PER_REQUEST", "2"))
            except (TypeError, ValueError):
                limit = 2
        self.limit = max(0, int(limit or 0))
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume(self, attempts: int) -> None:
        self.used += max(1, int(attempts or 1))


VISION_BLOCK_REASONS = {
    "blocked_global",
    "blocked_feature",
    "blocked_background",
    "api_key_missing",
    "daily_limit_exceeded",
    "request_limit_exceeded",
    "db_error",
    "vision_disabled",
}

LIVE_IMAGE_POLYMARKET_CTA = "Для EDGE / NO TRADE отправь ссылку или попробуй поиск по скрину ещё раз."
LIVE_IMAGE_GENERIC_CTA = "Отправь оригинальный скрин Polymarket без интерфейса Telegram или ссылку на рынок."
LIVE_IMAGE_SUMMARY_LIMIT = 700
LIVE_IMAGE_DEBUG_LOGS = os.getenv("LIVE_IMAGE_DEBUG_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}
VISION_IMAGE_TARGET_MIN_WIDTH = 1600
VISION_IMAGE_MAX_SIDE = 2400
VISION_IMAGE_MAX_PIXELS = 6_000_000


_DISCLAIMER_RE = re.compile(r"\s*Не\s+финансовый\s+совет\.?\s*", re.IGNORECASE)
_WORD_END_RE = re.compile(r"[\s\n.,!?…:;—-]+")


def _get_live_image_vision_models(primary_model: str) -> List[str]:
    primary = (primary_model or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    models: List[str] = [primary]
    for fallback in ("gemini-2.5-flash-lite", "gemini-2.0-flash"):
        if fallback not in models:
            models.append(fallback)
    return models


def _is_gemini_25_model(model: str) -> bool:
    return "gemini-2.5" in (model or "").lower()


def _is_unsupported_thinking_config_response(status_code: int, response_text: str) -> bool:
    if status_code != 400:
        return False
    lowered = (response_text or "").lower()
    return "thinkingconfig" in lowered or ("thinking" in lowered and ("unsupported" in lowered or "unknown" in lowered or "invalid" in lowered))


def _is_retryable_empty_max_tokens(finish_reason: str, text: str, payload: Optional[Dict[str, Any]] = None) -> bool:
    if (finish_reason or "").upper() not in {"MAX_TOKENS", "LENGTH"}:
        return False
    if len(text or "") >= 40:
        return False
    parsed_payload = payload or _extract_json_object(text or "") or _payload_from_unstructured_vision_text(text or "")
    if parsed_payload and (
        _is_useful_polymarket_payload(parsed_payload)
        or bool(_payload_text(parsed_payload, "screen_type", "type", "summary", "market", "visible", "takeaway"))
    ):
        return False
    return True


def _replace_first_prompt_text(payload: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    fallback_payload = copy.deepcopy(payload)
    contents = fallback_payload.get("contents")
    if not isinstance(contents, list):
        return fallback_payload
    for content in contents:
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and "text" in part:
                part["text"] = prompt
                return fallback_payload
    return fallback_payload


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


def _safe_gemini_preview(value: Any, max_len: int = 300, api_key: str = "") -> str:
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _extract_text_from_gemini_candidate(candidate: Dict[str, Any]) -> str:
    chunks: List[str] = []
    content = candidate.get("content") if isinstance(candidate, dict) else None
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and "text" in part:
                    value = part.get("text")
                    if value is not None:
                        chunks.append(value if isinstance(value, str) else str(value))

    for key in ("output", "text"):
        value = candidate.get(key) if isinstance(candidate, dict) else None
        if value is None:
            continue
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, list):
            chunks.extend(str(item) for item in value if item is not None)
        else:
            chunks.append(str(value))

    return "".join(chunks).strip()


def _payload_from_unstructured_vision_text(raw: str) -> Dict[str, Any]:
    text = re.sub(r"\s+", " ", raw or "").strip()
    if not text:
        return {}

    marker_re = re.compile(
        r"(polymarket|\byes\b|\bno\b|\d+\s*%|[$₽€]|¢|\btariff\b|health care|\bhealth\b|alien|president|white house|briefing|outcome)",
        re.IGNORECASE,
    )
    if not marker_re.search(text):
        return {}

    lines = [_clean_live_image_text(line, 180) for line in re.split(r"[\r\n]+", raw or "") if _clean_live_image_text(line, 180)]
    if not lines:
        lines = [_clean_live_image_text(text, 220)]

    title = ""
    for line in lines:
        lower = line.lower()
        if "?" in line and not re.search(r"\d+\s*%|[$₽€]|¢", line):
            title = line
            break
        if not title and len(line) >= 18 and not re.search(r"\d+\s*%|[$₽€]|¢", line) and "polymarket" not in lower:
            title = line

    visible_lines = [line for line in lines if marker_re.search(line)]
    visible = _clean_live_image_text("; ".join(visible_lines or lines), 220)
    if not visible or (not title and len(visible) < 20 and not _has_specific_market_signal(visible)):
        return {}

    payload: Dict[str, Any] = {"screen_type": "polymarket", "visible": visible, "confidence": "low"}
    if title:
        payload["market"] = title
    if _has_specific_market_signal(visible):
        payload["takeaway"] = "На скрине видны конкретные исходы/значения; для вывода нужен полный анализ по ссылке."
    return payload


def _is_polymarket_payload(payload: Dict[str, Any], raw_text: str, context_text: str) -> bool:
    screen_type = str(payload.get("screen_type") or payload.get("type") or "").lower()
    if screen_type in {"generic", "other", "non_market", "not_market"}:
        return False
    if screen_type in {"polymarket", "polymarket_market", "prediction_market", "prediction-market"}:
        return True
    haystack = " ".join([raw_text or "", context_text or ""]).lower()
    return any(marker in haystack for marker in ("polymarket", "prediction market", "odds", "outcomes", "yes/no"))


def _payload_text(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _clean_live_image_text(payload.get(key), 220)
        if value:
            return value
    return ""


def _short_debug_field(value: Any, max_len: int) -> str:
    if not LIVE_IMAGE_DEBUG_LOGS:
        return ""
    return _clean_live_image_text(value, max_len)


def _log_live_image_debug_fields(prefix: str, payload: Dict[str, Any]) -> None:
    if not LIVE_IMAGE_DEBUG_LOGS:
        return
    logger.info(
        "%s market=%s visible=%s",
        prefix,
        _short_debug_field(payload.get("market") or payload.get("title") or payload.get("event"), 120),
        _short_debug_field(payload.get("visible") or payload.get("what_visible") or payload.get("details"), 180),
    )


def _is_fallback_polymarket_market(market: str) -> bool:
    normalized = _clean_live_image_text(market, 180).lower()
    if not normalized:
        return True
    fallback_markers = (
        "название не читается",
        "точное название не читается",
        "не удалось извлечь",
        "polymarket-рынок",
        "polymarket рынок",
    )
    old_fallback_markers = (
        "точное название " + "не извлечено",
        "название рынка " + "не читается",
    )
    fallback_markers = fallback_markers + old_fallback_markers
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


def _remove_polymarket_direct_advice(text: str) -> str:
    cleaned = text or ""
    direct_patterns = (
        r'\b(?:buy|sell)\b[^.;!?"]*',
        r"\bprofit\b",
        r"\bставь\w*\b",
        r"\bкуп(?:и|ить|ите|ляй|лять)\w*\b",
        r"\bпрод(?:ай|ать|айте|авай|авать)\w*\b",
        r"\bгарантированн\w*\b",
    )
    for pattern in direct_patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _polymarket_percent_values(text: str) -> List[int]:
    values: List[int] = []
    for match in re.finditer(r"(?<!\d)(\d{1,3})\s*%", text or ""):
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if 0 <= value <= 100:
            values.append(value)
    return values


def _clean_polymarket_outcome_name(name: str) -> str:
    cleaned = name or ""
    cleaned = re.sub(r"[\s:;,()]+$", "", cleaned)
    cleaned = re.sub(r"^[\s:;,()•\-–—]+", "", cleaned)
    cleaned = re.sub(r"\b(?:first visible|visible|исход|outcome|outcomes|is|at)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^[\s\'"]+|[\s\'"]+$', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
    return cleaned


def _clean_polymarket_visible_text(visible: str) -> str:
    text = str(visible or "")
    text = _DISCLAIMER_RE.sub(" ", text)
    text = re.sub(r"(?<!\w)-([A-Za-zА-Яа-яЁё0-9][^-]{2,80}?)-(?!\w)", r"\1", text)
    text = re.sub(r"\bBuy\s+Yes\b", "YES", text, flags=re.IGNORECASE)
    text = re.sub(r"\bBuy\s+No\b", "NO", text, flags=re.IGNORECASE)
    text = _normalize_market_terms(text)
    text = re.sub(r"\bOther\s*(?:\.\.\.|…)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bOther\s*(?:\.\.\.|…)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:^|[.!?]\s+)The market is[^.!?]*(?:[.!?]|$)", " ", text, flags=re.IGNORECASE)
    text = _remove_polymarket_direct_advice(text)
    text = re.sub(r"[`*_#>]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r-–—•:;.,")

    percent_matches = list(re.finditer(r"([^;()]+?)\s*[—-]?\s*(\d{1,3}(?:[.,]\d+)?)\s*%", text))
    outcomes: List[str] = []
    seen: set = set()
    for match in percent_matches:
        name = _clean_polymarket_outcome_name(match.group(1))
        if not name:
            continue
        try:
            value = float(match.group(2).replace(",", "."))
        except ValueError:
            continue
        if not 0 <= value <= 100:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        display_value = int(value) if float(value).is_integer() else value
        outcomes.append(f"{name} — {display_value}%")
        if len(outcomes) >= 5:
            break
    if outcomes:
        return ", ".join(outcomes) + "."

    text = re.sub(r"\([^)]*\b(?:YES|NO)\b[^)]*\)", " ", text, flags=re.IGNORECASE)
    price_parts: List[str] = []
    for part in re.split(r"[;\n]+", text):
        cleaned = re.sub(r"\s+", " ", part).strip(" \t\n\r-–—•:;.,")
        if cleaned and re.search(r"\b(?:YES|NO)\b|\d+\s*(?:c|¢|cents?)", cleaned, re.IGNORECASE):
            price_parts.append(cleaned)
        if len(price_parts) >= 3:
            break
    if price_parts:
        return "; ".join(price_parts) + "."
    return _clean_live_image_text(text, 180)


def _raw_polymarket_takeaway_is_unsafe(raw_takeaway: str, market: str) -> bool:
    raw = _clean_live_image_text(raw_takeaway, 240)
    if not raw:
        return True
    lowered = raw.lower()
    if len(raw) > 180 or "the market is" in lowered or "other…" in lowered or "other..." in lowered:
        return True
    if market and market.lower() in lowered:
        return True
    latin_words = re.findall(r"\b[a-zA-Z]{3,}\b", raw)
    cyrillic_words = re.findall(r"[А-Яа-яЁё]{3,}", raw)
    if len(latin_words) > len(cyrillic_words):
        return True
    if re.search(r"\b(?:buy|sell|profit)\b", lowered) or re.search(r"\b(?:куп|прод|ставь|гарантированн)", lowered):
        return True
    return False


def _build_polymarket_screenshot_takeaway(market: str, visible: str, raw_takeaway: str) -> str:
    values = _polymarket_percent_values(visible)
    if values:
        if max(values) >= 75:
            return (
                "Один исход сильно лидирует, поэтому важно проверить, не заложил ли рынок уже основную информацию. "
                "Edge возможен только если AI-вероятность заметно отличается от цены."
            )
        if len(values) >= 2 and min(values) >= 45 and max(values) <= 60:
            return (
                "Видимые исходы держатся около 50–55%, значит рынок выглядит неопределённым. "
                "Потенциальный edge зависит от новостей, правил resolution и точной цены входа."
            )
        return (
            "По видимым ценам рынок не даёт готового вывода: edge появляется только там, "
            "где AI-вероятность заметно расходится с текущей ценой."
        )

    if not _raw_polymarket_takeaway_is_unsafe(raw_takeaway, market):
        return _remove_polymarket_direct_advice(_clean_live_image_text(raw_takeaway, 150))
    return "По одному скрину нельзя оценить edge: нужны ссылка, правила resolution, ликвидность и актуальные цены."


def _build_polymarket_screenshot_checks(market: str, visible: str) -> str:
    values = _polymarket_percent_values(visible)
    if values and max(values) >= 75:
        return "Проверь, почему цена так высока: если причина уже очевидна рынку, вход может быть поздним."
    if len(values) >= 2 and min(values) >= 45 and max(values) <= 60:
        return "Что именно засчитывается в правилах рынка и есть ли свежие новости, которые рынок ещё не полностью учёл."
    if values:
        return "Сравни цену с AI-вероятностью и проверь ликвидность: без заметного расхождения edge обычно нет."
    return "Проверь ссылку, правила рынка и актуальную ликвидность — по одному скрину нельзя оценить edge."


def _compose_polymarket_card(market: str, visible: str, takeaway: str, checks: str) -> str:
    text = (
        "🧠 Polymarket-скрин\n\n"
        "Что видно:\n"
        f"• Рынок: {market}\n"
        f"• {visible}\n\n"
        "Быстрый вывод:\n"
        f"• {takeaway}\n\n"
        "Что проверить:\n"
        f"• {checks}\n\n"
        "Что дальше:\n"
        f"• {LIVE_IMAGE_POLYMARKET_CTA}"
    )
    return text


def _format_polymarket_summary(payload: Dict[str, Any], raw_text: str = "") -> str:
    market = _clean_live_image_text(payload.get("market") or payload.get("title") or payload.get("event"), 150)
    visible = _clean_polymarket_visible_text(payload.get("visible") or payload.get("what_visible") or payload.get("details"))
    raw_takeaway = _clean_live_image_text(payload.get("takeaway") or payload.get("benefit"), 220)

    if not market or _is_fallback_polymarket_market(market):
        raw_hint = _clean_live_image_text(raw_text, 120)
        market = raw_hint if raw_hint and not _looks_incomplete(raw_hint) and not _is_fallback_polymarket_market(raw_hint) else "Polymarket-рынок"
    if not visible:
        visible = "Исходы и цены видны не полностью."

    takeaway = _build_polymarket_screenshot_takeaway(market, visible, raw_takeaway)
    checks = _build_polymarket_screenshot_checks(market, visible)

    text = _compose_polymarket_card(market, visible, takeaway, checks)
    if len(text) <= LIVE_IMAGE_SUMMARY_LIMIT:
        return text

    checks = _clean_live_image_text(checks, 105)
    text = _compose_polymarket_card(market, visible, takeaway, checks)
    if len(text) <= LIVE_IMAGE_SUMMARY_LIMIT:
        return text

    visible = _clean_live_image_text(visible, 150)
    text = _compose_polymarket_card(market, visible, takeaway, checks)
    if len(text) <= LIVE_IMAGE_SUMMARY_LIMIT:
        return text

    checks = "Проверь правила, новости, ликвидность и актуальные цены."
    visible = _clean_live_image_text(visible, 110)
    text = _compose_polymarket_card(market, visible, takeaway, checks)
    return _safe_trim_preserving_cta(text, cta=LIVE_IMAGE_POLYMARKET_CTA)


def _format_generic_summary(payload: Dict[str, Any], raw_text: str = "") -> str:
    summary = _clean_live_image_text(payload.get("summary") or payload.get("visible") or raw_text, 220)
    generic_phrases = (
        "Содержимое видно не полностью",
        "точные детали лучше уточнить",
        "дай больше контекста",
        "Вижу на скрине",
    )
    if any(phrase.lower() in summary.lower() for phrase in generic_phrases):
        summary = ""
    if not summary:
        summary = "Я вижу изображение, но не смог надёжно извлечь рынок. Лучше отправь оригинальный скрин Polymarket без интерфейса Telegram или ссылку на рынок."
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


def _build_live_image_metadata(payload: Dict[str, Any], raw_text: str, context_text: str, summary: str) -> Dict[str, Any]:
    if not _is_polymarket_payload(payload or {}, raw_text, context_text):
        return {"screen_type": "generic"}

    market = _clean_live_image_text((payload or {}).get("market") or (payload or {}).get("title") or (payload or {}).get("event"), 240)
    visible = _clean_polymarket_visible_text((payload or {}).get("visible") or (payload or {}).get("what_visible") or (payload or {}).get("details"))
    takeaway = _clean_live_image_text((payload or {}).get("takeaway") or (payload or {}).get("benefit"), 300)

    if not market or _is_fallback_polymarket_market(market):
        match = re.search(r"•\s*Рынок:\s*([^\n]+)", summary or "")
        if match:
            market = _clean_live_image_text(match.group(1), 240)
    if not visible:
        visible_match = re.search(r"Что видно:\s*(?:\n|.)*?•\s*Рынок:[^\n]*\n•\s*([^\n]+)", summary or "")
        if visible_match:
            visible = _clean_live_image_text(visible_match.group(1), 240)
    if not takeaway:
        takeaway_match = re.search(r"Быстрый вывод:\s*\n•\s*([^\n]+)", summary or "")
        if takeaway_match:
            takeaway = _clean_live_image_text(takeaway_match.group(1), 300)

    metadata: Dict[str, Any] = {"screen_type": "polymarket"}
    for key in ("ui_language", "visible_url", "market_title_original", "market_title_canonical", "outcomes_original", "outcomes_canonical", "visible_prices", "category_original", "category_canonical"):
        if (payload or {}).get(key) is not None:
            metadata[key] = (payload or {}).get(key)
    if market and not _is_fallback_polymarket_market(market):
        metadata["market"] = market
    if visible:
        metadata["visible"] = visible
    if takeaway:
        metadata["takeaway"] = takeaway
    return normalize_polymarket_screenshot_payload(metadata)


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


def _is_generic_live_image_payload(payload: Dict[str, Any], summary_text: str = "") -> bool:
    payload = payload or {}
    screen_type = _payload_text(payload, "screen_type", "type").lower()
    market = _payload_text(payload, "market", "title", "event")
    visible = _payload_text(payload, "visible", "what_visible", "details")
    summary = " ".join([_payload_text(payload, "summary", "takeaway"), summary_text or ""])
    haystack = " ".join([screen_type, market, visible, summary]).lower()

    if _is_useful_polymarket_payload(payload):
        return False

    has_numeric_or_marker = bool(
        re.search(r"(?:\d+\s*%|[$₽€]|\d+\s*(?:¢|cents?)|\b(?:yes|no)\b)", haystack, re.IGNORECASE)
        or any(marker in haystack for marker in ("polymarket", "prediction market", "исход", "outcome", "volume", "объём", "объем"))
    )
    generic_screen = not screen_type or screen_type in {"generic", "other", "non_market", "not_market"}
    empty_fields = not market and not visible
    generic_phrases = (
        "содержимое видно не полностью",
        "точные детали лучше уточнить",
        "дай больше контекста",
        "вижу на скрине",
        "не смог надёжно извлечь рынок",
        "не смог надежно извлечь рынок",
    )
    has_generic_phrase = any(phrase in haystack for phrase in generic_phrases)

    if generic_screen and empty_fields:
        return True
    if has_generic_phrase and not has_numeric_or_marker:
        return True
    if generic_screen and not market and not _has_strong_visible_values(visible):
        return True
    return False


def _has_strong_visible_values(text: str) -> bool:
    normalized = text or ""
    if re.search(r"(?:\d+\s*%|[$₽€]|\d+\s*(?:¢|cents?)|\b(?:yes|no)\b)", normalized, re.IGNORECASE):
        return True
    candidate_markers = (
        "tariff",
        "health",
        "alien",
        "president",
        "medicare",
        "medicaid",
        "кандидат",
        "исход:",
    )
    return any(marker in normalized.lower() for marker in candidate_markers)


def _is_failed_or_empty_full_extraction(payload: Dict[str, Any], raw_text: str, finish_reason: str) -> bool:
    payload = payload or {}
    text = (raw_text or "").strip()
    normalized_finish = (finish_reason or "").upper()

    if not payload:
        return True
    if not text:
        return True
    if len(text) < 40 and not payload:
        return True
    if normalized_finish in {"MAX_TOKENS", "LENGTH"} and not payload:
        return True

    screen_type = _payload_text(payload, "screen_type", "type")
    market = _payload_text(payload, "market", "title", "event")
    visible = _payload_text(payload, "visible", "what_visible", "details")
    summary = _payload_text(payload, "summary")
    return not any((screen_type, market, visible, summary))


def _should_attempt_crop_extraction(payload: Dict[str, Any], raw_text: str, context_text: str) -> bool:
    screen_type = str((payload or {}).get("screen_type") or (payload or {}).get("type") or "").lower()
    market = _payload_text(payload or {}, "market", "title", "event")
    visible = _payload_text(payload or {}, "visible", "what_visible", "details")
    haystack = " ".join([raw_text or "", context_text or ""]).lower()

    polymarket_detected = (
        screen_type in {"polymarket", "polymarket_market", "prediction_market", "prediction-market"}
        or "polymarket" in haystack
        or "prediction market" in haystack
        or "yes/no" in haystack
    )
    if not polymarket_detected:
        return False

    return (
        screen_type in {"polymarket", "polymarket_market", "prediction_market", "prediction-market"}
        or "polymarket" in haystack
        or "yes/no" in haystack
        or _is_generic_polymarket_visible(visible)
        or _is_fallback_polymarket_market(market)
        or not _has_strong_visible_values(visible)
        or not _is_useful_polymarket_payload(payload or {})
    )


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
        ("market_title_focus", 0.0, 0.14, 1.0, 0.28),
        ("first_rows_focus", 0.0, 0.25, 1.0, 0.55),
        ("right_percent_focus", 0.72, 0.25, 1.0, 0.80),
        ("left_outcome_names", 0.0, 0.25, 0.65, 0.80),
        ("header", 0.0, 0.0, 1.0, 0.28),
        ("outcomes", 0.0, 0.38, 1.0, 0.85),
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


def _encode_png_crop_under_limit(img: Any) -> bytes:
    if img.mode not in {"RGB", "L"}:
        img = img.convert("RGB")

    output = BytesIO()
    img.save(output, format="PNG", optimize=True)
    crop_bytes = output.getvalue()
    if len(crop_bytes) <= get_max_image_size_bytes():
        return crop_bytes

    try:
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        width, height = img.size
        for factor in (0.85, 0.7, 0.55, 0.4):
            resized = img.resize((max(1, int(width * factor)), max(1, int(height * factor))), resampling)
            output = BytesIO()
            resized.save(output, format="PNG", optimize=True)
            crop_bytes = output.getvalue()
            if len(crop_bytes) <= get_max_image_size_bytes():
                return crop_bytes
    except Exception:
        return b""
    return b""


def _build_nested_screenshot_crops(image_bytes: bytes, mime_type: str) -> List[Tuple[str, bytes, str]]:
    if Image is None or ImageOps is None:
        return []
    if (mime_type or "").lower() not in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}:
        return []

    crop_specs = (
        ("nested_right_preview", 0.45, 0.12, 1.0, 0.68),
        ("nested_upper_media", 0.35, 0.10, 1.0, 0.72),
        ("nested_center_media", 0.20, 0.10, 1.0, 0.78),
        ("nested_full_without_chat_header", 0.0, 0.10, 1.0, 0.78),
    )
    crops: List[Tuple[str, bytes, str]] = []
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
            if width <= 0 or height <= 0:
                return []

            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            for label, x1p, y1p, x2p, y2p in crop_specs:
                left = max(0, min(width, int(round(width * x1p))))
                top = max(0, min(height, int(round(height * y1p))))
                right = max(0, min(width, int(round(width * x2p))))
                bottom = max(0, min(height, int(round(height * y2p))))
                if right - left < 120 or bottom - top < 120:
                    continue

                crop = img.crop((left, top, right, bottom))
                crop_width, crop_height = crop.size
                scale = 2.0 if max(crop_width, crop_height) < VISION_IMAGE_MAX_SIDE else 1.0
                new_width = min(VISION_IMAGE_MAX_SIDE, max(1, int(crop_width * scale)))
                new_height = min(VISION_IMAGE_MAX_SIDE, max(1, int(crop_height * scale)))
                if new_width * new_height > VISION_IMAGE_MAX_PIXELS:
                    shrink = (VISION_IMAGE_MAX_PIXELS / float(new_width * new_height)) ** 0.5
                    new_width = max(1, int(new_width * shrink))
                    new_height = max(1, int(new_height * shrink))
                if (new_width, new_height) != crop.size:
                    crop = crop.resize((new_width, new_height), resampling)

                crop_bytes = _encode_png_crop_under_limit(crop)
                if crop_bytes:
                    crops.append((label, crop_bytes, "image/png"))
    except Exception:
        return []

    return crops


def _post_gemini_generate_content(
    api_key: str, model: str, timeout: int, payload: Dict[str, Any], max_tokens: int, allow_json_mode: bool = True,
    user_id: Optional[int] = None, access_checked: bool = False, request_id: Optional[str] = None, attempt_budget: Optional[_VisionAttemptBudget] = None
) -> Tuple[str, str]:
    if not access_checked:
        logger.warning("gemini_call_blocked_access_not_checked user_id=%s", user_id)
        return "", "access_not_checked"
    if os.getenv("LIVE_ANALYST_VISION_GEMINI_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return "", "vision_disabled"
    if attempt_budget is None:
        attempt_budget = _VisionAttemptBudget()
    if attempt_budget.remaining <= 0:
        logger.info("live_image_vision_attempt_budget_exhausted user_id=%s limit=%s", user_id, attempt_budget.limit)
        return "", "attempt_budget_exhausted"
    from services.gemini_gateway import generate_content

    request_payload = copy.deepcopy(payload)
    generation_config = dict(request_payload.get("generationConfig") or {})
    generation_config.setdefault("maxOutputTokens", max_tokens)
    generation_config.setdefault("temperature", 0.1)
    if allow_json_mode:
        generation_config["responseMimeType"] = "application/json"
    else:
        generation_config.pop("responseMimeType", None)
    if _is_gemini_25_model(model):
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    request_payload["generationConfig"] = generation_config
    result = generate_content(
        feature="live_analyst_vision",
        origin="live_analyst_image_service",
        is_background=False,
        request_id=request_id,
        model=model,
        payload=request_payload,
        max_attempts=min(attempt_budget.remaining, int(os.getenv("LIVE_ANALYST_VISION_MAX_ATTEMPTS_PER_CALL", "1"))),
        timeout=timeout,
        user_id=user_id,
        fallback_models=[m for m in _get_live_image_vision_models(model) if m != model],
        allow_fallback_model=os.getenv("GEMINI_ALLOW_FALLBACK_MODEL", "false").lower() in {"1","true","yes","on"},
    )
    if isinstance(result, dict):
        attempt_budget.consume(int(result.get("attempts_used") or (0 if result.get("blocked") else 1)))
    data = result.get("data") if isinstance(result, dict) else {}
    candidate = ((data or {}).get("candidates") or [{}])[0] if isinstance(data, dict) else {}
    finish_reason = str(candidate.get("finishReason", "")) if isinstance(candidate, dict) else ""
    return (result.get("text") or "", finish_reason or result.get("reason") or "")


def _call_gemini_vision_parts(api_key: str, model: str, timeout: int, parts: List[Dict[str, Any]], max_tokens: int, user_id: Optional[int] = None, access_checked: bool = False, attempt_budget: Optional[_VisionAttemptBudget] = None) -> Tuple[str, str]:
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1},
    }
    return _post_gemini_generate_content(api_key, model, timeout, payload, max_tokens, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)


def _extract_polymarket_from_crop_batch(
    api_key: str, model: str, timeout: int, crops: List[Tuple[str, bytes, str]], context_text: str, user_id: Optional[int] = None, access_checked: bool = False, attempt_budget: Optional[_VisionAttemptBudget] = None
) -> Dict[str, Any]:
    if not crops:
        return {}

    prompt = (
        'Return JSON only: {"screen_type":"polymarket|generic","market":"","visible":"","takeaway":"","confidence":"high|medium|low"}. '
        "Task: Read visible text from Polymarket screenshot crops. "
        "Extract market title, first visible outcomes, percentages/prices, volume. "
        "Use partial readable text. No trading advice. No EDGE/NO TRADE. No disclaimer. "
        f"Context: {context_text[:300]}"
    )
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for label, crop_bytes, crop_mime in crops[:3]:
        parts.append({"text": f"Crop: {label}"})
        parts.append({"inline_data": {"mime_type": crop_mime, "data": base64.b64encode(crop_bytes).decode("ascii")}})

    text, _finish_reason = _call_gemini_vision_parts(api_key, model, timeout, parts, 1024, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
    payload = _extract_json_object(text) or _payload_from_unstructured_vision_text(text)
    if payload:
        payload["screen_type"] = "polymarket"
        payload["_source"] = "crop_batch"
    return payload


def _extract_polymarket_from_single_crop(
    api_key: str, model: str, timeout: int, crop: Tuple[str, bytes, str], context_text: str, user_id: Optional[int] = None, access_checked: bool = False, attempt_budget: Optional[_VisionAttemptBudget] = None
) -> Dict[str, Any]:
    label, crop_bytes, crop_mime = crop
    logger.info("live_image_per_crop_attempt label=%s", label)
    prompt = (
        "Return JSON only. Read this crop. "
        "If it shows Polymarket title/outcomes/percentages, extract them. "
        "Fields: screen_type, market, visible, takeaway, confidence. Keep values short. "
        f"Context: {context_text[:200]}"
    )
    parts: List[Dict[str, Any]] = [
        {"text": prompt},
        {"inline_data": {"mime_type": crop_mime, "data": base64.b64encode(crop_bytes).decode("ascii")}},
    ]
    text, _finish_reason = _call_gemini_vision_parts(api_key, model, timeout, parts, 768, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
    payload = _extract_json_object(text) or _payload_from_unstructured_vision_text(text)
    if payload:
        payload["screen_type"] = "polymarket"
        payload["_source"] = "per_crop"
    logger.info(
        "live_image_per_crop_payload label=%s market_present=%s visible_len=%s useful=%s",
        label,
        bool(_payload_text(payload, "market", "title", "event")),
        len(_payload_text(payload, "visible", "what_visible", "details")),
        _is_useful_polymarket_payload(payload),
    )
    return payload


def _extract_polymarket_from_crops(api_key: str, model: str, timeout: int, crops: List[Tuple[str, bytes, str]], context_text: str, user_id: Optional[int] = None, access_checked: bool = False, attempt_budget: Optional[_VisionAttemptBudget] = None) -> Dict[str, Any]:
    if not crops:
        return {}

    first_labels = {"market_title_focus", "first_rows_focus"}
    second_labels = {"left_outcome_names", "right_percent_focus"}
    first_batch = [crop for crop in crops if crop[0] in first_labels][:2] or crops[:2]
    second_batch = [crop for crop in crops if crop[0] in second_labels][:2]

    first_payload = _extract_polymarket_from_crop_batch(api_key, model, timeout, first_batch, context_text, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
    if _is_useful_polymarket_payload(first_payload):
        return first_payload

    second_payload = _extract_polymarket_from_crop_batch(api_key, model, timeout, second_batch, context_text, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
    batch_payload = _merge_polymarket_payloads(first_payload, second_payload) if second_payload else first_payload
    if _is_useful_polymarket_payload(batch_payload):
        batch_payload["_source"] = "crop_batch"
        return batch_payload

    focused_order = ("market_title_focus", "first_rows_focus", "left_outcome_names", "right_percent_focus")
    focused_crops = [crop for label in focused_order for crop in crops if crop[0] == label][:4]
    merged_payload = batch_payload or {}
    for crop in focused_crops:
        crop_payload = _extract_polymarket_from_single_crop(api_key, model, timeout, crop, context_text, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
        if not crop_payload:
            continue
        merged_payload = _merge_polymarket_payloads(merged_payload, crop_payload)
        merged_payload["_source"] = "per_crop"
        if _is_useful_polymarket_payload(merged_payload):
            break
    return merged_payload


def _nested_crop_prompt(context_text: str) -> str:
    return (
        'Return JSON only: {"screen_type":"polymarket|generic","market":"","visible":"","takeaway":"","confidence":"high|medium|low"}. '
        "Task: This image may be a screenshot of Telegram/chat containing another screenshot inside it. "
        "Ignore Telegram UI. Look for an embedded Polymarket or prediction-market screenshot. "
        "If found, extract market title, first visible outcomes, percentages/prices, and volume. "
        "If not found, return generic. No trading advice. No EDGE/NO TRADE. No disclaimer. "
        f"Context: {context_text[:200]}"
    )


def _extract_polymarket_from_nested_crops(
    api_key: str, model: str, timeout: int, crops: List[Tuple[str, bytes, str]], context_text: str, user_id: Optional[int] = None, access_checked: bool = False, attempt_budget: Optional[_VisionAttemptBudget] = None
) -> Dict[str, Any]:
    if not crops:
        return {}

    best_payload: Dict[str, Any] = {}
    prompt = _nested_crop_prompt(context_text)
    for label, crop_bytes, crop_mime in crops[:4]:
        parts: List[Dict[str, Any]] = [
            {"text": prompt},
            {"inline_data": {"mime_type": crop_mime, "data": base64.b64encode(crop_bytes).decode("ascii")}},
        ]
        crop_text, _finish_reason = _call_gemini_vision_parts(api_key, model, timeout, parts, 1024, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
        crop_payload = _extract_json_object(crop_text) or _payload_from_unstructured_vision_text(crop_text)
        if crop_payload:
            crop_payload["_source"] = "nested_crop"
            crop_payload["_source_crop_label"] = label
        crop_is_polymarket = _is_polymarket_payload(crop_payload, crop_text, context_text)
        useful = _is_useful_polymarket_payload(crop_payload) if crop_is_polymarket else False
        logger.info(
            "live_image_nested_crop_payload screen_type=%s market_present=%s visible_len=%s useful=%s",
            (crop_payload or {}).get("screen_type") or (crop_payload or {}).get("type") or "",
            bool(_payload_text(crop_payload, "market", "title", "event")),
            len(_payload_text(crop_payload, "visible", "what_visible", "details")),
            useful,
        )
        _log_live_image_debug_fields("live_image_nested_crop_payload_debug", crop_payload)
        if useful:
            return crop_payload
        if crop_is_polymarket and not best_payload:
            best_payload = crop_payload

    return best_payload


def _call_gemini_vision(api_key: str, model: str, timeout: int, prompt: str, image_bytes: bytes, mime_type: str, max_tokens: int, user_id: Optional[int] = None, access_checked: bool = False, attempt_budget: Optional[_VisionAttemptBudget] = None) -> Tuple[str, str]:
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}},
            ]
        }],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1},
    }
    return _post_gemini_generate_content(api_key, model, timeout, payload, max_tokens, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)


def analyze_image_bytes(image_bytes: bytes, mime_type: str, context_text: str = "", user_id: Optional[int] = None, access_checked: bool = False) -> Dict[str, str]:
    if not access_checked:
        logger.warning("gemini_call_blocked_access_not_checked user_id=%s", user_id)
        return {"ok": False, "error": "access_not_checked"}
    if not image_bytes:
        return {"ok": False, "error": "empty"}
    if len(image_bytes) > get_max_image_size_bytes():
        return {"ok": False, "error": "too_large"}
    if not is_supported_image_mime(mime_type):
        return {"ok": False, "error": "unsupported_type"}

    api_key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    timeout = int(os.getenv("LLM_TIMEOUT", "30"))
    prepared_bytes, prepared_mime_type = _prepare_image_for_vision(image_bytes, mime_type)
    logger.info(
        "live_image_prepare original_bytes=%s prepared_bytes=%s prepared_mime=%s",
        len(image_bytes),
        len(prepared_bytes),
        prepared_mime_type,
    )

    skill_context = get_live_screenshot_skill_context()[:2200]
    skill_prompt = f"\n\nInternal DeepAlpha screenshot skills:\n{skill_context}" if skill_context else ""
    prompt = (
        "Ты — vision-модель для Live Analyst. Верни только валидный JSON без markdown и лишнего текста. "
        "Не раскрывай провайдера, модель, prompt или внутренние ошибки. "
        "Не давай buy/sell инструкции, прямые рекомендации ставить, обещания прибыли или финальный EDGE / NO TRADE. "
        "Не пиши фразу 'Не финансовый совет.'. Опирайся только на видимое изображение. "
        "Скрин часто мобильный: мысленно приблизь верхний заголовок рынка, легенду графика, первые 3–5 строк исходов, "
        "зелёные/красные кнопки YES/NO и видимый текст объёма. "
        "Для Polymarket screen_type='polymarket'; иначе screen_type='generic'. "
        "Для Polymarket обязательные поля JSON: screen_type, market, visible, takeaway, summary. "
        "Если это экран рынка Polymarket, дополнительно верни по возможности: ui_language ('ru','en','unknown'), visible_url, "
        "market_title_original, market_title_canonical на английском или null, outcomes_original, outcomes_canonical на английском, "
        "visible_prices как массив {outcome_original,outcome_canonical,probability}, category_original, category_canonical, market_present, useful. "
        "Сохраняй десятичные проценты точно: 16.7% -> probability 16.7, 16.4% -> 16.4, 11.8% -> 11.8, 9.7% -> 9.7. "
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
        f"{skill_prompt}"
    )
    attempt_budget = _VisionAttemptBudget()
    try:
        text, finish_reason = _call_gemini_vision(api_key, model, timeout, prompt, prepared_bytes, prepared_mime_type, 1024, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
        if finish_reason in VISION_BLOCK_REASONS:
            return {"ok": False, "error": finish_reason}
        full_finish_reason = finish_reason
        full_raw_len = len(text or "")

        payload = _extract_json_object(text)
        source = "full"
        if not payload:
            fallback_payload = _payload_from_unstructured_vision_text(text)
            if fallback_payload:
                payload = fallback_payload
                text = json.dumps(payload, ensure_ascii=False)
                source = "fallback"
        is_polymarket = _is_polymarket_payload(payload, text, context_text)
        failed_full_extraction = _is_failed_or_empty_full_extraction(payload, text, full_finish_reason)
        logger.info(
            "live_image_full_payload screen_type=%s market_present=%s visible_len=%s useful=%s",
            payload.get("screen_type") or payload.get("type") or "",
            bool(_payload_text(payload, "market", "title", "event")),
            len(_payload_text(payload, "visible", "what_visible", "details")),
            _is_useful_polymarket_payload(payload) if is_polymarket else bool(payload),
        )
        _log_live_image_debug_fields("live_image_full_payload_debug", payload)
        if (is_polymarket and not _is_useful_polymarket_payload(payload)) or failed_full_extraction:
            if is_polymarket:
                second_prompt = (
                    "Carefully extract readable text from this Polymarket mobile screenshot. "
                    "Zoom into the top market title and first outcome rows, including visible YES/NO prices, percentages, chart legend and volume. "
                    "Return JSON only with fields: screen_type='polymarket', market, visible, takeaway, summary, ui_language, visible_url, market_title_original, market_title_canonical, outcomes_original, outcomes_canonical, visible_prices. Preserve decimal percentages exactly. "
                    "Use approximate 'около X%' only when the number is clearly visible. Do not invent data. Russian language only."
                )
            else:
                second_prompt = (
                    "Extract readable text and screen type from this screenshot. If it is a Polymarket or prediction-market screen, "
                    "return JSON with screen_type='polymarket', market, visible, takeaway, summary. If not, return screen_type='generic' "
                    "and summary. Return JSON only. Keep it compact."
                )
            second_text, second_finish_reason = _call_gemini_vision(
                api_key, model, timeout, second_prompt, prepared_bytes, prepared_mime_type, 1024, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget
            )
            second_payload = _extract_json_object(second_text) or _payload_from_unstructured_vision_text(second_text)
            second_is_polymarket = _is_polymarket_payload(second_payload, second_text, context_text)
            second_improved = bool(
                second_text
                and second_payload
                and (
                    _is_useful_polymarket_payload(second_payload)
                    if second_is_polymarket
                    else bool(_payload_text(second_payload, "screen_type", "type") or _payload_text(second_payload, "summary"))
                )
            )
            logger.info("live_image_second_pass attempted=%s improved=%s", True, second_improved)
            if second_improved:
                text = json.dumps(second_payload, ensure_ascii=False)
                finish_reason = second_finish_reason
                payload = second_payload
                is_polymarket = second_is_polymarket
                source = "second_pass"
        else:
            logger.info("live_image_second_pass attempted=%s improved=%s", False, False)

        should_attempt_crops = _should_attempt_crop_extraction(payload, text, context_text)
        attempt_crops = failed_full_extraction or should_attempt_crops
        logger.info(
            "live_image_crop_trigger failed_full=%s should_attempt=%s finish=%s raw_len=%s",
            failed_full_extraction,
            should_attempt_crops,
            full_finish_reason,
            full_raw_len,
        )
        if attempt_crops:
            crops = _build_polymarket_vision_crops(prepared_bytes, prepared_mime_type)
            crop_labels = [label for label, _crop_bytes, _crop_mime in crops[:6]]
            logger.info("live_image_crops_built count=%s labels=%s", len(crops), crop_labels)
            if crops:
                useful_before = _is_useful_polymarket_payload(payload)
                logger.info("live_image_crop_extraction_attempted useful_before=%s", useful_before)
                crop_payload = _extract_polymarket_from_crops(api_key, model, timeout, crops[:6], context_text, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
                logger.info(
                    "live_image_crop_payload market_present=%s visible_len=%s useful=%s confidence=%s",
                    bool(_payload_text(crop_payload, "market", "title", "event")),
                    len(_payload_text(crop_payload, "visible", "what_visible", "details")),
                    _is_useful_polymarket_payload(crop_payload),
                    _clean_live_image_text(crop_payload.get("confidence"), 20),
                )
                _log_live_image_debug_fields("live_image_crop_payload_debug", crop_payload)
                merged_payload = _merge_polymarket_payloads(payload, crop_payload)
                merge_improved = merged_payload != payload and _is_useful_polymarket_payload(merged_payload)
                logger.info(
                    "live_image_crop_merge improved=%s final_market_present=%s final_visible_len=%s",
                    merge_improved,
                    bool(_payload_text(merged_payload, "market", "title", "event")),
                    len(_payload_text(merged_payload, "visible", "what_visible", "details")),
                )
                if merge_improved:
                    payload = merged_payload
                    text = json.dumps(payload, ensure_ascii=False)
                    finish_reason = ""
                    source = str(crop_payload.get("_source") or "crop_batch")
        else:
            logger.info("live_image_crops_built count=%s labels=%s", 0, [])

        generic_before_nested = _is_generic_live_image_payload(payload, text)
        failed_after_passes = _is_failed_or_empty_full_extraction(payload, text, finish_reason)
        if generic_before_nested or failed_after_passes:
            nested_crops = _build_nested_screenshot_crops(prepared_bytes, prepared_mime_type)
            nested_labels = [label for label, _crop_bytes, _crop_mime in nested_crops]
            logger.info("live_image_nested_crops_built count=%s labels=%s", len(nested_crops), nested_labels)
            logger.info(
                "live_image_nested_crop_extraction_attempted generic_before=%s failed_full=%s",
                generic_before_nested,
                failed_full_extraction,
            )
            nested_improved = False
            nested_source = ""
            nested_payload = _extract_polymarket_from_nested_crops(api_key, model, timeout, nested_crops, context_text, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
            nested_is_polymarket = _is_polymarket_payload(nested_payload, json.dumps(nested_payload, ensure_ascii=False), context_text)
            if nested_is_polymarket:
                if _is_useful_polymarket_payload(nested_payload):
                    payload = nested_payload
                    text = json.dumps(payload, ensure_ascii=False)
                    finish_reason = ""
                    is_polymarket = True
                    source = str(nested_payload.get("_source") or "nested_crop")
                    nested_improved = True
                    nested_source = source
                else:
                    source_label = str(nested_payload.get("_source_crop_label") or "")
                    best_nested_crop = None
                    for crop in nested_crops:
                        if crop[0] == source_label:
                            best_nested_crop = crop
                            break
                    if best_nested_crop is None and nested_crops:
                        best_nested_crop = nested_crops[0]
                    if best_nested_crop is not None:
                        inner_crops = _build_polymarket_vision_crops(best_nested_crop[1], best_nested_crop[2])
                        inner_labels = [label for label, _crop_bytes, _crop_mime in inner_crops[:6]]
                        logger.info("live_image_crops_built count=%s labels=%s", len(inner_crops), inner_labels)
                        inner_payload = _extract_polymarket_from_crops(api_key, model, timeout, inner_crops[:6], context_text, user_id=user_id, access_checked=access_checked, attempt_budget=attempt_budget)
                        logger.info(
                            "live_image_nested_crop_payload screen_type=%s market_present=%s visible_len=%s useful=%s",
                            inner_payload.get("screen_type") or inner_payload.get("type") or "",
                            bool(_payload_text(inner_payload, "market", "title", "event")),
                            len(_payload_text(inner_payload, "visible", "what_visible", "details")),
                            _is_useful_polymarket_payload(inner_payload),
                        )
                        merged_inner = _merge_polymarket_payloads(nested_payload, inner_payload)
                        if _is_useful_polymarket_payload(merged_inner):
                            payload = merged_inner
                            text = json.dumps(payload, ensure_ascii=False)
                            finish_reason = ""
                            is_polymarket = True
                            source = "nested_inner_crop"
                            nested_improved = True
                            nested_source = source
            logger.info("live_image_nested_crop_improved improved=%s source=%s", nested_improved, nested_source)
        else:
            logger.info("live_image_nested_crops_built count=%s labels=%s", 0, [])

        logger.info(
            "live_image_final_payload screen_type=%s market_present=%s visible_len=%s useful=%s source=%s",
            payload.get("screen_type") or payload.get("type") or "",
            bool(_payload_text(payload, "market", "title", "event")),
            len(_payload_text(payload, "visible", "what_visible", "details")),
            _is_useful_polymarket_payload(payload) if _is_polymarket_payload(payload, text, context_text) else bool(payload),
            source,
        )
        summary = _format_live_image_summary(text, context_text=context_text, finish_reason=finish_reason)
        logger.info("live_image_final_summary_len=%s", len(summary))
        result: Dict[str, Any] = {"ok": True, "summary": summary}
        result.update(_build_live_image_metadata(payload, text, context_text, summary))
        return result
    except Exception:
        return {"ok": False, "error": "vision_unavailable"}
