
"""
Резолвер для проверки разрешённых рынков Polymarket.
Используется воркером для обновления predictions_tracking.
"""

import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 30

logger = logging.getLogger(__name__)


def fetch_market_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """
    Получает рынок по slug (включая закрытые).
    Возвращает первый подходящий market из event или None.
    """
    if not slug:
        return None

    # Сначала пробуем через events endpoint (работает и для закрытых событий)
    try:
        response = requests.get(
            f"{GAMMA_BASE_URL}/events",
            params={"slug": slug, "limit": 5, "closed": "true"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            events = data if isinstance(data, list) else data.get("data", [])
            for event in events:
                if event.get("slug") == slug or slug in event.get("slug", ""):
                    markets = event.get("markets", [])
                    if markets:
                        # Берём первый рынок события
                        market = markets[0]
                        if not market.get("eventSlug"):
                            market["eventSlug"] = event.get("slug", "")
                        return market
    except Exception as e:
        logger.warning(f"fetch_market_by_slug events error: {e}")

    # Fallback: прямой запрос к markets endpoint
    try:
        response = requests.get(
            f"{GAMMA_BASE_URL}/markets",
            params={"slug": slug, "limit": 5},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            markets = data if isinstance(data, list) else data.get("data", [])
            for m in markets:
                if m.get("slug") == slug:
                    return m
            if markets:
                return markets[0]
    except Exception as e:
        logger.warning(f"fetch_market_by_slug markets error: {e}")

    return None


def is_market_resolved(market: Dict[str, Any]) -> bool:
    """
    Проверяет разрешён ли рынок:
    - closed = True
    - есть финальные цены (outcomePrices)
    - одна из цен близка к 0 или 1 (не 50/50)
    """
    if not market:
        return False

    closed = market.get("closed", False)
    if not closed:
        return False

    # Дополнительно проверяем что есть финальные цены
    prices = _parse_outcome_prices(market.get("outcomePrices"))
    if not prices:
        return False

    # Хотя бы одна цена должна быть резко разделена (>0.95 или <0.05)
    # Иначе рынок может быть закрыт но не разрешён (edge case)
    has_decisive = any(p >= 0.95 or p <= 0.05 for p in prices)
    return has_decisive


def extract_actual_outcome(market: Dict[str, Any]) -> Optional[str]:
    """
    Извлекает фактический исход из разрешённого рынка.
    Возвращает:
    - "Yes" или "No" для бинарных
    - название опции для multi-choice
    - None если не удалось определить
    """
    if not market:
        return None

    outcomes = _parse_outcomes(market.get("outcomes"))
    prices = _parse_outcome_prices(market.get("outcomePrices"))

    if not outcomes or not prices:
        return None
    if len(outcomes) != len(prices):
        return None

    # Ищем победителя — опцию с ценой близкой к 1.0
    best_idx = None
    best_price = 0.0
    for idx, price in enumerate(prices):
        if price > best_price:
            best_price = price
            best_idx = idx

    if best_idx is None or best_price < 0.95:
        return None

    winner = outcomes[best_idx].strip()

    # Нормализуем Yes/No к стандартному регистру
    if winner.lower() == "yes":
        return "Yes"
    if winner.lower() == "no":
        return "No"

    return winner


def compute_metrics(
    system_outcome: str,
    system_probability: float,
    actual_outcome: str,
) -> Tuple[bool, float, float]:
    """
    Считает метрики точности для одного предсказания.

    system_outcome: что мы предсказали ("Yes"/"No"/option)
    system_probability: наша вероятность в % (0-100)
    actual_outcome: что случилось на самом деле

    Returns:
        (is_correct, brier_score, log_loss)

    Brier: (p - outcome)^2, где outcome = 1 если угадали, 0 если нет
    Log loss: -ln(p) если угадали, -ln(1-p) если нет
    """
    is_correct = system_outcome.strip().lower() == actual_outcome.strip().lower()

    # Нормализуем вероятность в [0.001, 0.999] для log loss
    p = max(0.001, min(0.999, system_probability / 100.0))

    # Если выиграли — цель 1.0, если проиграли — цель 0.0
    target = 1.0 if is_correct else 0.0
    brier_score = (p - target) ** 2

    # Log loss
    if is_correct:
        log_loss = -math.log(p)
    else:
        log_loss = -math.log(1.0 - p)

    return is_correct, brier_score, log_loss


def resolve_prediction(
    system_outcome: str,
    system_probability: float,
    market_slug: str,
) -> Optional[Dict[str, Any]]:
    """
    Полный цикл: получить market → проверить resolved → посчитать метрики.

    Returns:
        {
            "actual_outcome": str,
            "is_correct": bool,
            "brier_score": float,
            "log_loss": float,
        }
        или None если рынок ещё не разрешён или не найден
    """
    if not market_slug:
        return None

    market = fetch_market_by_slug(market_slug)
    if not market:
        logger.info(f"Market not found: slug={market_slug}")
        return None

    if not is_market_resolved(market):
        logger.info(f"Market not yet resolved: slug={market_slug}")
        return None

    actual_outcome = extract_actual_outcome(market)
    if not actual_outcome:
        logger.info(f"Could not extract outcome: slug={market_slug}")
        return None

    is_correct, brier, log_loss = compute_metrics(
        system_outcome=system_outcome,
        system_probability=system_probability,
        actual_outcome=actual_outcome,
    )

    return {
        "actual_outcome": actual_outcome,
        "is_correct": is_correct,
        "brier_score": brier,
        "log_loss": log_loss,
    }


# ═══════════════════════════════════════════
# ВНУТРЕННИЕ HELPERS
# ═══════════════════════════════════════════

def _parse_outcomes(outcomes: Any) -> List[str]:
    """Парсит outcomes из разных форматов (list, JSON string, CSV)."""
    if isinstance(outcomes, list):
        return [str(x).strip() for x in outcomes]

    if isinstance(outcomes, str):
        s = outcomes.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s.strip("[]")
            parts = [x.strip().strip('"').strip("'") for x in s.split(",")]
            return [p for p in parts if p]
        if "," in s:
            return [x.strip() for x in s.split(",") if x.strip()]
        return [s] if s else []

    return []


def _parse_outcome_prices(outcome_prices: Any) -> List[float]:
    """Парсит outcomePrices в список float."""
    result = []

    if isinstance(outcome_prices, list):
        for p in outcome_prices:
            try:
                result.append(float(p))
            except Exception:
                continue
        return result

    if isinstance(outcome_prices, str):
        s = outcome_prices.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s.strip("[]")
            parts = [x.strip().strip('"').strip("'") for x in s.split(",")]
            for p in parts:
                try:
                    result.append(float(p))
                except Exception:
                    continue
        return result

    return result
