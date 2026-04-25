from typing import Dict, List, Optional


def analyze_time_shift(
    time_series: Optional[List[Dict]] = None,
    lang: str = "ru",
) -> dict:
    """
    Анализирует временной сдвиг вероятностей по нескольким дедлайнам.

    time_series пример:
    [
        {"date": "April 30", "yes_prob": 7.0},
        {"date": "May 31", "yes_prob": 35.0},
        {"date": "June 30", "yes_prob": 54.0},
    ]

    Returns:
        {
            "available": bool,
            "trend": "up" | "down" | "flat" | "none",
            "description": str,
            "insight": str,
            "prob_sequence": str,
            "date_sequence": str,
            "total_change": float,
        }
    """
    empty = {
        "available": False,
        "trend": "none",
        "description": "",
        "insight": "",
        "prob_sequence": "",
        "date_sequence": "",
        "total_change": 0.0,
    }

    if not time_series or len(time_series) < 2:
        return empty

    points = [
        p for p in time_series
        if isinstance(p.get("yes_prob"), (int, float))
        and 0 <= float(p["yes_prob"]) <= 100
    ]

    if len(points) < 2:
        return empty

    probs = [float(p["yes_prob"]) for p in points]
    dates = [str(p.get("date", f"T{i + 1}")) for i, p in enumerate(points)]

    deltas = [probs[i + 1] - probs[i] for i in range(len(probs) - 1)]
    avg_delta = sum(deltas) / len(deltas)
    up_steps = sum(1 for d in deltas if d > 1.0)
    down_steps = sum(1 for d in deltas if d < -1.0)
    total_steps = len(deltas)
    total_change = probs[-1] - probs[0]

    prob_sequence = " → ".join(f"{p:.1f}%" for p in probs)
    date_sequence = " → ".join(dates)

    if abs(total_change) < 5 and abs(avg_delta) < 3:
        trend = "flat"
    elif up_steps >= total_steps * 0.6 and avg_delta > 2:
        trend = "up"
    elif down_steps >= total_steps * 0.6 and avg_delta < -2:
        trend = "down"
    elif total_change > 10:
        trend = "up"
    elif total_change < -10:
        trend = "down"
    else:
        trend = "flat"

    if lang == "ru":
        if trend == "up":
            description = f"Вероятность по дедлайнам: {prob_sequence}"
            if probs[-1] >= 50:
                insight = (
                    "Рынок не отрицает событие, а скорее переносит его "
                    "за текущий дедлайн. Долгосрочные контракты оцениваются выше."
                )
            else:
                insight = (
                    "Рынок постепенно повышает оценку вероятности, "
                    "но пока остаётся ниже 50% даже в долгосрочной перспективе."
                )
        elif trend == "down":
            description = f"Вероятность по дедлайнам: {prob_sequence}"
            if probs[0] >= 40:
                insight = (
                    "Рынок теряет уверенность в событии "
                    "по мере расширения горизонта."
                )
            else:
                insight = (
                    "Рынок последовательно исключает этот исход "
                    "на всех горизонтах."
                )
        else:
            description = f"Вероятность по дедлайнам: {prob_sequence}"
            insight = "Рынок не показывает явного временного сдвига."
    else:
        if trend == "up":
            description = f"Probability by deadline: {prob_sequence}"
            if probs[-1] >= 50:
                insight = (
                    "Market does not deny the event — it expects it "
                    "beyond the current deadline. Longer-dated contracts priced higher."
                )
            else:
                insight = (
                    "Market gradually raises probability estimate "
                    "but stays below 50% even long-term."
                )
        elif trend == "down":
            description = f"Probability by deadline: {prob_sequence}"
            if probs[0] >= 40:
                insight = (
                    "Market loses confidence in the event "
                    "as the horizon expands."
                )
            else:
                insight = (
                    "Market consistently excludes this outcome "
                    "across all time horizons."
                )
        else:
            description = f"Probability by deadline: {prob_sequence}"
            insight = "Market shows no clear time shift signal."

    return {
        "available": True,
        "trend": trend,
        "description": description,
        "insight": insight,
        "prob_sequence": prob_sequence,
        "date_sequence": date_sequence,
        "total_change": round(total_change, 1),
    }


def build_time_shift_block(
    time_series: Optional[List[Dict]] = None,
    lang: str = "ru",
) -> str:
    """
    Формирует текстовый блок Time Shift Signal.
    Если данных нет или недостаточно — возвращает пустую строку.
    """
    result = analyze_time_shift(time_series, lang=lang)

    if not result["available"]:
        return ""

    trend = result["trend"]
    description = result["description"]
    insight = result["insight"]

    if trend == "up":
        icon = "📈"
    elif trend == "down":
        icon = "📉"
    else:
        icon = "➡️"

    if lang == "ru":
        return (
            f"{icon} Time Shift Signal:\n"
            f"{description}\n\n"
            f"📌 Интерпретация:\n"
            f"{insight}"
        )
    else:
        return (
            f"{icon} Time Shift Signal:\n"
            f"{description}\n\n"
            f"📌 Interpretation:\n"
            f"{insight}"
        )
