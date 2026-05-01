"""
DeepAlpha Turbo Signal — ShortTermPriceAgent v1.6
Ultra-short BTC Up/Down Polymarket markets (5m / 15m / 1h).
"""
import re
import time as _time
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════
# IN-MEMORY CACHE
# ═══════════════════════════════════════════

_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 8


def _cache_get(key: str) -> Any:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    value, expires = entry
    if _time.time() > expires:
        del _CACHE[key]
        return None
    return value


def _cache_set(key: str, value: Any, ttl: int = _CACHE_TTL) -> None:
    _CACHE[key] = (value, _time.time() + ttl)


def _cached_call(key: str, ttl: int, fn):
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        result = fn()
    except Exception:
        result = None
    _cache_set(key, result, ttl)
    return result


# ═══════════════════════════════════════════
# SAFE EXTERNAL IMPORTS
# ═══════════════════════════════════════════

def _safe_get_crypto_sources() -> dict:
    try:
        from crypto_analysis.crypto_sources import (
            coingecko_get_price,
            binance_get_ticker,
            binance_get_klines,
            binance_get_orderbook,
            bybit_get_ticker,
            bybit_get_klines,
        )
        return {
            "coingecko_get_price":   coingecko_get_price,
            "binance_get_ticker":    binance_get_ticker,
            "binance_get_klines":    binance_get_klines,
            "binance_get_orderbook": binance_get_orderbook,
            "bybit_get_ticker":      bybit_get_ticker,
            "bybit_get_klines":      bybit_get_klines,
        }
    except Exception:
        return {}


# ═══════════════════════════════════════════
# TIME HELPERS
# ═══════════════════════════════════════════

def _parse_intraday_time_range_minutes(text: str) -> Optional[int]:
    pattern_12 = r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)'
    m = re.search(pattern_12, text, re.IGNORECASE)
    if m:
        h1, min1, ap1 = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        h2, min2, ap2 = int(m.group(4)), int(m.group(5)), m.group(6).upper()

        def to_min(h, mn, ap):
            if ap == "AM":
                h = 0 if h == 12 else h
            else:
                h = 12 if h == 12 else h + 12
            return h * 60 + mn

        start = to_min(h1, min1, ap1)
        end   = to_min(h2, min2, ap2)
        if end < start:
            end += 24 * 60
        return end - start

    pattern_24 = r'(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})(?!\s*[AP]M)'
    m2 = re.search(pattern_24, text, re.IGNORECASE)
    if m2:
        h1, min1 = int(m2.group(1)), int(m2.group(2))
        h2, min2 = int(m2.group(3)), int(m2.group(4))
        start = h1 * 60 + min1
        end   = h2 * 60 + min2
        if end < start:
            end += 24 * 60
        diff = end - start
        if 0 < diff <= 180:
            return diff
    return None


def _extract_time_horizon(text: str) -> str:
    if re.search(r'\b5\s*(m|min|minutes?|м|мин)\b', text, re.IGNORECASE):
        return "5m"
    if re.search(r'\b15\s*(m|min|minutes?|м|мин)\b', text, re.IGNORECASE):
        return "15m"
    if re.search(r'\b30\s*(m|min|minutes?|м|мин)\b', text, re.IGNORECASE):
        return "30m"
    if re.search(r'\b1\s*(h|hour|ч|час)\b', text, re.IGNORECASE):
        return "1h"
    minutes = _parse_intraday_time_range_minutes(text)
    if minutes is not None:
        if minutes <= 7:
            return "5m"
        elif minutes <= 20:
            return "15m"
        elif minutes <= 40:
            return "30m"
        elif minutes <= 90:
            return "1h"
    return "unknown"


def _parse_title_end_time_utc(title: str) -> Optional[float]:
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9,
        "oct": 10, "nov": 11, "dec": 12,
    }
    m_date = re.search(
        r'(january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})',
        title, re.IGNORECASE
    )
    m_range = re.search(
        r'\d{1,2}:\d{2}\s*(?:AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)',
        title, re.IGNORECASE
    )
    if not m_date or not m_range:
        return None
    try:
        month_num = month_map.get(m_date.group(1).lower())
        if not month_num:
            return None
        day   = int(m_date.group(2))
        end_h = int(m_range.group(1))
        end_m = int(m_range.group(2))
        end_ap = m_range.group(3).upper()
        if end_ap == "AM":
            end_h = 0 if end_h == 12 else end_h
        else:
            end_h = 12 if end_h == 12 else end_h + 12

        import datetime as _dt
        year = _dt.datetime.utcnow().year
        try:
            from zoneinfo import ZoneInfo
            local_dt = _dt.datetime(year, month_num, day, end_h, end_m,
                                    tzinfo=ZoneInfo("America/New_York"))
            return local_dt.timestamp()
        except Exception:
            local_dt = _dt.datetime(year, month_num, day, end_h, end_m)
            return local_dt.timestamp() + 5 * 3600
    except Exception:
        return None


def _get_time_left_seconds(market_data: dict) -> Tuple[Optional[int], str]:
    for field in ("end_time", "close_time", "expiration", "deadline", "end_date"):
        val = market_data.get(field)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                ts = float(val)
                if ts > 1e12:
                    ts /= 1000
                diff = int(ts - _time.time())
                return (max(0, diff), "live" if diff > 0 else "expired")
            elif isinstance(val, str):
                val_clean = val.replace("Z", "+00:00")
                import datetime as _dt
                from datetime import timezone
                dt = _dt.datetime.fromisoformat(val_clean)
                diff = int((dt - _dt.datetime.now(timezone.utc)).total_seconds())
                return (max(0, diff), "live" if diff > 0 else "expired")
        except Exception:
            continue

    question = market_data.get("question") or ""
    end_ts = _parse_title_end_time_utc(question)
    if end_ts is not None:
        diff = int(end_ts - _time.time())
        return (max(0, diff), "live" if diff > 0 else "expired")

    return (None, "unknown")


# ═══════════════════════════════════════════
# DETECTION
# ═══════════════════════════════════════════

def _has_intraday_time_range(text: str) -> bool:
    return bool(re.search(
        r'\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM|ET|UTC)?',
        text, re.IGNORECASE
    ))


