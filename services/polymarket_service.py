import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
REQUEST_TIMEOUT = 30


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
                    # Добавляем eventSlug к маркету
                    market = markets[0]
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


def build_market_url(raw_market: Dict[str, Any]) -> str:
    """Строит правильный URL для рынка Polymarket."""
    # Сначала берём готовый URL из API
    url = raw_market.get("url", "")
    if url and url.startswith("https://"):
        return url

    slug = raw_market.get("slug", "")
    event_slug = raw_market.get("eventSlug", "") or raw_market.get("event_slug", "")

    # Пробуем получить eventSlug из вложенного event
    if not event_slug:
        event = raw_market.get("event", {})
        if isinstance(event, dict):
            event_slug = event.get("slug", "")

    if event_slug and slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    elif event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    elif slug:
        return f"https://polymarket.com/event/{slug}"

    return ""


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

    # Строим правильный URL
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
