import re
from typing import Any, Dict, List, Optional

import requests

from db.database import get_connection
from services.polymarket_resolver import fetch_market_by_slug as fetch_direct_market
from services.polymarket_service import (
    GAMMA_BASE_URL,
    REQUEST_TIMEOUT,
    extract_slug_from_url,
    list_markets,
)


def resolve_watch_market(
    market_slug: str,
    market_url: str,
    question: str,
) -> Optional[Dict[str, Any]]:
    """Resolve one exact Watchlist row to the correct Polymarket contract."""
    direct = fetch_direct_market(market_slug)
    if direct and market_matches_question(question, direct, minimum_score=0.90):
        return direct

    event_slug = extract_slug_from_url(str(market_url or "")) or str(market_slug or "")
    selected = select_best_market(question, _event_markets(event_slug))
    if selected:
        return selected

    candidates = list_markets(search=question, limit=20) if question else []
    selected = select_best_market(question, candidates)
    if selected:
        return selected

    # Never fall back to a mismatched first market from a multi-contract event.
    return None


def fetch_watch_market_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Backward-compatible helper for callers that only have a slug.

    New Edge Watch code passes the exact row directly to ``resolve_watch_market``.
    """
    row = _watchlist_market_context(slug)
    if not row:
        return fetch_direct_market(slug)
    return resolve_watch_market(
        market_slug=slug,
        market_url=str(row.get("market_url") or ""),
        question=str(row.get("question") or ""),
    )


def _watchlist_market_context(slug: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT market_url, question
            FROM watchlist
            WHERE market_slug = %s AND is_closed = 0
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(slug or ""),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"market_url": row[0], "question": row[1]}
    except Exception:
        return None
    finally:
        cur.close()
        conn.close()


def _event_markets(event_slug: str) -> List[Dict[str, Any]]:
    if not event_slug:
        return []
    try:
        response = requests.get(
            f"{GAMMA_BASE_URL}/events",
            params={"slug": event_slug, "limit": 5},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        events = payload if isinstance(payload, list) else payload.get("data", [])
        markets: List[Dict[str, Any]] = []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                market = dict(market)
                if not market.get("eventSlug"):
                    market["eventSlug"] = event.get("slug", event_slug)
                markets.append(market)
        return markets
    except Exception:
        return []


def market_matches_question(
    question: str,
    market: Dict[str, Any],
    minimum_score: float = 0.90,
) -> bool:
    candidate = _normalize((market or {}).get("question") or (market or {}).get("title") or "")
    return _similarity(_normalize(question), candidate) >= float(minimum_score)


def select_best_market(question: str, markets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not markets:
        return None
    target = _normalize(question)
    best = None
    best_score = -1.0
    for market in markets:
        if not isinstance(market, dict):
            continue
        candidate = _normalize(market.get("question") or market.get("title") or "")
        score = _similarity(target, candidate)
        if score > best_score:
            best_score = score
            best = market
    return best if best_score >= 0.55 else None


def _normalize(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9а-яё]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.92
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    jaccard = overlap / union if union else 0.0
    containment = overlap / min(len(left_tokens), len(right_tokens))
    return max(jaccard, containment * 0.9)
