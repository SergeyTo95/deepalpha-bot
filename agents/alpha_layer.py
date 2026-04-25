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
    """
    model_prob  — вероятность лидирующего исхода по модели
    market_prob — вероятность лидирующего исхода по рынку
    market_leader — "Yes" или "No" — какой исход лидирует на рынке

    model_prob > market_prob → рынок НЕДООЦЕНИВАЕТ лидера → альфа в сторону market_leader
    model_prob < market_prob → рынок ПЕРЕОЦЕНИВАЕТ лидера → альфа против market_leader
    """
    delta = abs(model_prob - market_prob)

    if delta < 5:
        edge = "NONE"
    elif delta < 10:
        edge = "WEAK"
    elif delta < 20:
        edge = "MODERATE"
    else:
        edge = "STRONG"

    opposite = "No" if market_leader == "Yes" else "Yes"

    if edge == "NONE":
        message = f"Δ: {delta:.1f}%"
        interpretation = "Рынок оценен справедливо. Модель и рынок сходятся."
        alpha_direction = None
    elif model_prob > market_prob:
        # Модель выше рынка → рынок недооценивает лидера
        message = f"Модель выше рынка на {delta:.1f}%"
        interpretation = (
            f"Рынок недооценивает {market_leader} ({market_prob:.1f}% vs {model_prob:.1f}% по модели). "
            f"Потенциальная альфа в сторону {market_leader}."
        )
        alpha_direction = market_leader
    else:
        # Модель ниже рынка → рынок переоценивает лидера
        message = f"Модель ниже рынка на {delta:.1f}%"
        interpretation = (
            f"Рынок переоценивает {market_leader} ({market_prob:.1f}% vs {model_prob:.1f}% по модели). "
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
    alpha_direction = result["alpha_direction"]
    opposite = "No" if market_leader == "Yes" else "Yes"

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
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: СЛАБЫЙ\n"
                "📌 Интерпретация:\n"
                f"{interpretation} "
                "Расхождение есть, но недостаточное для уверенного действия. "
                "Нужен новостной триггер для подтверждения."
            )
        elif edge == "MODERATE":
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: УМЕРЕННЫЙ\n"
                "📌 Интерпретация:\n"
                f"{interpretation} "
                "Проверь ликвидность — убедись что расхождение не из-за тонкого рынка."
            )
        else:
            return (
                "💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}% | Рынок: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: СИЛЬНЫЙ\n"
                "📌 Интерпретация:\n"
                f"{interpretation} "
                "Высокий риск — проверь источники, объём и скрытые риски перед входом."
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
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: WEAK\n"
                "📌 Interpretation:\n"
                f"{interpretation} "
                "Divergence exists but not strong enough for confident action. "
                "Needs news trigger for confirmation."
            )
        elif edge == "MODERATE":
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: MODERATE\n"
                "📌 Interpretation:\n"
                f"{interpretation} "
                "Check liquidity — confirm divergence isn't caused by thin market."
            )
        else:
            return (
                "💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}% | Market: {market_prob:.1f}% | Δ: {delta:.1f}%\n\n"
                "📊 Edge: STRONG\n"
                "📌 Interpretation:\n"
                f"{interpretation} "
                "High risk — verify sources, volume and hidden risks before entry."
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
    market_leader: str = "Yes",
) -> str:
    """
    model_prob > market_prob → рынок недооценивает лидера → альфа в сторону market_leader
    model_prob < market_prob → рынок переоценивает лидера → альфа против market_leader
    """
    delta = abs(model_prob - market_prob)
    opposite = "No" if market_leader == "Yes" else "Yes"

    # Определяем направление альфы
    if model_prob > market_prob:
        alpha_side = market_leader      # рынок недооценивает → берём лидера
        alpha_direction_ru = f"в сторону {market_leader} (рынок недооценивает)"
        alpha_direction_en = f"toward {market_leader} (market underpricing)"
    else:
        alpha_side = opposite           # рынок переоценивает → берём противоположный
        alpha_direction_ru = f"в сторону {opposite} (рынок переоценивает {market_leader})"
        alpha_direction_en = f"toward {opposite} (market overpricing {market_leader})"

    if lang == "ru":
        if delta < 5:
            if market_balance == "strong_consensus":
                note = (
                    "Альфа отсутствует — модель подтверждает рыночный консенсус. "
                    f"Входить против {market_leader} нет смысла. "
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
            note = (
                f"Слабая альфа ({delta:.1f}%) — {alpha_direction_ru}. "
                f"Возможность в {alpha_side}, но нужно подтверждение: "
                "жди новостного триггера перед входом. "
                "Без него риск выше потенциальной прибыли."
            )
        elif delta < 20:
            note = (
                f"Умеренная альфа — расхождение {delta:.1f}% {alpha_direction_ru}. "
                f"Рассмотреть позицию в {alpha_side} при подтверждении от 1–2 источников. "
                "Размер позиции — умеренный, риск ограничить."
            )
        else:
            note = (
                f"Сильная потенциальная альфа — расхождение {delta:.1f}% {alpha_direction_ru}. "
                f"Направление: {alpha_side}. "
                "Высокий риск: такие расхождения часто говорят о скрытой информации "
                "или тонком рынке. "
                "Проверь ликвидность, объём и источники перед входом."
            )
        return f"🟡 Alpha Note:\n{note}"
    else:
        if delta < 5:
            if market_balance == "strong_consensus":
                note = (
                    f"No alpha — model confirms market consensus. "
                    f"No reason to fade {market_leader}. "
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
            note = (
                f"Weak alpha ({delta:.1f}%) — {alpha_direction_en}. "
                f"Opportunity in {alpha_side}, but needs confirmation: "
                "wait for a news trigger before entering. "
                "Without it, risk outweighs potential gain."
            )
        elif delta < 20:
            note = (
                f"Moderate alpha — {delta:.1f}% divergence {alpha_direction_en}. "
                f"Consider position in {alpha_side} on confirmation from 1–2 sources. "
                "Moderate size, limit risk."
            )
        else:
            note = (
                f"Strong potential alpha — {delta:.1f}% divergence {alpha_direction_en}. "
                f"Direction: {alpha_side}. "
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
    market_leader: str = "Yes",
) -> str:
    """
    model_prob > market_prob → рынок недооценивает лидера → Trade Insight за market_leader
    model_prob < market_prob → рынок переоценивает лидера → Trade Insight за противоположный
    """
    delta = abs(model_prob - market_prob)
    opposite = "No" if market_leader == "Yes" else "Yes"

    # Определяем торговое направление
    if model_prob >= market_prob:
        trade_side = market_leader      # модель выше → берём лидера
        fade_side = opposite
        model_higher = True
    else:
        trade_side = opposite           # модель ниже → берём противоположный
        fade_side = market_leader
        model_higher = False

    alt_prob = round(100 - market_prob, 1)

    if lang == "ru":
        if market_balance == "strong_consensus":
            if model_higher:
                # Модель выше → рынок недооценивает лидера → подтверждаем позицию
                insight = (
                    f"Рынок и модель сходятся на {market_leader} ({market_prob:.1f}%). "
                    "Консенсус сильный — входить против нет смысла. "
                    f"Позиция в {trade_side} оправдана только при откате."
                )
                strategy = (
                    f"— удерживать или докупать {trade_side} на откатах\n"
                    "— не входить на максимуме вероятности"
                )
                entry = (
                    f"— если цена {trade_side} откатится к {market_prob - 8:.0f}–{market_prob - 5:.0f}%\n"
                    "— при подтверждении нового события в пользу этого исхода"
                )
                risk = f"— разворотный триггер может быстро сдвинуть рынок к {alt_prob}%"
            else:
                # Модель ниже → рынок переоценивает лидера → смотрим на fade
                insight = (
                    f"Рынок переоценивает {market_leader} ({market_prob:.1f}%), "
                    f"модель видит {model_prob:.1f}%. "
                    f"Потенциальная возможность в {trade_side}, но консенсус сильный — высокий риск."
                )
                strategy = (
                    f"— не входить в {trade_side} без сильного триггера\n"
                    "— ждать подтверждения разворота"
                )
                entry = (
                    f"— если цена {market_leader} упадёт ниже {market_prob - 10:.0f}%\n"
                    "— при появлении конкретного события меняющего расклад"
                )
                risk = f"— консенсус может сохраниться и {market_leader} продолжит расти"

        elif market_balance == "moderate_consensus":
            if model_higher:
                insight = (
                    f"Умеренный перевес {market_leader} ({market_prob:.1f}%), "
                    f"модель подтверждает ({model_prob:.1f}%). "
                    "Есть небольшое окно для входа — рынок не перегрет."
                )
                strategy = (
                    f"— рассмотреть вход в {trade_side} при подтверждении\n"
                    "— размер позиции умеренный"
                )
                entry = (
                    f"— при удержании {market_leader} выше {market_prob - 5:.0f}%\n"
                    "— при выходе подтверждающего события"
                )
                risk = "— смена сентимента может быстро развернуть рынок"
            else:
                insight = (
                    f"Рынок переоценивает {market_leader} ({market_prob:.1f}%), "
                    f"модель видит {model_prob:.1f}%. "
                    f"Расхождение {delta:.1f}% — потенциал в {trade_side}."
                )
                strategy = (
                    f"— ждать сигнала для входа в {trade_side}\n"
                    "— не входить без подтверждающего триггера"
                )
                entry = (
                    f"— если {market_leader} начнёт падать ниже {market_prob - 5:.0f}%\n"
                    "— при появлении новости меняющей расклад"
                )
                risk = f"— {market_leader} может продолжить рост если триггера не будет"

        elif market_balance in ("balanced", "slight_lean"):
            insight = (
                f"Рынок нестабилен ({market_leader}: {market_prob:.1f}% vs {opposite}: {alt_prob}%). "
                "Входить сейчас — высокий риск без чёткого сигнала."
            )
            if model_higher:
                strategy = (
                    f"— наблюдать за {market_leader}\n"
                    "— входить только при пробое и закреплении выше 60%"
                )
                entry = (
                    f"— если {market_leader} пробьёт отметку 60% и удержится\n"
                    "— при официальном заявлении или событии в пользу исхода"
                )
            else:
                strategy = (
                    f"— наблюдать за {trade_side}\n"
                    "— входить только при чётком сигнале разворота"
                )
                entry = (
                    f"— если {opposite} пробьёт 60% и {market_leader} начнёт падать\n"
                    "— при появлении события против текущего лидера"
                )
            risk = "— рынок может резко пойти в любую сторону без предупреждения"

        else:
            # lean_against
            insight = (
                f"Рынок против основного исхода ({market_leader}: {market_prob:.1f}%). "
                "Высокий риск входа без катализатора."
            )
            if model_higher:
                strategy = (
                    f"— осторожно наблюдать за {trade_side}\n"
                    "— входить только при явном подтверждении"
                )
            else:
                strategy = (
                    f"— игнорировать до разворотного события\n"
                    "— наблюдать"
                )
            entry = (
                "— при появлении подтверждённого события меняющего расклад\n"
                f"— при пробое {market_leader} ниже {market_prob - 10:.0f}%"
            )
            risk = f"— {market_leader} может продолжить движение в текущем направлении"

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
            if model_higher:
                insight = (
                    f"Market and model agree on {market_leader} ({market_prob:.1f}%). "
                    "Strong consensus — no reason to fade. "
                    f"Position in {trade_side} justified only on pullback."
                )
                strategy = (
                    f"— hold or add {trade_side} on dips\n"
                    "— avoid entering at probability peak"
                )
                entry = (
                    f"— if {trade_side} pulls back to {market_prob - 8:.0f}–{market_prob - 5:.0f}%\n"
                    "— on confirmation of new supporting event"
                )
                risk = f"— reversal trigger could quickly push market to {alt_prob}%"
            else:
                insight = (
                    f"Market overpricing {market_leader} ({market_prob:.1f}%), "
                    f"model sees {model_prob:.1f}%. "
                    f"Potential opportunity in {trade_side}, but consensus is strong — high risk."
                )
                strategy = (
                    f"— do not enter {trade_side} without strong trigger\n"
                    "— wait for reversal confirmation"
                )
                entry = (
                    f"— if {market_leader} drops below {market_prob - 10:.0f}%\n"
                    "— on specific event changing the setup"
                )
                risk = f"— consensus may hold and {market_leader} continues rising"

        elif market_balance == "moderate_consensus":
            if model_higher:
                insight = (
                    f"Moderate {market_leader} edge ({market_prob:.1f}%), "
                    f"model confirms ({model_prob:.1f}%). "
                    "Small entry window — market not overheated."
                )
                strategy = (
                    f"— consider entry in {trade_side} on confirmation\n"
                    "— moderate position size"
                )
                entry = (
                    f"— if {market_leader} holds above {market_prob - 5:.0f}%\n"
                    "— on confirming event"
                )
                risk = "— sentiment shift could reverse market quickly"
            else:
                insight = (
                    f"Market overpricing {market_leader} ({market_prob:.1f}%), "
                    f"model sees {model_prob:.1f}%. "
                    f"{delta:.1f}% divergence — potential in {trade_side}."
                )
                strategy = (
                    f"— wait for entry signal in {trade_side}\n"
                    "— don't enter without confirming trigger"
                )
                entry = (
                    f"— if {market_leader} starts falling below {market_prob - 5:.0f}%\n"
                    "— on news changing the setup"
                )
                risk = f"— {market_leader} may continue rising without trigger"

        elif market_balance in ("balanced", "slight_lean"):
            insight = (
                f"Market unstable ({market_leader}: {market_prob:.1f}% vs {opposite}: {alt_prob}%). "
                "High risk without clear signal."
            )
            if model_higher:
                strategy = (
                    f"— watch {market_leader}\n"
                    "— enter only on break and hold above 60%"
                )
                entry = (
                    f"— if {market_leader} breaks 60% and holds\n"
                    "— on official statement or supporting event"
                )
            else:
                strategy = (
                    f"— watch {trade_side}\n"
                    "— enter only on clear reversal signal"
                )
                entry = (
                    f"— if {opposite} breaks 60% and {market_leader} starts falling\n"
                    "— on event going against current leader"
                )
            risk = "— market can move sharply in either direction without warning"

        else:
            insight = (
                f"Market against main outcome ({market_leader}: {market_prob:.1f}%). "
                "High risk entering without catalyst."
            )
            if model_higher:
                strategy = (
                    f"— carefully watch {trade_side}\n"
                    "— enter only on clear confirmation"
                )
            else:
                strategy = (
                    "— ignore until reversal event\n"
                    "— observe only"
                )
            entry = (
                "— on confirmed event changing the setup\n"
                f"— on {market_leader} break below {market_prob - 10:.0f}%"
            )
            risk = f"— {market_leader} may continue in current direction"

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
