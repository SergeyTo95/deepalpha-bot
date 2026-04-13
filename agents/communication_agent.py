
import re
from typing import Any, Dict, Optional, Tuple


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> str:
        """Возвращает строку вывода с semantic rendering и alpha detection."""

        probability = decision_data.get("probability", "").strip()
        market_probability = str(decision_data.get("market_probability", "")).strip()
        confidence = decision_data.get("confidence", "").strip()
        reasoning = decision_data.get("reasoning", "").strip()
        conclusion = decision_data.get("conclusion", "").strip()
        question = decision_data.get("question", "").strip()
        market_type = decision_data.get("market_type", "binary")
        options_breakdown = decision_data.get("options_breakdown", "").strip()
        main_scenario = decision_data.get("main_scenario", "").strip()

        # Семантический рендеринг прогноза
        rendered_prediction = self._render_human_prediction(
            question=question,
            probability_str=probability,
            market_type=market_type,
        )

        # Вычисляем delta
        model_prob = self._extract_prob_value(probability)
        market_prob = self._extract_market_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob and market_prob else None

        # Alpha detection
        alpha_label, alpha_message = self._detect_alpha(
            delta=delta,
            model_prob=model_prob,
            market_prob=market_prob,
            market_type=market_type,
        )

        # Чистим текст
        clean_reasoning = self._clean_text(reasoning)
        clean_conclusion = self._clean_text(conclusion)
        clean_scenario = self._clean_text(main_scenario)

        # Генерируем семантический сценарий если дефолтный
        semantic_scenario = self._generate_semantic_scenario(
            question=question,
            rendered_prediction=rendered_prediction,
            model_prob=model_prob,
            market_prob=market_prob,
            clean_scenario=clean_scenario,
        )

        # Генерируем семантический вывод
        semantic_conclusion = self._generate_semantic_conclusion(
            rendered_prediction=rendered_prediction,
            clean_conclusion=clean_conclusion,
            model_prob=model_prob,
            market_prob=market_prob,
            delta=delta,
        )

        # Строим финальный вывод
        parts = []

        parts.append(f"🎯 Прогноз: {rendered_prediction}")

        if clean_reasoning:
            parts.append(f"\n💭 Логика:\n{clean_reasoning}")

        parts.append(f"\n✅ Основной сценарий:\n{semantic_scenario}")

        parts.append(f"\n📊 {alpha_label}:\n{alpha_message}")

        parts.append(f"\n📝 Вывод:\n{semantic_conclusion}")

        return "\n".join(parts)

    def _render_human_prediction(
        self,
        question: str,
        probability_str: str,
        market_type: str,
    ) -> str:
        """
        Семантический рендеринг прогноза.
        Конвертирует Yes/No в читаемое предсказание.

        Примеры:
        "Will NVIDIA be the largest company?" + "Yes — 98.3%" → "NVIDIA — 98.3%"
        "Will Bitcoin hit $150k?" + "Yes — 42%" → "Bitcoin достигнет $150k — 42%"
        "Will Trump win the election?" + "Yes — 61%" → "Trump победит — 61%"
        """
        if not probability_str:
            return probability_str

        # Извлекаем вероятность и исход
        outcome, prob_val = self._split_outcome_and_prob(probability_str)

        if not outcome:
            return probability_str

        prob_str = f"{prob_val:.1f}%" if prob_val else ""

        # Для multiple choice — уже правильное название
        if market_type == "multiple_choice":
            if outcome.lower() not in ("yes", "no"):
                return probability_str
            # Если всё ещё Yes/No в multiple choice — извлекаем из вопроса
            entity = self._extract_entity(question)
            if entity:
                return f"{entity} — {prob_str}"
            return probability_str

        # Binary рынок
        if outcome.lower() == "yes":
            semantic = self._convert_yes_to_semantic(question)
            if semantic:
                return f"{semantic} — {prob_str}"
            return probability_str

        elif outcome.lower() == "no":
            semantic = self._convert_no_to_semantic(question)
            if semantic:
                return f"{semantic} — {prob_str}"
            return probability_str

        # Уже не Yes/No — возвращаем как есть
        return probability_str

    def _split_outcome_and_prob(self, probability_str: str) -> Tuple[str, Optional[float]]:
        """Разделяет строку типа 'Yes — 98.3%' на ('Yes', 98.3)."""
        try:
            # Формат: "Outcome — XX%"
            match = re.match(r'^(.+?)\s*[—–-]\s*([\d.]+)%', probability_str)
            if match:
                outcome = match.group(1).strip()
                prob = float(match.group(2))
                return outcome, prob

            # Только процент
            match = re.match(r'^([\d.]+)%$', probability_str)
            if match:
                return "Yes", float(match.group(1))

        except Exception:
            pass
        return probability_str, None

    def _convert_yes_to_semantic(self, question: str) -> str:
        """
        Конвертирует Yes в семантическое предсказание.

        Примеры:
        "Will NVIDIA be the largest company?" → "NVIDIA"
        "Will Bitcoin hit $150k before July?" → "Bitcoin hits $150k before July"
        "Will Trump win the election?" → "Trump wins"
        "Will the Fed cut rates?" → "Fed cuts rates"
        """
        q = question.strip()

        # Убираем вопросительный знак
        q_clean = re.sub(r'\?$', '', q).strip()

        # Паттерн: "Will [ENTITY] [VERB]..."
        # Пример: "Will NVIDIA be the largest company..."
        match = re.match(
            r'^Will\s+([A-Z][A-Za-z\s&\.\-]+?)\s+(be|win|become|reach|hit|pass|exceed|lose|fall|drop|rise|get|make|break|cross|stay|remain|achieve|sign|launch|release|announce|complete|finish|happen|occur|pass|fail)',
            q_clean
        )
        if match:
            entity = match.group(1).strip()
            verb = match.group(2)

            # Короткие entity — просто возвращаем
            short_entities = [
                "NVIDIA", "Apple", "Microsoft", "Google", "Tesla", "Meta",
                "Amazon", "Bitcoin", "Ethereum", "Trump", "Biden", "Harris",
                "Fed", "NATO", "SpaceX", "OpenAI", "Anthropic", "Samsung",
            ]
            for se in short_entities:
                if se.lower() in entity.lower():
                    return se

            # Если entity длиннее — возвращаем его
            if len(entity.split()) <= 3:
                return entity

        # Паттерн: "Will [someone] [action]" → "[someone] [action]s"
        match2 = re.match(r'^Will\s+(.+)', q_clean)
        if match2:
            rest = match2.group(1).strip()
            # Берём первые 5 слов
            words = rest.split()[:5]
            short = " ".join(words)
            if len(short) < 50:
                return short

        return ""

    def _convert_no_to_semantic(self, question: str) -> str:
        """
        Конвертирует No в семантическое предсказание.
        Только если читаемо — иначе возвращаем пустую строку.
        """
        q_clean = re.sub(r'\?$', '', question.strip())

        # Паттерн: "Will X happen?" + No → "X не произойдёт"
        match = re.match(r'^Will\s+(.+)', q_clean)
        if match:
            rest = match.group(1).strip()
            words = rest.split()[:4]
            entity = " ".join(words)
            if len(entity) < 40:
                return f"{entity} — нет"

        return ""

    def _extract_entity(self, question: str) -> str:
        """Извлекает основной объект из вопроса."""
        entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Fed", "ECB", "NATO", "SpaceX", "Samsung", "Intel", "AMD",
        ]
        q_lower = question.lower()
        for entity in entities:
            if entity.lower() in q_lower:
                return entity
        return ""

    def _generate_semantic_scenario(
        self,
        question: str,
        rendered_prediction: str,
        model_prob: float,
        market_prob: float,
        clean_scenario: str,
    ) -> str:
        """Генерирует семантический основной сценарий."""
        # Если сценарий нормальный — используем его
        if clean_scenario and len(clean_scenario) > 20:
            return clean_scenario

        # Генерируем на основе прогноза
        prob = model_prob or market_prob or 50

        # Убираем " — XX%" из rendered_prediction для читаемости
        prediction_clean = re.sub(r'\s*—\s*[\d.]+%$', '', rendered_prediction).strip()

        if prob >= 80:
            return f"Рыночный консенсус и AI указывают на высокую вероятность: {prediction_clean}."
        elif prob >= 60:
            return f"Умеренная вероятность в пользу: {prediction_clean}."
        elif prob >= 40:
            return f"Неопределённость высокая, но небольшой перевес в пользу: {prediction_clean}."
        else:
            return f"Данный сценарий маловероятен согласно рыночным данным."

    def _generate_semantic_conclusion(
        self,
        rendered_prediction: str,
        clean_conclusion: str,
        model_prob: float,
        market_prob: float,
        delta: Optional[float],
    ) -> str:
        """Генерирует семантический вывод."""
        if clean_conclusion and len(clean_conclusion) > 20:
            # Заменяем Yes/No в выводе на semantic prediction
            conclusion = clean_conclusion
            prob_clean = re.sub(r'\s*—\s*[\d.]+%$', '', rendered_prediction).strip()
            conclusion = re.sub(r'\bYes\b', prob_clean, conclusion)
            conclusion = re.sub(r'\bNo\b', f"не {prob_clean.lower()}", conclusion)
            return conclusion

        # Генерируем
        return f"Следуем рыночной оценке: {rendered_prediction}."

    def _extract_prob_value(self, prob_str: str) -> float:
        try:
            match = re.search(r'([\d.]+)%', str(prob_str))
            if match:
                return float(match.group(1))
        except Exception:
            pass
        return 0.0

    def _extract_market_prob(self, market_probability: str, market_type: str) -> float:
        try:
            if market_type == "binary":
                match = re.search(r'Yes:\s*([\d.]+)%', market_probability)
                if match:
                    return float(match.group(1))
                match = re.search(r'([\d.]+)%', market_probability)
                if match:
                    return float(match.group(1))
            else:
                matches = re.findall(r'[\d.]+%', market_probability)
                if matches:
                    return max(float(m.replace('%', '')) for m in matches)
        except Exception:
            pass
        return 50.0

    def _detect_alpha(
        self,
        delta: Optional[float],
        model_prob: float,
        market_prob: float,
        market_type: str,
    ) -> Tuple[str, str]:
        if delta is None:
            return "📊 Анализ рынка", "Данных недостаточно для оценки."

        if delta < 5:
            label = "✅ Консенсус с рынком"
            if market_prob >= 90:
                msg = (
                    f"При вероятности {market_prob:.1f}% рынок уже учитывает всю доступную информацию. "
                    f"Такие позиции редко дают альфу — используй как подтверждение тренда."
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
                f"Возможна небольшая неэффективность. Требуется дополнительная проверка."
            )
        else:
            label = "🔥 Потенциальная альфа"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Значительное расхождение: модель {direction} рынка на {delta:.1f}%. "
                f"Возможна реальная неэффективность. Высокий риск — проверь тщательно."
            )

        return label, msg

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""

        bad_phrases = [
            "Альтернативный сценарий возможен при изменении внешних факторов.",
            "Alternative scenario depends on external factor changes.",
            "Базовый сценарий в пользу",
            "Резервный анализ для категории",
            "Резервная оценка:",
            "No conclusion available.",
            "Analysis unavailable.",
            "Communication Agent fallback mode.",
            "Рыночный консенсус указывает на",
            "Прогноз основан на рыночных данных.",
        ]

        for phrase in bad_phrases:
            if phrase.lower() in text.lower() and len(text) < 120:
                return ""

        if text.strip().startswith("Резервный анализ"):
            return ""

        # Убираем markdown
        text = text.replace("##", "").replace("###", "").replace("**", "").strip()

        # Если текст обрезан — не показываем
        if text and text[-1] not in ".!?%" and len(text) > 60:
            return ""

        return text
