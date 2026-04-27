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
        risk = self._build_risk(decision, ta_data, market_data, lang, news_data=news_data)
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
        news_data: Dict = None,
    ) -> str:
        rsi = ta_data.get("rsi")
        volatility = market_data.get("volatility")
        macd = ta_data.get("macd") or {}
        ma = ta_data.get("ma") or {}
        sr = ta_data.get("support_resistance") or {}
        news_quality = (news_data or {}).get("news_quality", "none")

        support = sr.get("nearest_support", 0)
        resistance = sr.get("nearest_resistance", 0)
        price = market_data.get("price", 0)
        ma20 = ma.get("ma20", 0)
        ma50 = ma.get("ma50", 0)

        risks = []

        if lang == "ru":
            if support and price and abs(price - support) / price < 0.03:
                risks.append(
                    f"цена рядом с поддержкой {support:.4f} — "
                    "возможен резкий отскок или пробой"
                )
            if resistance and price and abs(price - resistance) / price < 0.03:
                risks.append(
                    f"цена рядом с сопротивлением {resistance:.4f} — "
                    "возможна реакция продавцов"
                )
            if rsi and rsi < 35:
                risks.append(
                    f"RSI {rsi:.0f} близко к перепроданности — "
                    "шортить поздно без подтверждения"
                )
            elif rsi and rsi > 70:
                risks.append(
                    f"RSI {rsi:.0f} перекупленность — "
                    "лонговать поздно без отката"
                )
            if ma20 and ma50 and price:
                if price < ma20 and price < ma50:
                    risks.append(
                        f"цена ниже MA20/MA50 — риск продолжения снижения"
                    )
                elif price > ma20 and price > ma50 and ma20 < ma50:
                    risks.append(
                        "цена выше MA20/MA50 но тренд медвежий — "
                        "риск ложного пробоя"
                    )
            if macd.get("crossover") == "death_cross":
                risks.append("MACD пересечение смерти — усиление медвежьего давления")
            if volatility and volatility > 5:
                risks.append(
                    f"высокая волатильность ({volatility:.1f}%) — "
                    "использовать широкие стопы"
                )
            if news_quality == "none":
                risks.append(
                    "новостные данные отсутствуют — "
                    "сигнал основан в основном на TA"
                )
            elif news_quality == "limited":
                risks.append(
                    "новостные данные ограничены — "
                    "возможны незамеченные катализаторы"
                )
            if not risks:
                risks.append("стандартные рыночные риски — использовать стоп-лосс")
            risks.append(
                "это не финансовая рекомендация — решения принимаешь сам"
            )
        else:
            if support and price and abs(price - support) / price < 0.03:
                risks.append(
                    f"price near support {support:.4f} — "
                    "sharp bounce or breakdown possible"
                )
            if resistance and price and abs(price - resistance) / price < 0.03:
                risks.append(
                    f"price near resistance {resistance:.4f} — "
                    "seller reaction likely"
                )
            if rsi and rsi < 35:
                risks.append(
                    f"RSI {rsi:.0f} near oversold — "
                    "late to short without confirmation"
                )
            elif rsi and rsi > 70:
                risks.append(
                    f"RSI {rsi:.0f} overbought — "
                    "late to long without pullback"
                )
            if ma20 and ma50 and price:
                if price < ma20 and price < ma50:
                    risks.append(
                        "price below MA20/MA50 — risk of continued decline"
                    )
                elif price > ma20 and price > ma50 and ma20 < ma50:
                    risks.append(
                        "price above MA20/MA50 but trend is bearish — "
                        "risk of false breakout"
                    )
            if macd.get("crossover") == "death_cross":
                risks.append("MACD death cross — increasing bearish pressure")
            if volatility and volatility > 5:
                risks.append(
                    f"high volatility ({volatility:.1f}%) — use wider stops"
                )
            if news_quality == "none":
                risks.append(
                    "no news data available — "
                    "signal based primarily on TA"
                )
            elif news_quality == "limited":
                risks.append(
                    "limited news data — "
                    "untracked catalysts possible"
                )
            if not risks:
                risks.append("standard market risks — always use stop-loss")
            risks.append(
                "not financial advice — you make your own decisions"
            )

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
