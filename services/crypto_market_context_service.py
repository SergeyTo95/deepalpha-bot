import math
import os
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}


def _fallback(pair: str, timeframe: str, reason: str) -> Dict[str, Any]:
    print("live_crypto_market_context_fallback reason=%s" % str(reason).replace(" ", "_"))
    return {"ok": False, "partial": False, "pair": pair, "price": None, "price_source": "", "timeframe": timeframe or "", "ohlcv": [], "support_levels": [], "resistance_levels": [], "local_high": None, "local_low": None, "volume_24h": None, "volatility_note": "", "entry_context": {}, "sources": [], "error": reason}


def _fetch_binance_ohlcv(pair: str, timeframe: str, limit: int = 80) -> List[List[float]]:
    if not requests or not hasattr(requests, "get"):
        return []
    url = "https://api.binance.com/api/v3/klines"
    resp = requests.get(url, params={"symbol": pair.upper(), "interval": timeframe, "limit": limit}, timeout=int(os.getenv("CRYPTO_MARKET_TIMEOUT", "6")))
    resp.raise_for_status()
    rows = resp.json() if resp.content else []
    out = []
    for r in rows if isinstance(rows, list) else []:
        out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
    return out


def _round_step(price: float) -> float:
    if price >= 10000: return 500.0
    if price >= 1000: return 50.0
    if price >= 100: return 5.0
    if price >= 1: return 0.05
    return 0.0001


def _levels(ohlcv: List[List[float]]) -> Dict[str, Any]:
    recent = ohlcv[-48:] if len(ohlcv) >= 48 else ohlcv
    price = float(recent[-1][4])
    lows = [float(x[3]) for x in recent]
    highs = [float(x[2]) for x in recent]
    local_low = min(lows); local_high = max(highs)
    step = _round_step(price)
    below = math.floor(price / step) * step
    above = math.ceil(price / step) * step
    supports = sorted({round(x, 8) for x in [local_low, below] if x < price}, reverse=True)[:3]
    resistances = sorted({round(x, 8) for x in [local_high, above] if x > price})[:3]
    rng = local_high - local_low
    pct = (rng / price * 100.0) if price else 0.0
    better = supports[0] if supports else None
    quality = "risky" if price > local_low + rng * 0.65 else ("interesting" if price <= local_low + rng * 0.35 else "neutral")
    return {"price": price, "support_levels": supports, "resistance_levels": resistances, "local_high": local_high, "local_low": local_low, "volatility_note": "Recent range is about %.2f%% on selected timeframe." % pct, "entry_context": {"current_entry_quality": quality, "better_zone": better, "confirmation": "Wait for reaction/reclaim from support or breakout retest on the selected timeframe.", "invalidation": "Scenario weakens below the nearest derived support."}}


def get_crypto_market_context(pair: str, timeframe: str = "", horizon: str = "") -> Dict[str, Any]:
    pair = (pair or "").upper().strip()
    timeframe = (timeframe or "1h").strip() if (timeframe or "").strip() in _INTERVALS else "1h"
    if not pair:
        return _fallback(pair, timeframe, "pair is missing")
    print("live_crypto_market_context_started pair=%s timeframe=%s" % (pair, timeframe))
    try:
        ohlcv = _fetch_binance_ohlcv(pair, timeframe)
    except Exception as exc:
        return _fallback(pair, timeframe, "ohlcv provider failed: %s" % exc)
    if not ohlcv:
        return _fallback(pair, timeframe, "no ohlcv data available")
    calc = _levels(ohlcv)
    print("live_crypto_market_context_success price=%s support=%s resistance=%s" % (calc.get("price"), calc.get("support_levels"), calc.get("resistance_levels")))
    return {"ok": True, "partial": False, "pair": pair, "price": calc["price"], "price_source": "Binance public klines", "timeframe": timeframe, "ohlcv": ohlcv[-60:], "support_levels": calc["support_levels"], "resistance_levels": calc["resistance_levels"], "local_high": calc["local_high"], "local_low": calc["local_low"], "volume_24h": sum(float(x[5]) for x in ohlcv[-24:]) if timeframe == "1h" else None, "volatility_note": calc["volatility_note"], "entry_context": calc["entry_context"], "sources": ["https://api.binance.com/api/v3/klines"], "error": ""}