def _is_long_term_market(text: str) -> bool:
    price_target = bool(re.search(
        r'\b(hit|reach|surpass|exceed|cross|above|below)\s+\$[\d,]+',
        text, re.IGNORECASE
    ))
    long_date = bool(re.search(
        r'\bby\s+(june|january|february|march|april|may|july|august|'
        r'september|october|november|december)\b'
        r'|\bby\s+end\s+of\s+year\b'
        r'|\bby\s+q[1-4]\b'
        r'|\bin\s+20(2[5-9]|3\d)\b'
        r'|\bbefore\s+december\b'
        r'|\bon\s+december\s+31\b'
        r'|\bby\s+year.?end\b',
        text, re.IGNORECASE
    ))
    return price_target or long_date


def is_short_term_price_market(market_data: dict) -> bool:
    question    = (market_data.get("question") or "").strip()
    description = (market_data.get("description") or "")
    rules       = (market_data.get("rules") or "")
    outcomes    = [str(o).lower().strip() for o in (market_data.get("outcomes") or [])]

    text_all   = f"{question} {description} {rules}"
    text_lower = text_all.lower()

    if _is_long_term_market(text_lower):
        return False

    has_btc = bool(re.search(r'\bbtc\b|\bbitcoin\b|биткоин', text_lower))
    if not has_btc:
        return False

    has_updown_title = bool(re.search(
        r'\bup\s+or\s+down\b|\bup/down\b|вверх\s+или\s+вниз', text_lower
    ))
    has_updown_outcomes = (
        any(re.fullmatch(r'up|вверх|выше', o) for o in outcomes) and
        any(re.fullmatch(r'down|вниз|ниже', o) for o in outcomes)
    )
    rules_patterns = [
        r'price at the end of the time range',
        r'price at the beginning',
        r'greater than or equal to the price at the',
        r'resolve.*to.{0,15}up.{0,30}price',
        r'resolve.*to.{0,15}down.{0,30}price',
        r'chainlink\s+btc', r'chainlink.*btc/usd',
    ]
    has_price_rules = any(re.search(p, text_lower) for p in rules_patterns)
    has_intraday    = _has_intraday_time_range(question)
    has_short_h     = _extract_time_horizon(text_all) != "unknown"

    if has_btc and has_updown_title and has_intraday:
        return True
    if has_btc and has_updown_title and has_short_h:
        return True
    if has_btc and has_updown_outcomes and has_price_rules:
        return True
    if has_btc and has_price_rules:
        return True
    if has_btc and has_updown_outcomes and (has_intraday or has_short_h):
        return True
    return False


# ═══════════════════════════════════════════
# PRICE HELPERS
# ═══════════════════════════════════════════

def _parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = re.sub(r'[$,]', '', str(text))
    m = re.search(r'([\d]+(?:\.\d+)?)', cleaned)
    if m:
        try:
            val = float(m.group(1))
            return val if val > 0 else None
        except ValueError:
            pass
    return None


def _fetch_btc_price_consensus() -> dict:
    src = _safe_get_crypto_sources()
    prices: Dict[str, Optional[float]] = {
        "binance": None, "bybit": None, "coingecko": None
    }

    # Binance
    try:
        if "binance_get_ticker" in src:
            t = _cached_call(
                "bnc_ticker_btc", _CACHE_TTL,
                lambda: src["binance_get_ticker"]("BTCUSDT")
            )
            if t and t.get("lastPrice"):
                prices["binance"] = float(t["lastPrice"])
    except Exception:
        pass

    # Bybit
    try:
        if "bybit_get_ticker" in src:
            t = _cached_call(
                "bbt_ticker_btc", _CACHE_TTL,
                lambda: src["bybit_get_ticker"]("BTCUSDT")
            )
            if t and t.get("lastPrice"):
                prices["bybit"] = float(t["lastPrice"])
    except Exception:
        pass

    # CoinGecko
    try:
        if "coingecko_get_price" in src:
            d = _cached_call(
                "cg_btc_price", _CACHE_TTL * 4,
                lambda: src["coingecko_get_price"]("bitcoin")
            )
            if d and d.get("current_price"):
                prices["coingecko"] = float(d["current_price"])
    except Exception:
        pass

    available = {k: v for k, v in prices.items() if v is not None}
    sources_count = len(available)

    primary_price: Optional[float] = None
    primary_source = "unavailable"
    for src_name in ("binance", "bybit", "coingecko"):
        if prices[src_name] is not None:
            primary_price = prices[src_name]
            primary_source = src_name
            break

    spread_abs = spread_pct = None
    consensus_ok = True

    if sources_count >= 2 and primary_price:
        vals = list(available.values())
        spread_abs = max(vals) - min(vals)
        spread_pct = spread_abs / primary_price * 100
        consensus_ok = spread_pct <= 0.08
    elif sources_count == 1:
        consensus_ok = True  # only one source — can't compare

    return {
        "primary_price":  primary_price,
        "primary_source": primary_source,
        "prices":         prices,
        "spread_abs":     spread_abs,
        "spread_pct":     spread_pct,
        "consensus_ok":   consensus_ok,
        "sources_count":  sources_count,
    }


def _fetch_start_price_from_klines(title: str) -> Tuple[Optional[float], str]:
    """Returns (open_price, confidence). confidence: 'high' | 'medium' | None."""
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9,
        "oct": 10, "nov": 11, "dec": 12,
    }
    m_date  = re.search(
        r'(january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})',
        title, re.IGNORECASE
    )
    m_start = re.search(
        r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]',
        title, re.IGNORECASE
    )
    if not m_date or not m_start:
        return None, "unknown"

    try:
        month_num = month_map.get(m_date.group(1).lower())
        day = int(m_date.group(2))
        sh, sm, sap = int(m_start.group(1)), int(m_start.group(2)), m_start.group(3).upper()
        if sap == "AM":
            sh = 0 if sh == 12 else sh
        else:
            sh = 12 if sh == 12 else sh + 12

        import datetime as _dt
        year = _dt.datetime.utcnow().year
        try:
            from zoneinfo import ZoneInfo
            local_dt = _dt.datetime(year, month_num, day, sh, sm,
                                    tzinfo=ZoneInfo("America/New_York"))
            start_ts_ms = int(local_dt.timestamp() * 1000)
        except Exception:
            local_dt = _dt.datetime(year, month_num, day, sh, sm)
            start_ts_ms = int((local_dt.timestamp() + 5 * 3600) * 1000)

        src = _safe_get_crypto_sources()
        if "binance_get_klines" not in src:
            return None, "unknown"

        klines = _cached_call(
            "bnc_1m_klines", _CACHE_TTL,
            lambda: src["binance_get_klines"]("BTCUSDT", "1m", limit=15)
        )
        if not klines:
            return None, "unknown"

        best, best_diff = None, float("inf")
        for c in klines:
            try:
                ts   = int(c[0])
                diff = abs(ts - start_ts_ms)
                if diff < best_diff:
                    best_diff = diff
                    best = c
            except Exception:
                continue

        if best is None:
            return None, "unknown"

        diff_sec = best_diff / 1000
        if diff_sec <= 90:
            return float(best[1]), "high"
        elif diff_sec <= 120:
            return float(best[1]), "medium"
        else:
            return None, "unknown"

    except Exception:
        return None, "unknown"


