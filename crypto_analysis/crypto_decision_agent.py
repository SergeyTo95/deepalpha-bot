from typing import Any, Dict, Optional


class CryptoDecisionAgent:
    def __init__(self) -> None:
        pass

    def run(
        self,
        market_data: Dict[str, Any],
        ta_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str = "ru",
    ) -> Dict[str, Any]:
        ta_signal = ta_data.get("signal", "WAIT")
        sentiment = news_data.get("sentiment", "neutral")
        rsi = ta_data.get("rsi")
        macd = ta_data.get("macd") or {}
        ma = ta_data.get("ma") or {}
        volume_signal = ta_data.get("volume_signal", "unknown")
        price = market_data.get("price", 0)
        volatility = market_data.get("volatility")

        decision = self._make_decision(
            ta_signal, sentiment, rsi, macd, ma, volume_signal, volatility
        )
        entry_logic = self._build_entry_logic(
            decision, ta_data, market_data, lang
        )
        risk = self._build_risk(decision, ta_data, market_data, lang)
        market_logic = self._build_market_logic(
            ta_signal, sentiment, rsi, macd, ma, lang
        )

        return {
            "decision": decision,
            "entry_logic": entry_logic,
            "risk": risk,
            "market_logic": market_logic,
            "ta_signal": ta_signal,
            "sentiment": sentiment,
        }

    def _make_decision(
        self,
        ta_signal: str,
        sentiment: str,
        rsi: Optional[float],
        macd: Dict,
        ma: Dict,
        volume_signal: str,
        volatility: Optional[float],
    ) -> str:
        # NO TRADE при слабых данных
        if ta_signal == "NO DATA":
            return "NO TRADE"

        # Overheated
        if rsi and rsi > 80 and "LONG" in ta_signal:
            return "WAIT"
        if rsi and rsi < 20 and "SHORT" in ta_signal:
            return "WAIT"

        # Конфликт сигналов
        if ta_signal == "WAIT":
            return "WAIT"

        if "LONG" in ta_signal:
            if sentiment == "bearish":
                return "WAIT"
            if "confirmation" in volume_signal and "bullish" in volume_signal:
                if volatility and volatility > 8:
                    return "WAIT"
                return "TRADE"
            return "WAIT"

        if "SHORT" in ta_signal:
            if sentiment == "bullish":
                return "WAIT"
            if "confirmation" in volume_signal and "bearish" in volume_signal:
                return "TRADE"
            return "WAIT"

        return "WAIT"

    def _build_entry_logic(
        self,
        decision: str,
        ta_data: Dict,
        market_data: Dict,
        lang: str,
    ) -> str:
        sr = ta_data.get("support_resistance") or {}
        fib = ta_data.get("fibonacci") or {}
        price = market_data.get("price", 0)
        support = sr.get("nearest_support", 0)
        resistance = sr.get("nearest_resistance", 0)

        fib_50 = (fib.get("levels") or {}).get("0.5", 0)
        fib_618 = (fib.get("levels") or {}).get("0.618", 0)

        if lang == "ru":
            if decision == "TRADE":
                ta_signal = ta_data.get("signal", "")
                if "LONG" in ta_signal:
                    entry = (
                        f"Вход: откат к поддержке {support:.4f} или уровню Fib 0.5 ({fib_50:.4f}).\n"
                        f"Цель: сопротивление {resistance:.4f} или Fib 0.618 ({fib_618:.4f}).\n"
                        f"Стоп: ниже ближайшей поддержки {support:.4f}."
                    )
                else:
                    entry = (
                        f"Вход: отскок к сопротивлению {resistance:.4f}.\n"
                        f"Цель: поддержка {support:.4f}.\n"
                        f"Стоп: выше сопротивления {resistance:.4f}."
                    )
            elif decision == "WAIT":
                entry = (
                    f"Ждать подтверждения. Ключевые уровни:\n"
                    f"Поддержка: {support:.4f}\n"
                    f"Сопротивление: {resistance:.4f}\n"
                    f"Fib 0.5: {fib_50:.4f}"
                )
            else:
                entry = "Нет условий для входа — слабые данные или низкая ликвидность."
        else:
            if decision == "TRADE":
                ta_signal = ta_data.get("signal", "")
                if "LONG" in ta_signal:
                    entry = (
                        f"Entry: pullback to support {support:.4f} or Fib 0.5 ({fib_50:.4f}).\n"
                        f"Target: resistance {resistance:.4f} or Fib 0.618 ({fib_618:.4f}).\n"
                        f"Stop: below nearest support {support:.4f}."
                    )
                else:
                    entry = (
                        f"Entry: bounce to resistance {resistance:.4f}.\n"
                        f"Target: support {support:.4f}.\n"
                        f"Stop: above resistance {resistance:.4f}."
                    )
            elif decision == "WAIT":
                entry = (
                    f"Wait for confirmation. Key levels:\n"
                    f"Support: {support:.4f}\n"
                    f"Resistance: {resistance:.4f}\n"
                    f"Fib 0.5: {fib_50:.4f}"
                )
            else:
                entry = "No entry conditions — weak data or low liquidity."

        return entry

    def _build_risk(
        self,
        decision: str,
        ta_data: Dict,
        market_data: Dict,
        lang: str,
    ) -> str:
        rsi = ta_data.get("rsi")
        volatility = market_data.get("volatility")
        macd = ta_data.get("macd") or {}

        risks = []
        if lang == "ru":
            if volatility and volatility > 5:
                risks.append(f"высокая волатильность ({volatility:.1f}%) — широкие стопы")
            if rsi and rsi > 70:
                risks.append(f"RSI перегрет ({rsi:.0f}) — возможен откат")
            if rsi and rsi < 30:
                risks.append(f"RSI перепродан ({rsi:.0f}) — возможен отскок")
            if macd.get("crossover") == "death_cross":
                risks.append("MACD пересечение вниз — медвежий сигнал")
            if not risks:
                risks.append("стандартные рыночные риски — использовать стоп-лосс")
            risks.append("это не финансовая рекомендация — решения принимаешь сам")
        else:
            if volatility and volatility > 5:
                risks.append(f"high volatility ({volatility:.1f}%) — use wider stops")
            if rsi and rsi > 70:
                risks.append(f"RSI overbought ({rsi:.0f}) — pullback possible")
            if rsi and rsi < 30:
                risks.append(f"RSI oversold ({rsi:.0f}) — bounce possible")
            if macd.get("crossover") == "death_cross":
                risks.append("MACD death cross — bearish signal")
            if not risks:
                risks.append("standard market risks — always use stop-loss")
            risks.append("not financial advice — you make your own decisions")

        return "\n".join(f"— {r}" for r in risks)

    def _build_market_logic(
        self,
        ta_signal: str,
        sentiment: str,
        rsi: Optional[float],
        macd: Dict,
        ma: Dict,
        lang: str,
    ) -> str:
        if lang == "ru":
            sent_map = {
                "bullish": "позитивный", "bearish": "негативный", "neutral": "нейтральный"
            }
            sent_ru = sent_map.get(sentiment, sentiment)
            ma_trend = ma.get("trend", "unknown")
            trend_ru = {"bullish": "бычий", "bearish": "медвежий"}.get(ma_trend, "нейтральный")
            macd_trend = (macd.get("trend") or "unknown")
            macd_ru = {"bullish": "бычий", "bearish": "медвежий"}.get(macd_trend, "нейтральный")
            rsi_str = f"RSI {rsi:.0f}" if rsi else "RSI н/д"
            crossover = macd.get("crossover")
            cross_str = ""
            if crossover == "golden_cross":
                cross_str = " (золотое пересечение)"
            elif crossover == "death_cross":
                cross_str = " (пересечение смерти)"
            return (
                f"Тренд по MA: {trend_ru}. "
                f"MACD: {macd_ru}{cross_str}. "
                f"{rsi_str}. "
                f"Настроение новостей: {sent_ru}."
            )
        else:
            ma_trend = ma.get("trend", "unknown")
            macd_trend = macd.get("trend") or "unknown"
            rsi_str = f"RSI {rsi:.0f}" if rsi else "RSI N/A"
            crossover = macd.get("crossover")
            cross_str = ""
            if crossover == "golden_cross":
                cross_str = " (golden cross)"
            elif crossover == "death_cross":
                cross_str = " (death cross)"
            return (
                f"MA trend: {ma_trend}. "
                f"MACD: {macd_trend}{cross_str}. "
                f"{rsi_str}. "
                f"News sentiment: {sentiment}."
            )
