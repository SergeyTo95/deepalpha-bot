from typing import Any, Dict, Optional

from crypto_analysis.crypto_sources import (
    coingecko_get_price,
    coingecko_get_ohlcv,
    binance_get_ticker,
    binance_get_klines,
    bybit_get_ticker,
    bybit_get_klines,
)
from crypto_analysis.crypto_utils import (
    get_coingecko_id,
    coingecko_search,
    format_price,
    format_change,
    format_volume,
)


class CryptoMarketAgent:
    def __init__(self) -> None:
        pass

    def run(self, symbol_info: Dict[str, str], timeframe: str = "4h") -> Dict[str, Any]:
        base = symbol_info["base"]
        quote = symbol_info["quote"]
        symbol = symbol_info["symbol"]
        display = symbol_info["display"]

        price_data = self._fetch_price(base, symbol)
        ohlcv = self._fetch_ohlcv(base, symbol, timeframe)

        if not price_data:
            return {
                "found": False,
                "symbol": display,
                "error": f"Asset {display} not found in any source.",
            }

        volatility = self._calc_volatility(ohlcv) if ohlcv else None
        trend = self._detect_trend(ohlcv) if ohlcv else "Unknown"

        return {
            "found": True,
            "symbol": display,
            "base": base,
            "quote": quote,
            "timeframe": timeframe,
            "price": price_data.get("price", 0.0),
            "price_formatted": format_price(price_data.get("price", 0.0)),
            "change_24h": price_data.get("change_24h", 0.0),
            "change_24h_formatted": format_change(price_data.get("change_24h", 0.0)),
            "volume_24h": price_data.get("volume_24h", 0.0),
            "volume_formatted": format_volume(price_data.get("volume_24h", 0.0)),
            "market_cap": price_data.get("market_cap", 0.0),
            "high_24h": price_data.get("high_24h", 0.0),
            "low_24h": price_data.get("low_24h", 0.0),
            "volatility": volatility,
            "trend": trend,
            "ohlcv": ohlcv or [],
            "source": price_data.get("source", "unknown"),
        }

    def _fetch_price(self, base: str, symbol: str) -> Optional[Dict]:
        # 1. CoinGecko
        cg_id = get_coingecko_id(base)
        if not cg_id:
            cg_id = coingecko_search(base)
        if cg_id:
            data = coingecko_get_price(cg_id)
            if data:
                return {
                    "price": data.get("current_price", 0.0),
                    "change_24h": data.get("price_change_percentage_24h", 0.0),
                    "volume_24h": data.get("total_volume", 0.0),
                    "market_cap": data.get("market_cap", 0.0),
                    "high_24h": data.get("high_24h", 0.0),
                    "low_24h": data.get("low_24h", 0.0),
                    "source": "CoinGecko",
                }

        # 2. Binance
        data = binance_get_ticker(symbol)
        if data and "lastPrice" in data:
            return {
                "price": float(data.get("lastPrice", 0)),
                "change_24h": float(data.get("priceChangePercent", 0)),
                "volume_24h": float(data.get("quoteVolume", 0)),
                "market_cap": 0.0,
                "high_24h": float(data.get("highPrice", 0)),
                "low_24h": float(data.get("lowPrice", 0)),
                "source": "Binance",
            }

        # 3. Bybit
        data = bybit_get_ticker(symbol)
        if data:
            price = float(data.get("lastPrice", 0) or 0)
            if price > 0:
                return {
                    "price": price,
                    "change_24h": float(data.get("price24hPcnt", 0) or 0) * 100,
                    "volume_24h": float(data.get("volume24h", 0) or 0) * price,
                    "market_cap": 0.0,
                    "high_24h": float(data.get("highPrice24h", 0) or 0),
                    "low_24h": float(data.get("lowPrice24h", 0) or 0),
                    "source": "Bybit",
                }

        return None

    def _fetch_ohlcv(
        self, base: str, symbol: str, timeframe: str
    ) -> Optional[list]:
        # Binance timeframe map
        tf_binance = {
            "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
        }.get(timeframe, "4h")

        data = binance_get_klines(symbol, tf_binance, limit=100)
        if data and len(data) > 10:
            return [
                {
                    "ts": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                for c in data
            ]

        # Bybit timeframe map
        tf_bybit = {
            "15m": "15", "1h": "60", "4h": "240", "1d": "D",
        }.get(timeframe, "240")

        data = bybit_get_klines(symbol, tf_bybit, limit=100)
        if data and len(data) > 10:
            result = []
            for c in reversed(data):
                try:
                    result.append({
                        "ts": int(c[0]),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": float(c[5]),
                    })
                except Exception:
                    continue
            return result

        # CoinGecko OHLCV fallback
        cg_id = get_coingecko_id(base)
        if not cg_id:
            cg_id = coingecko_search(base)
        if cg_id:
            days = {"15m": 1, "1h": 7, "4h": 14, "1d": 90}.get(timeframe, 14)
            raw = coingecko_get_ohlcv(cg_id, days=days)
            if raw and len(raw) > 5:
                return [
                    {
                        "ts": int(c[0]),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": 0.0,
                    }
                    for c in raw
                ]

        return None

    def _calc_volatility(self, ohlcv: list) -> Optional[float]:
        if not ohlcv or len(ohlcv) < 5:
            return None
        recent = ohlcv[-20:]
        ranges = []
        for c in recent:
            h = c.get("high", 0)
            l = c.get("low", 0)
            cl = c.get("close", 0)
            if cl > 0:
                ranges.append((h - l) / cl * 100)
        return round(sum(ranges) / len(ranges), 2) if ranges else None

    def _detect_trend(self, ohlcv: list) -> str:
        if not ohlcv or len(ohlcv) < 20:
            return "Unknown"
        closes = [c["close"] for c in ohlcv if c.get("close")]
        if len(closes) < 20:
            return "Unknown"
        ma20 = sum(closes[-20:]) / 20
        ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else ma20
        last = closes[-1]
        if last > ma20 > ma50:
            return "Bullish"
        elif last < ma20 < ma50:
            return "Bearish"
        elif last > ma20:
            return "Moderately Bullish"
        else:
            return "Moderately Bearish"
