from typing import Any, Dict


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> str:
        """Возвращает только строку вывода — форматирование делает telegram_bot."""
        conclusion = decision_data.get("conclusion", "").strip()

        # Если есть нормальный вывод — возвращаем его
        bad_conclusions = {
            "No conclusion available.",
            "No conclusion generated.",
            "Communication Agent fallback mode.",
            "Analysis unavailable.",
            "Chief Agent orchestration works but decision logic needs connection.",
        }

        if conclusion and conclusion not in bad_conclusions:
            # Убираем markdown если есть
            conclusion = conclusion.replace("##", "").replace("###", "").replace("**", "").strip()
            return conclusion

        # Если вывода нет — формируем из probability
        probability = decision_data.get("probability", "").strip()
        confidence = decision_data.get("confidence", "").strip()
        category = decision_data.get("category", "")

        bad_probabilities = {
            "N/A", "Unknown", "Unavailable",
            "System probability not available yet",
            "System probability not available yet",
        }

        if probability and probability not in bad_probabilities:
            lang = decision_data.get("lang", "ru")
            if any(c in probability for c in ["—", "%", "-"]):
                return f"AI прогноз: {probability}. Уверенность: {confidence}."

        return "Анализ завершён. Смотри прогноз выше."
