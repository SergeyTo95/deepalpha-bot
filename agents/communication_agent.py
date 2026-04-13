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

        # Нормализуем prediction в читаемый русский
        display_prediction = self._normalize_prediction_text(
            question=question,
            semantic_outcome=semantic_outcome,
            prob_val=prob_val,
            is_negated=is_negated,
        )

        # Delta и alpha
        model_prob = prob_val or 0.0
        market_prob = self._extract_market_leader_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None

        # Определяем тип рынка по вероятности
        market_balance = self._classify_market_balance(market_prob)

        alpha_label, alpha_message = self._detect_alpha(
            delta=delta,
            model_prob=model_prob,
            market_prob=market_prob,
            market_balance=market_balance,
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
            market_balance=market_balance,
            question=question,
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

    def _classify_market_balance(self, market_prob: float) -> str:
        """Классифицирует рынок по уровню консенсуса."""
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

    def _normalize_prediction_text(
        self,
        question: str,
        semantic_outcome: str,
        prob_val: Optional[float],
        is_negated: bool,
    ) -> str:
        """
        Нормализует prediction в читаемый русский.

        Examples:
        "inflation reach more than 4%" → "Инфляция превысит 4%"
        "Bank of Japan" → "Банк Японии НЕ снизит ставку"
        "NVIDIA" → "NVIDIA — 98.1%"
        """
        prob_str = f"{prob_val:.1f}%" if prob_val else ""

        # Переводим известные английские фрагменты
        translations = {
            "inflation reach more than": "Инфляция превысит",
            "inflation exceed": "Инфляция превысит",
            "inflation above": "Инфляция выше",
            "inflation below": "Инфляция ниже",
            "inflation hit": "Инфляция достигнет",
            "rate cut": "снижение ставки",
            "rate hike": "повышение ставки",
            "win the election": "победит на выборах",
            "win the championship": "победит в чемпионате",
            "reach all-time high": "достигнет исторического максимума",
            "hit all-time high": "достигнет исторического максимума",
            "go bankrupt": "обанкротится",
            "file for bankruptcy": "подаст на банкротство",
        }

        outcome_lower = semantic_outcome.lower()
        translated = semantic_outcome

        for en, ru in translations.items():
            if en in outcome_lower:
                translated = semantic_outcome.replace(en, ru)
                translated = translated[:1].upper() + translated[1:]
                break

        # Если outcome содержит английские слова — оставляем как есть (бренды)
        # но добавляем НЕ если negated
        if is_negated and "нет" not in translated.lower() and "не " not in translated.lower():
            if translated == semantic_outcome:  # не переведено
                pass  # оставляем semantic как есть, он уже содержит отрицание

        result = f"{translated} — {prob_str}" if prob_str else translated
        return result

    def _extract_semantic_outcome(
        self,
        question: str,
        probability_str: str,
        market_type: str,
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
            semantic = self._no_to_semantic(question)
        else:
            semantic = self._yes_to_semantic(question)

        return semantic if semantic else raw_outcome, prob_val, is_negated

    def _yes_to_semantic(self, question: str) -> str:
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
        q = re.sub(r'\?$', '', question.strip())

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
            ("China", "Китай"),
            ("US", "США"),
        ]

        q_lower = q.lower()
        entity_ru = None
        for en, ru in known_entities:
            if en.lower() in q_lower:
                entity_ru = ru
                break

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
            "exceed": "НЕ превысит",
            "increase": "НЕ вырастет",
            "decrease": "НЕ снизится",
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
        market_balance: str,
        question: str,
    ) -> str:
        if clean_reasoning and len(clean_reasoning) > 30:
            # Убираем повторение рынка
            result = clean_reasoning
            result = re.sub(
                r'[Рр]ынок оценивает вероятность[^\.]+\.',
                '', result
            ).strip()
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            if len(result) > 30:
                return result

        # Генерируем по типу рынка
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
                f"Рынок находится в состоянии неопределённости — вероятность около {prob:.1f}%. "
                f"Это сбалансированная ситуация: ни один из факторов не доминирует однозначно. "
                f"Такие рынки часто реагируют резко на новые данные или события."
            )
        else:
            return (
                f"Перевес в пользу альтернативного исхода ({prob:.1f}%). "
                f"Текущие условия не благоприятствуют основному сценарию."
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
                    f"Для реализации сценария '{semantic_outcome}' достаточно сохранения "
                    f"текущей макроэкономической или политической обстановки. "
                    f"Условия, необходимые для противоположного исхода, отсутствуют."
                )
            else:
                return (
                    f"Сценарий реализуется при сохранении текущих условий. "
                    f"Для этого необходимо отсутствие резких внешних шоков "
                    f"и продолжение действующего тренда."
                )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Сценарий '{semantic_outcome}' реализуется если: "
                f"ключевые индикаторы продолжат текущую динамику, "
                f"не появится доминирующий противоположный катализатор, "
                f"и рыночный баланс сместится в сторону текущего лидера."
            )
        else:
            return (
                f"Умеренная вероятность реализации: '{semantic_outcome}'. "
                f"Требуется подтверждение со стороны дополнительных факторов."
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
            bad_phrases = ["внешних факторов", "external factor"]
            is_generic = any(p in clean_alt.lower() for p in bad_phrases)
            if not is_generic:
                return clean_alt

        alt_prob = 100 - market_prob

        if market_balance == "strong_consensus":
            return (
                f"Альтернативный исход ({alt_prob:.1f}%) возможен при: "
                f"резком изменении ключевой политики, неожиданном решении регулятора "
                f"или макроэкономическом шоке. "
                f"Рынок практически исключает этот сценарий."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Альтернативный сценарий ({alt_prob:.1f}%): изменение баланса ключевых факторов, "
                f"неожиданные данные по инфляции/занятости/геополитике "
                f"или разворот рыночного сентимента могут переломить тренд."
            )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"При сбалансированном рынке альтернативный исход ({alt_prob:.1f}%) "
                f"почти равновероятен. "
                f"Триггером может стать: выход важной статистики, "
                f"заявление ключевых лиц или неожиданное событие."
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
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            # Убираем повторение рынка
            result = re.sub(
                r'[Рр]ынок оценивает вероятность[^\.]+\.',
                '', result
            ).strip()
            if len(result) > 20:
                return result

        if market_balance in ("balanced", "slight_lean"):
            return (
                f"Сбалансированный рынок — окончательный вывод преждевременен. "
                f"Небольшой перевес: {display_prediction}. "
                f"Следи за новыми данными."
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

        # Для сбалансированных рынков — специальный блок
        if market_balance in ("balanced", "slight_lean"):
            return (
                "🟡 Сигнал: сбалансированный рынок",
                (
                    f"Вероятность близка к 50% — рынок не определился с исходом. "
                    f"Это может означать реальную неопределённость или недостаток информации. "
                    f"Возможна альфа при появлении новых данных — следи за катализаторами."
                )
            )

        if delta < 5:
            label = "✅ Консенсус с рынком"
            if market_prob >= 90:
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
                f"Модель оценивает вероятность на {delta:.1f}% {direction} рыночной ({market_prob:.1f}%). "
                f"Возможна небольшая неэффективность. "
                f"Требует дополнительного подтверждения."
            )
        else:
            label = "🔥 Потенциальная альфа"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Значительное расхождение на {delta:.1f}% {direction} рыночной оценки. "
                f"Возможна реальная неэффективность ценообразования. "
                f"Высокий риск — проверь тщательно перед принятием решения."
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
