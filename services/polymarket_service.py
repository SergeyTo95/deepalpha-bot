import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from services.polymarket_localized_normalizer import (
    canonicalize_visible_url,
    market_title_candidates,
    normalize_polymarket_screenshot_payload,
)

import requests


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
REQUEST_TIMEOUT = 30

logger = logging.getLogger(__name__)


def extract_slug_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return ""

        parts = path.split("/")

        if parts and len(parts[0]) == 2:
            parts = parts[1:]

        if len(parts) >= 2 and parts[0] == "event":
            return parts[1]

        if len(parts) >= 2 and parts[0] == "market":
            return parts[1]

        return parts[-1]
    except Exception:
        return ""


def _clean_slug(slug: str) -> str:
    """Убирает числовые суффиксы-ID из slug."""
    if not slug:
        return ""
    cleaned = re.sub(r'(-\d{3,})+$', '', slug)
    return cleaned if cleaned else slug


def build_market_url(raw_market: Dict[str, Any]) -> str:
    """Строит правильный URL для рынка Polymarket."""
    url = (
        raw_market.get("url", "") or
        raw_market.get("marketUrl", "") or
        raw_market.get("market_url", "")
    )
    if url and url.startswith("https://polymarket.com"):
        return url

    event_slug = (
        raw_market.get("eventSlug") or
        raw_market.get("event_slug") or
        ""
    )
    if not event_slug:
        event = raw_market.get("event", {})
        if isinstance(event, dict):
            event_slug = event.get("slug", "")

    if event_slug:
        return f"https://polymarket.com/event/{_clean_slug(event_slug)}"

    slug = raw_market.get("slug", "")
    if slug:
        return f"https://polymarket.com/event/{_clean_slug(slug)}"

    return ""


