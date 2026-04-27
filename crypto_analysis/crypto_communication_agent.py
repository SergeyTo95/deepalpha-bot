from typing import Any, Dict, List


class CryptoCommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(
        self,
        market_data: Dict[str, Any],
        ta_data: Dict[str, Any],
        news_data: Dict[str, Any],
        decision_data: Dict[str, Any],
        lang: str = "ru",
    ) -> str:
        sep = "────────────────"

        symbol = market_data.get("symbol", "N/A")
        timeframe = market_data.get("timeframe", "4h")
        price = market_data.get("price_formatted", "N/A")
        change = market_data.get("change_24h_formatted", "N/A")
        volume = market_data.get("volume_formatted", "N/A")
        trend = market_data.get("trend", "Unknown")
        volatility = market_data.get("volatility")
        source = market_data.get("source", "")

        decision = decision_data.get("decision", "WAIT")
        market_logic = decision_data.get("market_logic", "")
        entry_logic = decision_data.get("entry_logic", "")
        risk = decision_data.get("risk", "")

        rsi = ta_data.get("rsi")
        macd = ta_data.get("macd") or {}
        ma = ta_data.get("ma") or {}
        fib = ta_data.get("fibonacci") or {}
        sr = ta_data.get("support_resistance") or {}
        elliott = ta_data.get("elliott_hypothesis", "")

        sentiment = news_data.get("sentiment", "neutral")
        key_events = news_data.get("key_events", [])
        news_items = news_data.get("news_items", [])

        trigger_block = self._build_triggers(
            market_data, ta_data, news_data, lang
        )
        ta_block = self._build_ta_block(
            rsi, macd, ma, fib, sr, elliott, lang
        )
        news_block = self._build_news_block(
            sentiment, key_events, news_items, lang
        )
        conclusion = self._build_conclusion(decision, symbol, lang)

        vol_str = f"{volatility:.1f}%" if volatility is not None else "N/A"

        if lang == "ru":
            trend_map = {
                "Bullish": "📈 Бычий", "Bearish": "📉 Медвежий",
                "Moderately Bullish": "↗️ Умеренно бычий",
                "Moderately Bearish": "↘️ Умеренно медвежий",
                "Unknown": "➡️ Неизвестен",
            }
            trend_str = trend_map.get(trend, trend)
            sent_map = {
                "bullish": "🟢 Позитивный", "bearish": "🔴 Негативный", "neutral": "⚪ Нейтральный"
            }
            sent_str = sent_map.get(sentiment, sentiment)
            src_note = f"\n📡 Источник данных: {source}" if source else ""

            return (
                f"🪙 DeepAlpha Crypto Analysis\n"
                f"{sep}\n\n"
                f"📌 Asset: {symbol}\n"
                f"⏱ Таймфрейм: {timeframe}{src_note}\n\n"
                f"💵 Цена: {price}\n"
                f"📊 24h: {change}\n"
                f"📈 Тренд: {trend_str}\n"
                f"⚡ Волатильность: {vol_str}\n"
                f"📦 Объём 24h: {volume}\n\n"
                f"📊 Decision: {decision}\n\n"
                f"{sep}\n\n"
                f"💭 Логика рынка:\n{market_logic}\n\n"
                f"📐 Технический анализ:\n{ta_block}\n\n"
                f"📰 Новости:\n"
                f"Сентимент: {sent_str}\n"
                f"{news_block}\n\n"
                f"📡 Trigger Watch:\n{trigger_block}\n\n"
                f"📌 Логика входа:\n{entry_logic}\n\n"
                f"⚠️ Риски:\n{risk}\n\n"
                f"{sep}\n"
                f"📝 Вывод: {conclusion}"
            )
        else:
            trend_str = trend
            sent_map = {
                "bullish": "🟢 Bullish", "bearish": "🔴 Bearish", "neutral": "⚪ Neutral"
            }
            sent_str = sent_map.get(sentiment, sentiment)
            src_note = f"\n📡 Data source: {source}" if source else ""

            return (
                f"🪙 DeepAlpha Crypto Analysis\n"
                f"{sep}\n\n"
                f"📌 Asset: {symbol}\n"
                f"⏱ Timeframe: {timeframe}{src_note}\n\n"
                f"💵 Price: {price}\n"
                f"📊 24h: {change}\n"
                f"📈 Trend: {trend_str}\n"
                f"⚡ Volatility: {vol_str}\n"
                f"📦 Volume 24h: {volume}\n\n"
                f"📊 Decision: {decision}\n\n"
                f"{sep}\n\n"
                f"💭 Market Logic:\n{market_logic}\n\n"
                f"📐 Technical Picture:\n{ta_block}\n\n"
                f"📰 News:\n"
                f"Sentiment: {sent_str}\n"
                f"{news_block}\n\n"
                f"📡 Trigger Watch:\n{trigger_block}\n\n"
                f"📌 Entry Logic:\n{entry_logic}\n\n"
                f"⚠️ Risk:\n{risk}\n\n"
                f"{sep}\n"
                f"📝 Conclusion: {conclusion}"
            )

    def _build_ta_block(
        self, rsi, macd, ma, fib, sr, elliott, lang
    ) -> str:
        lines = []

        if rsi is not None:
            from crypto_analysis.crypto_utils import classify_rsi, classify_rsi_en
            rsi_label = classify_rsi(rsi) if lang == "ru" else classify_rsi_en(rsi)
            lines.append(f"— RSI: {rsi:.1f} ({rsi_label})")

        if macd:
            trend = macd.get("trend", "")
            crossover = macd.get("crossover")
            hist = macd.get("histogram", 0)
            if lang == "ru":
                trend_str = "бычий" if trend == "bullish" else "медвежий"
                cross_str = ""
                if crossover == "golden_cross":
                    cross_str = ", золотое пересечение ✅"
                elif crossover == "death_cross":
                    cross_str = ", пересечение смерти ❌"
                lines.append(f"— MACD: {trend_str}{cross_str} (hist: {hist:.6f})")
            else:
                cross_str = ""
                if crossover == "golden_cross":
                    cross_str = ", golden cross ✅"
                elif crossover == "death_cross":
                    cross_str = ", death cross ❌"
                lines.append(f"— MACD: {trend}{cross_str} (hist: {hist:.6f})")

        if ma:
            if lang == "ru":
                trend_str = "бычий" if ma.get("trend") == "bullish" else "медвежий"
                parts = []
                for p in [20, 50, 200]:
                    v = ma.get(f"ma{p}")
                    sig = ma.get(f"ma{p}_signal")
                    if v:
                        sign_str = "выше" if sig == "above" else "ниже"
                        parts.append(f"MA{p}: {v:.4f} ({sign_str})")
                lines.append(f"— MA тренд: {trend_str} | " + " | ".join(parts))
            else:
                parts = []
                for p in [20, 50, 200]:
                    v = ma.get(f"ma{p}")
                    sig = ma.get(f"ma{p}_signal")
                    if v:
                        parts.append(f"MA{p}: {v:.4f} ({sig})")
                lines.append(f"— MA trend: {ma.get('trend', 'N/A')} | " + " | ".join(parts))

        if fib and fib.get("levels"):
            levels = fib["levels"]
            if lang == "ru":
                lines.append(
                    f"— Fibonacci: 0.382={levels.get('0.382', 0):.4f} | "
                    f"0.5={levels.get('0.5', 0):.4f} | "
                    f"0.618={levels.get('0.618', 0):.4f}"
                )
            else:
                lines.append(
                    f"— Fibonacci: 0.382={levels.get('0.382', 0):.4f} | "
                    f"0.5={levels.get('0.5', 0):.4f} | "
                    f"0.618={levels.get('0.618', 0):.4f}"
                )

        if sr:
            if lang == "ru":
                lines.append(
                    f"— Поддержка: {sr.get('nearest_support', 0):.4f} | "
                    f"Сопротивление: {sr.get('nearest_resistance', 0):.4f}"
                )
            else:
                lines.append(
                    f"— Support: {sr.get('nearest_support', 0):.4f} | "
                    f"Resistance: {sr.get('nearest_resistance', 0):.4f}"
                )

        if elliott:
            if lang == "ru":
                lines.append(f"— Elliott (гипотеза): {elliott}")
            else:
                lines.append(f"— Elliott (hypothesis): {elliott}")

        return "\n".join(lines) if lines else ("Данные TA недоступны" if lang == "ru" else "TA data unavailable")

    def _build_news_block(
        self, sentiment, key_events, news_items, lang
    ) -> str:
        if not key_events and not news_items:
            return "— нет данных" if lang == "ru" else "— no data"
        lines = []
        if key_events:
            for event in key_events[:3]:
                lines.append(f"— {event}")
        elif news_items:
            for item in news_items[:3]:
                title = (item.get("title") or "")[:100]
                if title:
                    lines.append(f"— {title}")
        return "\n".join(lines) if lines else ("— нет событий" if lang == "ru" else "— no events")

    def _build_triggers(
        self, market_data, ta_data, news_data, lang
    ) -> str:
        base = market_data.get("base", "")
        rsi = ta_data.get("rsi")
        macd = ta_data.get("macd") or {}
        sentiment = news_data.get("sentiment", "neutral")

        if lang == "ru":
            high = []
            medium = []
            low = []
            high.append("решение регулятора по активу или сектору")
            high.append("крупная ликвидация или whale-движение")
            if macd.get("crossover") == "golden_cross":
                high.append("подтверждение золотого пересечения MACD на старшем ТФ")
            elif macd.get("crossover") == "death_cross":
                high.append("подтверждение пересечения смерти MACD на старшем ТФ")
            medium.append("пробой ключевого уровня сопротивления с объёмом")
            medium.append("изменение риторики крупных держателей")
            if rsi and (rsi > 70 or rsi < 30):
                medium.append(f"RSI достигает экстремума (сейчас {rsi:.0f})")
            low.append("медийные публикации без on-chain подтверждения")
            low.append("заявления инфлюенсеров без фактических данных")

            result = "🔴 High impact:\n"
            result += "\n".join(f"— {t}" for t in high[:2])
            result += "\n\n🟡 Medium:\n"
            result += "\n".join(f"— {t}" for t in medium[:2])
            result += "\n\n🟢 Low:\n"
            result += "\n".join(f"— {t}" for t in low[:2])
        else:
            high = []
            medium = []
            low = []
            high.append("regulatory decision on asset or sector")
            high.append("large liquidation or whale movement")
            if macd.get("crossover") == "golden_cross":
                high.append("MACD golden cross confirmation on higher timeframe")
            elif macd.get("crossover") == "death_cross":
                high.append("MACD death cross confirmation on higher timeframe")
            medium.append("resistance level breakout with volume confirmation")
            medium.append("shift in major holder rhetoric")
            if rsi and (rsi > 70 or rsi < 30):
                medium.append(f"RSI reaching extreme (currently {rsi:.0f})")
            low.append("media publications without on-chain confirmation")
            low.append("influencer statements without factual data")

            result = "🔴 High impact:\n"
            result += "\n".join(f"— {t}" for t in high[:2])
            result += "\n\n🟡 Medium:\n"
            result += "\n".join(f"— {t}" for t in medium[:2])
            result += "\n\n🟢 Low:\n"
            result += "\n".join(f"— {t}" for t in low[:2])

        return result

    def _build_conclusion(self, decision: str, symbol: str, lang: str) -> str:
        if lang == "ru":
            if decision == "TRADE":
                return (
                    f"Есть технический сетап для входа в {symbol}. "
                    "Входить при откате с подтверждением объёма. "
                    "Соблюдать риск-менеджмент."
                )
            elif decision == "WAIT":
                return (
                    f"Конфликт сигналов или рынок у ключевого уровня. "
                    f"Ждать подтверждения перед входом в {symbol}."
                )
            else:
                return (
                    f"Недостаточно данных или низкая ликвидность {symbol}. "
                    "Оставаться вне позиции."
                )
        else:
            if decision == "TRADE":
                return (
                    f"Technical setup present for {symbol} entry. "
                    "Enter on pullback with volume confirmation. "
                    "Apply proper risk management."
                )
            elif decision == "WAIT":
                return (
                    f"Signal conflict or market at key level. "
                    f"Wait for confirmation before entering {symbol}."
                )
            else:
                return (
                    f"Insufficient data or low liquidity for {symbol}. "
                    "Stay out of position."
                )
