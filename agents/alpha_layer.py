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
    outcome: str = "",
) -> dict:
    delta = abs(model_prob - market_prob)

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
        interpretation = "Рынок оценен справедливо. Явного расхождения нет."
    elif model_prob > market_prob:
        message = f"Модель выше рынка на {delta:.1f}%"
        interpretation = (
            "Модель оценивает вероятность выше рынка — "
            "рынок может недооценивать этот исход."
        )
    else:
        message = f"Модель ниже рынка на {delta:.1f}%"
        interpretation = (
            "Модель оценивает вероятность ниже рынка — "
            "рынок может переоценивать этот исход."
        )

    return {
        "delta": round(delta, 2),
        "edge": edge,
        "message": message,
        "interpretation": interpretation,
    }


def build_mispricing_block(
    model_prob: float,
    market_prob: float,
    lang: str = "ru",
) -> str:
    result = detect_mispricing(model_prob, market_prob)
    delta = result["delta"]
    edge = result["edge"]

    if lang == "ru":
        if edge == "NONE":
            return (
                "💣 Mispricing Signal:\n"
                f"Явного расхождения нет (Δ: {delta:.1f}%).\n\n"
                "📌 Интерпретация:\n"
                "Рынок оценен справедливо — модель и рынок сходятся. "
                "Прямой альфы нет. Стратегия: не входить против рынка, "
                "мониторить триггеры и реагировать быстрее толпы."
            )
        elif edge == "WEAK":
            direction = "выше" if model_prob > market_prob else "ниже"
            opposite = "ниже" if model_prob > market_prob else "выше"
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: СЛАБЫЙ\n"
                "📌 Интерпретация:\n"
                f"Модель на {delta:.1f}% {direction} рыночной оценки. "
                "Расхождение есть, но недостаточное для уверенного действия. "
                f"Рынок может быть {opposite} справедливой цены — "
                "нужен новостной триггер для подтверждения позиции."
            )
        elif edge == "MODERATE":
            direction = "выше" if model_prob > market_prob else "ниже"
            side = "недооценивает" if model_prob > market_prob else "переоценивает"
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: УМЕРЕННЫЙ\n"
                "📌 Интерпретация:\n"
                f"Рынок {side} исход на {delta:.1f}%. "
                "Потенциальная неэффективность — рынок мог не учесть свежие данные. "
                "Проверь ликвидность и убедись что расхождение не вызвано тонким рынком."
            )
        else:
            direction = "выше" if model_prob > market_prob else "ниже"
            action = "Yes" if model_prob > market_prob else "No"
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: СИЛЬНЫЙ\n"
                "📌 Интерпретация:\n"
                f"Значительное расхождение {delta:.1f}% — модель видит {action} "
                f"существенно {direction} рыночной цены. "
                "Потенциальная alpha-зона, но высокий риск. "
                "Обязательно: проверь источники, ликвидность и нет ли скрытого события."
            )
    else:
        if edge == "NONE":
            return (
                "💣 Mispricing Signal:\n"
                f"No significant divergence (Δ: {delta:.1f}%).\n\n"
                "📌 Interpretation:\n"
                "Market is fairly priced — model and market agree. "
                "No direct alpha. Strategy: don't fade the market, "
                "monitor triggers and react faster than the crowd."
            )
        elif edge == "WEAK":
            direction = "above" if model_prob > market_prob else "below"
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: WEAK\n"
                "📌 Interpretation:\n"
                f"Model is {delta:.1f}% {direction} market. "
                "Divergence exists but not strong enough for confident action. "
                "Needs a news trigger for confirmation."
            )
        elif edge == "MODERATE":
            direction = "above" if model_prob > market_prob else "below"
            side = "underpricing" if model_prob > market_prob else "overpricing"
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: MODERATE\n"
                "📌 Interpretation:\n"
                f"Market may be {side} this outcome by {delta:.1f}%. "
                "Check liquidity and confirm divergence isn't caused by thin market."
            )
        else:
            direction = "above" if model_prob > market_prob else "below"
            action = "Yes" if model_prob > market_prob else "No"
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: STRONG\n"
                "📌 Interpretation:\n"
                f"Strong divergence {delta:.1f}% — model sees {action} significantly "
                f"{direction} market price. Potential alpha zone, but high risk. "
                "Verify sources, liquidity, and hidden event risk."
            )


