import re
from typing import Any, Dict


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> str:
        """Возвращает строку вывода с интерпретацией и alpha detection."""

        probability = decision_data.get("probability", "").strip()
        market_probability = str(decision_data.get("market_probability", "")).strip()
        confidence = decision_data.get("confidence", "").strip()
        reasoning = decision_data.get("reasoning", "").strip()
        conclusion = decision_data.get("conclusion", "").strip()
        question = decision_data.get("question", "").strip()
        market_type = decision_data.get("market_type", "binary")
        options_breakdown = decision_data.get("options_breakdown", "").strip()

        # Вычисляем delta между моделью и рынком
        model_prob = self._extract_prob_value(probability)
        market_prob = self._extract_market_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob and market_prob else None

        # Определяем alpha label
        alpha_label, alpha_message = self._detect_alpha(
            delta=delta,
            model_prob=model_prob,
            market_prob=market_prob,
            market_type=market_type,
        )

        # Интерпретируем вероятность в реальный смысл
        readable_prob = self._make_readable_probability(
            probability=probability,
            question=question,
            market_type=market_type,
        )

        # Строим финальный вывод
        parts = []

        # Прогноз
        parts.append(f"🎯 Прогноз: {readable_prob}")

        # Логика — убираем обрезанные и дефолтные фразы
        clean_reasoning = self._clean_text(reasoning)
        if clean_reasoning:
            parts.append(f"\n💭 Логика:\n{clean_reasoning}")

        # Alpha detection блок
        parts.append(f"\n📊 {alpha_label}:\n{alpha_message}")

        # Вывод
        clean_conclusion = self._clean_text(conclusion)
        if clean_conclusion and clean_conclusion != clean_reasoning:
            parts.append(f"\n📝 Вывод:\n{clean_conclusion}")
        elif not clean_conclusion:
            parts.append(f"\n📝 Вывод:\nАнализ основан на рыночных данных и доступной информации.")

        return "\n".join(parts)

    def _extract_prob_value(self, prob_str: str) -> float:
        """Извлекает числовое значение вероятности."""
        try:
            match = re.search(r'([\d.]+)%', str(prob_str))
            if match:
                return float(match.group(1))
        except Exception:
            pass
        return 0.0

    def _extract_market_prob(self, market_probability: str, market_type: str) -> float:
        """Извлекает рыночную вероятность лидера."""
        try:
            if market_type == "binary":
                match = re.search(r'Yes:\s*([\d.]+)%', market_probability)
                if match:
                    return float(match.group(1))
                match = re.search(r'([\d.]+)%', market_probability)
                if match:
                    return float(match.group(1))
            else:
                # Multiple choice — берём максимальную
                matches = re.findall(r'[\d.]+%', market_probability)
                if matches:
                    return max(float(m.replace('%', '')) for m in matches)
        except Exception:
            pass
        return 50.0

    def _detect_alpha(
        self,
        delta: float,
        model_prob: float,
        market_prob: float,
        market_type: str,
    ):
        """Определяет есть ли alpha возможность."""
        if delta is None:
            return "📊 Анализ рынка", "Данных недостаточно для оценки."

        if delta < 5:
            label = "✅ Консенсус с рынком"
            if market_prob >= 90:
                msg = (
                    f"При вероятности {market_prob:.1f}% рынок уже учитывает всю доступную информацию. "
                    f"Такие позиции редко дают альфу и чаще используются как подтверждение тренда, "
                    f"а не точка входа."
                )
            else:
                msg = (
                    f"Модель согласна с рынком (расхождение {delta:.1f}%). "
                    f"Явной недооценки не обнаружено."
                )
        elif delta < 20:
            label = "⚠️ Слабый сигнал"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Модель оценивает вероятность на {delta:.1f}% {direction} рыночной. "
                f"Возможна небольшая неэффективность ценообразования. "
                f"Требуется дополнительная проверка данных."
            )
        else:
            label = "🔥 Потенциальная альфа"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Значительное расхождение: модель {direction} рынка на {delta:.1f}%. "
                f"Это может указывать на реальную неэффективность. "
                f"Высокий риск — требуется тщательная проверка."
            )

        return label, msg

    def _make_readable_probability(
        self,
        probability: str,
        question: str,
        market_type: str,
    ) -> str:
        """Делает вероятность читаемой — убирает Yes/No для multiple choice."""
        if not probability:
            return "Нет данных"

        # Для multiple choice уже должно быть правильное название
        if market_type == "multiple_choice":
            # Если всё ещё Yes/No — пробуем извлечь из вопроса
            if probability.lower().startswith("yes") or probability.lower().startswith("no"):
                entity = self._extract_entity_from_question(question)
                prob_val = self._extract_prob_value(probability)
                if entity and prob_val:
                    return f"{entity} — {prob_val:.1f}%"
            return probability

        # Для binary — Yes/No нормально
        return probability

    def _extract_entity_from_question(self, question: str) -> str:
        """Пытается извлечь основной объект из вопроса."""
        # Ищем компании
        companies = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Amazon",
            "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
        ]
        for company in companies:
            if company.lower() in question.lower():
                return company

        # Ищем первое существительное после "Will"
        match = re.search(r'Will\s+([A-Z][a-zA-Z\s]+?)\s+(be|win|become|reach)', question)
        if match:
            return match.group(1).strip()

        return ""

    def _clean_text(self, text: str) -> str:
        """Убирает дефолтные и обрезанные фразы."""
        if not text:
            return ""

        bad_phrases = [
            "Альтернативный сценарий возможен при изменении внешних факторов.",
            "Alternative scenario depends on external factor changes.",
            "Резервный анализ для категории",
            "Базовый сценарий в пользу",
            "Резервная оценка:",
            "No conclusion available.",
            "Analysis unavailable.",
            "Communication Agent fallback mode.",
        ]

        for phrase in bad_phrases:
            if text.strip() == phrase.strip():
                return ""
            if text.strip().startswith("Резервный анализ"):
                return ""

        # Убираем markdown
        text = text.replace("##", "").replace("###", "").replace("**", "").strip()

        # Проверяем что текст не обрезан (заканчивается нормально)
        if text and not text[-1] in ".!?%":
            # Если обрезан — не показываем
            if len(text) > 50:
                return ""

        return text


