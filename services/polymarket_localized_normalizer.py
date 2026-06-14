import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_RU_TITLE_EXACT = {
    "победитель кубка мира": "2026 FIFA World Cup Winner",
}
_RU_PHRASES = (
    ("Кубок мира", "World Cup"),
    ("Победитель", "Winner"),
)
_RU_OUTCOMES = {
    "Испания": "Spain",
    "Франция": "France",
    "Португалия": "Portugal",
    "Англия": "England",
    "Бразилия": "Brazil",
    "Аргентина": "Argentina",
    "Германия": "Germany",
    "Нидерланды": "Netherlands",
    "Италия": "Italy",
    "Уругвай": "Uruguay",
    "Бельгия": "Belgium",
    "США": "United States",
    "Мексика": "Mexico",
    "Хорватия": "Croatia",
    "Марокко": "Morocco",
}
_RU_CATEGORIES = {
    "Спорт": "Sports",
    "Футбол": "Football",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_market_title(original_title: str, ui_language: Optional[str] = None) -> Optional[str]:
    title = _clean(original_title)
    if not title:
        return None
    if (ui_language or "").lower().startswith("en"):
        return title
    lowered = title.lower()
    if lowered in _RU_TITLE_EXACT:
        return _RU_TITLE_EXACT[lowered]
    canonical = title
    changed = False
    for ru, en in _RU_PHRASES:
        if re.search(re.escape(ru), canonical, flags=re.IGNORECASE):
            canonical = re.sub(re.escape(ru), en, canonical, flags=re.IGNORECASE)
            changed = True
    canonical = _clean(canonical)
    return canonical if changed else title


def market_title_candidates(original_title: str, ui_language: Optional[str] = None) -> List[str]:
    candidates: List[str] = []
    canonical = normalize_market_title(original_title, ui_language)

    def add(value: Optional[str]) -> None:
        value = _clean(value)
        if value and value.lower() not in {x.lower() for x in candidates}:
            candidates.append(value)

    add(canonical)
    normalized = _clean(original_title).lower()
    if normalized == "победитель кубка мира" or (canonical and "World Cup" in canonical and "Winner" in canonical):
        add("World Cup Winner")
        add("FIFA World Cup Winner")
    return candidates


def normalize_outcome_name(name: str, ui_language: Optional[str] = None) -> Optional[str]:
    cleaned = _clean(name).strip("•:-–—,;")
    if not cleaned:
        return None
    if cleaned in _RU_OUTCOMES:
        return _RU_OUTCOMES[cleaned]
    return cleaned


def normalize_category(category: str, ui_language: Optional[str] = None) -> Optional[str]:
    cleaned = _clean(category)
    if not cleaned:
        return None
    parts = [p.strip() for p in re.split(r"[·•>|/]+", cleaned) if p.strip()]
    mapped = [_RU_CATEGORIES.get(part, part) for part in parts]
    return " · ".join(mapped) if mapped else cleaned


def extract_visible_prices_from_text(text: str, ui_language: Optional[str] = None) -> List[Dict[str, Any]]:
    prices: List[Dict[str, Any]] = []
    pattern = re.compile(r"([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\s.'+-]{1,60}?)\s*[—–-]?\s*(\d{1,3}(?:[.,]\d+)?)\s*%")
    for match in pattern.finditer(text or ""):
        original = _clean(match.group(1)).strip("•:-–—,;")
        try:
            probability = float(match.group(2).replace(",", "."))
        except ValueError:
            continue
        if not (0 <= probability <= 100):
            continue
        prices.append({
            "outcome_original": original,
            "outcome_canonical": normalize_outcome_name(original, ui_language),
            "probability": probability,
        })
    return prices


def canonicalize_visible_url(url: str) -> str:
    value = _clean(url)
    if not value:
        return ""
    if value.startswith("polymarket.com"):
        value = "https://" + value
    try:
        parsed = urlparse(value)
        if not parsed.netloc or "polymarket.com" not in parsed.netloc.lower():
            return value
        path = re.sub(r"^/(?:ru|en|es|fr|de|pt|uk|zh)(?=/)", "", parsed.path, flags=re.IGNORECASE)
        return f"{parsed.scheme or 'https'}://{parsed.netloc}{path}"
    except Exception:
        return value


def normalize_polymarket_screenshot_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload or {})
    ui_language = str(normalized.get("ui_language") or "").strip().lower() or None
    original_title = _clean(normalized.get("market_title_original") or normalized.get("market") or normalized.get("title") or normalized.get("event"))
    original_outcomes = normalized.get("outcomes_original") if isinstance(normalized.get("outcomes_original"), list) else []
    visible = _clean(normalized.get("visible") or normalized.get("what_visible") or normalized.get("details"))
    if not original_outcomes and visible:
        original_outcomes = [item["outcome_original"] for item in extract_visible_prices_from_text(visible, ui_language)]

    canonical_title = _clean(normalized.get("market_title_canonical")) or normalize_market_title(original_title, ui_language)
    canonical_outcomes = normalized.get("outcomes_canonical") if isinstance(normalized.get("outcomes_canonical"), list) else []
    if not canonical_outcomes:
        canonical_outcomes = [normalize_outcome_name(str(x), ui_language) for x in original_outcomes]
        canonical_outcomes = [x for x in canonical_outcomes if x]

    visible_prices = normalized.get("visible_prices") if isinstance(normalized.get("visible_prices"), list) else []
    if not visible_prices and visible:
        visible_prices = extract_visible_prices_from_text(visible, ui_language)

    if original_title:
        normalized["market_title_original"] = original_title
        normalized.setdefault("market", original_title)
    if canonical_title:
        normalized["market_title_canonical"] = canonical_title
    if original_outcomes:
        normalized["outcomes_original"] = original_outcomes
    if canonical_outcomes:
        normalized["outcomes_canonical"] = canonical_outcomes
    if visible_prices:
        normalized["visible_prices"] = visible_prices
    if normalized.get("category_original") and not normalized.get("category_canonical"):
        normalized["category_canonical"] = normalize_category(str(normalized.get("category_original")), ui_language)
    if normalized.get("visible_url"):
        normalized["visible_url_hint"] = canonicalize_visible_url(str(normalized.get("visible_url")))
    if normalized.get("screen_type") == "polymarket_market":
        normalized["screen_type"] = "polymarket"
    logger.info(
        "localized_market_normalized ui_language=%s original_title=%s canonical_title=%s outcomes_count=%s",
        ui_language or "unknown",
        original_title[:120],
        (canonical_title or "")[:120],
        len(canonical_outcomes),
    )
    return normalized
