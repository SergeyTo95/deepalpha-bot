import math
from typing import Any, Dict, List, Optional, Tuple


class CryptoTAAgent:
    def __init__(self) -> None:
        pass

    def run(self, market_data: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
        ohlcv = market_data.get("ohlcv", [])

        if not ohlcv or len(ohlcv) < 14:
            return {
                "available": False,
                "rsi": None,
                "macd": None,
                "ma": None,
                "support_resistance": None,
                "fibonacci": None,
                "elliott_hypothesis": None,
                "signal": "NO DATA",
                "summary": "Insufficient data for technical analysis.",
            }

        closes = [c["close"] for c in ohlcv if c.get("close")]
        highs = [c["high"] for c in ohlcv if c.get("high")]
        lows = [c["low"] for c in ohlcv if c.get("low")]
        volumes = [c["volume"] for c in ohlcv if "volume" in c]

        rsi = self._calc_rsi(closes)
        macd_data = self._calc_macd(closes)
        ma_data = self._calc_mas(closes)
        sr_data = self._calc_support_resistance(highs, lows, closes)
        fib_data = self._calc_fibonacci(highs, lows)
        elliott = self._elliott_hypothesis(closes, lang=lang)
        volume_signal = self._volume_analysis(volumes, closes)
        signal = self._determine_signal(rsi, macd_data, ma_data, volume_signal)

        return {
            "available": True,
            "rsi": rsi,
            "macd": macd_data,
            "ma": ma_data,
            "support_resistance": sr_data,
            "fibonacci": fib_data,
            "elliott_hypothesis": elliott,
            "volume_signal": volume_signal,
            "signal": signal,
        }

    # ═══════════════════════════════════════════
    # RSI
    # ═══════════════════════════════════════════

    def _calc_rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    # ═══════════════════════════════════════════
    # MACD
    # ═══════════════════════════════════════════

    def _calc_macd(
        self,
        closes: List[float],
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Optional[Dict]:
        if len(closes) < slow + signal:
            return None
        ema_fast = self._ema(closes, fast)
        ema_slow = self._ema(closes, slow)
        if ema_fast is None or ema_slow is None:
            return None
        macd_line = ema_fast - ema_slow

        macd_series = []
        for i in range(slow - 1, len(closes)):
            ef = self._ema(closes[:i + 1], fast)
            es = self._ema(closes[:i + 1], slow)
            if ef and es:
                macd_series.append(ef - es)

        if len(macd_series) < signal:
            return None

        signal_line = self._ema(macd_series, signal)
        if signal_line is None:
            return None

        histogram = macd_line - signal_line
        trend = "bullish" if macd_line > signal_line else "bearish"
        crossover = None
        if len(macd_series) >= 2:
            prev_macd = macd_series[-2]
            prev_signal = self._ema(macd_series[:-1], signal)
            if prev_signal:
                if prev_macd < prev_signal and macd_line > signal_line:
                    crossover = "golden_cross"
                elif prev_macd > prev_signal and macd_line < signal_line:
                    crossover = "death_cross"

        return {
            "macd": round(macd_line, 6),
            "signal": round(signal_line, 6),
            "histogram": round(histogram, 6),
            "trend": trend,
            "crossover": crossover,
        }

    def _ema(self, data: List[float], period: int) -> Optional[float]:
        if len(data) < period:
            return None
        k = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    # ═══════════════════════════════════════════
    # MOVING AVERAGES
    # ═══════════════════════════════════════════

    def _calc_mas(self, closes: List[float]) -> Dict:
        result = {}
        current = closes[-1] if closes else 0

        for period in [20, 50, 200]:
            if len(closes) >= period:
                ma = sum(closes[-period:]) / period
                result[f"ma{period}"] = round(ma, 6)
                result[f"ma{period}_signal"] = (
                    "above" if current > ma else "below"
                )

        ma20 = result.get("ma20")
        ma50 = result.get("ma50")

        if ma20 and ma50:
            long_bullish = ma20 > ma50
            price_above_ma20 = current > ma20
            price_above_ma50 = current > ma50

            if long_bullish and not price_above_ma20 and not price_above_ma50:
                result["trend"] = "bullish"
                result["ma_summary_ru"] = (
                    "MA структура долгосрочно бычья, но цена ниже MA20/MA50 — "
                    "краткосрочная слабость."
                )
                result["ma_summary_en"] = (
                    "MA structure is longer-term bullish, but price is below "
                    "MA20/MA50 — short-term weakness."
                )
            elif not long_bullish and price_above_ma20 and price_above_ma50:
                result["trend"] = "bearish"
                result["ma_summary_ru"] = (
                    "MA структура медвежья, но цена выше MA20/MA50 — "
                    "возможная попытка разворота."
                )
                result["ma_summary_en"] = (
                    "MA structure is bearish, but price is above MA20/MA50 — "
                    "possible reversal attempt."
                )
            elif long_bullish and price_above_ma20 and price_above_ma50:
                result["trend"] = "bullish"
                result["ma_summary_ru"] = "MA структура бычья, цена подтверждает силу."
                result["ma_summary_en"] = "MA structure bullish, price confirms strength."
            elif not long_bullish and not price_above_ma20 and not price_above_ma50:
                result["trend"] = "bearish"
                result["ma_summary_ru"] = "MA структура медвежья, цена подтверждает слабость."
                result["ma_summary_en"] = "MA structure bearish, price confirms weakness."
            elif long_bullish:
                result["trend"] = "bullish"
                result["ma_summary_ru"] = "MA структура бычья, цена в переходной зоне."
                result["ma_summary_en"] = "MA structure bullish, price in transition zone."
            else:
                result["trend"] = "bearish"
                result["ma_summary_ru"] = "MA структура медвежья, цена в переходной зоне."
                result["ma_summary_en"] = "MA structure bearish, price in transition zone."
        elif ma20:
            price_above = current > ma20
            if price_above:
                result["trend"] = "bullish"
                result["ma_summary_ru"] = "Цена выше MA20 — краткосрочный бычий сигнал."
                result["ma_summary_en"] = "Price above MA20 — short-term bullish signal."
            else:
                result["trend"] = "bearish"
                result["ma_summary_ru"] = "Цена ниже MA20 — краткосрочный медвежий сигнал."
                result["ma_summary_en"] = "Price below MA20 — short-term bearish signal."
        else:
            result["trend"] = "unknown"
            result["ma_summary_ru"] = "Недостаточно данных для MA анализа."
            result["ma_summary_en"] = "Insufficient data for MA analysis."

        return result

    # ═══════════════════════════════════════════
    # SUPPORT / RESISTANCE
    # ═══════════════════════════════════════════

    def _calc_support_resistance(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        lookback: int = 20,
    ) -> Dict:
        recent_highs = highs[-lookback:] if len(highs) >= lookback else highs
        recent_lows = lows[-lookback:] if len(lows) >= lookback else lows
        current = closes[-1] if closes else 0

        resistance = max(recent_highs) if recent_highs else 0
        support = min(recent_lows) if recent_lows else 0

        pivot_highs = self._find_pivot_highs(recent_highs)
        pivot_lows = self._find_pivot_lows(recent_lows)

        nearest_resistance = min(
            (h for h in pivot_highs if h > current), default=resistance
        )
        nearest_support = max(
            (l for l in pivot_lows if l < current), default=support
        )

        return {
            "resistance": round(resistance, 6),
            "support": round(support, 6),
            "nearest_resistance": round(nearest_resistance, 6),
            "nearest_support": round(nearest_support, 6),
            "current": round(current, 6),
        }

    def _find_pivot_highs(self, data: List[float], window: int = 3) -> List[float]:
        pivots = []
        for i in range(window, len(data) - window):
            if data[i] == max(data[i - window:i + window + 1]):
                pivots.append(data[i])
        return pivots

    def _find_pivot_lows(self, data: List[float], window: int = 3) -> List[float]:
        pivots = []
        for i in range(window, len(data) - window):
            if data[i] == min(data[i - window:i + window + 1]):
                pivots.append(data[i])
        return pivots

    # ═══════════════════════════════════════════
    # FIBONACCI
    # ═══════════════════════════════════════════

    def _calc_fibonacci(
        self, highs: List[float], lows: List[float], lookback: int = 50
    ) -> Optional[Dict]:
        if not highs or not lows:
            return None
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        swing_high = max(recent_highs)
        swing_low = min(recent_lows)
        diff = swing_high - swing_low
        if diff <= 0:
            return None
        levels = {
            "0.0": round(swing_low, 6),
            "0.236": round(swing_low + 0.236 * diff, 6),
            "0.382": round(swing_low + 0.382 * diff, 6),
            "0.5": round(swing_low + 0.5 * diff, 6),
            "0.618": round(swing_low + 0.618 * diff, 6),
            "0.786": round(swing_low + 0.786 * diff, 6),
            "1.0": round(swing_high, 6),
        }
        return {
            "swing_high": round(swing_high, 6),
            "swing_low": round(swing_low, 6),
            "levels": levels,
        }

    # ═══════════════════════════════════════════
    # ELLIOTT WAVE HYPOTHESIS
    # ═══════════════════════════════════════════

    def _elliott_hypothesis(self, closes: List[float], lang: str = "en") -> str:
        if len(closes) < 30:
            return (
                "Недостаточно данных для волновой гипотезы."
                if lang == "ru"
                else "Insufficient data for Elliott Wave hypothesis."
            )

        recent = closes[-30:]
        pivots = []
        for i in range(1, len(recent) - 1):
            if recent[i] > recent[i - 1] and recent[i] > recent[i + 1]:
                pivots.append(("high", i, recent[i]))
            elif recent[i] < recent[i - 1] and recent[i] < recent[i + 1]:
                pivots.append(("low", i, recent[i]))

        if len(pivots) < 5:
            return (
                "Волновая структура неясна — недостаточно pivot-точек."
                if lang == "ru"
                else "Wave structure unclear — not enough pivot points detected."
            )

        first_type = pivots[0][0]
        if first_type == "low":
            return (
                "Гипотеза: возможная импульсная структура вверх. "
                "Использовать только как дополнительный сигнал."
                if lang == "ru"
                else "Hypothesis: possible impulse wave up — "
                "low → high → low → high → low pattern detected. "
                "Treat as hypothesis only."
            )
        else:
            return (
                "Гипотеза: возможная коррекционная структура вниз. "
                "Использовать только как дополнительный сигнал."
                if lang == "ru"
                else "Hypothesis: possible corrective wave — "
                "high → low → high → low → high pattern detected. "
                "Treat as hypothesis only."
            )

    # ═══════════════════════════════════════════
    # VOLUME
    # ═══════════════════════════════════════════

    def _volume_analysis(
        self, volumes: List[float], closes: List[float]
    ) -> str:
        if not volumes or len(volumes) < 5:
            return "unknown"
        avg_vol = sum(volumes[-20:]) / min(len(volumes), 20)
        last_vol = volumes[-1]
        last_close = closes[-1] if closes else 0
        prev_close = closes[-2] if len(closes) >= 2 else last_close
        price_up = last_close > prev_close
        vol_high = last_vol > avg_vol * 1.2

        if price_up and vol_high:
            return "bullish_confirmation"
        elif not price_up and vol_high:
            return "bearish_confirmation"
        elif price_up and not vol_high:
            return "bullish_weak"
        else:
            return "bearish_weak"

    # ═══════════════════════════════════════════
    # SIGNAL
    # ═══════════════════════════════════════════

    def _determine_signal(
        self,
        rsi: Optional[float],
        macd_data: Optional[Dict],
        ma_data: Dict,
        volume_signal: str,
    ) -> str:
        bullish_score = 0
        bearish_score = 0

        if rsi is not None:
            if rsi < 40:
                bullish_score += 1
            elif rsi > 60:
                bearish_score += 1
            if rsi > 75:
                bearish_score += 2
            if rsi < 25:
                bullish_score += 2

        if macd_data:
            if macd_data["trend"] == "bullish":
                bullish_score += 1
            else:
                bearish_score += 1
            if macd_data.get("crossover") == "golden_cross":
                bullish_score += 2
            elif macd_data.get("crossover") == "death_cross":
                bearish_score += 2

        if ma_data.get("trend") == "bullish":
            bullish_score += 1
        elif ma_data.get("trend") == "bearish":
            bearish_score += 1

        if "bullish" in volume_signal:
            bullish_score += 1
            if volume_signal == "bullish_confirmation":
                bullish_score += 1
        elif "bearish" in volume_signal:
            bearish_score += 1
            if volume_signal == "bearish_confirmation":
                bearish_score += 1

        diff = bullish_score - bearish_score
        if diff >= 4:
            return "LONG"
        elif diff <= -4:
            return "SHORT"
        elif diff >= 2:
            return "WEAK LONG"
        elif diff <= -2:
            return "WEAK SHORT"
        else:
            return "WAIT"