def build_market_psychology(
    probability: float,
    outcome: str = "",
    lang: str = "ru",
) -> str:
    if lang == "ru":
        if probability >= 80:
            text = (
                f"Рынок показывает сильный консенсус ({probability:.1f}%). "
                "Участники уверены — ставить против крайне рискованно. "
                "Рынок уже учёл большинство доступной информации."
            )
        elif probability >= 65:
            text = (
                f"Рынок уверенно склоняется к исходу ({probability:.1f}%), "
                "но не закрывает альтернативный сценарий. "
                "Чувствителен к новостному фону — одна значимая новость "
                "может сдвинуть на 5–10%."
            )
        elif probability >= 55:
            text = (
                f"Рынок имеет умеренный перевес ({probability:.1f}%). "
                "Ситуация открытая — участники не уверены. "
                "Любой триггер может резко изменить баланс."
            )
        elif probability >= 45:
            text = (
                f"Рынок практически сбалансирован (~{probability:.1f}%). "
                "Максимальная неопределённость — оба исхода реальны. "
                "Рынок ждёт катализатора."
            )
        else:
            text = (
                f"Рынок против основного исхода ({probability:.1f}%). "
                "Участники не верят в реализацию — "
                "для разворота нужен сильный внешний шок."
            )
        return f"🧠 Market Psychology:\n{text}"
    else:
        if probability >= 80:
            text = (
                f"Strong consensus ({probability:.1f}%). "
                "Participants are confident — fading is extremely risky. "
                "Market has priced in most available information."
            )
        elif probability >= 65:
            text = (
                f"Market confidently leans toward outcome ({probability:.1f}%) "
                "but doesn't rule out the alternative. "
                "Sensitive to news flow — one significant headline "
                "could move it 5–10%."
            )
        elif probability >= 55:
            text = (
                f"Moderate edge ({probability:.1f}%). "
                "Situation is open — participants are uncertain. "
                "Any trigger could sharply shift the balance."
            )
        elif probability >= 45:
            text = (
                f"Market nearly balanced (~{probability:.1f}%). "
                "Maximum uncertainty — both outcomes are real. "
                "Market is waiting for a catalyst."
            )
        else:
            text = (
                f"Market against main outcome ({probability:.1f}%). "
                "Participants don't believe in realisation — "
                "strong external shock needed for reversal."
            )
        return f"🧠 Market Psychology:\n{text}"