def _extract_prices(market_data: dict) -> Tuple[dict, dict, str]:
    """Returns (prices, price_consensus, target_confidence)."""
    result = {
        "up_price": None, "down_price": None,
        "current_price": None, "current_price_source": None,
        "target_price": None, "reference_price": None,
        "target_price_source": None,
    }

    # Market odds
    prices_arr = market_data.get("prices") or []
    if len(prices_arr) >= 2:
        try:
            result["up_price"]   = float(prices_arr[0])
            result["down_price"] = float(prices_arr[1])
        except (ValueError, TypeError):
            pass

    mp = market_data.get("market_probability") or ""
    up_m = re.search(r'(?:up|yes|вверх)[:\s]+([\d.]+)%', mp, re.IGNORECASE)
    dn_m = re.search(r'(?:down|no|вниз)[:\s]+([\d.]+)%', mp, re.IGNORECASE)
    if up_m:
        result["up_price"]   = float(up_m.group(1)) / 100
    if dn_m:
        result["down_price"] = float(dn_m.group(1)) / 100

    # Current price: market_data first
    for field in ("current_price", "btc_price", "live_price", "last_price", "oracle_price"):
        val = market_data.get(field)
        if val is not None:
            parsed = _parse_money(str(val))
            if parsed and 1_000 < parsed < 500_000:
                result["current_price"] = parsed
                result["current_price_source"] = "market_data"
                break

    # Price consensus (fetch regardless — used for risk analysis)
    price_consensus = _fetch_btc_price_consensus()

    # If still no current price, use consensus
    if result["current_price"] is None and price_consensus.get("primary_price"):
        result["current_price"] = price_consensus["primary_price"]
        result["current_price_source"] = price_consensus["primary_source"]

    # Target / start price
    target_confidence = "unknown"
    for field in ("target_price", "start_price", "reference_price", "open_price",
                  "initial_price", "startValue", "start_value", "resolution_price_start"):
        val = market_data.get(field)
        if val is not None:
            parsed = _parse_money(str(val))
            if parsed and 1_000 < parsed < 500_000:
                result["target_price"]    = parsed
                result["reference_price"] = parsed
                result["target_price_source"] = "market_data"
                target_confidence = "high"
                break

    if result["target_price"] is None:
        text = " ".join([
            market_data.get("description") or "",
            market_data.get("rules") or "",
        ])
        money_matches = re.findall(r'\$[\d,]+(?:\.\d+)?', text)
        btc_range = [
            v for v in (_parse_money(m) for m in money_matches)
            if v and 10_000 < v < 500_000
        ]
        if btc_range:
            result["target_price"]    = max(btc_range)
            result["reference_price"] = result["target_price"]
            result["target_price_source"] = "text_extraction"
            target_confidence = "medium"

    if result["target_price"] is None:
        question = market_data.get("question") or ""
        if _has_intraday_time_range(question):
            kline_open, kline_conf = _fetch_start_price_from_klines(question)
            if kline_open and 1_000 < kline_open < 500_000:
                result["target_price"]    = kline_open
                result["reference_price"] = kline_open
                result["target_price_source"] = "binance_1m_open_nearest_start"
                target_confidence = kline_conf

    return result, price_consensus, target_confidence


# ═══════════════════════════════════════════
# MARKET ODDS ANALYSIS
# ═══════════════════════════════════════════

def _analyze_market_odds(up_price: Optional[float], down_price: Optional[float]) -> dict:
    if up_price is None or down_price is None:
        return {
            "is_resolved_like": False, "is_extreme": False,
            "favorite_side": "unknown", "favorite_price": None,
            "underdog_price": None, "odds_sum": None,
            "market_spread_proxy": None, "risk_note": "odds unavailable",
        }

    odds_sum = up_price + down_price
    spread   = abs(odds_sum - 1.0)

    if up_price >= down_price:
        fav_side, fav_price, dog_price = "UP", up_price, down_price
    else:
        fav_side, fav_price, dog_price = "DOWN", down_price, up_price

    resolved_like = fav_price >= 0.98 and dog_price <= 0.02
    extreme       = fav_price >= 0.90

    risk_note = ""
    if resolved_like:
        risk_note = "market fully resolved (100/0)"
    elif extreme:
        risk_note = f"extreme favorite {fav_side} {int(fav_price*100)}% — very low edge"
    elif spread > 0.08:
        risk_note = f"wide market spread proxy {spread:.3f}"
    else:
        risk_note = "odds normal"

    return {
        "is_resolved_like":    resolved_like,
        "is_extreme":          extreme,
        "favorite_side":       fav_side,
        "favorite_price":      fav_price,
        "underdog_price":      dog_price,
        "odds_sum":            odds_sum,
        "market_spread_proxy": spread,
        "risk_note":           risk_note,
    }


# ═══════════════════════════════════════════
# TECHNICAL INDICATORS
# ═══════════════════════════════════════════

