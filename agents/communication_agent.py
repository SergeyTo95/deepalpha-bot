
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

        # Определяем тип рынка семантически
        semantic_market_type = self._classify_semantic_type(question, market_type)

        # Семантический рендеринг
        semantic_outcome, prob_val, is_negated = self._extract_semantic_outcome(
            question=question,
            probability_str=probability,
            market_type=market_type,
            semantic_market_type=semantic_market_type,
        )

        prob_display = f"{prob_val:.1f}%" if prob_val else ""

        # Нормализуем в читаемый русский
        display_prediction = self._build_display_prediction(
            question=question,
            semantic_outcome=semantic_outcome,
            prob_val=prob_val,
            is_negated=is_negated,
            semantic_market_type=semantic_market_type,
        )

        # Delta и баланс
        model_prob = prob_val or 0.0
        market_prob = self._extract_market_leader_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None
        market_balance = self._classify_market_balance(market_prob)

        # Alpha detection
        alpha_label, alpha_message = self._detect_alpha(
            delta=delta,
            model_prob=model_prob,
            market_prob=market_prob,
            market_balance=market_balance,
        )

        # Чистим и строим тексты
        clean_reasoning = self._clean_text(reasoning)
        clean_scenario = self._clean_text(main_scenario)
        clean_alt = self._clean_text(alt_scenario)
        clean_conclusion = self._clean_text(conclusion)

        final_reasoning = self._build_reasoning(
            clean_reasoning=clean_reasoning,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            model_prob=model_prob,
            market_prob=market_prob,
            market_balance=market_balance,
        )

        final_scenario = self._build_scenario(
            clean_scenario=clean_scenario,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            model_prob=model_prob,
            market_balance=market_balance,
        )

        final_alt = self._build_alt_scenario(
            clean_alt=clean_alt,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            market_prob=market_prob,
            market_balance=market_balance,
        )

        final_conclusion = self._build_conclusion(
            clean_conclusion=clean_conclusion,
            display_prediction=display_prediction,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            market_balance=market_balance,
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

    def _classify_semantic_type(self, question: str, market_type: str) -> str:
        """
        Определяет семантический тип рынка:
        - binary_action: Will X do Y?
        - binary_threshold: Will X exceed/reach Y?
        - single_entity: Who/Which will be #1?
        - multi_outcome: множество исходов
        """
        if market_type == "multiple_choice":
            return "multi_outcome"

        q = question.lower()

        threshold_keywords = [
            "exceed", "surpass", "above", "below", "reach", "hit",
            "more than", "less than", "over", "under", "cross",
            "превысит", "достигнет", "выше", "ниже",
        ]
        if any(k in q for k in threshold_keywords):
            return "binary_threshold"

        entity_keywords = [
            "who will", "which company", "which team", "which country",
            "largest", "biggest", "most", "first to",
        ]
        if any(k in q for k in entity_keywords):
            return "single_entity"

        return "binary_action"

    def _build_display_prediction(
        self,
        question: str,
        semantic_outcome: str,
        prob_val: Optional[float],
        is_negated: bool,
        semantic_market_type: str,
    ) -> str:
        """Строит финальный текст прогноза в читаемом русском."""
        prob_str = f"{prob_val:.1f}%" if prob_val else ""

        if not prob_str:
            return semantic_outcome

        # Если уже нормальный semantic — используем
        if semantic_outcome.lower() not in ("yes", "no"):
            # Проверяем что нет сломанного английского
            if self._contains_broken_english(semantic_outcome):
                fixed = self._fix_broken_english(question, semantic_outcome, is_negated)
                return f"{fixed} — {prob_str}"
            return f"{semantic_outcome} — {prob_str}"

        return f"{semantic_outcome} — {prob_str}"

    def _contains_broken_english(self, text: str) -> bool:
        """Проверяет наличие сломанных английских фраз."""
        broken_patterns = [
            r'\binflation\s+reach\b',
            r'\binflation\s+exceed\b',
            r'\brate\s+cut\b',
            r'\bwin\s+the\b',
            r'\bhit\s+\$',
            r'\bmore than\b',
            r'\bless than\b',
        ]
        text_lower = text.lower()
        return any(re.search(p, text_lower) for p in broken_patterns)

    def _fix_broken_english(self, question: str, outcome: str, is_negated: bool) -> str:
        """Исправляет сломанные английские фразы."""
        q = question.lower()

        # Инфляция
        if "inflation" in q:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', q)
            threshold = match.group(0) if match else ""
            year_match = re.search(r'(202\d)', question)
            year = year_match.group(1) if year_match else ""
            year_str = f" в {year} году" if year else ""
            if is_negated:
                return f"Инфляция НЕ превысит {threshold}{year_str}"
            return f"Инфляция превысит {threshold}{year_str}"

        # Ставки
        if "rate" in q and ("cut" in q or "decrease" in q):
            entity = self._extract_central_bank(q)
            if is_negated:
                return f"{entity} НЕ снизит ставку"
            return f"{entity} снизит ставку"

        if "rate" in q and ("hike" in q or "raise" in q or "increase" in q):
            entity = self._extract_central_bank(q)
            if is_negated:
                return f"{entity} НЕ повысит ставку"
            return f"{entity} повысит ставку"

        return outcome

    def _extract_central_bank(self, q: str) -> str:
        banks = {
            "bank of japan": "Банк Японии",
            "boj": "Банк Японии",
            "federal reserve": "ФРС",
            "fed ": "ФРС",
            "ecb": "ЕЦБ",
            "bank of england": "Банк Англии",
            "boe": "Банк Англии",
            "rba": "РБА",
        }
        for en, ru in banks.items():
            if en in q:
                return ru
        return "Центробанк"

    def _extract_semantic_outcome(
        self,
        question: str,
        probability_str: str,
        market_type: str,
        semantic_market_type: str,
    ) -> Tuple[str, Optional[float], bool]:
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

        if raw_outcome.lower() not in ("yes", "no"):
            return raw_outcome, prob_val, False

        is_negated = raw_outcome.lower() == "no"

        if is_negated:
            semantic = self._no_to_semantic(question, semantic_market_type)
        else:
            semantic = self._yes_to_semantic(question, semantic_market_type)

        return semantic if semantic else raw_outcome, prob_val, is_negated

    def _yes_to_semantic(self, question: str, semantic_type: str) -> str:
        """
        TYPE A (binary_action): Will X do Y? → X сделает Y
        TYPE B (binary_threshold): Will X exceed Y? → X превысит Y
        TYPE C (single_entity): Who will be #1? → EntityName
        """
        q = re.sub(r'\?$', '', question.strip())
        q_lower = q.lower()

        # TYPE B — пороговые события
        if semantic_type == "binary_threshold":
            result = self._convert_threshold_yes(q)
            if result:
                return result

        # Известные сущности
        known_entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH", "XRP",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Macron", "Modi", "Xi", "Fed", "ECB", "NATO", "SpaceX",
            "Intel", "AMD", "Netflix", "Disney", "Uber", "Airbnb",
            "Russia", "Ukraine", "China", "Iran", "Israel",
            "Bank of Japan", "BOJ", "OPEC", "IMF", "MicroStrategy",
        ]
        for entity in known_entities:
            if entity.lower() in q_lower:
                return entity

        # TYPE A — действие
        match = re.match(
            r'^Will\s+([A-Z][A-Za-z0-9\s&\.\-\']+?)\s+'
            r'(be|win|become|reach|hit|pass|exceed|lose|fall|drop|rise|'
            r'get|make|break|cross|stay|remain|achieve|sign|launch|'
            r'announce|complete|finish|happen|occur|fail|enter|take|'
            r'deploy|implement|approve|reject|survive|collapse|'
            r'decrease|increase|cut|raise|hike|pause|hold|sell|buy)',
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

    def _no_to_semantic(self, question: str, semantic_type: str) -> str:
        """
        TYPE A: Will X do Y? → X НЕ сделает Y
        TYPE B: Will X exceed Y? → X НЕ превысит Y
        """
        q = re.sub(r'\?$', '', question.strip())
        q_lower = q.lower()

        # TYPE B — пороговые события с отрицанием
        if semantic_type == "binary_threshold":
            result = self._convert_threshold_no(q)
            if result:
                return result

        # Известные сущности
        entity_map = [
            ("MicroStrategy", "MicroStrategy"),
            ("Bank of Japan", "Банк Японии"),
            ("BOJ", "Банк Японии"),
            ("Federal Reserve", "ФРС"),
            ("Fed", "ФРС"),
            ("ECB", "ЕЦБ"),
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
            ("Russia", "Россия"),
            ("Ukraine", "Украина"),
            ("Iran", "Иран"),
            ("Israel", "Израиль"),
            ("China", "Китай"),
        ]

        entity_ru = None
        for en, ru in entity_map:
            if en.lower() in q_lower:
                entity_ru = ru
                break

        # Действие
        action_map = [
            ("sell bitcoin", "НЕ продаст Bitcoin"),
            ("sell any bitcoin", "НЕ продаст Bitcoin"),
            ("buy bitcoin", "НЕ купит Bitcoin"),
            ("decrease rates", "НЕ снизит ставку"),
            ("cut rates", "НЕ снизит ставку"),
            ("raise rates", "НЕ повысит ставку"),
            ("hike rates", "НЕ повысит ставку"),
            ("hold rates", "НЕ сохранит ставку"),
            ("win the election", "НЕ победит на выборах"),
            ("win the", "НЕ победит"),
            ("win ", "НЕ победит"),
            ("become president", "НЕ станет президентом"),
            ("become", "НЕ станет"),
            ("reach", "НЕ достигнет"),
            ("hit $", "НЕ достигнет"),
            ("hit ", "НЕ достигнет"),
            ("pass", "НЕ пройдёт"),
            ("enter", "НЕ войдёт"),
            ("launch", "НЕ запустит"),
            ("sign", "НЕ подпишет"),
            ("collapse", "НЕ обрушится"),
            ("approve", "НЕ одобрит"),
            ("reject", "НЕ отклонит"),
            ("exceed", "НЕ превысит"),
            ("increase", "НЕ вырастет"),
            ("decrease", "НЕ снизится"),
            ("file for bankruptcy", "НЕ обанкротится"),
            ("go bankrupt", "НЕ обанкротится"),
        ]

        action_ru = None
        for en_action, ru_action in action_map:
            if en_action in q_lower:
                action_ru = ru_action
                break

        if entity_ru and action_ru:
            return f"{entity_ru} {action_ru}"

        if entity_ru:
            return f"{entity_ru} — этого не произойдёт"

        # Общий паттерн
        match = re.match(r'^Will\s+(.+)', q)
        if match:
            rest = match.group(1).strip()
            words = rest.split()[:4]
            entity = " ".join(words)
            if len(entity) < 40:
                return f"{entity} — нет"

        return ""

    def _convert_threshold_yes(self, q: str) -> str:
        """Конвертирует пороговый вопрос в утвердительное предсказание."""
        q_lower = q.lower()

        # Инфляция
        if "inflation" in q_lower:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', q)
            threshold = match.group(0) if match else "порогового значения"
            year_match = re.search(r'(202\d)', q)
            year_str = f" в {year_match.group(1)} году" if year_match else ""
            return f"Инфляция превысит {threshold}{year_str}"

        # Bitcoin/цена
        if "bitcoin" in q_lower or "btc" in q_lower:
            match = re.search(r'\$[\d,]+[k]?|\d+k', q, re.IGNORECASE)
            price = match.group(0) if match else "целевого уровня"
            return f"Bitcoin достигнет {price}"

        # Общий порог
        match = re.match(r'^Will\s+(.+?)\s+(exceed|surpass|reach|hit|pass|cross|go above|be above)\s+(.+)', q, re.IGNORECASE)
        if match:
            subject = match.group(1).strip()
            threshold = match.group(3).strip()
            return f"{subject} превысит {threshold}"

        return ""

    def _convert_threshold_no(self, q: str) -> str:
        """Конвертирует пороговый вопрос в отрицательное предсказание."""
        q_lower = q.lower()

        if "inflation" in q_lower:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', q)
            threshold = match.group(0) if match else "порогового значения"
            year_match = re.search(r'(202\d)', q)
            year_str = f" в {year_match.group(1)} году" if year_match else ""
            return f"Инфляция НЕ превысит {threshold}{year_str}"

        if "bitcoin" in q_lower or "btc" in q_lower:
            match = re.search(r'\$[\d,]+[k]?|\d+k', q, re.IGNORECASE)
            price = match.group(0) if match else "целевого уровня"
            return f"Bitcoin НЕ достигнет {price}"

        match = re.match(r'^Will\s+(.+?)\s+(exceed|surpass|reach|hit|pass|cross|go above|be above)\s+(.+)', q, re.IGNORECASE)
        if match:
            subject = match.group(1).strip()
            threshold = match.group(3).strip()
            return f"{subject} НЕ превысит {threshold}"

        return ""

    def _classify_market_balance(self, market_prob: float) -> str:
        if market_prob >= 85:
            return "strong_consensus"
        elif market_prob >= 65:
            return "moderate_consensus"
        elif market_prob >= 55:
            return "slight_lean"
        elif market_prob >= 45:
            return "balanced"
        else:
            return "lean_against"

    def _build_reasoning(
        self,
        clean_reasoning: str,
        semantic_outcome: str,
        is_negated: bool,
        model_prob: float,
        market_prob: float,
        market_balance: str,
    ) -> str:
        if clean_reasoning and len(clean_reasoning) > 30:
            result = clean_reasoning
            result = re.sub(r'[Рр]ынок оценивает вероятность[^\.]+\.', '', result).strip()
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            if len(result) > 30:
                return result

        prob = market_prob or model_prob or 50

        if market_balance == "strong_consensus":
            return (
                f"Подавляющий консенсус трейдеров ({prob:.1f}%) говорит о том, что "
                f"профессиональные участники уже учли доступную информацию в цене. "
                f"Уровень уверенности настолько высок, что любое отклонение требует "
                f"экстраординарных доказательств."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Умеренный консенсус ({prob:.1f}%) указывает на устойчивый перевес, "
                f"но ситуация ещё не окончательная. "
                f"Ключевые факторы складываются в пользу текущего лидера, "
                f"однако возможны изменения при новых данных."
            )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Рынок в состоянии неопределённости — вероятность около {prob:.1f}%. "
                f"Ни один из факторов не доминирует однозначно. "
                f"Такие рынки реагируют резко на новые данные или события."
            )
        else:
            return (
                f"Текущие условия не благоприятствуют основному сценарию. "
                f"Вероятность {prob:.1f}% указывает на перевес альтернативного исхода."
            )

    def _build_scenario(
        self,
        clean_scenario: str,
        semantic_outcome: str,
        is_negated: bool,
        model_prob: float,
        market_balance: str,
    ) -> str:
        if clean_scenario and len(clean_scenario) > 30:
            result = clean_scenario
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            result = result.replace("указывают на:", "подтверждают:")
            result = result.replace("указывают на: ", "")
            return result

        if market_balance == "strong_consensus":
            if is_negated:
                return (
                    f"Сценарий реализуется при сохранении текущей обстановки. "
                    f"Условия, необходимые для противоположного исхода, отсутствуют — "
                    f"нет признаков разворота."
                )
            return (
                f"Для реализации сценария достаточно сохранения текущих условий. "
                f"Тренд устойчив, серьёзных угроз не выявлено."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Сценарий реализуется если текущая динамика сохранится "
                f"и не появится доминирующий противоположный катализатор. "
                f"Ключевые индикаторы пока указывают на текущего лидера."
            )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Сценарий реализуется при: сохранении текущего баланса факторов, "
                f"отсутствии неожиданных событий и постепенном смещении консенсуса. "
                f"Любой значимый катализатор может изменить расклад."
            )
        else:
            return (
                f"Умеренная вероятность реализации — требуется подтверждение "
                f"со стороны дополнительных факторов."
            )

    def _build_alt_scenario(
        self,
        clean_alt: str,
        semantic_outcome: str,
        is_negated: bool,
        market_prob: float,
        market_balance: str,
    ) -> str:
        if clean_alt and len(clean_alt) > 30:
            bad = ["внешних факторов", "external factor", "изменении внешних"]
            if not any(p in clean_alt.lower() for p in bad):
                return clean_alt

        alt_prob = 100 - market_prob

        if market_balance == "strong_consensus":
            return (
                f"Маловероятный сценарий ({alt_prob:.1f}%): резкое изменение политики "
                f"регулятора, неожиданное геополитическое событие или масштабный "
                f"макроэкономический шок. Рынок практически исключает этот вариант."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Альтернативный сценарий ({alt_prob:.1f}%): смена монетарной политики, "
                f"неожиданные данные по инфляции или занятости, "
                f"либо разворот рыночного сентимента могут переломить тренд."
            )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Альтернативный исход ({alt_prob:.1f}%) практически равновероятен. "
                f"Триггером может стать: выход важной статистики, "
                f"заявление ключевых лиц или неожиданное событие на рынке."
            )
        else:
            return (
                f"Альтернативный исход ({alt_prob:.1f}%) остаётся возможным "
                f"при позитивном развитии ключевых факторов."
            )

    def _build_conclusion(
        self,
        clean_conclusion: str,
        display_prediction: str,
        semantic_outcome: str,
        is_negated: bool,
        market_balance: str,
    ) -> str:
        if clean_conclusion and len(clean_conclusion) > 20:
            result = clean_conclusion
            result = re.sub(r'[Рр]ынок оценивает вероятность[^\.]+\.', '', result).strip()
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            if len(result) > 20:
                return result

        if market_balance in ("balanced", "slight_lean"):
            return (
                f"Сбалансированный рынок — окончательный вывод преждевременен. "
                f"Небольшой перевес: {display_prediction}. Следи за новыми данными."
            )
        elif market_balance == "strong_consensus":
            return f"Высокий консенсус подтверждает: {display_prediction}."
        else:
            return f"Следуем рыночной оценке: {display_prediction}."

    def _extract_market_leader_prob(self, market_probability: str, market_type: str) -> float:
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
        market_balance: str,
    ) -> Tuple[str, str]:
        if delta is None:
            return "📊 Анализ рынка", "Данных недостаточно для оценки."

        if market_balance in ("balanced", "slight_lean"):
            return (
                "🟡 Сигнал: сбалансированный рынок",
                (
                    f"Вероятность около {market_prob:.1f}% — явного консенсуса нет. "
                    f"Рынок не определился с исходом. "
                    f"Возможна альфа при появлении новых данных — следи за катализаторами."
                )
            )

        if delta < 5:
            label = "✅ Консенсус с рынком"
            if market_prob >= 95:
                msg = (
                    f"При вероятности {market_prob:.1f}% рынок уже учёл всю доступную информацию. "
                    f"Позиции с таким консенсусом редко дают значимую альфу. "
                    f"Используй как подтверждение тренда."
                )
            else:
                msg = (
                    f"Модель подтверждает рыночный консенсус — расхождение {delta:.1f}%. "
                    f"Явной недооценки не обнаружено."
                )
        elif delta < 20:
            label = "⚠️ Слабый сигнал"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Модель оценивает вероятность на {delta:.1f}% {direction} рыночной. "
                f"Возможна небольшая неэффективность. Требует дополнительного подтверждения."
            )
        else:
            label = "🔥 Потенциальная альфа"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Значительное расхождение на {delta:.1f}% {direction} рыночной оценки. "
                f"Возможна реальная неэффективность ценообразования. "
                f"Высокий риск — проверь тщательно перед решением."
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
