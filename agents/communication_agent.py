import re
from typing import Any, Dict, Optional, Tuple


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> Dict[str, Any]:
        probability = decision_data.get("probability", "").strip()
        market_probability = str(decision_data.get("market_probability", "")).strip()
        reasoning = decision_data.get("reasoning", "").strip()
        conclusion = decision_data.get("conclusion", "").strip()
        question = decision_data.get("question", "").strip()
        market_type = decision_data.get("market_type", "binary")
        main_scenario = decision_data.get("main_scenario", "").strip()
        alt_scenario = decision_data.get("alt_scenario", "").strip()

        # Семантический рендеринг
        semantic_outcome, prob_val, is_negated = self._extract_semantic_outcome(
            question=question,
            probability_str=probability,
            market_type=market_type,
        )
        prob_display = f"{prob_val:.1f}%" if prob_val else ""
        display_prediction = f"{semantic_outcome} — {prob_display}" if prob_display else semantic_outcome

        # Delta и alpha
        model_prob = prob_val or 0.0
        market_prob = self._extract_market_leader_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None

        alpha_label, alpha_message = self._detect_alpha(
            delta=delta,
            model_prob=model_prob,
            market_prob=market_prob,
        )

        # Чистим тексты
        clean_reasoning = self._clean_text(reasoning)
        clean_scenario = self._clean_text(main_scenario)
        clean_alt = self._clean_text(alt_scenario)
        clean_conclusion = self._clean_text(conclusion)

        # Строим финальные тексты
        final_reasoning = self._build_reasoning(
            clean_reasoning=clean_reasoning,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            model_prob=model_prob,
            market_prob=market_prob,
        )

        final_scenario = self._build_scenario(
            clean_scenario=clean_scenario,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            model_prob=model_prob,
        )

        final_alt = self._build_alt_scenario(
            clean_alt=clean_alt,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            market_prob=market_prob,
        )

        final_conclusion = self._build_conclusion(
            clean_conclusion=clean_conclusion,
            display_prediction=display_prediction,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
        )

        return {
            "display_prediction": display_prediction,
            "semantic_outcome": semantic_outcome,
            "is_negated": is_negated,
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
    ) -> Tuple[str, Optional[float], bool]:
        """
        Извлекает semantic outcome, вероятность и флаг отрицания.
        Возвращает (semantic_text, prob_value, is_negated)
        """
        if not probability_str:
            return probability_str, None, False

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
                return probability_str, None, False

        # Уже не Yes/No — возвращаем как есть
        if raw_outcome.lower() not in ("yes", "no"):
            return raw_outcome, prob_val, False

        is_negated = raw_outcome.lower() == "no"

        if is_negated:
            semantic = self._no_to_semantic(question)
        else:
            semantic = self._yes_to_semantic(question)

        return semantic if semantic else raw_outcome, prob_val, is_negated

    def _yes_to_semantic(self, question: str) -> str:
        """
        Конвертирует Yes в утвердительное предсказание.
        Will NVIDIA be #1? → NVIDIA
        Will Bitcoin hit $150k? → Bitcoin достигнет $150k
        Will Trump win? → Trump победит
        """
        q = re.sub(r'\?$', '', question.strip())

        known_entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH", "XRP",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Macron", "Modi", "Xi", "Fed", "ECB", "NATO", "SpaceX",
            "Intel", "AMD", "Netflix", "Disney", "Uber", "Airbnb",
            "Russia", "Ukraine", "China", "Iran", "Israel",
            "Bank of Japan", "BOJ", "OPEC", "IMF", "World Bank",
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
            r'deploy|implement|approve|reject|survive|collapse|decrease|increase|'
            r'cut|raise|hike|pause|hold)',
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
        """
        Конвертирует No в отрицательное предсказание.

        Will Bank of Japan decrease rates? → Банк Японии НЕ снизит ставку
        Will Trump win? → Trump НЕ победит
        Will Bitcoin hit $150k? → Bitcoin НЕ достигнет $150k
        Will Russia enter Dovha Balka? → Россия НЕ войдёт в Довгу Балку
        """
        q = re.sub(r'\?$', '', question.strip())

        # Известные сущности — строим отрицание
        known_entities = [
            ("Bank of Japan", "Банк Японии"),
            ("BOJ", "Банк Японии"),
            ("NVIDIA", "NVIDIA"),
            ("Apple", "Apple"),
            ("Microsoft", "Microsoft"),
            ("Google", "Google"),
            ("Tesla", "Tesla"),
            ("Bitcoin", "Bitcoin"),
            ("Ethereum", "Ethereum"),
            ("Trump", "Trump"),
            ("Biden", "Biden"),
            ("Harris", "Harris"),
            ("Putin", "Путин"),
            ("Fed", "ФРС"),
            ("Russia", "Россия"),
            ("Ukraine", "Украина"),
            ("Iran", "Иран"),
            ("Israel", "Израиль"),
        ]

        q_lower = q.lower()
        entity_ru = None
        for en, ru in known_entities:
            if en.lower() in q_lower:
                entity_ru = ru
                break

        # Определяем действие из вопроса
        action_map = {
            "decrease rates": "НЕ снизит ставку",
            "cut rates": "НЕ снизит ставку",
            "raise rates": "НЕ повысит ставку",
            "hike rates": "НЕ повысит ставку",
            "hold rates": "НЕ сохранит ставку",
            "win": "НЕ победит",
            "become": "НЕ станет",
            "reach": "НЕ достигнет",
            "hit": "НЕ достигнет",
            "pass": "НЕ пройдёт",
            "enter": "НЕ войдёт",
            "launch": "НЕ запустит",
            "sign": "НЕ подпишет",
            "collapse": "НЕ обрушится",
            "approve": "НЕ одобрит",
            "reject": "НЕ отклонит",
        }

        action_ru = None
        for en_action, ru_action in action_map.items():
            if en_action in q_lower:
                action_ru = ru_action
                break

        if entity_ru and action_ru:
            return f"{entity_ru} {action_ru}"

        if entity_ru:
            return f"{entity_ru} — этого не произойдёт"

        # Общий паттерн: "Will X Y?" → "X НЕ Y"
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
        is_negated: bool,
        model_prob: float,
        market_prob: float,
    ) -> str:
        if clean_reasoning and len(clean_reasoning) > 30:
            result = clean_reasoning
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
                result = re.sub(r'\bYes\b', f"не {semantic_outcome.lower()}", result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
                result = re.sub(r'\bNo\b', f"не {semantic_outcome.lower()}", result)
            return result

        prob = market_prob or model_prob or 50
        if prob >= 90:
            return (
                f"Рыночный консенсус на уровне {prob:.1f}% отражает высокую уверенность участников. "
                f"При такой вероятности большинство доступной информации уже учтено в цене — "
                f"рынок практически единодушен."
            )
        elif prob >= 65:
            return (
                f"Умеренно высокий консенсус {prob:.1f}% указывает на устойчивый перевес. "
                f"Баланс факторов складывается в пользу текущего лидера."
            )
        else:
            return (
                f"Вероятность {prob:.1f}% указывает на неопределённость. "
                f"Ни один из исходов не имеет явного преимущества."
            )

    def _build_scenario(
        self,
        clean_scenario: str,
        semantic_outcome: str,
        is_negated: bool,
        model_prob: float,
    ) -> str:
        if clean_scenario and len(clean_scenario) > 30:
            result = clean_scenario
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            # Убираем роботизированные фразы
            result = result.replace("указывают на:", "подтверждает:")
            result = result.replace("указывает на: ", "")
            return result

        prob = model_prob or 50
        if prob >= 90:
            if is_negated:
                return (
                    f"Событие с высокой вероятностью не произойдёт — {semantic_outcome}. "
                    f"Текущие условия не создают предпосылок для реализации противоположного сценария."
                )
            else:
                return (
                    f"{semantic_outcome} с высокой вероятностью реализуется. "
                    f"Текущее положение устойчиво, серьёзных угроз не выявлено."
                )
        elif prob >= 65:
            return (
                f"Умеренно высокая вероятность реализации: {semantic_outcome}. "
                f"Для подтверждения необходимо сохранение текущих условий."
            )
        else:
            return (
                f"Сценарий '{semantic_outcome}' возможен, но неопределённость высокая. "
                f"Исход зависит от развития ключевых факторов."
            )

    def _build_alt_scenario(
        self,
        clean_alt: str,
        semantic_outcome: str,
        is_negated: bool,
        market_prob: float,
    ) -> str:
        if clean_alt and len(clean_alt) > 30:
            bad_phrases = [
                "внешних факторов",
                "external factor",
                "изменении внешних",
            ]
            is_generic = any(p in clean_alt.lower() for p in bad_phrases)
            if not is_generic:
                return clean_alt

        alt_prob = 100 - market_prob

        if market_prob >= 90:
            if is_negated:
                return (
                    f"Маловероятный сценарий ({alt_prob:.1f}%): резкое изменение политики, "
                    f"неожиданное решение регулятора или внешний шок могут привести к противоположному исходу. "
                    f"Рынок практически исключает этот вариант."
                )
            else:
                return (
                    f"Маловероятный сценарий ({alt_prob:.1f}%): резкая коррекция, "
                    f"неожиданное решение или внешний шок способны изменить исход. "
                    f"Рынок практически исключает этот вариант."
                )
        elif market_prob >= 65:
            return (
                f"Альтернативный исход ({alt_prob:.1f}%) возможен при существенном изменении "
                f"текущих условий или появлении нового доминирующего фактора. "
                f"Необходимо следить за ключевыми индикаторами."
            )
        else:
            return (
                f"Альтернативный исход практически равновероятен ({alt_prob:.1f}%). "
                f"Небольшое изменение условий способно переломить текущий тренд."
            )

    def _build_conclusion(
        self,
        clean_conclusion: str,
        display_prediction: str,
        semantic_outcome: str,
        is_negated: bool,
    ) -> str:
        if clean_conclusion and len(clean_conclusion) > 20:
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, clean_conclusion)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, clean_conclusion)
            return result

        return f"Следуем рыночной оценке: {display_prediction}."

    def _extract_market_leader_prob(self, market_probability: str, market_type: str) -> float:
        """Извлекает вероятность лидирующего исхода."""
        try:
            if market_type == "binary":
                yes_match = re.search(r'Yes:\s*([\d.]+)%', market_probability)
                no_match = re.search(r'No:\s*([\d.]+)%', market_probability)
                yes_prob = float(yes_match.group(1)) if yes_match else 0
                no_prob = float(no_match.group(1)) if no_match else 0
                return max(yes_prob, no_prob)
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
                    f"Позиции с такой вероятностью редко дают альфу — цена уже отражает консенсус трейдеров. "
                    f"Используй как подтверждение тренда, а не как точку входа."
                )
            elif market_prob >= 80:
                msg = (
                    f"Модель подтверждает рыночный консенсус — расхождение всего {delta:.1f}%. "
                    f"При вероятности {market_prob:.1f}% большинство участников уже заняли позиции. "
                    f"Потенциал для получения значимой альфы ограничен."
                )
            else:
                msg = (
                    f"Модель согласна с рынком — расхождение {delta:.1f}%. "
                    f"Явной недооценки или переоценки не обнаружено."
                )
        elif delta < 20:
            label = "⚠️ Слабый сигнал"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Модель оценивает вероятность на {delta:.1f}% {direction} рыночной ({market_prob:.1f}%). "
                f"Возможна небольшая неэффективность ценообразования. "
                f"Слабый сигнал — требует дополнительного подтверждения перед принятием решения."
            )
        else:
            label = "🔥 Потенциальная альфа"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Значительное расхождение: модель оценивает вероятность на {delta:.1f}% {direction} рыночной. "
                f"Это может указывать на реальную неэффективность ценообразования. "
                f"Высокий риск — тщательно проверь источники и логику перед принятием решения."
            )

        return label, msg

    def _clean_text(self, text: str) -> str:
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
        ]

        text_stripped = text.strip()
        for phrase in bad_starts:
            if text_stripped.startswith(phrase):
                return ""

        bad_exact = [
            "Альтернативный сценарий возможен при изменении внешних факторов.",
            "Alternative scenario depends on external factor changes.",
            "Альтернативный сценарий требует существенного изменения условий.",
        ]
        for phrase in bad_exact:
            if text_stripped == phrase:
                return ""

        text_stripped = (
            text_stripped
            .replace("##", "")
            .replace("###", "")
            .replace("**", "")
            .strip()
        )

        return text_stripped
