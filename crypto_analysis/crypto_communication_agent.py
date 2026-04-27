from typing import Any, Dict, List, Optional


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
            sentiment, key_events, news_items, lang, news_data=news_data
        )
        conclusion = self._build_conclusion(decision, symbol, lang)

        vol_str = f"{volatility:.1f}%" if volatility is not None else "N/A"

        if lang == "ru":
            trend_map = {
                "Bullish": "📈 Бычий",
                "Bearish": "📉 Медвежий",
                "Moderately Bullish": "↗️ Умеренно бычий",
                "Moderately Bearish": "↘️ Умеренно медвежий",
                "Unknown": "➡️ Неизвестен",
            }
            trend_str = trend_map.get(trend, trend)
            sent_map = {
                "bullish": "🟢 Позитивный",
                "bearish": "🔴 Негативный",
                "neutral": "⚪ Нейтральный",
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
                "bullish": "🟢 Bullish",
                "bearish": "🔴 Bearish",
                "neutral": "⚪ Neutral",
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

    # ═══════════════════════════════════════════
    # TA BLOCK
    # ═══════════════════════════════════════════

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
            ma_summary = (
                ma.get("ma_summary_ru") if lang == "ru"
                else ma.get("ma_summary_en", "")
            )
            parts = []
            for p in [20, 50, 200]:
                v = ma.get(f"ma{p}")
                sig = ma.get(f"ma{p}_signal")
                if v:
                    if lang == "ru":
                        sign_str = "выше" if sig == "above" else "ниже"
                        parts.append(f"MA{p}: {v:.4f} ({sign_str})")
                    else:
                        parts.append(f"MA{p}: {v:.4f} ({sig})")
            ma_levels = " | ".join(parts) if parts else ""
            if ma_summary and ma_levels:
                lines.append(f"— MA: {ma_summary} | {ma_levels}")
            elif ma_summary:
                lines.append(f"— MA: {ma_summary}")
            elif ma_levels:
                lines.append(f"— MA: {ma_levels}")

        if fib and fib.get("levels"):
            levels = fib["levels"]
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

        if not lines:
            return (
                "Данные TA недоступны" if lang == "ru"
                else "TA data unavailable"
            )

        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # NEWS BLOCK
    # ═══════════════════════════════════════════

    def _build_news_block(
        self,
        sentiment,
        key_events,
        news_items,
        lang,
        news_data: Optional[Dict] = None,
    ) -> str:
        news_quality = (news_data or {}).get("news_quality", "none")
        base = (news_data or {}).get("base", "")

        if not key_events and not news_items:
            if lang == "ru":
                return (
                    f"— значимых новостей по {base} в открытых источниках не найдено\n"
                    "— новостной фон считается нейтральным"
                )
            else:
                return (
                    f"— no significant {base} news found in open sources\n"
                    "— news background is treated as neutral"
                )

        lines = []

        if news_quality == "limited" and not key_events:
            if lang == "ru":
                lines.append(
                    f"— специфических новостей по {base} не найдено, общий фон:"
                )
            else:
                lines.append(
                    f"— no specific {base} news, general market context:"
                )

        if key_events:
            for event in key_events[:3]:
                lines.append(f"— {event}")
        elif news_items:
            for item in news_items[:3]:
                title = (item.get("title") or "")[:100]
                if title:
                    lines.append(f"— {title}")

        if news_quality == "none" and lines:
            if lang == "ru":
                lines.append(
                    "— прямых новостей по активу нет, сигнал основан на TA"
                )
            else:
                lines.append(
                    "— no direct asset news, signal based primarily on TA"
                )

        return "\n".join(lines) if lines else (
            "— нет данных" if lang == "ru" else "— no data"
        )

    # ═══════════════════════════════════════════
    # TRIGGERS
    # ═══════════════════════════════════════════

    def _build_triggers(
        self, market_data, ta_data, news_data, lang
    ) -> str:
        base = market_data.get("base", "")
        rsi = ta_data.get("rsi")
        macd = ta_data.get("macd") or {}
        sr = ta_data.get("support_resistance") or {}
        fib = ta_data.get("fibonacci") or {}
        decision = ta_data.get("signal", "WAIT")
        news_quality = (news_data or {}).get("news_quality", "none")

        support = sr.get("nearest_support", 0)
        resistance = sr.get("nearest_resistance", 0)
        fib_levels = fib.get("levels") or {}
        fib_50 = fib_levels.get("0.5", 0)
        fib_618 = fib_levels.get("0.618", 0)
        fib_382 = fib_levels.get("0.382", 0)

        support_str = f"{support:.4f}" if support else "н/д"
        resistance_str = f"{resistance:.4f}" if resistance else "н/д"
        fib_50_str = f"{fib_50:.4f}" if fib_50 else "н/д"
        fib_618_str = f"{fib_618:.4f}" if fib_618 else "н/д"
        fib_382_str = f"{fib_382:.4f}" if fib_382 else "н/д"

        macd_crossover = macd.get("crossover", "")

        if lang == "ru":
            if "LONG" in decision or "TRADE" in decision:
                high = [
                    "удержание цены выше MA20 с подтверждением объёма",
                    f"пробой сопротивления {resistance_str} на повышенном объёме",
                ]
                medium = [
                    "RSI держится выше 50 без перегрева",
                    "MACD продолжает бычий импульс",
                ]
                if fib_618:
                    medium.append(f"удержание выше Fib 0.618: {fib_618_str}")
                low = [
                    "позитивные новости без немедленной реакции цены",
                    "рост интереса в соцсетях без on-chain подтверждения",
                ]

            elif "SHORT" in decision:
                high = [
                    f"закрепление ниже поддержки {support_str}",
                    "рост объёма на падении",
                ]
                medium = [
                    "MACD усиливает медвежий histogram",
                    f"потеря уровня Fib 0.382: {fib_382_str}",
                ]
                if rsi and rsi < 35:
                    medium.append(
                        f"RSI {rsi:.0f} — близко к перепроданности, осторожно шортить"
                    )
                low = [
                    "медвежьи публикации без on-chain подтверждения",
                    "общий негативный фон без специфики по активу",
                ]

            else:
                # WAIT
                high = [
                    f"пробой и закрепление выше сопротивления {resistance_str}",
                    f"потеря поддержки {support_str} с высоким объёмом",
                ]
                medium = []
                if fib_50:
                    medium.append(f"возврат выше Fib 0.5: {fib_50_str}")
                if rsi:
                    if rsi < 40:
                        medium.append(f"RSI выходит выше 40–45 (сейчас {rsi:.0f})")
                    elif rsi > 60:
                        medium.append(f"RSI опускается ниже 60 (сейчас {rsi:.0f})")
                    else:
                        medium.append(f"RSI {rsi:.0f} — нейтральная зона")
                if macd_crossover == "golden_cross":
                    medium.append("подтверждение золотого пересечения MACD")
                elif macd_crossover == "death_cross":
                    medium.append("подтверждение пересечения смерти MACD")
                else:
                    medium.append("MACD начинает разворот в нужную сторону")
                low = [
                    "нейтральные новости без реакции цены",
                    "слабые публикации в соцсетях без объёма",
                ]
                if news_quality == "none":
                    low.append(f"появление значимых новостей по {base}")

            result = "🔴 High impact:\n"
            result += "\n".join(f"— {t}" for t in high[:2])
            result += "\n\n🟡 Medium:\n"
            result += "\n".join(f"— {t}" for t in medium[:3])
            result += "\n\n🟢 Low:\n"
            result += "\n".join(f"— {t}" for t in low[:2])

        else:
            if "LONG" in decision or "TRADE" in decision:
                high = [
                    "price holds above MA20 with volume confirmation",
                    f"breakout above resistance {resistance_str} on high volume",
                ]
                medium = [
                    "RSI stays above 50 without overheating",
                    "MACD continues bullish momentum",
                ]
                if fib_618:
                    medium.append(f"hold above Fib 0.618: {fib_618_str}")
                low = [
                    "positive news without immediate price reaction",
                    "social media interest without on-chain confirmation",
                ]

            elif "SHORT" in decision:
                high = [
                    f"close below support {support_str}",
                    "volume surge on down move",
                ]
                medium = [
                    "MACD strengthens bearish histogram",
                    f"loss of Fib 0.382: {fib_382_str}",
                ]
                if rsi and rsi < 35:
                    medium.append(
                        f"RSI {rsi:.0f} — near oversold, careful shorting"
                    )
                low = [
                    "bearish publications without on-chain confirmation",
                    "general negative sentiment without asset specifics",
                ]

            else:
                # WAIT
                high = [
                    f"breakout and close above resistance {resistance_str}",
                    f"loss of support {support_str} on high volume",
                ]
                medium = []
                if fib_50:
                    medium.append(f"recovery above Fib 0.5: {fib_50_str}")
                if rsi:
                    if rsi < 40:
                        medium.append(f"RSI breaks above 40–45 (now {rsi:.0f})")
                    elif rsi > 60:
                        medium.append(f"RSI drops below 60 (now {rsi:.0f})")
                    else:
                        medium.append(f"RSI {rsi:.0f} — neutral zone")
                if macd_crossover == "golden_cross":
                    medium.append("MACD golden cross confirmation")
                elif macd_crossover == "death_cross":
                    medium.append("MACD death cross confirmation")
                else:
                    medium.append("MACD starts reversing in expected direction")
                low = [
                    "neutral news without price reaction",
                    "weak social media posts without volume",
                ]
                if news_quality == "none":
                    low.append(f"significant {base} news appearing")

            result = "🔴 High impact:\n"
            result += "\n".join(f"— {t}" for t in high[:2])
            result += "\n\n🟡 Medium:\n"
            result += "\n".join(f"— {t}" for t in medium[:3])
            result += "\n\n🟢 Low:\n"
            result += "\n".join(f"— {t}" for t in low[:2])

        return result

    # ═══════════════════════════════════════════
    # CONCLUSION
    # ═══════════════════════════════════════════

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