def build_alpha_note(
    model_prob: float,
    market_prob: float,
    market_balance: str = "",
    lang: str = "ru",
) -> str:
    delta = abs(model_prob - market_prob)

    if lang == "ru":
        if delta < 5:
            if market_balance == "strong_consensus":
                note = (
                    "Альфа отсутствует — модель подтверждает рыночный консенсус. "
                    "Входить против рынка нет смысла. "
                    "Альфа появится только при резком внешнем событии — "
                    "быть готовым реагировать первым."
                )
            elif market_balance in ("balanced", "slight_lean"):
                note = (
                    "Прямой альфы нет, но рынок нестабилен. "
                    "Возможность появится при первом значимом триггере — "
                    "следи за новостями и реагируй быстрее толпы."
                )
            else:
                note = (
                    "Текущая позиция — следование рынку. "
                    "Альфа возможна только при появлении новых триггеров, "
                    "которые рынок ещё не учёл."
                )
        elif delta < 10:
            direction = "выше" if model_prob > market_prob else "ниже"
            outcome = "Yes" if model_prob > market_prob else "No"
            note = (
                f"Слабая альфа — модель на {delta:.1f}% {direction} рынка. "
                f"Возможность в {outcome}, но нужно подтверждение: "
                "жди новостного триггера перед входом. "
                "Без него риск выше потенциальной прибыли."
            )
        elif delta < 20:
            direction = "выше" if model_prob > market_prob else "ниже"
            outcome = "Yes" if model_prob > market_prob else "No"
            note = (
                f"Умеренная альфа — расхождение {delta:.1f}% в пользу {outcome}. "
                "Рынок может быть неэффективен. "
                "Действие: рассмотреть позицию при подтверждении от 1–2 источников. "
                "Размер позиции — умеренный, риск ограничить."
            )
        else:
            direction = "выше" if model_prob > market_prob else "ниже"
            outcome = "Yes" if model_prob > market_prob else "No"
            note = (
                f"Сильная потенциальная альфа — расхождение {delta:.1f}% в пользу {outcome}. "
                "Высокий риск: такие расхождения часто говорят о скрытой информации "
                "или тонком рынке. "
                "Проверь ликвидность, объём и источники перед входом."
            )
        return f"🟡 Alpha Note:\n{note}"
    else:
        if delta < 5:
            if market_balance == "strong_consensus":
                note = (
                    "No alpha — model confirms market consensus. "
                    "No reason to fade the market. "
                    "Alpha only appears on sharp external event — "
                    "be ready to react first."
                )
            elif market_balance in ("balanced", "slight_lean"):
                note = (
                    "No direct alpha, but market is unstable. "
                    "Opportunity appears on first significant trigger — "
                    "watch news and react faster than the crowd."
                )
            else:
                note = (
                    "Current position: follow the market. "
                    "Alpha only possible when new triggers emerge "
                    "that the market hasn't priced in yet."
                )
        elif delta < 10:
            direction = "above" if model_prob > market_prob else "below"
            outcome = "Yes" if model_prob > market_prob else "No"
            note = (
                f"Weak alpha — model {delta:.1f}% {direction} market. "
                f"Opportunity in {outcome}, but needs confirmation: "
                "wait for a news trigger before entering. "
                "Without it, risk outweighs potential gain."
            )
        elif delta < 20:
            direction = "above" if model_prob > market_prob else "below"
            outcome = "Yes" if model_prob > market_prob else "No"
            note = (
                f"Moderate alpha — {delta:.1f}% divergence in favour of {outcome}. "
                "Market may be inefficient. "
                "Action: consider position on confirmation from 1–2 sources. "
                "Moderate size, limit risk."
            )
        else:
            direction = "above" if model_prob > market_prob else "below"
            outcome = "Yes" if model_prob > market_prob else "No"
            note = (
                f"Strong potential alpha — {delta:.1f}% divergence in favour of {outcome}. "
                "High risk: large divergences often signal hidden info or thin market. "
                "Check liquidity, volume and sources before entering."
            )
        return f"🟡 Alpha Note:\n{note}"


