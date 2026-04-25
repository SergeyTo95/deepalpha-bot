import re
from typing import Optional


def _parse_prob(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r'([\d.]+)%', value)
        if m:
            return float(m.group(1))
        m = re.search(r'([\d.]+)', value)
        if m:
            return float(m.group(1))
    return None


def detect_mispricing(
    model_prob: float,
    market_prob: float,
    market_leader: str = "Yes",
) -> dict:
    delta = abs(model_prob - market_prob)
    opposite = "No" if market_leader == "Yes" else "Yes"

    if delta < 5:
        edge = "NONE"
    elif delta < 10:
        edge = "WEAK"
    elif delta < 20:
        edge = "MODERATE"
    else:
        edge = "STRONG"

    if edge == "NONE":
        message = f"Δ: {delta:.1f}%"
        interpretation = "Рынок оценен справедливо. Модель и рынок сходятся."
        alpha_direction = None
    elif model_prob > market_prob:
        message = f"Модель выше рынка на {delta:.1f}%"
        interpretation = (
            f"Рынок недооценивает {market_leader} "
            f"({market_prob:.1f}% на рынке vs {model_prob:.1f}% по модели). "
            f"Потенциальная альфа в сторону {market_leader}."
        )
        alpha_direction = market_leader
    else:
        message = f"Модель ниже рынка на {delta:.1f}%"
        interpretation = (
            f"Рынок переоценивает {market_leader} "
            f"({market_prob:.1f}% на рынке vs {model_prob:.1f}% по модели). "
            f"Потенциальная альфа в сторону {opposite}."
        )
        alpha_direction = opposite

    return {
        "delta": round(delta, 2),
        "edge": edge,
        "message": message,
        "interpretation": interpretation,
        "alpha_direction": alpha_direction,
        "market_leader": market_leader,
        "opposite": opposite,
    }


def build_mispricing_block(
    model_prob: float,
    market_prob: float,
    lang: str = "ru",
    market_leader: str = "Yes",
) -> str:
    result = detect_mispricing(model_prob, market_prob, market_leader)
    delta = result["delta"]
    edge = result["edge"]
    interpretation = result["interpretation"]

    if lang == "ru":
        if edge == "NONE":
            return (
                "💣 Mispricing Signal:\n"
                f"Расхождения нет (Δ: {delta:.1f}%).\n\n"
                "📌 Интерпретация:\n"
                "Рынок эффективен — модель и рынок дают одинаковую оценку. "
                "Альфа отсутствует. "
                "Действие: ждать новых данных, мониторить триггеры."
            )
        elif edge == "WEAK":
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: СЛАБЫЙ\n"
                "📌 Интерпретация:\n"
                f"{interpretation}\n"
                "Расхождение есть, но небольшое. "
                "Для действия нужен подтверждающий триггер — без него риск не оправдан."
            )
        elif edge == "MODERATE":
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: УМЕРЕННЫЙ\n"
                "📌 Интерпретация:\n"
                f"{interpretation}\n"
                "Рынок может быть неэффективен. "
                "Проверь ликвидность — убедись что расхождение не вызвано тонким рынком."
            )
        else:
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: СИЛЬНЫЙ\n"
                "📌 Интерпретация:\n"
                f"{interpretation}\n"
                "Значительное расхождение — потенциальная alpha-зона. "
                "Высокий риск: проверь источники, объём и скрытые риски перед входом."
            )
    else:
        if edge == "NONE":
            return (
                "💣 Mispricing Signal:\n"
                f"No divergence (Δ: {delta:.1f}%).\n\n"
                "📌 Interpretation:\n"
                "Market is efficient — model and market agree. "
                "No alpha. "
                "Action: wait for new data, monitor triggers."
            )
        elif edge == "WEAK":
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: WEAK\n"
                "📌 Interpretation:\n"
                f"{interpretation}\n"
                "Small divergence — needs confirming trigger before acting. "
                "Risk outweighs potential gain without it."
            )
        elif edge == "MODERATE":
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: MODERATE\n"
                "📌 Interpretation:\n"
                f"{interpretation}\n"
                "Market may be inefficient. "
                "Check liquidity — confirm divergence isn't from thin market."
            )
        else:
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: STRONG\n"
                "📌 Interpretation:\n"
                f"{interpretation}\n"
                "Large divergence — potential alpha zone. "
                "High risk: verify sources, volume and hidden risks before entry."
            )