def list_events(limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    """Получает события с правильными slug для URL."""
    url = f"{GAMMA_BASE_URL}/events"
    params = {
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
    }
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []
    except Exception:
        return []


def normalize_event_for_channel(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Нормализует событие из events API для постинга в канал."""
    if not event:
        return None

    title = event.get("title") or event.get("slug", "")
    if not title:
        return None

    slug = event.get("slug", "")
    if not slug:
        return None

    markets = event.get("markets", [])
    market_prob = "Unknown"

    for m in markets:
        if not m.get("active") or m.get("closed"):
            continue
        outcomes = m.get("outcomes", "")
        outcome_prices = m.get("outcomePrices", "")
        opts = _normalize_options(outcomes)
        prob, _ = _extract_market_probability_and_token(
            options=opts,
            outcome_prices=outcome_prices,
            tokens=[],
        )
        if prob != "Unknown":
            market_prob = prob
            break

    url = f"https://polymarket.com/event/{slug}"

    return {
        "id": event.get("id", ""),
        "slug": slug,
        "url": url,
        "question": title,
        "market_probability": market_prob,
        "volume24hr": float(event.get("volume24hr", 0) or 0),
        "liquidity": str(event.get("liquidity", "Unknown")),
        "volume": str(event.get("volume", "Unknown")),
    }


def search_markets_by_slug(slug: str, limit: int = 10) -> List[Dict[str, Any]]:
    if not slug:
        return []

    results = _public_search(slug)
    if results:
        markets = _extract_markets_from_public_search(results)
        if markets:
            return markets[:limit]

    return list_markets(search=slug, limit=limit)


def list_markets(search: str = "", limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    url = f"{GAMMA_BASE_URL}/markets"
    params = {
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false",
    }

    if search:
        params["search"] = search

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, list):
            return data

        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]

        return []
    except Exception:
        return []


def get_primary_market_from_url(url: str) -> Dict[str, Any]:
    slug = extract_slug_from_url(url)

    try:
        response = requests.get(
            f"{GAMMA_BASE_URL}/events",
            params={"slug": slug, "limit": 5},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            events = data if isinstance(data, list) else data.get("data", [])
            for event in events:
                markets = event.get("markets", [])
                if markets:
                    market = _pick_best_sub_market(markets)
                    if not market.get("eventSlug"):
                        market["eventSlug"] = event.get("slug", "")
                    return market
    except Exception:
        pass

    candidates = search_markets_by_slug(slug, limit=10)
    if not candidates:
        return {}
    best = _pick_best_market(candidates, slug)
    return best or {}


def find_related_markets(question: str, category_hint: str = "", limit: int = 5) -> List[Dict[str, Any]]:
    keywords = _extract_keywords(question)
    if category_hint:
        keywords.insert(0, category_hint.lower())

    seen = set()
    scored: List[Tuple[int, Dict[str, Any]]] = []

    for kw in keywords[:5]:
        items = list_markets(search=kw, limit=10)
        for item in items:
            market_id = str(item.get("id", ""))
            if not market_id or market_id in seen:
                continue

            seen.add(market_id)

            item_question = str(item.get("question") or item.get("title") or "")
            if not item_question:
                continue

            score = _score_relatedness(question, item_question)
            if score <= 0:
                continue

            scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    result: List[Dict[str, Any]] = []
    for score, item in scored:
        result.append(item)
        if len(result) >= limit:
            break

    return result


def normalize_market_data(raw_market: Dict[str, Any]) -> Dict[str, Any]:
    if not raw_market:
        return {}

    question = raw_market.get("question") or raw_market.get("title") or "Unknown market"
    slug = raw_market.get("slug", "")
    end_date = (
        raw_market.get("endDate")
        or raw_market.get("end_date_iso")
        or raw_market.get("end_date")
        or "Unknown"
    )

    liquidity = raw_market.get("liquidity", "Unknown")
    volume = raw_market.get("volume", "Unknown")

    outcomes = raw_market.get("outcomes")
    outcome_prices = raw_market.get("outcomePrices")
    tokens = raw_market.get("tokens", [])

    options = _normalize_options(outcomes)
    market_probability, primary_token_id = _extract_market_probability_and_token(
        options=options,
        outcome_prices=outcome_prices,
        tokens=tokens,
    )

    trend_data = get_market_trend_context(primary_token_id) if primary_token_id else _empty_trend_context()
    market_url = build_market_url(raw_market)

    return {
        "id": raw_market.get("id", ""),
        "slug": slug,
        "url": market_url,
        "question": question,
        "market_probability": market_probability,
        "primary_token_id": primary_token_id,
        "options": options,
        "liquidity": str(liquidity),
        "volume": str(volume),
        "end_date": str(end_date),
        "trend_summary": trend_data["trend_summary"],
        "crowd_behavior": trend_data["crowd_behavior"],
        "price_history": trend_data["price_history"],
        "raw_market_data": raw_market,
    }


def normalize_related_markets(items: List[Dict[str, Any]], main_question: str) -> List[Dict[str, Any]]:
    result = []

    for item in items:
        normalized = normalize_market_data(item)
        if not normalized:
            continue

        title = normalized.get("question", "Unknown related market")
        if title == main_question:
            continue

        result.append({
            "title": title,
            "probability": normalized.get("market_probability", "Unknown"),
            "change_24h": _extract_change_from_trend_summary(normalized.get("trend_summary", "")),
            "change_7d": "Unknown",
            "volume": normalized.get("volume", "Unknown"),
            "liquidity": normalized.get("liquidity", "Unknown"),
            "relation_type": "related_unknown",
            "trend_summary": normalized.get("trend_summary", "Unknown"),
        })

    return result



def _expand_multilingual_title_terms(title: str) -> str:
    text = (title or "").lower()
    replacements = (
        (r"\b(?:доктор|доктор\.|др|др\.)\s+оз\b", " dr oz "),
        (r"\bоз\b", " oz "),
        (r"брифинг(?:а|е|у|ом)?\s+белого\s+дома", " white house press briefing "),
        (r"бел(?:ого|ый|ом|ому)\s+дом(?:а|е|ом|у)?", " white house "),
        (r"что\s+скажет", " what will say "),
        (r"следующ(?:его|ий|ем|ая|ую|ее)", " next "),
        (r"президентск(?:ие|их|ая|ой|ую)\s+выбор(?:ы|ов|ах)?", " presidential election "),
        (r"колумби(?:и|я|ю|ей)", " colombia "),
        (r"биткоин(?:а|у|ом|е)?", " bitcoin "),
        (r"эфириум(?:а|у|ом|е)?", " ethereum "),
        (r"тариф(?:ы|ов|ам|ами|е|а)?", " tariff "),
        (r"здравоохранени(?:е|я|ю|ем|и)", " health healthcare "),
        (r"победитель\s+кубка\s+мира", " 2026 FIFA World Cup Winner "),
        (r"куб(?:ок|ка)\s+мира", " World Cup "),
        (r"победител(?:ь|я|ю|ем|и)", " Winner "),
        (r"испани(?:я|и|ю|ей)", " Spain "),
        (r"франци(?:я|и|ю|ей)", " France "),
        (r"португали(?:я|и|ю|ей)", " Portugal "),
        (r"англи(?:я|и|ю|ей)", " England "),
        (r"бразили(?:я|и|ю|ей)", " Brazil "),
        (r"аргентин(?:а|ы|у|ой)", " Argentina "),
        (r"германи(?:я|и|ю|ей)", " Germany "),
        (r"медикер", " medicare "),
        (r"медикейд", " medicaid "),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return _normalize_market_title_for_resolution(text)


def _normalize_market_title_for_resolution(title: str) -> str:
    text = (title or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b(?:polymarket|market|рынок|скрин|screenshot|question|title)\b", " ", text)
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _market_title_tokens(title: str) -> List[str]:
    normalized = _normalize_market_title_for_resolution(title)
    stopwords = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "will", "what",
        "during", "next", "this", "that", "by", "with", "at", "is", "are", "be", "say",
        "что", "кто", "где", "когда", "исход", "будет", "скажет", "во", "время",
        "во время", "следующего", "следующий", "следующем", "дома", "дом",
    }
    return [token for token in normalized.split() if len(token) >= 2 and token not in stopwords]


def _market_title_similarity_basic(extracted_title: str, candidate_title: str) -> float:
    extracted = _normalize_market_title_for_resolution(extracted_title)
    candidate = _normalize_market_title_for_resolution(candidate_title)
    if not extracted or not candidate:
        return 0.0
    if extracted == candidate:
        return 1.0
    if len(extracted) >= 12 and extracted in candidate:
        return 0.92
    if len(candidate) >= 12 and candidate in extracted:
        return 0.88

    extracted_tokens = set(_market_title_tokens(extracted))
    candidate_tokens = set(_market_title_tokens(candidate))
    if not extracted_tokens or not candidate_tokens:
        return 0.0
    overlap = len(extracted_tokens & candidate_tokens)
    containment = overlap / float(max(1, len(extracted_tokens)))
    jaccard = overlap / float(max(1, len(extracted_tokens | candidate_tokens)))
    return max(jaccard, containment * 0.86)


def _market_title_similarity(extracted_title: str, candidate_title: str) -> float:
    variants = [_normalize_market_title_for_resolution(extracted_title)]
    expanded = _expand_multilingual_title_terms(extracted_title)
    if expanded and expanded not in variants:
        variants.append(expanded)
    scores = [_market_title_similarity_basic(variant, candidate_title) for variant in variants if variant]
    return max(scores) if scores else 0.0


def _is_too_generic_market_title(title: str) -> bool:
    normalized = _normalize_market_title_for_resolution(title)
    expanded = _expand_multilingual_title_terms(title)
    combined = " ".join(part for part in (normalized, expanded) if part)
    if len(combined) < 12:
        return True
    tokens = _market_title_tokens(combined)
    if len(tokens) < 3:
        return True
    generic = {"yes", "no", "price", "odds", "outcome", "outcomes", "volume", "chart"}
    return len([token for token in tokens if token not in generic]) < 3


def _candidate_market_title(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("question") or candidate.get("title") or candidate.get("name") or "").strip()


def _is_market_open(candidate: Dict[str, Any]) -> bool:
    if candidate.get("closed") is True:
        return False
    if candidate.get("active") is False:
        return False
    return True


def _search_events_for_title(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    if not query:
        return []
    params = {
        "limit": limit,
        "active": "true",
        "closed": "false",
        "search": query,
    }
    try:
        response = requests.get(f"{GAMMA_BASE_URL}/events", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        events = data if isinstance(data, list) else data.get("data", [])
        return [event for event in events if isinstance(event, dict)]
    except Exception:
        return []


def _normalize_resolved_market(candidate: Dict[str, Any], confidence: float) -> Optional[Dict[str, Any]]:
    title = _candidate_market_title(candidate)
    url = build_market_url(candidate)
    slug = str(candidate.get("slug") or candidate.get("eventSlug") or candidate.get("event_slug") or "")
    market_id = str(candidate.get("id") or candidate.get("conditionId") or candidate.get("condition_id") or "")
    if not title or not url:
        return None
    return {
        "title": title,
        "url": url,
        "slug": slug,
        "market_id": market_id,
        "confidence": round(float(confidence), 4),
    }


def _compact_title_query(title: str) -> str:
    expanded = _expand_multilingual_title_terms(title)
    normalized = _normalize_market_title_for_resolution(title)
    haystack = " ".join([expanded, normalized])
    parts: List[str] = []
    if "dr oz" in haystack or re.search(r"\boz\b", haystack):
        parts.append("Dr Oz")
    if "white house" in haystack:
        parts.append("White House")
    if "press briefing" in haystack or "briefing" in haystack:
        parts.append("press briefing")
    if "world cup" in haystack:
        parts.append("World Cup")
    if "winner" in haystack:
        parts.append("Winner")
    compact = " ".join(parts).strip()
    return compact if len(parts) >= 2 else ""


def _add_unique_query(queries: List[Tuple[str, str]], variant: str, query: str) -> None:
    cleaned = _normalize_market_title_for_resolution(query)
    if not cleaned:
        return
    for _variant, existing in queries:
        if existing == cleaned:
            return
    queries.append((variant, cleaned))


def _visible_outcome_terms(visible: str, limit: int = 5) -> List[str]:
    terms: List[str] = []
    for chunk in re.split(r"[,;\n•]+", visible or ""):
        cleaned = re.sub(r"(?<!\d)\d{1,3}(?:[.,]\d+)?\s*%", " ", chunk)
        cleaned = re.sub(r"\b(?:yes|no|price|odds|chance)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:/")
        if not cleaned:
            continue
        # Keep only the first alias before slash for compact search, but preserve useful words.
        parts = [part.strip() for part in re.split(r"/", cleaned) if part.strip()]
        for part in parts[:2]:
            normalized = _normalize_market_title_for_resolution(part)
            if len(normalized) < 3:
                continue
            display = " ".join(word.capitalize() if word.islower() else word for word in normalized.split()[:3])
            if display and display not in terms:
                terms.append(display)
            if len(terms) >= limit:
                return terms
    return terms


def build_polymarket_screenshot_search_variants(title: str, visible: str = "") -> List[str]:
    variants: List[str] = []

    def add(query: str) -> None:
        query = re.sub(r"\s+", " ", (query or "").strip())
        normalized = _normalize_market_title_for_resolution(query)
        if not normalized:
            return
        if all(_normalize_market_title_for_resolution(existing) != normalized for existing in variants):
            variants.append(query)

    expanded = _expand_multilingual_title_terms(title)
    compact = _compact_title_query(title)
    outcomes = _visible_outcome_terms(visible, limit=5)

    if compact:
        add(compact)
    if compact and outcomes:
        add(f"{compact} {' '.join(outcomes[:3])}")
    if expanded:
        add(expanded)
    if title:
        add(title)
    title_tokens = _market_title_tokens(expanded or title)
    if title_tokens and outcomes:
        add(" ".join(title_tokens[:5] + [term for outcome in outcomes[:3] for term in outcome.split()[:2]]))
    if outcomes:
        add(" ".join(outcomes[:5]))
    return variants


def _score_market_candidates(title: str, candidates: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for candidate in candidates:
        candidate_title = _candidate_market_title(candidate)
        confidence = _market_title_similarity(title, candidate_title)
        if _is_market_open(candidate):
            confidence = min(1.0, confidence + 0.03)
        scored.append((confidence, candidate))
    scored.sort(key=lambda item: (item[0], 1 if _is_market_open(item[1]) else 0), reverse=True)
    return scored


def _is_ambiguous_title_match(scored: List[Tuple[float, Dict[str, Any]]], threshold: float = 0.70) -> bool:
    if len(scored) < 2:
        return False
    best_confidence, best = scored[0]
    second_confidence, second = scored[1]
    if second_confidence < threshold or (best_confidence - second_confidence) >= 0.08:
        return False
    best_title = _normalize_market_title_for_resolution(_candidate_market_title(best))
    second_title = _normalize_market_title_for_resolution(_candidate_market_title(second))
    return best_title != second_title


async def resolve_polymarket_market_from_title(title: str) -> Optional[Dict[str, Any]]:
    logger.info("live_image_market_resolve_attempt title_len=%s", len(title or ""))
    if _is_too_generic_market_title(title):
        logger.info("live_image_market_resolve_result found=%s confidence=%s", False, 0.0)
        return None

    queries: List[Tuple[str, str]] = []
    cleaned = _normalize_market_title_for_resolution(title)
    _add_unique_query(queries, "original", cleaned)
    tokens = _market_title_tokens(title)
    if tokens:
        _add_unique_query(queries, "original_tokens", " ".join(tokens[:8]))
        if len(tokens) > 4:
            _add_unique_query(queries, "original_tokens_short", " ".join(tokens[:4]))

    expanded = _expand_multilingual_title_terms(title)
    if expanded and expanded != cleaned:
        _add_unique_query(queries, "expanded", expanded)
        expanded_tokens = _market_title_tokens(expanded)
        if expanded_tokens:
            _add_unique_query(queries, "expanded_tokens", " ".join(expanded_tokens[:8]))

    compact = _compact_title_query(title)
    if compact:
        _add_unique_query(queries, "compact_entities", compact)

    candidates: List[Dict[str, Any]] = []
    seen = set()
    best_scored: List[Tuple[float, Dict[str, Any]]] = []
    for variant, query in queries:
        logger.info("polymarket_title_resolve_search_variant variant=%s query_len=%s", variant, len(query or ""))
        for item in _search_events_for_title(query, limit=20) + list_markets(search=query, limit=20):
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("conditionId") or item.get("slug") or _candidate_market_title(item))
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(item)

        scored = _score_market_candidates(title, candidates)
        if scored and (not best_scored or scored[0][0] > best_scored[0][0]):
            best_scored = scored
        if scored and scored[0][0] >= 0.82 and not _is_ambiguous_title_match(scored, threshold=0.70):
            break

    scored = best_scored or _score_market_candidates(title, candidates)
    if not scored:
        logger.info("live_image_market_resolve_result found=%s confidence=%s", False, 0.0)
        return None

    best_confidence, best = scored[0]
    if _is_ambiguous_title_match(scored, threshold=0.70):
        logger.info("live_image_market_resolve_result found=%s confidence=%s", False, round(float(best_confidence), 4))
        return None

    threshold = 0.70
    if best_confidence < threshold:
        logger.info("live_image_market_resolve_result found=%s confidence=%s", False, round(float(best_confidence), 4))
        return None

    resolved = _normalize_resolved_market(best, best_confidence)
    logger.info(
        "live_image_market_resolve_result found=%s confidence=%s",
        bool(resolved),
        round(float(best_confidence), 4),
    )
    return resolved


def _candidate_outcomes(candidate: Dict[str, Any]) -> List[str]:
    outcomes = _normalize_options(candidate.get("outcomes"))
    if not outcomes and isinstance(candidate.get("markets"), list):
        for market in candidate.get("markets") or []:
            outcomes.extend(_normalize_options((market or {}).get("outcomes")))
    return [str(x) for x in outcomes if str(x or "").strip()]


def _screenshot_candidate_score(
    candidate: Dict[str, Any],
    canonical_title: str = "",
    canonical_outcomes: Optional[List[str]] = None,
    category_hint: str = "",
    source: str = "search",
) -> Tuple[float, int]:
    canonical_outcomes = [str(x).strip() for x in (canonical_outcomes or []) if str(x).strip()]
    candidate_title = _candidate_market_title(candidate)
    title_score = _market_title_similarity(canonical_title, candidate_title) if canonical_title else 0.0
    cand_norm_outcomes = {_normalize_market_title_for_resolution(x) for x in _candidate_outcomes(candidate)}
    shot_norm_outcomes = {_normalize_market_title_for_resolution(x) for x in canonical_outcomes}
    outcome_overlap = len(cand_norm_outcomes & shot_norm_outcomes) if cand_norm_outcomes and shot_norm_outcomes else 0
    score = title_score * 0.68
    if title_score >= 0.94:
        score = max(score, 0.82)
    if outcome_overlap:
        score += min(0.24, 0.08 * outcome_overlap)
    title_norm = _normalize_market_title_for_resolution(candidate_title)
    shot_title_norm = _normalize_market_title_for_resolution(canonical_title)
    if "world cup" in shot_title_norm and "winner" in shot_title_norm and "world cup" in title_norm and "winner" in title_norm and outcome_overlap >= 3:
        score = max(score, 0.90)
    if source == "url":
        score = max(score, 0.94)
    if _is_market_open(candidate):
        score += 0.03
    if category_hint and category_hint.lower() in str(candidate).lower():
        score += 0.02
    try:
        volume = float(str(candidate.get("volume") or candidate.get("volume24hr") or 0).replace(",", ""))
    except Exception:
        volume = 0.0
    if volume > 0:
        score += 0.01
    return min(1.0, score), outcome_overlap


def _normalized_outcome_overlap(screenshot_outcomes: List[str], candidate_outcomes: List[str]) -> int:
    shot = {_normalize_market_title_for_resolution(x) for x in screenshot_outcomes if str(x).strip()}
    cand = {_normalize_market_title_for_resolution(x) for x in candidate_outcomes if str(x).strip()}
    shot.discard("")
    cand.discard("")
    return len(shot & cand)


def _is_generic_binary_candidate(candidate: Dict[str, Any]) -> bool:
    outcomes = {_normalize_market_title_for_resolution(x) for x in _candidate_outcomes(candidate)}
    outcomes.discard("")
    return bool(outcomes) and outcomes.issubset({"yes", "no"})


def _screenshot_payload_is_world_cup_football(payload: Dict[str, Any]) -> bool:
    title = _normalize_market_title_for_resolution(
        str(payload.get("market_title_canonical") or payload.get("market_title_original") or payload.get("market") or "")
    )
    category = _normalize_market_title_for_resolution(str(payload.get("category_canonical") or payload.get("category") or ""))
    has_title_signal = any(term in title for term in ("world cup", "fifa", " cup", "winner", "champion"))
    has_sport_signal = "sport" in category and ("football" in category or "soccer" in category)
    return has_title_signal and (has_sport_signal or "world cup" in title)


def _screenshot_title_category_contradiction(payload: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    if not _screenshot_payload_is_world_cup_football(payload):
        return False
    title = _normalize_market_title_for_resolution(_candidate_market_title(candidate))
    relevant_terms = ("world cup", "fifa", "cup", "winner", "football", "soccer", "champion")
    if any(term in title for term in relevant_terms):
        return False
    screenshot_outcomes = [str(x) for x in (payload.get("outcomes_canonical") or []) if str(x).strip()]
    return _normalized_outcome_overlap(screenshot_outcomes, _candidate_outcomes(candidate)) < 3


def _validate_screenshot_candidate_consistency(
    screenshot_payload: Dict[str, Any],
    candidate: Dict[str, Any],
    score: float,
    source: Optional[str] = None,
) -> Tuple[bool, str]:
    screenshot_outcomes = [str(x).strip() for x in (screenshot_payload.get("outcomes_canonical") or []) if str(x).strip()]
    candidate_outcomes = _candidate_outcomes(candidate)
    overlap = _normalized_outcome_overlap(screenshot_outcomes, candidate_outcomes)
    if len(screenshot_outcomes) >= 2 and candidate_outcomes:
        if overlap == 0:
            return False, "outcome_overlap_zero"
        if _is_generic_binary_candidate(candidate):
            return False, "outcome_overlap_binary_outright"
    if _screenshot_title_category_contradiction(screenshot_payload, candidate):
        return False, "title_category_contradiction"
    if candidate_outcomes and len(screenshot_outcomes) >= 2 and overlap == 1 and score >= 0.82:
        return True, "downgrade_outcome_overlap_one"
    if candidate_outcomes and len(screenshot_outcomes) >= 3 and score >= 0.82 and overlap < 2:
        return False, "outcome_overlap_strong_lt_2"
    if candidate_outcomes and _screenshot_payload_is_world_cup_football(screenshot_payload) and score >= 0.82 and overlap < 3:
        return True, "downgrade_world_cup_overlap_lt_3"
    return True, "ok"


async def resolve_polymarket_market_from_screenshot(payload_or_title: Any, visible: str = "") -> Optional[Dict[str, Any]]:
    if isinstance(payload_or_title, dict):
        payload = normalize_polymarket_screenshot_payload(payload_or_title)
    else:
        payload = normalize_polymarket_screenshot_payload({"market": payload_or_title, "visible": visible})
    title = str(payload.get("market_title_canonical") or payload.get("market_title_original") or payload.get("market") or "")
    visible = str(payload.get("visible") or visible or "")
    canonical_outcomes = [str(x) for x in (payload.get("outcomes_canonical") or []) if str(x)]
    visible_url = canonicalize_visible_url(str(payload.get("visible_url_hint") or payload.get("visible_url") or ""))
    category_hint = str(payload.get("category_canonical") or "")
    logger.info(
        "screenshot_market_resolution_started has_visible_url=%s canonical_title=%s canonical_outcomes=%s",
        bool(visible_url), title[:120], canonical_outcomes[:8],
    )
    if _is_too_generic_market_title(title) and len(canonical_outcomes) < 2 and not _visible_outcome_terms(visible, limit=3):
        logger.info("screenshot_market_resolution_failed reason=%s", "generic_payload")
        return None

    candidates: List[Tuple[str, Dict[str, Any]]] = []
    seen = set()

    def add_candidate(source: str, item: Dict[str, Any]) -> None:
        if not isinstance(item, dict):
            return
        key = str(item.get("id") or item.get("conditionId") or item.get("slug") or item.get("eventSlug") or _candidate_market_title(item))
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append((source, item))

    if visible_url:
        slug = extract_slug_from_url(visible_url)
        valid_full_slug = bool(slug and len(slug) >= 6 and "..." not in slug and not slug.endswith("…") and re.search(r"[a-z0-9]-[a-z0-9]", slug, flags=re.IGNORECASE))
        if slug and valid_full_slug:
            direct = get_primary_market_from_url(visible_url if visible_url.startswith("http") else f"https://polymarket.com/event/{slug}")
            if direct:
                add_candidate("url", direct)
            for item in search_markets_by_slug(slug, limit=10):
                add_candidate("url", item)
        elif slug:
            logger.warning("screenshot_url_hint_rejected reason=%s visible_url=%s", "partial_or_invalid_slug", visible_url[:120])

    queries: List[Tuple[str, str]] = []
    for candidate_title in market_title_candidates(title, str(payload.get("ui_language") or "")):
        _add_unique_query(queries, "canonical_title", candidate_title)
    for variant in build_polymarket_screenshot_search_variants(title, visible):
        _add_unique_query(queries, "screenshot", variant)
    if canonical_outcomes:
        _add_unique_query(queries, "outcomes", " ".join(canonical_outcomes[:5]))

    for source, query in queries:
        logger.info("polymarket_screenshot_resolve_search_variant variant=%s query_len=%s", source, len(query or ""))
        for item in _search_events_for_title(query, limit=20) + list_markets(search=query, limit=20):
            add_candidate(source, item)

    scored: List[Tuple[float, int, str, Dict[str, Any]]] = []
    for source, item in candidates:
        score, overlap = _screenshot_candidate_score(item, title, canonical_outcomes, category_hint, source)
        valid, reason = _validate_screenshot_candidate_consistency(payload, item, score, source)
        if not valid:
            if source == "url":
                logger.warning("screenshot_url_hint_rejected reason=%s visible_url=%s", reason, visible_url[:120])
            logger.info(
                "screenshot_market_candidate_rejected title=%s score=%.2f outcome_overlap=%s source=%s reason=%s",
                _candidate_market_title(item)[:120], score, overlap, source, reason,
            )
            continue
        if reason.startswith("downgrade_"):
            score = min(score, 0.8199)
        if len(scored) < 10 or score >= 0.65:
            logger.info(
                "screenshot_market_candidate_score title=%s score=%.2f outcome_overlap=%s source=%s reason=%s",
                _candidate_market_title(item)[:120], score, overlap, source, reason,
            )
        scored.append((score, overlap, source, item))
    scored.sort(key=lambda row: (row[0], row[1], 1 if _is_market_open(row[3]) else 0), reverse=True)
    if not scored:
        logger.info("screenshot_market_resolution_failed reason=%s", "no_candidates")
        return None
    best_score, overlap, source, best = scored[0]
    if best_score < 0.62:
        logger.info("screenshot_market_resolution_failed reason=%s", "low_score")
        return None
    resolved = _normalize_resolved_market(best, best_score)
    if resolved:
        resolved["match_strength"] = "strong" if best_score >= 0.82 else "medium"
        logger.info(
            "screenshot_market_resolved confidence=%s market_id=%s slug=%s",
            resolved.get("confidence"), resolved.get("market_id"), resolved.get("slug"),
        )
    return resolved


def get_market_trend_context(token_id: str) -> Dict[str, Any]:
    if not token_id:
        return _empty_trend_context()

    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    history_24h = get_price_history(
        market=token_id,
        start_ts=int(day_ago.timestamp()),
        end_ts=int(now.timestamp()),
        interval="1h",
        fidelity=60,
    )

    history_7d = get_price_history(
        market=token_id,
        start_ts=int(week_ago.timestamp()),
        end_ts=int(now.timestamp()),
        interval="1d",
        fidelity=60,
    )

    trend_summary = build_trend_summary(history_24h, history_7d)
    crowd_behavior = build_crowd_behavior(history_24h, history_7d)

    return {
        "trend_summary": trend_summary,
        "crowd_behavior": crowd_behavior,
        "price_history": {
            "24h": history_24h,
            "7d": history_7d,
        },
    }


def get_price_history(
    market: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    interval: str = "1h",
    fidelity: int = 60,
) -> List[Dict[str, Any]]:
    if not market:
        return []

    url = f"{CLOB_BASE_URL}/prices-history"
    params: Dict[str, Any] = {
        "market": market,
        "interval": interval,
        "fidelity": fidelity,
    }

    if start_ts is not None:
        params["startTs"] = start_ts
    if end_ts is not None:
        params["endTs"] = end_ts

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        history = data.get("history", [])
        if isinstance(history, list):
            return history

        return []
    except Exception:
        return []


def build_trend_summary(
    history_24h: List[Dict[str, Any]],
    history_7d: List[Dict[str, Any]],
) -> str:
    if not history_24h and not history_7d:
        return "No price history available yet."

    summary_parts = []

    if history_24h:
        start_24, end_24, delta_24 = _compute_change(history_24h)
        summary_parts.append(
            f"24h move: {start_24:.2f} → {end_24:.2f} ({delta_24:+.2f} pts)"
        )

    if history_7d:
        start_7, end_7, delta_7 = _compute_change(history_7d)
        summary_parts.append(
            f"7d move: {start_7:.2f} → {end_7:.2f} ({delta_7:+.2f} pts)"
        )

    acceleration = _estimate_acceleration(history_24h)
    if acceleration:
        summary_parts.append(acceleration)

    return " | ".join(summary_parts)


def build_crowd_behavior(
    history_24h: List[Dict[str, Any]],
    history_7d: List[Dict[str, Any]],
) -> str:
    if not history_24h:
        return "Crowd behavior unavailable due to missing recent history."

    _, _, delta_24 = _compute_change(history_24h)
    volatility_24 = _estimate_volatility(history_24h)

    if delta_24 > 10:
        direction = "Crowd conviction strengthened sharply in the last 24h."
    elif delta_24 > 3:
        direction = "Crowd moved moderately toward one side in the last 24h."
    elif delta_24 < -10:
        direction = "Crowd reversed sharply in the last 24h."
    elif delta_24 < -3:
        direction = "Crowd softened or partially reversed in the last 24h."
    else:
        direction = "Crowd stayed relatively balanced in the last 24h."

    if volatility_24 > 8:
        tone = "Price action looks nervous and reactive."
    elif volatility_24 > 3:
        tone = "Price action shows moderate uncertainty."
    else:
        tone = "Price action looks relatively stable."

    return f"{direction} {tone}"


def _public_search(query: str) -> Any:
    url = f"{GAMMA_BASE_URL}/public-search"
    params = {"query": query}
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _extract_markets_from_public_search(data: Any) -> List[Dict[str, Any]]:
    if not data:
        return []

    if isinstance(data, dict):
        if isinstance(data.get("markets"), list):
            return data["markets"]
        if isinstance(data.get("data"), dict) and isinstance(data["data"].get("markets"), list):
            return data["data"]["markets"]

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    return []

def _pick_best_sub_market(markets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Выбирает лучший sub-рынок из события.
    Приоритет:
    1. Активный и не закрытый
    2. Не практически решённый (< 95%)
    3. Наиболее поздняя дата закрытия (дальше = интереснее)
    4. Наибольшая неопределённость
    """
    if not markets:
        return {}
    if len(markets) == 1:
        return markets[0]

    def parse_end_date(m: Dict[str, Any]) -> float:
        """Возвращает timestamp даты окончания или 0."""
        for key in ("endDate", "end_date", "endDateIso"):
            val = m.get(key, "")
            if val:
                try:
                    from datetime import datetime, timezone
                    val = val.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(val)
                    return dt.timestamp()
                except Exception:
                    pass
        return 0.0

    def get_max_prob(m: Dict[str, Any]) -> float:
        outcome_prices = m.get("outcomePrices", "")
        try:
            if isinstance(outcome_prices, str):
                cleaned = outcome_prices.strip("[]")
                prices = [float(p.strip().strip('"')) for p in cleaned.split(",") if p.strip()]
            elif isinstance(outcome_prices, list):
                prices = [float(p) for p in outcome_prices]
            else:
                return 0.5
            return max(prices) if prices else 0.5
        except Exception:
            return 0.5

    # Фильтруем закрытые и практически решённые (>= 95%)
    active = []
    for m in markets:
        if m.get("closed"):
            continue
        if not m.get("active", True):
            continue
        max_prob = get_max_prob(m)
        if max_prob >= 0.95:
            continue
        m["_max_prob"] = max_prob
        m["_end_ts"] = parse_end_date(m)
        m["_uncertainty"] = 1.0 - abs(max_prob - 0.5) * 2
        active.append(m)

    # Если все >= 95% — берём с наибольшей датой и наименьшей уверенностью
    if not active:
        fallback = [m for m in markets if not m.get("closed")]
        if not fallback:
            return markets[0]
        for m in fallback:
            m["_max_prob"] = get_max_prob(m)
            m["_end_ts"] = parse_end_date(m)
            m["_uncertainty"] = 1.0 - abs(m["_max_prob"] - 0.5) * 2
        # Сначала самые поздние, потом менее решённые
        return max(fallback, key=lambda x: (x["_end_ts"], x["_uncertainty"]))

    # Основной выбор: сначала поздняя дата, потом неопределённость
    def score(m: Dict[str, Any]) -> float:
        end_ts = m.get("_end_ts", 0)
        uncertainty = m.get("_uncertainty", 0.5)
        try:
            vol = float(str(m.get("volume", "0")).replace(",", "") or 0)
        except Exception:
            vol = 0
        # Дата — главный фактор, неопределённость — второй
        return end_ts * 10 + uncertainty * 1000 + vol * 0.001

    return max(active, key=score)

def _pick_best_market(candidates: List[Dict[str, Any]], slug: str) -> Optional[Dict[str, Any]]:
    slug = (slug or "").lower()

    exact = []
    partial = []

    for item in candidates:
        item_slug = str(item.get("slug", "")).lower()
        question = str(item.get("question", "")).lower()

        if item_slug == slug:
            exact.append(item)
        elif slug and (slug in item_slug or slug.replace("-", " ") in question):
            partial.append(item)

    if exact:
        return exact[0]
    if partial:
        return partial[0]
    return candidates[0] if candidates else None


def _normalize_options(outcomes: Any) -> List[str]:
    if isinstance(outcomes, list):
        return [str(x) for x in outcomes]

    if isinstance(outcomes, str):
        cleaned = outcomes.strip()

        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned.strip("[]")
            parts = [x.strip().strip('"').strip("'") for x in cleaned.split(",")]
            return [p for p in parts if p]

        if "," in cleaned:
            return [x.strip() for x in cleaned.split(",") if x.strip()]

        return [cleaned]

    return []


def _extract_market_probability_and_token(
    options: List[str],
    outcome_prices: Any,
    tokens: Any,
) -> Tuple[str, str]:
    token_id = ""
    prices: List[str] = []

    if isinstance(tokens, list) and tokens:
        for token in tokens:
            if isinstance(token, dict) and token.get("token_id"):
                token_id = str(token["token_id"])
                break

    if isinstance(outcome_prices, list):
        prices = [str(x) for x in outcome_prices]
    elif isinstance(outcome_prices, str):
        cleaned = outcome_prices.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned.strip("[]")
            prices = [x.strip().strip('"').strip("'") for x in cleaned.split(",")]

    if options and prices and len(options) == len(prices):
        joined = []
        for idx, (opt, price) in enumerate(zip(options, prices)):
            try:
                pct = round(float(price) * 100, 2)
                joined.append(f"{opt}: {pct}%")
                if not token_id and isinstance(tokens, list) and idx < len(tokens):
                    token = tokens[idx]
                    if isinstance(token, dict) and token.get("token_id"):
                        token_id = str(token["token_id"])
            except Exception:
                joined.append(f"{opt}: {price}")
        return " | ".join(joined), token_id

    if prices:
        try:
            pct = round(float(prices[0]) * 100, 2)
            return f"{pct}%", token_id
        except Exception:
            return str(prices[0]), token_id

    return "Unknown", token_id


def _extract_keywords(text: str) -> List[str]:
    text = (text or "").lower()
    words = re.findall(r"[a-zA-Z0-9]+", text)
    stop = {
        "will", "the", "a", "an", "to", "of", "and", "or", "in", "on",
        "for", "be", "is", "are", "today", "this", "that", "what", "when"
    }
    keywords = [w for w in words if len(w) > 2 and w not in stop]
    return keywords[:8]


def _score_relatedness(main_question: str, candidate_question: str) -> int:
    main_keywords = set(_extract_keywords(main_question))
    candidate_keywords = set(_extract_keywords(candidate_question))

    if not main_keywords or not candidate_keywords:
        return 0

    overlap = main_keywords.intersection(candidate_keywords)
    score = len(overlap) * 10

    mq = main_question.lower()
    cq = candidate_question.lower()

    named_entities = [
        "trump", "biden", "bitcoin", "ethereum", "solana", "fed",
        "europe", "eu", "white house", "senate", "tesla", "openai"
    ]

    for entity in named_entities:
        if entity in mq and entity in cq:
            score += 8

    if cq == mq:
        score -= 100

    return score


def _extract_primary_entities(text: str) -> List[str]:
    return _extract_keywords(text)[:5]


def _compute_change(history: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    prices = [float(item["p"]) for item in history if "p" in item]
    if not prices:
        return 0.0, 0.0, 0.0

    start_price = prices[0] * 100
    end_price = prices[-1] * 100
    delta = end_price - start_price
    return start_price, end_price, delta


def _estimate_volatility(history: List[Dict[str, Any]]) -> float:
    prices = [float(item["p"]) * 100 for item in history if "p" in item]
    if len(prices) < 2:
        return 0.0

    max_price = max(prices)
    min_price = min(prices)
    return max_price - min_price


def _estimate_acceleration(history: List[Dict[str, Any]]) -> str:
    prices = [float(item["p"]) * 100 for item in history if "p" in item]
    if len(prices) < 4:
        return ""

    first_half = prices[: len(prices) // 2]
    second_half = prices[len(prices) // 2:]

    if not first_half or not second_half:
        return ""

    first_move = first_half[-1] - first_half[0]
    second_move = second_half[-1] - second_half[0]

    if abs(second_move) > abs(first_move) * 1.5:
        return "Momentum accelerated in the later part of the observed window."

    if abs(second_move) < abs(first_move) * 0.5:
        return "Momentum slowed down in the later part of the observed window."

    return "Momentum stayed relatively consistent across the observed window."


def _extract_change_from_trend_summary(text: str) -> str:
    if not text:
        return "Unknown"

    match = re.search(r"24h move: .*?\(([+-]?[0-9.]+) pts\)", text)
    if match:
        return f"{match.group(1)} pts"

    return "Unknown"


def _empty_trend_context() -> Dict[str, Any]:
    return {
        "trend_summary": "No price history available yet.",
        "crowd_behavior": "Crowd behavior unavailable due to missing price history.",
        "price_history": {"24h": [], "7d": []},
    }
