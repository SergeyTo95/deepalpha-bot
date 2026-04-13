import re
from typing import Any, Dict, Optional, Tuple


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Возвращает словарь с семантически обработанными полями.
        Все поля используют semantic_prediction вместо Yes/No.
        """
        probability = decision_data.get("probability", "").strip()
        market_probability = str(decision_data.get("market_probability", "")).strip()
        reasoning = decision_data.get("reasoning", "").strip()
        conclusion = decision_data.get("conclusion", "").strip()
        question = decision_data.get("question", "").strip()
        market_type = decision_data.get("market_type", "binary")
        main_scenario = decision_data.get("main_scenario", "").strip()
        alt_scenario = decision_data.get("alt_scenario", "").strip()

        # Шаг 1: Семантический рендеринг прогноза
        semantic_outcome, prob_val = self._extract_semantic_outcome(
            question=question,
            probability_str=probability,
            market_type=market_type,
        )
        prob_display = f"{prob_val:.1f}%" if prob_val else ""
        display_prediction = f"{semantic_outcome} — {prob_display}" if prob_display else semantic_outcome

        # Шаг 2: Вычисляем delta
        model_prob = prob_val or 0.0
        market_prob = self._extract_market_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None

        # Шаг 3: Alpha detection
        alpha_label, alpha_message = self._detect_alpha(
            delta=delta,
            model_prob=model_prob,
            market_prob=market_prob,
        )

        # Шаг 4: Чистим тексты
        clean_reasoning = self._clean_text(reasoning)
        clean_scenario = self._clean_text(main_scenario)
        clean_alt = self._clean_text(alt_scenario)
        clean_conclusion = self._clean_text(conclusion)

        # Шаг 5: Финальные тексты с semantic outcome
        final_scenario = self._build_scenario(
            clean_scenario=clean_scenario,
            semantic_outcome=semantic_outcome,
            model_prob=model_prob,
        )

        final_conclusion = self._build_conclusion(
            clean_conclusion=clean_conclusion,
            display_prediction=display_prediction,
            semantic_outcome=semantic_outcome,
        )

        final_alt = self._build_alt_scenario(
            clean_alt=clean_alt,
            semantic_outcome=semantic_outcome,
            market_prob=market_prob,
        )

        final_reasoning = self._build_reasoning(
            clean_reasoning=clean_reasoning,
            semantic_outcome=semantic_outcome,
            model_prob=model_prob,
            market_prob=market_prob,
        )

        return {
            "display_prediction": display_prediction,
            "semantic_outcome": semantic_outcome,
            "reasoning": final_reasoning,
            "main_scenario": final_scenario,
            "alt_scenario": final_alt,
            "conclusion": final_conclusion,
            "alpha_label": alpha_label,
            "alpha_message": alpha_message,
        }

    def _extract_semantic_outcome(
        self,
        question: str,
        probability_str: str,
        market_type: str,
    ) -> Tuple[str, Optional[float]]:
        if not probability_str:
            return probability_str, None

        raw_outcome = ""
        prob_val = None

        match = re.match(r'^(.+?)\s*[—–-]\s*([\d.]+)%', probability_str)
        if match:
            raw_outcome = match.group(1).strip()
            prob_val = float(match.group(2))
        else:
            match2 = re.match(r'^([\d.]+)%$', probability_str)
            if match2:
                raw_outcome = "Yes"
                prob_val = float(match2.group(1))
            else:
                return probability_str, None

        if raw_outcome.lower() not in ("yes", "no"):
            return raw_outcome, prob_val

        if raw_outcome.lower() == "yes":
            semantic = self._yes_to_semantic(question)
        else:
            semantic = self._no_to_semantic(question)

        return semantic if semantic else raw_outcome, prob_val

    def _yes_to_semantic(self, question: str) -> str:
        q = re.sub(r'\?$', '', question.strip())

        known_entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH", "XRP",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Macron", "Modi", "Xi", "Fed", "ECB", "NATO", "SpaceX",
            "Intel", "AMD", "Netflix", "Disney", "Uber", "Airbnb",
            "Russia", "Ukraine", "China", "Iran", "Israel", "US", "USA",
        ]
        q_lower = q.lower()
        for entity in known_entities:
            if entity.lower() in q_lower:
                return entity

        match = re.match(
            r'^Will\s+([A-Z][A-Za-z0-9\s&\.\-\']+?)\s+'
            r'(be|win|become|reach|hit|pass|exceed|lose|fall|drop|rise|'
            r'get|make|break|cross|stay|remain|achieve|sign|launch|release|'
            r'announce|complete|finish|happen|occur|fail|enter|capture|take|'
            r'deploy|implement|approve|reject|survive|collapse)',
            q
        )
        if match:
            entity = match.group(1).strip()
            if len(entity.split()) <= 4:
                return entity

        match2 = re.match(r'^Will\s+(.+)', q)
        if match2:
            rest = match2.group(1).strip()
            words = rest.split()[:5]
            short = " ".join(words)
            if len(short) < 50:
                return short

        return ""

    def _no_to_semantic(self, question: str) -> str:
        q = re.sub(r'\?$', '', question.strip())
        match = re.match(r'^Will\s+(.+)', q)
        if match:
            rest = match.group(1).strip()
            words = rest.split()[:4]
            entity = " ".join(words)
            if len(entity) < 40:
                return f"{entity} — нет"
        return ""

    def _build_reasoning(
        self,
        clean_reasoning: str,
        semantic_outcome: str,
        model_prob: float,
        market_prob: float,
    ) -> str:
        """Строит логику с заменой Yes/No на semantic."""
        if clean_reasoning and len(clean_reasoning) > 30:
            result = re.sub(r'\bYes\b', semantic_outcome, clean_reasoning)
            result = re.sub(r'\bNo\b', f"не {semantic_outcome.lower()}", result)
            return result

        # Генерируем если нет нормального
        delta = abs(model_prob - market_prob) if model_prob and market_prob else 0
        if market_prob >= 90:
            return (
                f"Рыночный консенсус на уровне {market_prob:.1f}% отражает высокую уверенность участников рынка. "
                f"При такой вероятности большинство доступной информации уже учтено в цене."
            )
        elif market_prob >= 65:
            return (
                f"Рынок оценивает вероятность {semantic_outcome} в {market_prob:.1f}% — "
                f"умеренный, но устойчивый перевес. Баланс факторов указывает на текущего лидера."
            )
        else:
            return (
                f"Высокая неопределённость: рынок оценивает вероятность в {market_prob:.1f}%. "
                f"Ситуация может измениться при появлении новых данных."
            )

    def _build_scenario(
        self,
        clean_scenario: str,
        semantic_outcome: str,
        model_prob: float,
    ) -> str:
        if clean_scenario and len(clean_scenario) > 30:
            result = re.sub(r'\bYes\b', semantic_outcome, clean_scenario)
            result = re.sub(r'\bNo\b', f"не {semantic_outcome.lower()}", result)
            # Убираем роботизированные фразы
            result = result.replace("указывают на:", "подтверждает:")
            result = result.replace("указывают на: ", "")
            return result

        prob = model_prob or 50
        if prob >= 90:
            return (
                f"{semantic_outcome} с высокой вероятностью реализуется к указанной дате. "
                f"Текущее положение лидера устойчиво, серьёзных угроз не выявлено."
            )
        elif prob >= 65:
            return (
                f"Умеренно высокая вероятность реализации сценария: {semantic_outcome}. "
                f"Для подтверждения необходимо сохранение текущих условий."
            )
        else:
            return (
                f"Сценарий {semantic_outcome} возможен, но неопределённость остаётся высокой. "
                f"Исход зависит от развития ключевых факторов."
            )

    def _build_alt_scenario(
        self,
        clean_alt: str,
        semantic_outcome: str,
        market_prob: float,
    ) -> str:
        if clean_alt and len(clean_alt) > 30:
            # Заменяем дефолтные фразы на конкретные
            if "внешних факторов" in clean_alt or "external factor" in clean_alt.lower():
                pass  # Заменим ниже
            else:
                return clean_alt

        alt_prob = 100 - market_prob
        if market_prob >= 90:
            return (
                f"Маловероятный сценарий ({alt_prob:.1f}%): резкий разворот ключевых факторов или "
                f"неожиданный внешний шок способны изменить исход. "
                f"Рынок практически исключает этот вариант."
            )
        elif market_prob >= 65:
            return (
                f"Альтернативный сценарий ({alt_prob:.1f}%): изменение баланса сил или "
                f"появление нового доминирующего фактора может привести к иному исходу. "
                f"Необходимо следить за ключевыми индикаторами."
            )
        else:
            return (
                f"Альтернативный исход почти равновероятен ({alt_prob:.1f}%). "
                f"Небольшое изменение условий способно переломить текущий тренд."
            )

    def _build_conclusion(
        self,
        clean_conclusion: str,
        display_prediction: str,
        semantic_outcome: str,
    ) -> str:
        if clean_conclusion and len(clean_conclusion) > 30:
            result = re.sub(r'\bYes\b', semantic_outcome, clean_conclusion)
            result = re.sub(r'\bNo\b', f"не {semantic_outcome.lower()}", result)
            return result

        return f"Следуем рыночной оценке: {display_prediction}."

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
    ) -> Tuple[str, str]:
        if delta is None:
            return "📊 Анализ рынка", "Данных недостаточно для оценки эффективности ценообразования."

        if delta < 5:
            label = "✅ Консенсус с рынком"
            if market_prob >= 95:
                msg = (
                    f"При вероятности {market_prob:.1f}% рынок уже учёл практически всю доступную информацию. "
                    f"Позиции с такой вероятностью редко дают альфу — цена уже отражает консенсус. "
                    f"Используй как подтверждение тренда, а не как точку входа."
                )
            elif market_prob >= 80:
                msg = (
                    f"Модель подтверждает рыночный консенсус (расхождение {delta:.1f}%). "
                    f"При вероятности {market_prob:.1f}% большинство участников уже заняли позиции. "
                    f"Потенциал для получения альфы ограничен."
                )
            else:
                msg = (
                    f"Модель согласна с рынком — расхождение всего {delta:.1f}%. "
                    f"Явной недооценки не обнаружено."
                )
        elif delta < 20:
            label = "⚠️ Слабый сигнал"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Модель оценивает вероятность на {delta:.1f}% {direction} рыночной ({market_prob:.1f}%). "
                f"Возможна небольшая неэффективность ценообразования. "
                f"Требуется дополнительная проверка — небольшой сигнал не всегда означает реальную альфу."
            )
        else:
            label = "🔥 Потенциальная альфа"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Значительное расхождение: модель оценивает вероятность на {delta:.1f}% {direction} рыночной. "
                f"Это может указывать на реальную неэффективность ценообразования. "
                f"Высокий риск — тщательно проверь источники перед принятием решения."
            )

        return label, msg

    def _clean_text(self, text: str) -> str:
        """Убирает дефолтные и роботизированные фразы."""
        if not text:
            return ""

        bad_starts = [
            "Резервный анализ для категории",
            "Базовый сценарий в пользу",
            "Резервная оценка:",
            "No conclusion available.",
            "Analysis unavailable.",
            "Communication Agent fallback mode.",
            "Прогноз основан на рыночных данных.",
            "Рыночный консенсус указывает на '",
        ]

        text_stripped = text.strip()
        for phrase in bad_starts:
            if text_stripped.startswith(phrase):
                return ""

        bad_exact = [
            "Альтернативный сценарий возможен при изменении внешних факторов.",
            "Alternative scenario depends on external factor changes.",
        ]
        for phrase in bad_exact:
            if text_stripped == phrase:
                return ""

        # Убираем markdown
        text_stripped = (
            text_stripped
            .replace("##", "")
            .replace("###", "")
            .replace("**", "")
            .strip()
        )

        return text_stripped
