import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.polymarket_service import build_market_url, list_events, list_markets


DEFAULT_SCAN_LIMIT = 100
DEFAULT_RESULT_LIMIT = 10
DEFAULT_CACHE_SECONDS = 120
DEFAULT_MIN_LIQUIDITY = 500.0
DEFAULT_MIN_VOLUME_24H = 100.0
DEFAULT_MIN_HOURS_TO_CLOSE = 6.0
DEFAULT_MAX_DAYS_TO_CLOSE = 365.0

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"expires_at": 0.0, "result": None}


@dataclass(frozen=True)
class FreeOpportunityCandidate:
    market_id: str
    event_key: str
    question: str
    url: str
    category: str
    yes_price: float
    no_price: float
    liquidity: float
    volume_24h: float
    volume_total: float
    hours_to_close: Optional[float]
    price_move_24h_pp: float
    event_market_count: int
    score: int
    tier: str
    reasons: List[str]
    risk_flags: List[str]
    score_components: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def scan_free_opportunities(
    *,
    scan_limit: Optional[int] = None,
    result_limit: Optional[int] = None,
    category_filter: str = "All",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Rank public Polymarket markets without any LLM/provider request.

    This is a deterministic pre-screen. It identifies markets that are worth a
    later deep analysis; it never calculates fair probability, edge, or BUY.
    """
    scan_limit = _bounded_int(
        scan_limit,
        env_name="FREE_OPPORTUNITY_SCAN_LIMIT",
        default=DEFAULT_SCAN_LIMIT,
        minimum=10,
        maximum=500,
    )
    result_limit = _bounded_int(
        result_limit,
        env_name="FREE_OPPORTUNITY_RESULT_LIMIT",
        default=DEFAULT_RESULT_LIMIT,
        minimum=1,
        maximum=25,
    )
    category_filter = str(category_filter or "All").strip()
    cache_seconds = _bounded_int(
        None,
        env_name="FREE_OPPORTUNITY_CACHE_SECONDS",
        default=DEFAULT_CACHE_SECONDS,
        minimum=15,
        maximum=1800,
    )
    cache_key = f"{scan_limit}:{result_limit}:{category_filter.lower()}"

    now = time.time()
    if not force_refresh:
        with _CACHE_LOCK:
            cached = _CACHE.get("result")
            if (
                isinstance(cached, dict)
                and cached.get("cache_key") == cache_key
                and float(_CACHE.get("expires_at") or 0.0) > now
            ):
                result = dict(cached)
                result["cached"] = True
                return result

    raw_markets = _load_public_markets(scan_limit)
    scored: List[FreeOpportunityCandidate] = []
    rejection_counts: Dict[str, int] = {}

    for market, event in raw_markets:
        candidate, rejected_reason = score_market_candidate(market, event=event)
        if candidate is None:
            reason = rejected_reason or "not_eligible"
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        if category_filter.lower() != "all" and candidate.category.lower() != category_filter.lower():
            rejection_counts["category_filter"] = rejection_counts.get("category_filter", 0) + 1
            continue
        scored.append(candidate)

    scored.sort(
        key=lambda item: (
            item.score,
            item.volume_24h,
            item.liquidity,
            item.volume_total,
        ),
        reverse=True,
    )
    selected = _select_diverse(scored, result_limit=result_limit)

    result = {
        "mode": "free_opportunity_prescan",
        "provider_calls": 0,
        "paid_ai_used": False,
        "scan_limit": scan_limit,
        "markets_received": len(raw_markets),
        "eligible_markets": len(scored),
        "candidates": [item.to_dict() for item in selected],
        "rejection_counts": rejection_counts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_key": cache_key,
        "cached": False,
        "disclaimer": (
            "Deterministic public-data pre-screen only. No fair probability, "
            "edge, WATCH, or BUY is produced without a separate analysis."
        ),
    }

    with _CACHE_LOCK:
        _CACHE["expires_at"] = now + cache_seconds
        _CACHE["result"] = dict(result)
    return result


def score_market_candidate(
    market: Dict[str, Any],
    *,
    event: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Tuple[Optional[FreeOpportunityCandidate], str]:
    raw = market if isinstance(market, dict) else {}
    event = event if isinstance(event, dict) else {}
    question = str(raw.get("question") or raw.get("title") or "").strip()
    if not question:
        return None, "missing_question"
    if _looks_like_noise(question):
        return None, "noise"
    if raw.get("closed") is True or raw.get("active") is False:
        return None, "inactive"

    binary = _binary_prices(raw)
    if binary is None:
        return None, "not_binary_or_missing_price"
    yes_price, no_price = binary
    if min(yes_price, no_price) < 1.0 or max(yes_price, no_price) > 99.0:
        return None, "near_resolved"

    liquidity = _first_number(
        raw.get("liquidityNum"), raw.get("liquidity"), event.get("liquidity")
    )
    volume_24h = _first_number(
        raw.get("volume24hr"), raw.get("volume24h"), raw.get("volume24Hr"),
        event.get("volume24hr"), event.get("volume24h")
    )
    volume_total = _first_number(raw.get("volumeNum"), raw.get("volume"), event.get("volume"))

    min_liquidity = _env_float("FREE_OPPORTUNITY_MIN_LIQUIDITY", DEFAULT_MIN_LIQUIDITY, 0.0, 1_000_000_000.0)
    min_volume_24h = _env_float("FREE_OPPORTUNITY_MIN_VOLUME_24H", DEFAULT_MIN_VOLUME_24H, 0.0, 1_000_000_000.0)
    if liquidity < min_liquidity and volume_24h < min_volume_24h:
        return None, "illiquid"

    now = now or datetime.now(timezone.utc)
    hours_to_close = _hours_to_close(raw, event, now)
    min_hours = _env_float("FREE_OPPORTUNITY_MIN_HOURS_TO_CLOSE", DEFAULT_MIN_HOURS_TO_CLOSE, 0.0, 24 * 30.0)
    max_days = _env_float("FREE_OPPORTUNITY_MAX_DAYS_TO_CLOSE", DEFAULT_MAX_DAYS_TO_CLOSE, 1.0, 3650.0)
    if hours_to_close is not None and hours_to_close < min_hours:
        return None, "closing_too_soon"
    if hours_to_close is not None and hours_to_close > max_days * 24.0:
        return None, "deadline_too_far"

    event_markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    event_market_count = max(1, len([m for m in event_markets if isinstance(m, dict)]))
    move_24h = _price_move_24h_pp(raw)
    category = _detect_category(question)

    components = {
        "liquidity": _log_score(liquidity, floor=500.0, strong=100_000.0, maximum=22),
        "volume_24h": _log_score(volume_24h, floor=100.0, strong=50_000.0, maximum=18),
        "volume_total": _log_score(volume_total, floor=1_000.0, strong=1_000_000.0, maximum=8),
        "price_discovery": _price_discovery_score(yes_price),
        "deadline": _deadline_score(hours_to_close),
        "movement": _movement_score(move_24h),
        "event_structure": min(10, 2 + int(round(math.log2(max(1, event_market_count)))) * 2),
        "data_accessibility": _data_accessibility_score(question, category),
    }
    score = max(0, min(100, sum(components.values())))

    risk_flags: List[str] = []
    if liquidity < 2_000:
        risk_flags.append("low_liquidity")
    if volume_24h < 500:
        risk_flags.append("low_24h_volume")
    if min(yes_price, no_price) < 8:
        risk_flags.append("one_sided_price")
    if hours_to_close is None:
        risk_flags.append("unknown_deadline")
    if _data_accessibility_score(question, category) < 6:
        risk_flags.append("hard_to_model_without_external_data")

    tier = "DEEP_ANALYSIS_CANDIDATE" if score >= 68 else "WATCH_CANDIDATE" if score >= 52 else "LOW_PRIORITY"
    reasons = _candidate_reasons(
        components=components,
        yes_price=yes_price,
        liquidity=liquidity,
        volume_24h=volume_24h,
        move_24h=move_24h,
        event_market_count=event_market_count,
        hours_to_close=hours_to_close,
    )

    event_key = str(
        raw.get("eventSlug")
        or raw.get("event_slug")
        or event.get("slug")
        or raw.get("slug")
        or raw.get("id")
        or question
    )
    market_id = str(raw.get("id") or raw.get("conditionId") or raw.get("slug") or question)
    url = build_market_url({**event, **raw})

    return FreeOpportunityCandidate(
        market_id=market_id,
        event_key=event_key,
        question=question,
        url=url,
        category=category,
        yes_price=round(yes_price, 2),
        no_price=round(no_price, 2),
        liquidity=round(liquidity, 2),
        volume_24h=round(volume_24h, 2),
        volume_total=round(volume_total, 2),
        hours_to_close=round(hours_to_close, 2) if hours_to_close is not None else None,
        price_move_24h_pp=round(move_24h, 2),
        event_market_count=event_market_count,
        score=score,
        tier=tier,
        reasons=reasons,
        risk_flags=risk_flags,
        score_components=components,
    ), ""


def format_free_opportunity_result(scan: Dict[str, Any], lang: str = "ru") -> Dict[str, Any]:
    candidates = scan.get("candidates") if isinstance(scan, dict) else []
    candidates = candidates if isinstance(candidates, list) else []
    is_ru = str(lang or "ru").lower() != "en"

    if not candidates:
        return {
            "mode": "free_opportunity_prescan",
            "question": "Подходящие рынки не найдены" if is_ru else "No suitable markets found",
            "category": "Pre-scan",
            "market_probability": "—",
            "probability": "Не рассчитывалась" if is_ru else "Not calculated",
            "confidence": "Предварительный фильтр" if is_ru else "Pre-screen",
            "reasoning": (
                "Бесплатный фильтр не нашёл достаточно ликвидных активных кандидатов."
                if is_ru else
                "The free filter found no sufficiently liquid active candidates."
            ),
            "main_scenario": "Повторить сканирование позже." if is_ru else "Run the scan again later.",
            "alt_scenario": "Изменить фильтр категории." if is_ru else "Change the category filter.",
            "conclusion": "AI-запросы не выполнялись." if is_ru else "No AI requests were made.",
            "opportunity_score": 0,
            "url": "",
            "news_items": [],
            "news_sources": [],
            "free_candidates": [],
            "provider_calls": 0,
            "paid_ai_used": False,
        }

    best = candidates[0]
    top_lines = []
    for index, candidate in enumerate(candidates[:5], 1):
        q = str(candidate.get("question") or "")
        score = int(candidate.get("score") or 0)
        yes = float(candidate.get("yes_price") or 0.0)
        no = float(candidate.get("no_price") or 0.0)
        top_lines.append(f"{index}. {q[:95]} — score {score}/100, YES {yes:.1f}% / NO {no:.1f}%")

    reasons = best.get("reasons") if isinstance(best.get("reasons"), list) else []
    reason_text = "; ".join(str(item) for item in reasons[:4])
    if is_ru:
        reasoning = (
            "Бесплатный pre-scan без Kimi/Gemini. Почему рынок поднялся в рейтинге: "
            f"{reason_text or 'ликвидность, активность и структура рынка'}.\n\n"
            "Топ кандидатов:\n" + "\n".join(top_lines)
        )
        conclusion = (
            "Это кандидат для отдельного глубокого анализа, а не BUY-сигнал. "
            "Справедливая вероятность и edge на бесплатном этапе не рассчитываются."
        )
    else:
        reasoning = (
            "Free pre-scan without Kimi/Gemini. Ranking reasons: "
            f"{reason_text or 'liquidity, activity, and market structure'}.\n\n"
            "Top candidates:\n" + "\n".join(top_lines)
        )
        conclusion = (
            "This is a candidate for a separate deep analysis, not a BUY signal. "
            "Fair probability and edge are not calculated at the free stage."
        )

    return {
        "mode": "free_opportunity_prescan",
        "question": str(best.get("question") or "Unknown market"),
        "category": str(best.get("category") or "Unknown"),
        "market_probability": f"YES {float(best.get('yes_price') or 0.0):.1f}% / NO {float(best.get('no_price') or 0.0):.1f}%",
        "probability": "Не рассчитывалась — бесплатный pre-scan" if is_ru else "Not calculated — free pre-scan",
        "confidence": "Предварительный фильтр" if is_ru else "Pre-screen",
        "reasoning": reasoning,
        "main_scenario": (
            "Запустить обычный анализ только для выбранного кандидата."
            if is_ru else
            "Run the normal analysis only for the selected candidate."
        ),
        "alt_scenario": (
            "Добавить рынок в Watchlist и дождаться более интересной цены."
            if is_ru else
            "Add the market to Watchlist and wait for a more interesting price."
        ),
        "conclusion": conclusion,
        "opportunity_score": int(best.get("score") or 0),
        "url": str(best.get("url") or ""),
        "news_items": [],
        "news_sources": [],
        "free_candidates": candidates,
        "provider_calls": 0,
        "paid_ai_used": False,
        "prescan_tier": str(best.get("tier") or ""),
    }


def _load_public_markets(scan_limit: int) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    event_limit = max(10, min(100, int(math.ceil(scan_limit / 3))))
    events = list_events(limit=event_limit, offset=0)
    rows: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    seen = set()

    for event in events or []:
        if not isinstance(event, dict):
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
            if not market_id or market_id in seen:
                continue
            seen.add(market_id)
            enriched = dict(market)
            enriched.setdefault("eventSlug", event.get("slug"))
            rows.append((enriched, event))
            if len(rows) >= scan_limit:
                return rows

    if len(rows) < scan_limit:
        for market in list_markets(limit=min(500, scan_limit), offset=0) or []:
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
            if not market_id or market_id in seen:
                continue
            seen.add(market_id)
            rows.append((market, {}))
            if len(rows) >= scan_limit:
                break
    return rows


def _select_diverse(
    candidates: Sequence[FreeOpportunityCandidate],
    *,
    result_limit: int,
    max_per_event: int = 2,
) -> List[FreeOpportunityCandidate]:
    selected: List[FreeOpportunityCandidate] = []
    per_event: Dict[str, int] = {}
    for candidate in candidates:
        count = per_event.get(candidate.event_key, 0)
        if count >= max_per_event:
            continue
        per_event[candidate.event_key] = count + 1
        selected.append(candidate)
        if len(selected) >= result_limit:
            break
    return selected


def _binary_prices(raw: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    outcomes = [str(value).strip().upper() for value in _as_list(raw.get("outcomes"))]
    prices_raw = _as_list(raw.get("outcomePrices"))
    prices: List[float] = []
    for value in prices_raw:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        prices.append(number * 100.0 if abs(number) <= 1.000001 else number)

    if len(outcomes) != 2 or len(prices) != 2:
        return None
    mapping = {outcomes[index]: prices[index] for index in range(2)}
    if "YES" not in mapping or "NO" not in mapping:
        return None
    yes = max(0.0, min(100.0, float(mapping["YES"])))
    no = max(0.0, min(100.0, float(mapping["NO"])))
    if abs((yes + no) - 100.0) > 5.0:
        total = yes + no
        if total <= 0:
            return None
        yes = yes / total * 100.0
        no = no / total * 100.0
    return yes, no


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [part.strip().strip('"\'') for part in raw.strip("[]").split(",") if part.strip()]
    return []


def _first_number(*values: Any) -> float:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return max(0.0, float(str(value).replace(",", "").replace("$", "").strip()))
        except (TypeError, ValueError):
            continue
    return 0.0


def _price_move_24h_pp(raw: Dict[str, Any]) -> float:
    value = _first_signed_number(
        raw.get("oneDayPriceChange"),
        raw.get("priceChange24h"),
        raw.get("change24h"),
    )
    if abs(value) <= 1.0:
        value *= 100.0
    return max(-100.0, min(100.0, value))


def _first_signed_number(*values: Any) -> float:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(str(value).replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            continue
    return 0.0


def _hours_to_close(raw: Dict[str, Any], event: Dict[str, Any], now: datetime) -> Optional[float]:
    value = (
        raw.get("endDate")
        or raw.get("end_date_iso")
        or raw.get("end_date")
        or event.get("endDate")
        or event.get("end_date_iso")
    )
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds() / 3600.0
    except Exception:
        return None


def _log_score(value: float, *, floor: float, strong: float, maximum: int) -> int:
    if value <= 0:
        return 0
    if value <= floor:
        return max(1, int(round(maximum * 0.15 * value / max(floor, 1.0))))
    if value >= strong:
        return maximum
    ratio = (math.log10(value) - math.log10(floor)) / (math.log10(strong) - math.log10(floor))
    return max(1, min(maximum, int(round(maximum * (0.2 + 0.8 * ratio)))))


def _price_discovery_score(yes_price: float) -> int:
    distance_from_even = abs(float(yes_price) - 50.0)
    if distance_from_even <= 15:
        return 14
    if distance_from_even <= 30:
        return 11
    if distance_from_even <= 40:
        return 7
    return 2


def _deadline_score(hours_to_close: Optional[float]) -> int:
    if hours_to_close is None:
        return 4
    days = hours_to_close / 24.0
    if 1 <= days <= 30:
        return 14
    if 30 < days <= 90:
        return 11
    if 0.25 <= days < 1:
        return 7
    if 90 < days <= 180:
        return 7
    return 3


def _movement_score(move_pp: float) -> int:
    move = abs(float(move_pp))
    if 2 <= move <= 12:
        return 9
    if 0.5 <= move < 2:
        return 5
    if 12 < move <= 25:
        return 6
    if move > 25:
        return 2
    return 1


def _data_accessibility_score(question: str, category: str) -> int:
    q = str(question or "").lower()
    score = 1
    if re.search(r"\b20\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", q):
        score += 2
    if re.search(r"\d", q):
        score += 2
    if any(token in q for token in ("above", "below", "reach", "price", "posts", "votes", "rate", "score", "win")):
        score += 2
    if category in {"Crypto", "Sports", "Economy"}:
        score += 2
    if any(token in q for token in ("official", "reported", "announced", "release", "election")):
        score += 1
    if any(token in q for token in ("something", "other", "unknown", "rumor")):
        score -= 2
    return max(0, min(10, score))


def _candidate_reasons(
    *,
    components: Dict[str, int],
    yes_price: float,
    liquidity: float,
    volume_24h: float,
    move_24h: float,
    event_market_count: int,
    hours_to_close: Optional[float],
) -> List[str]:
    reasons: List[str] = []
    ranked = sorted(components.items(), key=lambda item: item[1], reverse=True)
    labels = {
        "liquidity": "достаточная ликвидность",
        "volume_24h": "активный объём за 24 часа",
        "volume_total": "накопленный интерес рынка",
        "price_discovery": "цена ещё не выглядит полностью решённой",
        "deadline": "подходящее расстояние до дедлайна",
        "movement": "заметное движение цены",
        "event_structure": "несколько контрактов внутри события",
        "data_accessibility": "вопрос допускает проверку объективными данными",
    }
    for key, value in ranked:
        if value <= 3:
            continue
        reasons.append(labels[key])
        if len(reasons) >= 4:
            break
    if event_market_count > 2 and "несколько контрактов внутри события" not in reasons:
        reasons.append("можно сравнить соседние контракты события")
    if abs(move_24h) >= 2 and "заметное движение цены" not in reasons:
        reasons.append(f"движение линии за 24ч около {move_24h:+.1f} п.п.")
    return reasons[:5]


def _detect_category(question: str) -> str:
    q = str(question or "").lower()
    if any(token in q for token in ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "token")):
        return "Crypto"
    if any(token in q for token in ("election", "president", "trump", "congress", "senate", "government", "minister")):
        return "Politics"
    if any(token in q for token in ("match", "game", "win", "champion", "league", "cup", "nba", "nfl", "tennis", "goal")):
        return "Sports"
    if any(token in q for token in ("fed", "interest rate", "inflation", "gdp", "unemployment", "recession")):
        return "Economy"
    if any(token in q for token in ("ai", "openai", "apple", "google", "microsoft", "spacex", "launch")):
        return "Tech"
    return "Other"


def _looks_like_noise(question: str) -> bool:
    q = str(question or "").lower()
    return any(pattern in q for pattern in ("test market", "demo market", "sample market", "mock market"))


def _bounded_int(
    value: Optional[int],
    *,
    env_name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = value if value is not None else os.getenv(env_name, str(default))
    try:
        parsed = int(float(raw))
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, min(maximum, parsed))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(minimum, min(maximum, value))