def _calc_sma(values: list, period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _calc_ema(values: list, period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _calc_rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0.0 for d in deltas[-period:]]
    ag, al = sum(gains) / period, sum(losses) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


def _calc_macd_bias(closes: list) -> str:
    if len(closes) < 26:
        return "unknown"
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    if ema12 is None or ema26 is None:
        return "unknown"
    return "bullish" if ema12 > ema26 else "bearish"


def _calc_atr(candles: list, period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        try:
            h  = float(candles[i][2])
            l  = float(candles[i][3])
            pc = float(candles[i - 1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        except Exception:
            pass
    if not trs:
        return None
    return sum(trs[-period:]) / min(len(trs), period)


def _calc_candle_momentum(klines: list, last_n: int = 5) -> str:
    if not klines or len(klines) < last_n:
        return "unknown"
    recent = klines[-last_n:]
    try:
        up   = sum(1 for c in recent if float(c[4]) > float(c[1]))
        down = sum(1 for c in recent if float(c[4]) <= float(c[1]))
        if up >= 4:
            return "bullish"
        if down >= 4:
            return "bearish"
        return "mixed"
    except Exception:
        return "unknown"


def _fetch_technical(horizon: str) -> dict:
    result = {
        "rsi_1m": None, "rsi_5m": None,
        "macd_bias": "unknown", "sma_trend_1m": "unknown",
        "last_5_candle_momentum": "unknown",
        "atr_1m": None, "volatility_state": "unknown",
        "vwap_1m": None, "available": False,
    }
    src = _safe_get_crypto_sources()
    if "binance_get_klines" not in src:
        return result

    try:
        klines_1m = _cached_call(
            "bnc_1m_klines", _CACHE_TTL,
            lambda: src["binance_get_klines"]("BTCUSDT", "1m", limit=30)
        )
        if klines_1m and len(klines_1m) >= 10:
            closes_1m = [float(c[4]) for c in klines_1m]
            result["rsi_1m"]   = _calc_rsi(closes_1m)
            result["atr_1m"]   = _calc_atr(klines_1m)
            result["last_5_candle_momentum"] = _calc_candle_momentum(klines_1m, 5)
            result["available"] = True
            sma5  = _calc_sma(closes_1m, 5)
            sma20 = _calc_sma(closes_1m, 20)
            if sma5 and sma20:
                result["sma_trend_1m"] = "up" if sma5 > sma20 else "down"
            atr = result["atr_1m"]
            if atr and closes_1m:
                atr_pct = atr / closes_1m[-1] * 100
                if atr_pct < 0.05:
                    result["volatility_state"] = "low"
                elif atr_pct < 0.15:
                    result["volatility_state"] = "normal"
                else:
                    result["volatility_state"] = "high"
    except Exception:
        pass

    try:
        klines_5m = _cached_call(
            "bnc_5m_klines", _CACHE_TTL * 2,
            lambda: src["binance_get_klines"]("BTCUSDT", "5m", limit=50)
        )
        if klines_5m and len(klines_5m) >= 15:
            closes_5m = [float(c[4]) for c in klines_5m]
            result["rsi_5m"]    = _calc_rsi(closes_5m)
            result["macd_bias"] = _calc_macd_bias(closes_5m)
            result["available"] = True
    except Exception:
        pass

    return result


def _fetch_orderbook() -> dict:
    result = {
        "best_bid": None, "best_ask": None,
        "spread_abs": None, "spread_pct": None,
        "imbalance": None, "bias": "unknown",
        "available": False,
    }
    src = _safe_get_crypto_sources()
    if "binance_get_orderbook" not in src:
        return result
    try:
        ob = _cached_call(
            "bnc_ob_btc", _CACHE_TTL,
            lambda: src["binance_get_orderbook"]("BTCUSDT", limit=20)
        )
        if not ob:
            return result

        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            result.update({
                "best_bid":   best_bid,
                "best_ask":   best_ask,
                "spread_abs": best_ask - best_bid,
                "available":  True,
            })
            mid = (best_bid + best_ask) / 2
            result["spread_pct"] = (result["spread_abs"] / mid * 100) if mid else None
            bid_d = sum(float(b[0]) * float(b[1]) for b in bids[:10])
            ask_d = sum(float(a[0]) * float(a[1]) for a in asks[:10])
            total = bid_d + ask_d
            if total > 0:
                imbal = (bid_d - ask_d) / total
                result["imbalance"] = round(imbal, 4)
                if imbal > 0.15:
                    result["bias"] = "bid_support"
                elif imbal < -0.15:
                    result["bias"] = "ask_pressure"
                else:
                    result["bias"] = "neutral"
    except Exception:
        pass
    return result


# ═══════════════════════════════════════════
# DETECTION HELPERS
# ═══════════════════════════════════════════

def _detect_direction_labels(market_data: dict) -> Tuple[str, str]:
    outcomes  = [str(o) for o in (market_data.get("outcomes") or [])]
    up_label  = "Up"
    dn_label  = "Down"
    for o in outcomes:
        ol = o.lower()
        if ol in ("up", "вверх", "выше"):
            up_label = o
        if ol in ("down", "вниз", "ниже"):
            dn_label = o
    return up_label, dn_label


def _extract_resolution_source(market_data: dict) -> str:
    text = " ".join([
        market_data.get("description") or "",
        market_data.get("rules") or "",
    ])
    if re.search(r'chainlink', text, re.IGNORECASE):
        m = re.search(r'chainlink\s+([\w/]+)', text, re.IGNORECASE)
        return f"Chainlink {m.group(1)}" if m else "Chainlink BTC/USD"
    return "Polymarket resolution"


def _is_market_resolved(up_price: Optional[float], down_price: Optional[float]) -> bool:
    if up_price is None or down_price is None:
        return False
    return (up_price >= 0.98 and down_price <= 0.02) or \
           (down_price >= 0.98 and up_price <= 0.02)


# ═══════════════════════════════════════════
# SCORING v1.6
# ═══════════════════════════════════════════

def _calculate_scores(
    up_price: Optional[float],
    down_price: Optional[float],
    current_price: Optional[float],
    target_price: Optional[float],
    time_left_sec: Optional[int],
    time_status: str,
    price_consensus: dict,
    market_odds: dict,
    technical: dict,
    orderbook: dict,
    target_confidence: str,
    lang: str,
) -> Tuple[int, int, str, str, List[str], List[str], List[str]]:
    """Returns (edge_score, risk_score, confidence, decision, edge_reasons, risk_reasons, hard_blockers)."""

    edge_reasons:  List[str] = []
    risk_reasons:  List[str] = []
    hard_blockers: List[str] = []

    # ── Hard NO-TRADE checks ──────────────────────────────────────────────────
    if market_odds.get("is_resolved_like"):
        hard_blockers.append("market resolved (100/0)")
        return 0, 100, "Low", "NO TRADE", edge_reasons, risk_reasons, hard_blockers

    if time_status == "expired":
        hard_blockers.append("market expired")
        return 0, 100, "Low", "NO TRADE", edge_reasons, risk_reasons, hard_blockers

    if current_price is None:
        hard_blockers.append("no live BTC price")
        return 0, 85, "Low", "NO TRADE", edge_reasons, risk_reasons, hard_blockers

    if target_price is None:
        hard_blockers.append("no start/target price")
        return 0, 85, "Low", "NO TRADE", edge_reasons, risk_reasons, hard_blockers

    if time_left_sec is not None and time_left_sec < 20:
        hard_blockers.append(f"time_left {time_left_sec}s < 20s — latency risk")
        return 0, 95, "Low", "NO TRADE", edge_reasons, risk_reasons, hard_blockers

    fav_price = market_odds.get("favorite_price")
    if fav_price is not None and fav_price >= 0.97:
        hard_blockers.append(f"favorite side already {int(fav_price*100)}% — no edge")
        return 0, 90, "Low", "NO TRADE", edge_reasons, risk_reasons, hard_blockers

    pc = price_consensus or {}
    if not pc.get("consensus_ok", True) and (pc.get("spread_pct") or 0) > 0.15:
        hard_blockers.append(f"price sources disagree spread {pc.get('spread_pct', 0):.3f}%")
        return 0, 85, "Low", "NO TRADE", edge_reasons, risk_reasons, hard_blockers

    # ── Base risk ─────────────────────────────────────────────────────────────
    edge_score = 0
    risk_score = 28

    distance     = current_price - target_price
    distance_pct = abs(distance) / target_price * 100 if target_price else 0
    up_winning   = distance >= 0

    # ATR-aware distance
    atr = technical.get("atr_1m")
    if atr and atr > 0:
        dist_atr = abs(distance) / atr
        if dist_atr > 1.0:
            edge_score += 25
            edge_reasons.append(f"distance {dist_atr:.1f}x ATR — strong buffer")
        elif dist_atr > 0.5:
            edge_score += 15
            edge_reasons.append(f"distance {dist_atr:.1f}x ATR — moderate buffer")
        elif dist_atr > 0.25:
            edge_score += 6
            risk_score += 12
            risk_reasons.append(f"distance only {dist_atr:.2f}x ATR — thin buffer")
        else:
            risk_score += 28
            risk_reasons.append(f"distance < 0.25 ATR — near threshold, high flip risk")
    else:
        if distance_pct > 0.8:
            edge_score += 20
            edge_reasons.append("strong % distance from target")
        elif distance_pct > 0.3:
            edge_score += 10
            edge_reasons.append("moderate % distance")
        elif distance_pct > 0.05:
            edge_score += 4
            risk_score += 14
            risk_reasons.append("thin % distance")
        else:
            risk_score += 28
            risk_reasons.append("near target — high flip risk")

    # Target confidence
    if target_confidence in ("unknown", "low"):
        risk_score += 14
        risk_reasons.append(f"target confidence {target_confidence}")
    elif target_confidence == "medium":
        risk_score += 5

    # Time
    if time_left_sec is None:
        risk_score += 20
        risk_reasons.append("time_left unknown")
    elif time_left_sec > 180:
        risk_score += 20
        risk_reasons.append(f"time_left {time_left_sec}s > 180s — too early for MICRO LONG")
    elif time_left_sec <= 30:
        risk_score += 18
        risk_reasons.append(f"time_left {time_left_sec}s ≤ 30s — latency risk")
    elif time_left_sec <= 80:
        edge_score += 16
        edge_reasons.append(f"time_left {time_left_sec}s — strong time window")
    elif time_left_sec <= 150:
        edge_score += 10
        edge_reasons.append(f"time_left {time_left_sec}s — acceptable time window")
    else:
        edge_score += 4

    # Market mispricing
    if up_price is not None and down_price is not None:
        winning_market_price = up_price if up_winning else down_price
        if winning_market_price < 0.52:
            edge_score += 14
            edge_reasons.append(f"market underprices winning side ({int(winning_market_price*100)}%)")
        elif winning_market_price > 0.85:
            risk_score += 12
            risk_reasons.append(f"winning side already expensive ({int(winning_market_price*100)}%)")
        elif winning_market_price > 0.75:
            risk_score += 6
            risk_reasons.append(f"winning side pricey ({int(winning_market_price*100)}%)")
        spread = abs((up_price + down_price) - 1.0)
        if spread > 0.12:
            risk_score += 14
            risk_reasons.append(f"wide market spread proxy {spread:.3f}")
        elif spread < 0.03:
            edge_score += 4
            edge_reasons.append("tight market spread")

    # Extreme odds
    if fav_price is not None and fav_price >= 0.90:
        risk_score += 16
        risk_reasons.append(f"extreme favorite {int(fav_price*100)}%")

    # Technical
    rsi_1m = technical.get("rsi_1m")
    macd   = technical.get("macd_bias", "unknown")
    mom    = technical.get("last_5_candle_momentum", "unknown")
    sma    = technical.get("sma_trend_1m", "unknown")
    vol    = technical.get("volatility_state", "unknown")

    if not technical.get("available"):
        risk_score += 10
        risk_reasons.append("technical unavailable")
    else:
        if rsi_1m is not None:
            if up_winning and rsi_1m < 40:
                edge_score += 7
                edge_reasons.append(f"RSI 1m {rsi_1m} oversold supports UP")
            elif up_winning and rsi_1m > 72:
                risk_score += 10
                risk_reasons.append(f"RSI 1m {rsi_1m} overbought")
            elif not up_winning and rsi_1m > 60:
                edge_score += 7
                edge_reasons.append(f"RSI 1m {rsi_1m} supports DOWN")
            elif not up_winning and rsi_1m < 28:
                risk_score += 10
                risk_reasons.append(f"RSI 1m {rsi_1m} oversold — risky for DOWN")

        if up_winning:
            if macd == "bullish":
                edge_score += 5
                edge_reasons.append("MACD bullish")
            elif macd == "bearish":
                risk_score += 7
                risk_reasons.append("MACD bearish vs UP")
            if mom == "bullish":
                edge_score += 5
                edge_reasons.append("1m momentum bullish")
            elif mom == "bearish":
                risk_score += 7
                risk_reasons.append("1m momentum bearish vs UP")
            if sma == "up":
                edge_score += 4
                edge_reasons.append("SMA trend up")
            elif sma == "down":
                risk_score += 4
                risk_reasons.append("SMA trend down vs UP")
        else:
            if macd == "bearish":
                edge_score += 5
                edge_reasons.append("MACD bearish supports DOWN")
            elif macd == "bullish":
                risk_score += 7
                risk_reasons.append("MACD bullish vs DOWN")
            if mom == "bearish":
                edge_score += 5
                edge_reasons.append("1m momentum bearish supports DOWN")
            elif mom == "bullish":
                risk_score += 7
                risk_reasons.append("1m momentum bullish vs DOWN")
            if sma == "down":
                edge_score += 4
            elif sma == "up":
                risk_score += 4
                risk_reasons.append("SMA trend up vs DOWN")

        if vol == "high":
            risk_score += 14
            risk_reasons.append("high 1m volatility")
        elif vol == "low":
            risk_score += 5

    # Orderbook
    ob_bias = orderbook.get("bias", "unknown")
    sp_pct  = orderbook.get("spread_pct")
    if not orderbook.get("available"):
        risk_score += 8
        risk_reasons.append("orderbook unavailable")
    else:
        if sp_pct is not None and sp_pct > 0.05:
            risk_score += 8
            risk_reasons.append(f"wide orderbook spread {sp_pct:.4f}%")
        if up_winning and ob_bias == "bid_support":
            edge_score += 6
            edge_reasons.append("orderbook bid support for UP")
        elif up_winning and ob_bias == "ask_pressure":
            risk_score += 8
            risk_reasons.append("orderbook ask pressure vs UP")
        elif not up_winning and ob_bias == "ask_pressure":
            edge_score += 6
            edge_reasons.append("orderbook ask pressure supports DOWN")
        elif not up_winning and ob_bias == "bid_support":
            risk_score += 8
            risk_reasons.append("orderbook bid support vs DOWN")

    # Price consensus
    if not pc.get("consensus_ok", True):
        sp = pc.get("spread_pct", 0) or 0
        risk_score += 10
        risk_reasons.append(f"price sources disagree {sp:.3f}%")
    elif pc.get("sources_count", 0) >= 2:
        edge_score += 3
        edge_reasons.append("price consensus OK across sources")

    # Latency baseline
    risk_score += 12

    edge_score = max(0, min(edge_score, 100))
    risk_score = max(0, min(risk_score, 100))

    # Confidence
    if technical.get("available") and rsi_1m is not None and edge_score >= 50 and risk_score <= 55:
        confidence = "Medium"
    elif edge_score >= 38 and risk_score <= 65:
        confidence = "Medium-Low"
    else:
        confidence = "Low"

    # Decision — conservative
    winning_mp = (up_price if up_winning else down_price) or 1.0
    can_micro = (
        time_left_sec is not None
        and 30 <= time_left_sec <= 150
        and time_status == "live"
        and risk_score <= 60
        and edge_score >= 60
        and (edge_score - risk_score) >= 18
        and winning_mp <= 0.85
        and distance_pct > 0.1
    )
    if can_micro:
        decision = "MICRO LONG UP" if up_winning else "MICRO LONG DOWN"
    elif (edge_score - risk_score) >= 5 and current_price is not None and target_price is not None:
        decision = "WAIT"
    else:
        decision = "NO TRADE"

    return edge_score, risk_score, confidence, decision, edge_reasons, risk_reasons, hard_blockers


# ═══════════════════════════════════════════
# FORMATTER
# ═══════════════════════════════════════════

def _fmt_opt(val, fmt=".2f", prefix="", suffix="") -> str:
    if val is None:
        return "—"
    try:
        return f"{prefix}{val:{fmt}}{suffix}"
    except Exception:
        return str(val)


def _format_turbo_signal(
    market_data: dict,
    prices: dict,
    price_consensus: dict,
    market_odds: dict,
    horizon: str,
    resolution_source: str,
    up_label: str,
    down_label: str,
    time_left_sec: Optional[int],
    time_status: str,
    edge_score: int,
    risk_score: int,
    confidence: str,
    decision: str,
    is_resolved: bool,
    technical: dict,
    orderbook: dict,
    target_confidence: str,
    edge_reasons: List[str],
    risk_reasons: List[str],
    hard_blockers: List[str],
    lang: str,
) -> str:
    sep      = "──────────────────────────────"
    question = market_data.get("question") or market_data.get("title") or "BTC Up/Down"

    up_price_raw = prices.get("up_price")
    dn_price_raw = prices.get("down_price")
    current      = prices.get("current_price")
    target       = prices.get("target_price") or prices.get("reference_price")
    cur_src      = prices.get("current_price_source") or "—"
    tgt_src      = prices.get("target_price_source") or "—"

    up_str = f"{int(round(up_price_raw * 100))}¢" if up_price_raw is not None else "—"
    dn_str = f"{int(round(dn_price_raw * 100))}¢" if dn_price_raw is not None else "—"
    up_pct = f"{int(round(up_price_raw * 100))}%" if up_price_raw is not None else "—"
    dn_pct = f"{int(round(dn_price_raw * 100))}%" if dn_price_raw is not None else "—"

    # Price block
    if current is not None and target is not None:
        diff    = current - target
        sign    = "+" if diff >= 0 else ""
        pos     = ("выше" if lang == "ru" else "above") if diff >= 0 else ("ниже" if lang == "ru" else "below")
        winning = up_label if diff >= 0 else down_label
        price_block = (
            f"Start/Target: ${target:,.2f} ({tgt_src})\n"
            f"Current: ${current:,.2f} ({cur_src})\n"
            f"Distance: {sign}${abs(diff):,.2f} {pos} target\n"
            f"Currently winning: {winning}"
        )
    else:
        price_block = "Текущая цена недоступна" if lang == "ru" else "Live price unavailable"

    # Time block
    if time_left_sec is not None and time_left_sec > 0:
        if time_left_sec < 60:
            t_str = f"{time_left_sec}s"
        elif time_left_sec < 3600:
            t_str = f"{time_left_sec // 60}m {time_left_sec % 60}s"
        else:
            t_str = f"{time_left_sec // 3600}h {(time_left_sec % 3600) // 60}m"
    elif time_status == "expired":
        t_str = "0 (expired)" if lang != "ru" else "0 (истёк)"
    else:
        t_str = "unknown" if lang != "ru" else "неизвестно"

    # Technical block
    rsi_1m = technical.get("rsi_1m")
    rsi_5m = technical.get("rsi_5m")
    macd   = technical.get("macd_bias", "unknown")
    mom    = technical.get("last_5_candle_momentum", "unknown")
    vol    = technical.get("volatility_state", "unknown")
    atr    = technical.get("atr_1m")

    if technical.get("available"):
        tech_block = (
            f"RSI 1m: {_fmt_opt(rsi_1m, '.1f')} | RSI 5m: {_fmt_opt(rsi_5m, '.1f')}\n"
            f"MACD 5m: {macd} | Momentum: {mom}\n"
            f"Volatility: {vol} | ATR 1m: {_fmt_opt(atr, ',.2f', prefix='$')}"
        )
    else:
        tech_block = "недоступно" if lang == "ru" else "unavailable"

    # Orderbook block
    ob_bid  = orderbook.get("best_bid")
    ob_sp   = orderbook.get("spread_pct")
    ob_imb  = orderbook.get("imbalance")
    ob_bias = orderbook.get("bias", "unknown")
    if orderbook.get("available"):
        ob_block = (
            f"Spread: {_fmt_opt(ob_sp, '.4f', suffix='%')} | "
            f"Imbalance: {_fmt_opt(ob_imb, '.3f')} | Bias: {ob_bias}"
        )
    else:
        ob_block = "недоступно" if lang == "ru" else "unavailable"

    # Data quality block
    pc = price_consensus or {}
    consensus_label = (
        "OK" if pc.get("consensus_ok", True) else "WARNING"
    ) if pc.get("sources_count", 0) >= 2 else ("OK" if pc.get("primary_source") != "unavailable" else "—")
    src_names = ", ".join(
        k for k, v in (pc.get("prices") or {}).items() if v is not None
    ) or "—"
    tgt_conf_label = target_confidence

    if lang == "ru":
        dq_block = (
            f"Согласованность цены: {consensus_label} ({src_names})\n"
            f"Надёжность target: {tgt_conf_label}"
        )
    else:
        dq_block = (
            f"Price consensus: {consensus_label} ({src_names})\n"
            f"Target confidence: {tgt_conf_label}"
        )

    # Risk filters block
    all_blockers = hard_blockers[:4] + risk_reasons[:4]
    rf_lines = [f"— {r}" for r in all_blockers[:4]]
    rf_block = "\n".join(rf_lines) if rf_lines else ("— нет критических фильтров" if lang == "ru" else "— no critical filters")

    # Edge
    e_lines = [f"+ {e}" for e in edge_reasons[:4]]
    e_block = "\n".join(e_lines) if e_lines else ""

    # Decision icon
    d_icon = {
        "MICRO LONG UP":   "🟢",
        "MICRO LONG DOWN": "🔴",
        "WAIT":            "🟡",
        "NO TRADE":        "⚫",
    }.get(decision, "⚫")

    resolved_note = ""
    if is_resolved:
        resolved_note = (
            "\n⚠️ Рынок уже разрешён или полностью оценён (100/0).\n"
            if lang == "ru" else
            "\n⚠️ Market appears resolved or fully priced (100/0).\n"
        )
    if time_status == "expired" and not is_resolved:
        resolved_note += (
            "\n⚠️ Время экспирации истекло.\n"
            if lang == "ru" else
            "\n⚠️ Expiration time has passed.\n"
        )

    if lang == "ru":
        logic_map = {
            "MICRO LONG UP":
                "Цена выше target с достаточным буфером, времени мало. "
                "Технические сигналы и orderbook поддерживают UP.",
            "MICRO LONG DOWN":
                "Цена ниже target с достаточным буфером, времени мало. "
                "Сигналы поддерживают DOWN.",
            "WAIT":
                "Есть потенциальная позиция, но риск/время/буфер "
                "недостаточно хороши для уверенного входа.",
            "NO TRADE":
                "Нет достаточного edge или сработали hard-фильтры. "
                "Оставаться вне позиции.",
        }
        conclusion_map = {
            "MICRO LONG UP":   "Осторожный вход в UP при подтверждении. Строгий риск-менеджмент.",
            "MICRO LONG DOWN": "Осторожный вход в DOWN при подтверждении. Строгий риск-менеджмент.",
            "WAIT":            "Ждать улучшения условий. Буфер или время недостаточны.",
            "NO TRADE":        "Оставаться вне позиции. Нет подтверждённого edge.",
        }
        body = (
            f"⚡ DeepAlpha Turbo Signal\n{sep}\n\n"
            f"📌 Рынок: {question}\n"
            f"⏱ Горизонт: {horizon}\n"
            f"🔗 Resolution: {resolution_source}\n\n"
            f"📊 Polymarket:\n"
            f"{up_label}: {up_str} | {down_label}: {dn_str}\n"
            f"Implied: {up_label} {up_pct} / {down_label} {dn_pct}\n"
            f"{resolved_note}"
            f"\n🎯 Условие:\n"
            f"{up_label} побеждает, если BTC в конце >= стартовой цены.\n"
            f"{down_label} побеждает, если BTC ниже стартовой цены.\n\n"
            f"📍 Цена:\n{price_block}\n\n"
            f"⏰ Время:\nДо экспирации: {t_str} | Статус: {time_status}\n\n"
            f"📈 Микротренд:\n{tech_block}\n\n"
            f"📚 Orderbook:\n{ob_block}\n\n"
            f"🧪 Качество данных:\n{dq_block}\n\n"
        )
        if e_block:
            body += f"✅ Edge-сигналы:\n{e_block}\n\n"
        body += (
            f"🚦 Риск-фильтры:\n{rf_block}\n\n"
            f"🧠 Оценка:\n"
            f"Edge Score: {edge_score}/100\n"
            f"Risk Score: {risk_score}/100\n"
            f"Confidence: {confidence}\n\n"
            f"{d_icon} Decision: {decision}\n\n"
            f"{sep}\n\n"
            f"💬 Логика:\n{logic_map.get(decision, '')}\n\n"
            f"⚠️ Риски:\n"
            f"— last-second wick может изменить исход\n"
            f"— Chainlink может отличаться от биржевой цены\n"
            f"— высокий latency risk при экспирации\n"
            f"— 5m рынки зашумлены\n\n"
            f"📝 Вывод:\n{conclusion_map.get(decision, '')}"
        )
        return body
    else:
        logic_map = {
            "MICRO LONG UP":
                "Price above target with sufficient buffer, time is short. "
                "Technical and orderbook support UP.",
            "MICRO LONG DOWN":
                "Price below target with sufficient buffer, time is short. "
                "Signals support DOWN.",
            "WAIT":
                "Potential position exists but risk/time/buffer "
                "not ideal for confident entry.",
            "NO TRADE":
                "Insufficient edge or hard filters triggered. Stay out.",
        }
        conclusion_map = {
            "MICRO LONG UP":   "Cautious UP entry on confirmation. Strict risk management.",
            "MICRO LONG DOWN": "Cautious DOWN entry on confirmation. Strict risk management.",
            "WAIT":            "Wait for better conditions. Buffer or time not sufficient.",
            "NO TRADE":        "Stay out. No confirmed edge.",
        }
        body = (
            f"⚡ DeepAlpha Turbo Signal\n{sep}\n\n"
            f"📌 Market: {question}\n"
            f"⏱ Horizon: {horizon}\n"
            f"🔗 Resolution: {resolution_source}\n\n"
            f"📊 Polymarket:\n"
            f"{up_label}: {up_str} | {down_label}: {dn_str}\n"
            f"Implied: {up_label} {up_pct} / {down_label} {dn_pct}\n"
            f"{resolved_note}"
            f"\n🎯 Resolution Logic:\n"
            f"{up_label} wins if BTC at end >= start price.\n"
            f"{down_label} wins if BTC < start price.\n\n"
            f"📍 Price:\n{price_block}\n\n"
            f"⏰ Time:\nUntil expiry: {t_str} | Status: {time_status}\n\n"
            f"📈 Micro Trend:\n{tech_block}\n\n"
            f"📚 Orderbook:\n{ob_block}\n\n"
            f"🧪 Data Quality:\n{dq_block}\n\n"
        )
        if e_block:
            body += f"✅ Edge signals:\n{e_block}\n\n"
        body += (
            f"🚦 Risk Filters:\n{rf_block}\n\n"
            f"🧠 Assessment:\n"
            f"Edge Score: {edge_score}/100\n"
            f"Risk Score: {risk_score}/100\n"
            f"Confidence: {confidence}\n\n"
            f"{d_icon} Decision: {decision}\n\n"
            f"{sep}\n\n"
            f"💬 Reasoning:\n{logic_map.get(decision, '')}\n\n"
            f"⚠️ Risks:\n"
            f"— last-second wick can flip outcome\n"
            f"— Chainlink may differ from exchange price\n"
            f"— high latency risk near expiration\n"
            f"— 5m markets are noisy\n\n"
            f"📝 Conclusion:\n{conclusion_map.get(decision, '')}"
        )
        return body


# ═══════════════════════════════════════════
# MAIN AGENT
# ═══════════════════════════════════════════

class ShortTermPriceAgent:

    def is_short_term_price_market(self, market_data: dict) -> bool:
        return is_short_term_price_market(market_data)

    def run(self, market_data: dict, lang: str = "ru") -> dict:
        question = market_data.get("question") or market_data.get("title") or ""
        category = market_data.get("category") or "Crypto"
        mp       = market_data.get("market_probability") or ""

        text_all = f"{question} {market_data.get('description','')} {market_data.get('rules','')}"

        horizon           = _extract_time_horizon(text_all)
        time_left_sec, time_status = _get_time_left_seconds(market_data)
        is_resolved_early = _is_market_resolved(
            *[None] * 2  # will re-check after price extraction
        )

        prices, price_consensus, target_confidence = _extract_prices(market_data)

        up_label, dn_label   = _detect_direction_labels(market_data)
        resolution_source    = _extract_resolution_source(market_data)
        is_resolved          = _is_market_resolved(prices.get("up_price"), prices.get("down_price"))
        market_odds          = _analyze_market_odds(prices.get("up_price"), prices.get("down_price"))

        technical = {}
        orderbook = {}
        if not is_resolved and time_status != "expired":
            try:
                technical = _fetch_technical(horizon)
            except Exception:
                pass
            try:
                orderbook = _fetch_orderbook()
            except Exception:
                pass

        (edge_score, risk_score, confidence, decision,
         edge_reasons, risk_reasons, hard_blockers) = _calculate_scores(
            up_price          = prices.get("up_price"),
            down_price        = prices.get("down_price"),
            current_price     = prices.get("current_price"),
            target_price      = prices.get("target_price"),
            time_left_sec     = time_left_sec,
            time_status       = time_status,
            price_consensus   = price_consensus,
            market_odds       = market_odds,
            technical         = technical,
            orderbook         = orderbook,
            target_confidence = target_confidence,
            lang              = lang,
        )

        full_analysis = _format_turbo_signal(
            market_data       = market_data,
            prices            = prices,
            price_consensus   = price_consensus,
            market_odds       = market_odds,
            horizon           = horizon,
            resolution_source = resolution_source,
            up_label          = up_label,
            down_label        = dn_label,
            time_left_sec     = time_left_sec,
            time_status       = time_status,
            edge_score        = edge_score,
            risk_score        = risk_score,
            confidence        = confidence,
            decision          = decision,
            is_resolved       = is_resolved,
            technical         = technical,
            orderbook         = orderbook,
            target_confidence = target_confidence,
            edge_reasons      = edge_reasons,
            risk_reasons      = risk_reasons,
            hard_blockers     = hard_blockers,
            lang              = lang,
        )

        up_p = prices.get("up_price")
        probability = (
            f"{up_label} — {int(round(up_p * 100))}%"
            if up_p is not None else f"{up_label} / {dn_label}"
        )

        return {
            "analysis_mode":      "turbo_short_term",
            "category":           category,
            "question":           question,
            "market_probability": mp,
            "probability":        probability,
            "confidence":         confidence,
            "decision":           decision,
            "edge_score":         edge_score,
            "risk_score":         risk_score,
            "full_analysis":      full_analysis,
            "display_prediction": decision,
            "reasoning":          full_analysis,
            "main_scenario":      "",
            "alt_scenario":       "",
            "conclusion":         decision,
            "sources":            [],
            "news_sources":       [],
            "key_signals":        [],
            "market_structure": {
                "market_format": "binary",
                "domain":        "Crypto",
                "subtype":       "btc_short_term_updown",
                "resolution_logic": {
                    "yes_means":      f"{up_label} wins if BTC >= start price at expiration.",
                    "no_means":       f"{dn_label} wins if BTC < start price at expiration.",
                    "draw_handling":  "Not applicable.",
                    "ambiguity_risk": "medium",
                },
            },
            "user_context_used": False,
            "source_summary":    {},
            "evidence_matrix":   "",
            "debug": {
                "detected_horizon":  horizon,
                "time_left_sec":     time_left_sec,
                "time_status":       time_status,
                "has_live_data":     bool(prices.get("current_price") and prices.get("target_price")),
                "is_resolved":       is_resolved,
                "prices":            prices,
                "price_consensus":   price_consensus,
                "market_odds":       market_odds,
                "technical":         technical,
                "orderbook":         orderbook,
                "edge_reasons":      edge_reasons,
                "risk_reasons":      risk_reasons,
                "hard_blockers":     hard_blockers,
                "target_confidence": target_confidence,
                "version":           "turbo_v1.6",
            },
        }
