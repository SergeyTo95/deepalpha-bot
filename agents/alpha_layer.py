import re
from typing import Optional


def _parse_prob(value) -> Optional[float]:
    """Безопасно извлекает float вероятность из строки или числа."""
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
    """
    Определяет расхождение модели с рынком.

    Returns:
        {
            "delta": float,
            "edge": "NONE" | "WEAK" | "MODERATE" | "STRONG",
            "message": str,
            "interpretation": str,
        }
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

    if edge == "NONE":
        message = f"Δ: {delta:.1f}%"
        interpretation = (
            "Явной альфы нет: модель и рынок дают близкую оценку."
        )
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
    """Формирует текстовый блок Mispricing Signal."""
    result = detect_mispricing(model_prob, market_prob)
    delta = result["delta"]
    edge = result["edge"]
    interpretation = result["interpretation"]

    if lang == "ru":
        if edge == "NONE":
            return (
                f"💣 Mispricing Signal:\n"
                f"Явного расхождения с рынком нет (Δ: {delta:.1f}%).\n"
                f"📌 Интерпретация:\n"
                f"{interpretation}"
            )
        else:
            edge_labels = {
                "WEAK": "СЛАБЫЙ",
                "MODERATE": "УМЕРЕННЫЙ",
                "STRONG": "СИЛЬНЫЙ",
            }
            edge_ru = edge_labels.get(edge, edge)
            return (
                f"💣 Mispricing Signal:\n"
                f"Модель: {model_prob:.1f}%\n"
                f"Рынок: {market_prob:.1f}%\n"
                f"Δ: {delta:.1f}%\n\n"
                f"📊 Edge: {edge_ru}\n"
                f"📌 Интерпретация:\n"
                f"{interpretation}"
            )
    else:
        if edge == "NONE":
            return (
                f"💣 Mispricing Signal:\n"
                f"No significant divergence (Δ: {delta:.1f}%).\n"
                f"📌 Interpretation:\n"
                f"Model and market are closely aligned — current edge is weak."
            )
        else:
            return (
                f"💣 Mispricing Signal:\n"
                f"Model: {model_prob:.1f}%\n"
                f"Market: {market_prob:.1f}%\n"
                f"Δ: {delta:.1f}%\n\n"
                f"📊 Edge: {edge}\n"
                f"📌 Interpretation:\n"
                f"{result['interpretation']}"
            )


def build_market_psychology(
    probability: float,
    outcome: str = "",
    lang: str = "ru",
) -> str:
    """Формирует блок Market Psychology на основе уровня вероятности."""
    if lang == "ru":
        if probability >= 80:
            text = (
                f"Рынок показывает сильный консенсус ({probability:.1f}%). "
                f"Участники уверены в исходе — позиция против требует "
                f"экстраординарного катализатора."
            )
        elif probability >= 65:
            text = (
                f"Рынок уверенно склоняется к исходу ({probability:.1f}%), "
                f"но не закрывает альтернативный сценарий. "
                f"Высокая чувствительность к новостному фону."
            )
        elif probability >= 55:
            text = (
                f"Рынок имеет умеренный перевес ({probability:.1f}%). "
                f"Ситуация остаётся открытой — любой триггер может "
                f"изменить баланс."
            )
        elif probability >= 45:
            text = (
                f"Рынок почти сбалансирован (~{probability:.1f}%). "
                f"Максимальная неопределённость — оба исхода реальны."
            )
        else:
            text = (
                f"Рынок против основного исхода ({probability:.1f}%). "
                f"Слабый сценарий — для реализации нужен сильный разворот."
            )
        return f"🧠 Market Psychology:\n{text}"
    else:
        if probability >= 80:
            text = (
                f"Market shows strong consensus ({probability:.1f}%). "
                f"Participants are confident — fading requires extraordinary catalyst."
            )
        elif probability >= 65:
            text = (
                f"Market confidently leans toward outcome ({probability:.1f}%) "
                f"but doesn't rule out the alternative. "
                f"High sensitivity to news flow."
            )
        elif probability >= 55:
            text = (
                f"Market has moderate edge ({probability:.1f}%). "
                f"Situation remains open — any trigger could shift the balance."
            )
        elif probability >= 45:
            text = (
                f"Market is nearly balanced (~{probability:.1f}%). "
                f"Maximum uncertainty — both outcomes are real."
            )
        else:
            text = (
                f"Market is against the main outcome ({probability:.1f}%). "
                f"Weak scenario — strong reversal needed for realisation."
            )
        return f"🧠 Market Psychology:\n{text}"


def build_alpha_note(
    model_prob: float,
    market_prob: float,
    market_balance: str = "",
    lang: str = "ru",
) -> str:
    """Формирует блок Alpha Note."""
    delta = abs(model_prob - market_prob)

    if lang == "ru":
        if delta < 5:
            note = (
                "Модель подтверждает рынок. "
                "Главная ценность — мониторинг триггеров, "
                "а не ставка против консенсуса."
            )
        elif delta < 10:
            note = (
                f"Есть слабое расхождение (Δ {delta:.1f}%), "
                f"но для действия нужен дополнительный новостной триггер."
            )
        elif delta < 20:
            note = (
                f"Заметное расхождение (Δ {delta:.1f}%) — "
                f"рынок может быть неэффективен. "
                f"Проверь качество источников и ликвидность."
            )
        else:
            note = (
                f"Сильное расхождение (Δ {delta:.1f}%) — "
                f"потенциальная alpha-зона. "
                f"Требует проверки ликвидности, источников и наличия скрытых рисков."
            )
        return f"🟡 Alpha Note:\n{note}"
    else:
        if delta < 5:
            note = (
                "Model confirms market. "
                "Main value is monitoring triggers, "
                "not betting against consensus."
            )
        elif delta < 10:
            note = (
                f"Weak divergence (Δ {delta:.1f}%) — "
                f"additional news trigger needed before acting."
            )
        elif delta < 20:
            note = (
                f"Notable divergence (Δ {delta:.1f}%) — "
                f"market may be inefficient. "
                f"Check source quality and liquidity."
            )
        else:
            note = (
                f"Strong divergence (Δ {delta:.1f}%) — "
                f"potential alpha zone. "
                f"Verify liquidity, sources and hidden risks."
            )
        return f"🟡 Alpha Note:\n{note}"