def build_trade_insight(
    model_prob: float,
    market_prob: float,
    market_balance: str = "",
    category: str = "",
    lang: str = "ru",
) -> str:
    delta = abs(model_prob - market_prob)
    leader_side = "Yes" if market_prob >= 50 else "No"
    leader_prob = market_prob if market_prob >= 50 else 100 - market_prob
    alt_prob = 100 - leader_prob

    if lang == "ru":
        if market_balance == "strong_consensus":
            insight = (
                f"Рынок сильно смещён в сторону {leader_side} ({leader_prob:.1f}%). "
                "Прямого value для входа против рынка нет — консенсус слишком сильный."
            )
            strategy = "— не входить против рынка\n— ждать отката или новостного триггера"
            if leader_prob >= 85:
                entry = (
                    f"— если цена {leader_side} откатится к {leader_prob - 10:.0f}–{leader_prob - 7:.0f}%\n"
                    "— при подтверждении нового негативного/позитивного события"
                )
            else:
                entry = (
                    f"— при откате к {leader_prob - 8:.0f}–{leader_prob - 5:.0f}%\n"
                    "— если выйдут данные подтверждающие сценарий"
                )
            risk = f"— разворотный триггер может быстро сдвинуть рынок к {alt_prob:.1f}%"

        elif market_balance == "moderate_consensus":
            insight = (
                f"Умеренный перевес {leader_side} ({leader_prob:.1f}%). "
                "Есть небольшое окно для входа — рынок не перегрет."
            )
            if delta >= 10:
                strategy = "— рассмотреть вход при подтверждении триггера\n— размер позиции — умеренный"
                entry = (
                    f"— при удержании цены {leader_side} выше {leader_prob - 5:.0f}%\n"
                    "— при выходе подтверждающей новости"
                )
            else:
                strategy = "— ждать более чёткого сигнала\n— наблюдать за новостным фоном"
                entry = (
                    f"— если цена {leader_side} вырастет выше {leader_prob + 3:.0f}%\n"
                    "— при появлении конкретного катализатора"
                )
            risk = "— смена сентимента или неожиданные данные могут быстро развернуть рынок"

        elif market_balance in ("balanced", "slight_lean"):
            insight = (
                f"Рынок нестабилен (~{leader_prob:.1f}% за {leader_side}). "
                "Входить сейчас — высокий риск без чёткого сигнала."
            )
            strategy = "— не входить до появления триггера\n— следить за первыми значимыми новостями"
            entry = (
                "— при пробое выше 60% в любую сторону\n"
                "— при официальном заявлении или новом событии"
            )
            risk = "— рынок может резко пойти в любую сторону без предупреждения"

        else:
            insight = (
                f"Рынок против основного исхода ({leader_prob:.1f}%). "
                "Входить в противоположную сторону слишком рискованно без катализатора."
            )
            strategy = "— игнорировать рынок до появления разворотного события\n— наблюдать"
            entry = (
                "— при появлении подтверждённого позитивного/негативного события\n"
                f"— при смене лидера выше {100 - leader_prob + 5:.0f}%"
            )
            risk = "— рынок может продолжить движение в текущем направлении"

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
        if market_balance == "strong_consensus":
            insight = (
                f"Market strongly skewed toward {leader_side} ({leader_prob:.1f}%). "
                "No direct value for fading — consensus is too strong."
            )
            strategy = "— do not fade the market\n— wait for pullback or news trigger"
            if leader_prob >= 85:
                entry = (
                    f"— if {leader_side} price pulls back to {leader_prob - 10:.0f}–{leader_prob - 7:.0f}%\n"
                    "— on confirmation of new negative/positive event"
                )
            else:
                entry = (
                    f"— on pullback to {leader_prob - 8:.0f}–{leader_prob - 5:.0f}%\n"
                    "— if confirming data emerges"
                )
            risk = f"— reversal trigger could quickly push market to {alt_prob:.1f}%"

        elif market_balance == "moderate_consensus":
            insight = (
                f"Moderate {leader_side} edge ({leader_prob:.1f}%). "
                "Small entry window — market is not overheated."
            )
            if delta >= 10:
                strategy = "— consider entry on trigger confirmation\n— moderate position size"
                entry = (
                    f"— if {leader_side} holds above {leader_prob - 5:.0f}%\n"
                    "— on confirming news"
                )
            else:
                strategy = "— wait for clearer signal\n— watch news flow"
                entry = (
                    f"— if {leader_side} rises above {leader_prob + 3:.0f}%\n"
                    "— on specific catalyst"
                )
            risk = "— sentiment shift or surprise data could reverse market quickly"

        elif market_balance in ("balanced", "slight_lean"):
            insight = (
                f"Market unstable (~{leader_prob:.1f}% for {leader_side}). "
                "Entering now is high risk without clear signal."
            )
            strategy = "— do not enter until trigger appears\n— watch for first significant news"
            entry = (
                "— on break above 60% in either direction\n"
                "— on official statement or new event"
            )
            risk = "— market can move sharply in either direction without warning"

        else:
            insight = (
                f"Market against main outcome ({leader_prob:.1f}%). "
                "Fading without catalyst is too risky."
            )
            strategy = "— ignore until reversal event appears\n— observe only"
            entry = (
                "— on confirmed positive/negative event\n"
                f"— on leader flip above {100 - leader_prob + 5:.0f}%"
            )
            risk = "— market may continue in current direction"

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
