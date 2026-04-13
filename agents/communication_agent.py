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

        # Шаг 4: Чистим и улучшаем тексты
        clean_reasoning = self._clean_text(reasoning)
        clean_scenario = self._clean_text(main_scenario)
        clean_alt = self._clean_text(alt_scenario)
        clean_conclusion = self._clean_text(conclusion)

        # Шаг 5: Генерируем семантические тексты если нужно
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

        final_alt = clean_alt if clean_alt else self._build_alt_scenario(semantic_outcome)

        return {
            "display_prediction": display_prediction,
            "semantic_outcome": semantic_outcome,
            "reasoning": clean_reasoning,
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
        """
        Извлекает семантический исход и числовую вероятность.
        Конвертирует Yes/No в читаемое предсказание.
        """
        if not probability_str:
            return probability_str, None

        # Парсим строку вида "Yes — 98.3%" или "NVIDIA — 98.3%"
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

        # Если уже не Yes/No — возвращаем как есть
        if raw_outcome.lower() not in ("yes", "no"):
            return raw_outcome, prob_val

        # Конвертируем Yes/No в семантику
        if raw_outcome.lower() == "yes":
            semantic = self._yes_to_semantic(question)
        else:
            semantic = self._no_to_semantic(question)

        return semantic if semantic else raw_outcome, prob_val

    def _yes_to_semantic(self, question: str) -> str:
        """
        Конвертирует Yes в семантическое предсказание.

        Will NVIDIA be the largest company? → NVIDIA
        Will Bitcoin hit $150k? → Bitcoin hits $150k
        Will Trump win the election? → Trump wins
        Will the Fed cut rates? → Fed cuts rates
        Will Russia enter Dovha Balka? → Russia enters Dovha Balka
        """
        q = re.sub(r'\?$', '', question.strip())

        # Известные сущности — возвращаем сразу
        known_entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH", "XRP",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Macron", "Modi", "Xi", "Fed", "ECB", "NATO", "SpaceX",
            "Intel", "AMD", "Netflix", "Disney", "Uber", "Airbnb",
        ]
        q_lower = q.lower()
        for entity in known_entities:
            if entity.lower() in q_lower:
                return entity

        # Паттерн: "Will [ENTITY] [verb]..."
        match = re.match(
            r'^Will\s+([A-Z][A-Za-z0-9\s&\.\-\']+?)\s+'
            r'(be|win|become|reach|hit|pass|exceed|lose|fall|drop|rise|'
            r'get|make|break|cross|stay|remain|achieve|sign|launch|release|'
            r'announce|complete|finish|happen|occur|fail|enter|capture|take)',
            q
        )
        if match:
            entity = match.group(1).strip()
            verb = match.group(2)
            verb_map = {
                "win": "wins", "be": "becomes", "become": "becomes",
                "reach": "reaches", "hit": "hits", "pass": "passes",
                "exceed": "exceeds", "rise": "rises", "fall": "falls",
                "get": "gets", "make": "makes", "break": "breaks",
                "cross": "crosses", "stay": "stays", "remain": "remains",
                "achieve": "achieves", "sign": "signs", "launch": "launches",
                "release": "releases", "announce": "announces",
                "complete": "completes", "finish": "finishes",
                "happen": "happens", "occur": "occurs", "fail": "fails",
                "enter": "enters", "capture": "captures", "take": "takes",
            }
            verb_form = verb_map.get(verb, verb + "s")

            # Короткий entity — только его
            if len(entity.split()) <= 3:
                return entity

        # Общий паттерн: "Will [rest]" → берём первые 5 слов
        match2 = re.match(r'^Will\s+(.+)', q)
        if match2:
            rest = match2.group(1).strip()
            words = rest.split()[:5]
            short = " ".join(words)
            if len(short) < 50:
                return short

        return ""

    def _no_to_semantic(self, question: str) -> str:
        """Конвертирует No в семантическое предсказание."""
        q = re.sub(r'\?$', '', question.strip())

        match = re.match(r'^Will\s+(.+)', q)
        if match:
            rest = match.group(1).strip()
            words = rest.split()[:4]
            entity = " ".join(words)
            if len(entity) < 40:
                return f"{entity} — нет"

        return ""

    def _build_scenario(
        self,
        clean_scenario: str,
        semantic_outcome: str,
        model_prob: float,
    ) -> str:
        """Строит основной сценарий с semantic outcome."""
        # Если сценарий хороший и не содержит Yes/No — используем
        if clean_scenario and len(clean_scenario) > 20:
            # Заменяем Yes/No на semantic
            result = re.sub(r'\bYes\b', semantic_outcome, clean_scenario)
            result = re.sub(r'\bNo\b', f"не {semantic_outcome.lower()}", result)
            return result

        # Генерируем семантический сценарий
        prob = model_prob or 50
        if prob >= 90:
            return (
                f"Рыночный консенсус и AI с высокой уверенностью указывают на: "
                f"{semantic_outcome}."
            )
        elif prob >= 70:
            return f"Умеренно высокая вероятность в пользу: {semantic_outcome}."
        elif prob >= 50:
            return f"Небольшой перевес в пользу: {semantic_outcome}."
        else:
            return f"Данный сценарий маловероятен. Преобладает альтернативный исход."

    def _build_alt_scenario(self, semantic_outcome: str) -> str:
        """Строит альтернативный сценарий."""
        if semantic_outcome and semantic_outcome.lower() not in ("yes", "no"):
            return f"При изменении ключевых факторов возможен альтернативный исход."
        return "Альтернативный сценарий возможен при появлении новых данных."

    def _build_conclusion(
        self,
        clean_conclusion: str,
        display_prediction: str,
        semantic_outcome: str,
    ) -> str:
        """Строит финальный вывод с semantic outcome."""
        if clean_conclusion and len(clean_conclusion) > 20:
            # Заменяем Yes/No
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
            return "📊 Анализ рынка", "Данных недостаточно для оценки."

        if delta < 5:
            label = "✅ Консенсус с рынком"
            if market_prob >= 90:
                msg = (
                    f"При вероятности {market_prob:.1f}% рынок уже учитывает всю информацию. "
                    f"Такие позиции редко дают альфу — используй как подтверждение тренда."
                )
            else:
                msg = f"Модель согласна с рынком (расхождение {delta:.1f}%). Явной недооценки не обнаружено."
        elif delta < 20:
            label = "⚠️ Слабый сигнал"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Модель оценивает вероятность на {delta:.1f}% {direction} рыночной. "
                f"Возможна небольшая неэффективность."
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
        """Убирает дефолтные фразы БЕЗ обрезки по последнему символу."""
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
            "Прогноз основан на рыночных данных.",
            "Рыночный консенсус указывает на",
        ]

        text_stripped = text.strip()

        for phrase in bad_phrases:
            if text_stripped.startswith(phrase[:30]):
                return ""
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