def build_market_psychology(
    probability: float,
    outcome: str = "",
    lang: str = "ru",
) -> str:
    if lang == "ru":
        if probability >= 80:
            text = (
                f"Сильный консенсус ({probability:.1f}%). "
                "Рынок уверен — ставить против крайне рискованно. "
                "Большинство доступной информации уже заложено в цену."
            )
        elif probability >= 65:
            text = (
                f"Рынок склоняется к исходу ({probability:.1f}%), "
                "но альтернативный сценарий не закрыт. "
                "Высокая чувствительность к новостному фону."
            )
        elif probability >= 55:
            text = (
                f"Умеренный перевес ({probability:.1f}%). "
                "Участники не уверены — ситуация остаётся открытой. "
                "Любой триггер может резко изменить баланс."
            )
        elif probability >= 45:
            text = (
                f"Рынок сбалансирован (~{probability:.1f}%). "
                "Максимальная неопределённость — оба исхода равновероятны. "
                "Рынок ждёт катализатора."
            )
        else:
            text = (
                f"Рынок против основного исхода ({probability:.1f}%). "
                "Участники не верят в реализацию. "
                "Для разворота нужен сильный внешний шок."
            )
        return f"🧠 Market Psychology:\n{text}"
    else:
        if probability >= 80:
            text = (
                f"Strong consensus ({probability:.1f}%). "
                "Market is confident — fading is extremely risky. "
                "Most available information is priced in."
            )
        elif probability >= 65:
            text = (
                f"Market leans toward outcome ({probability:.1f}%) "
                "but alternative isn't ruled out. "
                "Highly sensitive to news flow."
            )
        elif probability >= 55:
            text = (
                f"Moderate edge ({probability:.1f}%). "
                "Participants are uncertain — situation remains open. "
                "Any trigger could sharply shift the balance."
            )
        elif probability >= 45:
            text = (
                f"Market balanced (~{probability:.1f}%). "
                "Maximum uncertainty — both outcomes equally likely. "
                "Market is waiting for a catalyst."
            )
        else:
            text = (
                f"Market against main outcome ({probability:.1f}%). "
                "Participants don't believe in realisation. "
                "Strong external shock needed for reversal."
            )
        return f"🧠 Market Psychology:\n{text}"


def build_alpha_note(
    model_prob: float,
    market_prob: float,
    market_balance: str = "",
    lang: str = "ru",
    market_leader: str = "Yes",
) -> str:
    delta = abs(model_prob - market_prob)
    opposite = "No" if market_leader == "Yes" else "Yes"

    if model_prob > market_prob:
        alpha_side = market_leader
    else:
        alpha_side = opposite

    if lang == "ru":
        if delta < 5:
            note = (
                "Альфа отсутствует. "
                "Модель подтверждает рыночную оценку — расхождения нет. "
                "Стратегия ожидания: не входить против рынка, "
                "мониторить триггеры и реагировать первым при появлении новых данных."
            )
        elif delta < 10:
            note = (
                f"Слабая альфа (Δ {delta:.1f}%) в сторону {alpha_side}. "
                "Расхождение есть, но недостаточное для уверенного действия. "
                f"Возможность в {alpha_side} при подтверждении триггером — "
                "без него риск превышает потенциальную прибыль. "
                "Стратегия: наблюдать, не входить без сигнала."
            )
        elif delta < 20:
            note = (
                f"Умеренная альфа (Δ {delta:.1f}%) в сторону {alpha_side}. "
                f"Рынок может быть неэффективен — возможность в {alpha_side}. "
                "Условия входа: дождаться отката от максимума, "
                "подтверждение от 1–2 независимых источников. "
                "Размер позиции умеренный, риск ограничить."
            )
        else:
            note = (
                f"Сильная потенциальная альфа (Δ {delta:.1f}%) в сторону {alpha_side}. "
                "Высокий риск: большие расхождения часто указывают на тонкий рынок "
                "или скрытую информацию. "
                "Обязательно: проверь ликвидность, объём и источники расхождения "
                "перед любым действием."
            )
        return f"🟡 Alpha Note:\n{note}"
    else:
        if delta < 5:
            note = (
                "No alpha. "
                "Model confirms market pricing — no divergence. "
                "Waiting strategy: don't fade the market, "
                "monitor triggers and be first to react when new data appears."
            )
        elif delta < 10:
            note = (
                f"Weak alpha (Δ {delta:.1f}%) toward {alpha_side}. "
                "Divergence exists but not strong enough for confident action. "
                f"Opportunity in {alpha_side} on trigger confirmation — "
                "without it risk exceeds potential gain. "
                "Strategy: observe, do not enter without signal."
            )
        elif delta < 20:
            note = (
                f"Moderate alpha (Δ {delta:.1f}%) toward {alpha_side}. "
                f"Market may be inefficient — opportunity in {alpha_side}. "
                "Entry conditions: wait for pullback from peak, "
                "confirmation from 1–2 independent sources. "
                "Moderate position size, limit risk."
            )
        else:
            note = (
                f"Strong potential alpha (Δ {delta:.1f}%) toward {alpha_side}. "
                "High risk: large divergences often indicate thin market "
                "or hidden information. "
                "Must verify: liquidity, volume and source of divergence "
                "before any action."
            )
        return f"🟡 Alpha Note:\n{note}"


def build_trade_insight(
    model_prob: float,
    market_prob: float,
    market_balance: str = "",
    category: str = "",
    lang: str = "ru",
    market_leader: str = "Yes",
) -> str:
    """
    Δ < 5  → альфы нет → НЕ предлагать вход
    Δ 5-10 → слабая альфа → вход только при триггере
    Δ 10-20 → умеренная альфа → вход через откаты, не на максимумах
    Δ > 20 → сильная альфа → высокий риск, проверка

    model_prob > market_prob → рынок недооценивает → торгуем за market_leader
    model_prob < market_prob → рынок переоценивает → торгуем за opposite
    """
    delta = abs(model_prob - market_prob)
    opposite = "No" if market_leader == "Yes" else "Yes"
    alt_prob = round(100 - market_prob, 1)
    has_alpha = delta >= 5

    if model_prob >= market_prob:
        trade_side = market_leader
    else:
        trade_side = opposite

    if lang == "ru":
        if not has_alpha:
            # Δ < 5 — альфы нет
            if market_balance == "strong_consensus":
                insight = (
                    f"Расхождение модели и рынка минимально (Δ {delta:.1f}%). "
                    f"Рынок эффективен — альфы нет. "
                    f"Консенсус {market_leader} ({market_prob:.1f}%) сильный."
                )
                strategy = (
                    "— не входить: нет преимущества перед рынком\n"
                    "— мониторить триггеры способные изменить оценку\n"
                    "— реагировать первым при появлении значимого события"
                )
                entry = "— только при появлении нового сильного триггера изменившего расклад"
                risk = "— без триггера вход даёт нулевое преимущество"
            elif market_balance in ("balanced", "slight_lean"):
                insight = (
                    f"Расхождение минимально (Δ {delta:.1f}%). "
                    f"Рынок нестабилен ({market_leader}: {market_prob:.1f}% | "
                    f"{opposite}: {alt_prob}%) — но альфы нет."
                )
                strategy = (
                    "— не входить: нет преимущества\n"
                    "— ждать первого значимого триггера\n"
                    "— наблюдать кто первым сдвинет рынок"
                )
                entry = "— при пробое и удержании любым исходом выше 60%"
                risk = "— без чёткого сигнала вход равносилен монете"
            else:
                insight = (
                    f"Расхождение минимально (Δ {delta:.1f}%). "
                    f"Модель подтверждает рынок — прямой альфы нет."
                )
                strategy = (
                    "— не входить: нет edge\n"
                    "— мониторить новостной фон\n"
                    "— ждать триггера который рынок ещё не учёл"
                )
                entry = "— только при появлении новых данных изменивших оценку"
                risk = "— рынок уже правильно оценивает ситуацию"

        else:
            if market_balance == "strong_consensus":
                if model_prob > market_prob:
                    insight = (
                        f"Рынок недооценивает {market_leader} ({market_prob:.1f}%), "
                        f"модель видит {model_prob:.1f}%. "
                        f"Расхождение {delta:.1f}% — потенциал в {trade_side}. "
                        "Консенсус сильный — входить только на откатах."
                    )
                    if delta < 20:
                        strategy = (
                            f"— рассматривать вход в {trade_side} при откатах от максимума\n"
                            f"— не заходить на локальных максимумах\n"
                            "— умеренный размер позиции"
                        )
                        entry = (
                            f"— если цена {trade_side} откатится к "
                            f"{market_prob - 8:.0f}–{market_prob - 5:.0f}%\n"
                            "— при подтверждении события в пользу этого исхода"
                        )
                    else:
                        strategy = (
                            f"— рассматривать {trade_side} только на глубоких откатах\n"
                            "— не входить вблизи текущей цены\n"
                            "— минимальный размер позиции из-за высокого риска"
                        )
                        entry = (
                            f"— при откате {trade_side} к {market_prob - 12:.0f}% или ниже\n"
                            "— при появлении сильного подтверждающего события"
                        )
                    risk = (
                        f"— разворотный триггер может быстро сдвинуть к {alt_prob}%\n"
                        "— консенсус может быть завышен"
                    )
                else:
                    insight = (
                        f"Рынок переоценивает {market_leader} ({market_prob:.1f}%), "
                        f"модель видит {model_prob:.1f}%. "
                        f"Расхождение {delta:.1f}% — потенциал в {trade_side}. "
                        "Консенсус сильный — высокий риск входа против рынка."
                    )
                    strategy = (
                        f"— не входить в {trade_side} без сильного разворотного триггера\n"
                        "— ждать подтверждения смены сентимента\n"
                        "— риск высокий — размер позиции минимальный"
                    )
                    entry = (
                        f"— если {market_leader} начнёт падать ниже {market_prob - 10:.0f}%\n"
                        "— при появлении конкретного события меняющего расклад"
                    )
                    risk = (
                        f"— консенсус может сохраниться, {market_leader} продолжит рост\n"
                        "— потеря всей позиции при отсутствии триггера"
                    )

            elif market_balance == "moderate_consensus":
                if model_prob > market_prob:
                    insight = (
                        f"Умеренный перевес {market_leader} ({market_prob:.1f}%), "
                        f"модель даёт {model_prob:.1f}%. "
                        f"Расхождение {delta:.1f}% — рынок недооценивает {trade_side}."
                    )
                    strategy = (
                        f"— рассматривать вход в {trade_side} при откатах от текущих уровней\n"
                        f"— не заходить на локальных максимумах вероятности\n"
                        "— умеренный размер позиции"
                    )
                    entry = (
                        f"— при откате {trade_side} к {market_prob - 5:.0f}–{market_prob - 3:.0f}%\n"
                        "— при выходе подтверждающего события"
                    )
                    risk = (
                        "— смена сентимента или неожиданные данные могут развернуть\n"
                        f"— если {market_leader} упадёт ниже {market_prob - 10:.0f}% — пересмотреть"
                    )
                else:
                    insight = (
                        f"Рынок переоценивает {market_leader} ({market_prob:.1f}%), "
                        f"модель видит {model_prob:.1f}%. "
                        f"Расхождение {delta:.1f}% — потенциал в {trade_side}."
                    )
                    strategy = (
                        f"— ждать сигнала для входа в {trade_side}\n"
                        "— не входить до появления подтверждающего триггера\n"
                        "— умеренный размер позиции при подтверждении"
                    )
                    entry = (
                        f"— если {market_leader} начнёт падать ниже {market_prob - 5:.0f}%\n"
                        "— при новости меняющей расклад"
                    )
                    risk = (
                        f"— {market_leader} может продолжить рост без явного триггера\n"
                        "— позиция убыточна без подтверждения"
                    )

            elif market_balance in ("balanced", "slight_lean"):
                insight = (
                    f"Рынок нестабилен ({market_leader}: {market_prob:.1f}% | "
                    f"{opposite}: {alt_prob}%). "
                    f"Расхождение {delta:.1f}% в сторону {trade_side} — "
                    "без чёткого сигнала риск высокий."
                )
                strategy = (
                    f"— наблюдать за {trade_side}\n"
                    "— входить только при пробое и закреплении выше 60%\n"
                    "— без сигнала — не входить"
                )
                entry = (
                    f"— при пробое {trade_side} выше 60% с удержанием\n"
                    "— при официальном заявлении или событии в пользу исхода"
                )
                risk = "— рынок может резко пойти в любую сторону без предупреждения"

            else:
                insight = (
                    f"Рынок против основного исхода ({market_leader}: {market_prob:.1f}%). "
                    f"Расхождение {delta:.1f}% — потенциал в {trade_side}, "
                    "но без катализатора вход рискованный."
                )
                strategy = (
                    f"— наблюдать за {trade_side}\n"
                    "— входить только при явном подтверждении разворота\n"
                    "— минимальный размер позиции"
                )
                entry = (
                    "— при появлении подтверждённого события меняющего расклад\n"
                    f"— при пробое {market_leader} ниже {market_prob - 10:.0f}%"
                )
                risk = (
                    f"— {market_leader} может продолжить движение в текущем направлении\n"
                    "— потеря позиции без триггера"
                )

        return (
            "📊 Trade Insight:\n"
            f"{insight}\n\n"
            "📌 Стратегия:\n"
            f"{strategy}\n\n"
            "📌 Условия входа:\n"
            f"{entry}\n\n"
            "📌 Риск:\n"
            f"{risk}"
        )

    else:
        # English
        if not has_alpha:
            if market_balance == "strong_consensus":
                insight = (
                    f"Minimal model-market divergence (Δ {delta:.1f}%). "
                    f"Market is efficient — no alpha. "
                    f"Strong {market_leader} consensus ({market_prob:.1f}%)."
                )
                strategy = (
                    "— do not enter: no edge over the market\n"
                    "— monitor triggers that could change the pricing\n"
                    "— be first to react when a significant event appears"
                )
                entry = "— only if a strong new trigger materially changes the setup"
                risk = "— without trigger, entry gives zero advantage"
            elif market_balance in ("balanced", "slight_lean"):
                insight = (
                    f"Minimal divergence (Δ {delta:.1f}%). "
                    f"Market unstable ({market_leader}: {market_prob:.1f}% | "
                    f"{opposite}: {alt_prob}%) — but no alpha."
                )
                strategy = (
                    "— do not enter: no edge\n"
                    "— wait for first significant trigger\n"
                    "— watch who moves the market first"
                )
                entry = "— on break and hold above 60% in either direction"
                risk = "— without clear signal, entry is equivalent to a coin flip"
            else:
                insight = (
                    f"Minimal divergence (Δ {delta:.1f}%). "
                    "Model confirms market — no direct alpha."
                )
                strategy = (
                    "— do not enter: no edge\n"
                    "— monitor news flow\n"
                    "— wait for trigger market hasn't priced in yet"
                )
                entry = "— only on new data that materially changes the assessment"
                risk = "— market is already pricing the situation correctly"

        else:
            if market_balance == "strong_consensus":
                if model_prob > market_prob:
                    insight = (
                        f"Market underpricing {market_leader} ({market_prob:.1f}%), "
                        f"model sees {model_prob:.1f}%. "
                        f"{delta:.1f}% divergence — opportunity in {trade_side}. "
                        "Strong consensus — enter on pullbacks only."
                    )
                    if delta < 20:
                        strategy = (
                            f"— look for entry in {trade_side} on pullbacks from peak\n"
                            "— do not enter at local probability highs\n"
                            "— moderate position size"
                        )
                        entry = (
                            f"— if {trade_side} pulls back to "
                            f"{market_prob - 8:.0f}–{market_prob - 5:.0f}%\n"
                            "— on confirmation of supporting event"
                        )
                    else:
                        strategy = (
                            f"— consider {trade_side} only on deep pullbacks\n"
                            "— avoid entering near current price level\n"
                            "— minimum position size due to high risk"
                        )
                        entry = (
                            f"— on {trade_side} pullback to {market_prob - 12:.0f}% or below\n"
                            "— on strong confirming event"
                        )
                    risk = (
                        f"— reversal trigger could quickly push to {alt_prob}%\n"
                        "— consensus may be inflated"
                    )
                else:
                    insight = (
                        f"Market overpricing {market_leader} ({market_prob:.1f}%), "
                        f"model sees {model_prob:.1f}%. "
                        f"{delta:.1f}% divergence — opportunity in {trade_side}. "
                        "Strong consensus — high risk fading the market."
                    )
                    strategy = (
                        f"— do not enter {trade_side} without strong reversal trigger\n"
                        "— wait for confirmed sentiment change\n"
                        "— minimum position size if entering"
                    )
                    entry = (
                        f"— if {market_leader} drops below {market_prob - 10:.0f}%\n"
                        "— on specific event changing the setup"
                    )
                    risk = (
                        f"— consensus may hold, {market_leader} continues rising\n"
                        "— full position loss without trigger"
                    )

            elif market_balance == "moderate_consensus":
                if model_prob > market_prob:
                    insight = (
                        f"Moderate {market_leader} edge ({market_prob:.1f}%), "
                        f"model gives {model_prob:.1f}%. "
                        f"{delta:.1f}% divergence — market underpricing {trade_side}."
                    )
                    strategy = (
                        f"— look for entry in {trade_side} on pullbacks from current levels\n"
                        "— do not enter at local probability highs\n"
                        "— moderate position size"
                    )
                    entry = (
                        f"— on {trade_side} pullback to "
                        f"{market_prob - 5:.0f}–{market_prob - 3:.0f}%\n"
                        "— on confirming event"
                    )
                    risk = (
                        "— sentiment shift or surprise data could reverse\n"
                        f"— reassess if {market_leader} drops below {market_prob - 10:.0f}%"
                    )
                else:
                    insight = (
                        f"Market overpricing {market_leader} ({market_prob:.1f}%), "
                        f"model sees {model_prob:.1f}%. "
                        f"{delta:.1f}% divergence — opportunity in {trade_side}."
                    )
                    strategy = (
                        f"— wait for entry signal in {trade_side}\n"
                        "— don't enter without confirming trigger\n"
                        "— moderate size on confirmation"
                    )
                    entry = (
                        f"— if {market_leader} starts falling below {market_prob - 5:.0f}%\n"
                        "— on news changing the setup"
                    )
                    risk = (
                        f"— {market_leader} may continue rising without trigger\n"
                        "— position unprofitable without confirmation"
                    )

            elif market_balance in ("balanced", "slight_lean"):
                insight = (
                    f"Market unstable ({market_leader}: {market_prob:.1f}% | "
                    f"{opposite}: {alt_prob}%). "
                    f"{delta:.1f}% divergence toward {trade_side} — "
                    "high risk without clear signal."
                )
                strategy = (
                    f"— watch {trade_side}\n"
                    "— enter only on break and hold above 60%\n"
                    "— no signal — no entry"
                )
                entry = (
                    f"— on {trade_side} break above 60% with hold\n"
                    "— on official statement or supporting event"
                )
                risk = "— market can move sharply in either direction without warning"

            else:
                insight = (
                    f"Market against main outcome ({market_leader}: {market_prob:.1f}%). "
                    f"{delta:.1f}% divergence — opportunity in {trade_side}, "
                    "but risky without catalyst."
                )
                strategy = (
                    f"— watch {trade_side}\n"
                    "— enter only on clear reversal confirmation\n"
                    "— minimum position size"
                )
                entry = (
                    "— on confirmed event changing the setup\n"
                    f"— on {market_leader} break below {market_prob - 10:.0f}%"
                )
                risk = (
                    f"— {market_leader} may continue in current direction\n"
                    "— position loss without trigger"
                )

        return (
            "📊 Trade Insight:\n"
            f"{insight}\n\n"
            "📌 Strategy:\n"
            f"{strategy}\n\n"
            "📌 Entry Conditions:\n"
            f"{entry}\n\n"
            "📌 Risk:\n"
            f"{risk}"
        )
