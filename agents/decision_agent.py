import re
from typing import Any, Dict, List, Optional, Tuple

from services.llm_service import generate_decision_text


class DecisionAgent:
    def __init__(self) -> None:
        pass

    def run(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str = "en",
    ) -> Dict[str, Any]:
        print(f"DecisionAgent.run: called, lang={lang}")
        question = market_data.get("question", "Unknown market")
        print(f"DecisionAgent.run: question={question[:50]}")

        category = market_data.get("category", "Unknown")
        market_probability = market_data.get("market_probability", "Unknown")
        options = market_data.get("options", [])
        trend_summary = market_data.get("trend_summary", "Unknown")
        crowd_behavior = market_data.get("crowd_behavior", "Unknown")
        market_type = market_data.get("market_type", "binary")
        end_date = market_data.get("date_context", "Unknown")

        news_summary = news_data.get("news_summary", "")
        sentiment = news_data.get("sentiment", "Unknown")

        market_prob_value, market_leader = self._parse_market_probability(
            market_probability, options, market_type
        )
        print(f"DecisionAgent.run: market_prob={market_prob_value}, leader={market_leader}")

        days_to_event = self._days_to_event(end_date)
        market_balance = self._classify_balance(market_prob_value)
        semantic_type = self._classify_semantic_type(question, market_type)
        print(f"DecisionAgent.run: days={days_to_event}, balance={market_balance}, semantic={semantic_type}")

        prompt = self._build_prompt(
            question=question,
            category=category,
            market_probability=market_probability,
            market_prob_value=market_prob_value,
            market_leader=market_leader,
            options=options,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            news_summary=news_summary,
            sentiment=sentiment,
            market_type=market_type,
            days_to_event=days_to_event,
            market_balance=market_balance,
            semantic_type=semantic_type,
            lang=lang,
        )

        print(f"DecisionAgent.run: calling LLM, prompt length={len(prompt)}")
        raw_response = generate_decision_text(prompt)
        print(f"DecisionAgent.run: LLM response length={len(raw_response)}")

        if raw_response:
            parsed = self._parse_llm_output(raw_response, market_type=market_type)
            wrapped = self._wrap_llm_result(
                question=question,
                category=category,
                market_probability=market_probability,
                market_prob_value=market_prob_value,
                market_leader=market_leader,
                parsed=parsed,
                raw_text=raw_response,
                lang=lang,
                market_type=market_type,
                days_to_event=days_to_event,
                market_balance=market_balance,
            )

            if not wrapped.get("main_scenario") or not wrapped.get("conclusion"):
                from agents.summary_agent import SummaryAgent
                summary = SummaryAgent().run(
                    question=question,
                    category=category,
                    market_probability=str(market_probability),
                    probability=wrapped.get("probability", ""),
                    confidence=wrapped.get("confidence", ""),
                    reasoning=wrapped.get("reasoning", ""),
                    lang=lang,
                )
                wrapped["main_scenario"] = summary.get("main_scenario") or wrapped.get("main_scenario", "")
                wrapped["alt_scenario"] = summary.get("alt_scenario") or wrapped.get("alt_scenario", "")
                wrapped["conclusion"] = summary.get("conclusion") or wrapped.get("conclusion", "")

            if self._is_valid_result(wrapped):
                print(f"DecisionAgent.run: valid, probability={wrapped.get('probability')}")
                return wrapped

        print(f"DecisionAgent.run: using fallback")
        return self._market_aligned_fallback(
            question=question,
            category=category,
            market_probability=market_probability,
            market_prob_value=market_prob_value,
            market_leader=market_leader,
            options=options,
            market_type=market_type,
            days_to_event=days_to_event,
            market_balance=market_balance,
            news_summary=news_summary,
            trend_summary=trend_summary,
            lang=lang,
        )

    def _classify_semantic_type(self, question: str, market_type: str) -> str:
        """Определяет семантический тип рынка."""
        if market_type == "multiple_choice":
            return "multi_outcome"

        q = question.lower()

        threshold_keywords = [
            "exceed", "surpass", "above", "below", "reach", "hit",
            "more than", "less than", "over", "under", "cross",
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

    def _classify_balance(self, market_prob_value: float) -> str:
        if market_prob_value >= 85:
            return "strong_consensus"
        elif market_prob_value >= 65:
            return "moderate_consensus"
        elif market_prob_value >= 55:
            return "slight_lean"
        elif market_prob_value >= 45:
            return "balanced"
        else:
            return "lean_against"

    def _parse_market_probability(
        self,
        market_probability: str,
        options: List[str],
        market_type: str,
    ) -> Tuple[float, str]:
        """Парсит вероятность и возвращает ЛИДИРУЮЩИЙ исход."""
        try:
            if market_type == "binary" or not options:
                yes_match = re.search(r'Yes:\s*([\d.]+)%', str(market_probability))
                no_match = re.search(r'No:\s*([\d.]+)%', str(market_probability))
                yes_prob = float(yes_match.group(1)) if yes_match else None
                no_prob = float(no_match.group(1)) if no_match else None

                if yes_prob is not None and no_prob is not None:
                    if no_prob > yes_prob:
                        print(f"DecisionAgent: No leads ({no_prob}% > {yes_prob}%)")
                        return no_prob, "No"
                    return yes_prob, "Yes"
                elif yes_prob is not None:
                    no_prob_calc = 100 - yes_prob
                    if no_prob_calc > yes_prob:
                        return no_prob_calc, "No"
                    return yes_prob, "Yes"
                else:
                    match = re.search(r'([\d.]+)%', str(market_probability))
                    if match:
                        p = float(match.group(1))
                        if p < 50:
                            return 100 - p, "No"
                        return p, "Yes"
                    return 50.0, "Yes"
            else:
                best_prob = 0.0
                best_option = options[0] if options else "Unknown"
                for part in str(market_probability).split("|"):
                    match = re.search(r'(.+?):\s*([\d.]+)%', part.strip())
                    if match:
                        opt = match.group(1).strip()
                        prob = float(match.group(2))
                        if prob > best_prob:
                            best_prob = prob
                            best_option = opt
                return best_prob, best_option
        except Exception:
            return 50.0, "Unknown"

    def _days_to_event(self, end_date: str) -> Optional[int]:
        try:
            from datetime import datetime
            if not end_date or end_date == "Unknown":
                return None
            for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]:
                try:
                    dt = datetime.strptime(end_date[:19], fmt[:len(end_date[:19])])
                    delta = (dt - datetime.utcnow()).days
                    return max(0, delta)
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _get_divergence_rules(
        self,
        market_prob_value: float,
        days_to_event: Optional[int],
    ) -> Tuple[float, str]:
        time_factor = 1.0
        if days_to_event is not None:
            if days_to_event <= 3:
                time_factor = 0.3
            elif days_to_event <= 7:
                time_factor = 0.5
            elif days_to_event <= 14:
                time_factor = 0.7

        if market_prob_value >= 95:
            return 5 * time_factor, "market>95%: max 5%"
        elif market_prob_value >= 90:
            return 10 * time_factor, "market>90%: max 10%"
        elif market_prob_value >= 75:
            return 20 * time_factor, "market>75%: max 20%"
        elif market_prob_value >= 50:
            return 30 * time_factor, "market>50%: max 30%"
        else:
            return 40 * time_factor, "market<50%: max 40%"

    def _build_prompt(
        self,
        question: str,
        category: str,
        market_probability: str,
        market_prob_value: float,
        market_leader: str,
        options: List[str],
        trend_summary: str,
        crowd_behavior: str,
        news_summary: str,
        sentiment: str,
        market_type: str,
        days_to_event: Optional[int],
        market_balance: str,
        semantic_type: str,
        lang: str = "ru",
    ) -> str:

        news_block = news_summary[:500] if news_summary else "Нет данных"
        days_str = f"{days_to_event} дней" if days_to_event is not None else "неизвестно"
        max_dev, rule = self._get_divergence_rules(market_prob_value, days_to_event)

        # Инструкции по типу рынка
        type_instruction = self._get_type_instruction(
            semantic_type=semantic_type,
            market_leader=market_leader,
            market_prob_value=market_prob_value,
            question=question,
        )

        # Инструкции по балансу
        balance_instruction = self._get_balance_instruction(
            market_balance=market_balance,
            market_prob_value=market_prob_value,
        )

        leader_note = ""
        if market_type == "binary":
            leader_note = f"\nЛИДЕР: {market_leader} ({market_prob_value:.1f}%) — исход с НАИБОЛЬШЕЙ вероятностью."

        if market_type == "multiple_choice" and options:
            options_block = "\n".join([f"- {opt}" for opt in options])
            return f"""Ты старший аналитик предсказательных рынков DeepAlpha.
Отвечай ТОЛЬКО на русском языке. Давай ГЛУБОКИЙ причинно-следственный анализ.

ВОПРОС: {question}
КАТЕГОРИЯ: {category}
ДО СОБЫТИЯ: {days_str}
СТАВКИ РЫНКА: {market_probability}{leader_note}

ВАРИАНТЫ:
{options_block}

ТРЕНД: {trend_summary[:200]}
НОВОСТИ: {news_block}
НАСТРОЕНИЕ: {sentiment}

{type_instruction}
{balance_instruction}

ПРАВИЛА:
1. Выбери вариант с НАИБОЛЬШЕЙ вероятностью
2. НЕ выбирай Yes/No — только конкретный вариант из списка
3. Максимальное отклонение: {max_dev:.0f}%
4. НЕ повторяй цифры рынка — объясняй ПРИЧИНЫ
5. Все тексты только на русском

Заполни ВСЕ пункты (2-3 предложения каждый):

Вероятность системы: [вариант — %]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [конкретные причины без повторения цифр рынка]
Расклад по вариантам: [все варианты с %]
Основной сценарий: [условия реализации — НЕ повторяй прогноз]
Альтернативный сценарий: [конкретные триггеры изменения]
Вывод: [итоговая оценка]""".strip()

        else:
            return f"""Ты старший аналитик предсказательных рынков DeepAlpha.
Отвечай ТОЛЬКО на русском языке. Давай ГЛУБОКИЙ причинно-следственный анализ.

ВОПРОС: {question}
КАТЕГОРИЯ: {category}
ДО СОБЫТИЯ: {days_str}
СТАВКИ РЫНКА: {market_probability}{leader_note}

ТРЕНД: {trend_summary[:200]}
НОВОСТИ: {news_block}
НАСТРОЕНИЕ: {sentiment}

{type_instruction}
{balance_instruction}

ПРАВИЛА:
1. Лидирующий исход: {market_leader} ({market_prob_value:.1f}%)
2. Максимальное отклонение: {max_dev:.0f}% ({rule})
3. НЕ повторяй цифры рынка — объясняй ПРИЧИНЫ
4. Если No лидирует — объясни почему событие НЕ произойдёт
5. Все тексты только на русском языке

Заполни ВСЕ пункты (2-3 предложения каждый):

Вероятность системы: [Yes или No — %]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [механизм и причины без повторения цифр]
Основной сценарий: [условия при которых реализуется — НЕ повторяй прогноз]
Альтернативный сценарий: [конкретные триггеры изменения]
Вывод: [итоговая оценка]""".strip()

    def _get_type_instruction(
        self,
        semantic_type: str,
        market_leader: str,
        market_prob_value: float,
        question: str,
    ) -> str:
        """Инструкции по семантическому типу рынка."""
        if semantic_type == "binary_action":
            leader_meaning = "произойдёт" if market_leader == "Yes" else "НЕ произойдёт"
            return (
                f"ТИП РЫНКА: Бинарное действие (Will X do Y?)\n"
                f"Лидер '{market_leader}' означает: событие {leader_meaning}.\n"
                f"Объясни ПОЧЕМУ действие {'произойдёт' if market_leader == 'Yes' else 'НЕ произойдёт'} — "
                f"конкретные причины, не 'рынок оценивает'."
            )
        elif semantic_type == "binary_threshold":
            leader_meaning = "превысит порог" if market_leader == "Yes" else "НЕ превысит порог"
            return (
                f"ТИП РЫНКА: Пороговое событие (Will X exceed Y?)\n"
                f"Лидер '{market_leader}' означает: показатель {leader_meaning}.\n"
                f"Объясни какие факторы {'толкают к превышению' if market_leader == 'Yes' else 'удерживают ниже порога'}."
            )
        elif semantic_type == "single_entity":
            return (
                f"ТИП РЫНКА: Определение лидера (Who/Which will be #1?)\n"
                f"Верни название конкретной компании/персоны/актива как победителя.\n"
                f"Объясни почему именно этот участник лидирует."
            )
        else:
            return (
                f"ТИП РЫНКА: Множественный выбор.\n"
                f"Выбери вариант с наибольшей вероятностью и объясни его преимущество."
            )

    def _get_balance_instruction(
        self,
        market_balance: str,
        market_prob_value: float,
    ) -> str:
        """Инструкции по балансу рынка."""
        if market_balance in ("balanced", "slight_lean"):
            return (
                f"ВАЖНО — СБАЛАНСИРОВАННЫЙ РЫНОК ({market_prob_value:.1f}%):\n"
                f"- Явно укажи что ситуация неопределённая\n"
                f"- Объясни что может сдвинуть вероятность ВВЕРХ\n"
                f"- Объясни что может сдвинуть вероятность ВНИЗ\n"
                f"- НЕ делай уверенных заявлений\n"
                f"- Основной сценарий = условия реализации, НЕ повтор прогноза"
            )
        elif market_balance == "moderate_consensus":
            return (
                f"ВАЖНО — УМЕРЕННЫЙ КОНСЕНСУС ({market_prob_value:.1f}%):\n"
                f"- Объясни конкретные факторы создающие этот перевес\n"
                f"- Укажи что может изменить баланс\n"
                f"- Альтернативный сценарий = конкретные триггеры разворота"
            )
        elif market_balance == "strong_consensus":
            return (
                f"ВАЖНО — СИЛЬНЫЙ КОНСЕНСУС ({market_prob_value:.1f}%):\n"
                f"- Объясни МЕХАНИЗМ почему рынок так уверен\n"
                f"- Раскрой реальные причины уверенности участников\n"
                f"- Альтернативный сценарий = только экстраординарные события"
            )
        else:
            return ""

    def _parse_llm_output(self, text: str, market_type: str = "binary") -> Dict[str, str]:
        fields = {
            "System Probability": "",
            "Confidence": "",
            "Reasoning": "",
            "Main Scenario": "",
            "Alternative Scenario": "",
            "Conclusion": "",
            "Options Breakdown": "",
        }

        russian_map = {
            "Вероятность системы": "System Probability",
            "Системная вероятность": "System Probability",
            "Уверенность": "Confidence",
            "Логика": "Reasoning",
            "Рассуждение": "Reasoning",
            "Основной сценарий": "Main Scenario",
            "Альтернативный сценарий": "Alternative Scenario",
            "Вывод": "Conclusion",
            "Заключение": "Conclusion",
            "Расклад по вариантам": "Options Breakdown",
            "Варианты": "Options Breakdown",
        }

        current_key = None
        for line in text.splitlines():
            stripped = line.strip()
            matched = False

            for key in list(fields.keys()) + list(russian_map.keys()):
                prefix = f"{key}:"
                if stripped.startswith(prefix):
                    actual_key = russian_map.get(key, key)
                    value = stripped[len(prefix):].strip()
                    fields[actual_key] = value
                    current_key = actual_key
                    matched = True
                    break

            if not matched and current_key and stripped:
                if fields[current_key]:
                    fields[current_key] += " " + stripped
                else:
                    fields[current_key] = stripped

        return fields

    def _extract_prob_value(self, prob_str: str) -> Optional[float]:
        try:
            match = re.search(r'([\d.]+)%', str(prob_str))
            if match:
                return float(match.group(1))
        except Exception:
            pass
        return None

    def _validate_and_adjust_probability(
        self,
        prob_str: str,
        market_prob_value: float,
        market_leader: str,
        market_type: str,
        days_to_event: Optional[int],
    ) -> Tuple[str, bool]:
        max_dev, _ = self._get_divergence_rules(market_prob_value, days_to_event)
        model_prob = self._extract_prob_value(prob_str)
        if model_prob is None:
            return prob_str, False

        if market_type == "binary":
            prob_outcome = "yes" if "yes" in prob_str.lower() else "no"
            expected = market_leader.lower()

            if prob_outcome != expected and market_prob_value >= 80:
                print(f"DecisionAgent: Wrong outcome, correcting to {market_leader}")
                return f"{market_leader} — {market_prob_value:.1f}%", True

            delta = abs(model_prob - market_prob_value)
            if delta > max_dev:
                print(f"DecisionAgent: Divergence {delta:.1f}% > {max_dev:.1f}% — adjusting")
                return f"{market_leader} — {market_prob_value:.1f}%", True

        return prob_str, False

    def _calibrate_confidence(
        self,
        confidence: str,
        probability: str,
        market_prob_value: float,
        market_type: str,
        days_to_event: Optional[int],
        market_balance: str,
    ) -> str:
        conf_lower = confidence.lower()
        if "high" in conf_lower or "высок" in conf_lower:
            base_score = 3
        elif "medium" in conf_lower or "средн" in conf_lower:
            base_score = 2
        else:
            base_score = 1

        # Сбалансированный рынок — макс Medium
        if market_balance in ("balanced", "slight_lean"):
            base_score = min(base_score, 2)

        model_prob = self._extract_prob_value(probability)
        if model_prob is not None and market_type == "binary":
            delta = abs(model_prob - market_prob_value)
            if delta > 20:
                base_score -= 1

        if days_to_event is not None and days_to_event <= 7:
            base_score = min(3, base_score + 1)

        if base_score >= 3:
            return "Высокая" if "high" not in confidence.lower() else "High"
        elif base_score >= 2:
            return "Средняя" if "medium" not in confidence.lower() else "Medium"
        else:
            return "Низкая" if "low" not in confidence.lower() else "Low"

    def _wrap_llm_result(
        self,
        question: str,
        category: str,
        market_probability: str,
        market_prob_value: float,
        market_leader: str,
        parsed: Dict[str, str],
        raw_text: str,
        lang: str = "ru",
        market_type: str = "binary",
        days_to_event: Optional[int] = None,
        market_balance: str = "moderate_consensus",
    ) -> Dict[str, Any]:
        probability = parsed.get("System Probability", "").strip() or "N/A"
        confidence = parsed.get("Confidence", "").strip() or "Medium"
        reasoning = parsed.get("Reasoning", "").strip() or ""
        main_scenario = parsed.get("Main Scenario", "").strip() or ""
        alt_scenario = parsed.get("Alternative Scenario", "").strip() or ""
        conclusion = parsed.get("Conclusion", "").strip() or ""
        options_breakdown = parsed.get("Options Breakdown", "").strip() or ""

        if not conclusion:
            conclusion = reasoning
        if not reasoning:
            reasoning = conclusion

        if not alt_scenario:
            alt_scenario = "Альтернативный сценарий требует изменения ключевых факторов."

        # Multiple choice — убираем Yes/No
        if market_type == "multiple_choice":
            if probability.lower().startswith("yes") or probability.lower().startswith("no"):
                if options_breakdown:
                    first = options_breakdown.split(",")[0].strip()
                    if first:
                        probability = first
                elif market_leader not in ("Yes", "No"):
                    probability = f"{market_leader} — {market_prob_value:.1f}%"

        # Binary — валидируем
        if market_type == "binary":
            probability, was_adjusted = self._validate_and_adjust_probability(
                probability, market_prob_value, market_leader, market_type, days_to_event
            )
            if was_adjusted:
                reasoning = f"Прогноз скорректирован к рыночному лидеру. {reasoning}"

        confidence = self._calibrate_confidence(
            confidence, probability, market_prob_value,
            market_type, days_to_event, market_balance
        )

        return {
            "question": question,
            "category": category,
            "market_probability": market_probability,
            "probability": probability,
            "confidence": confidence,
            "reasoning": reasoning,
            "main_scenario": main_scenario,
            "alt_scenario": alt_scenario,
            "conclusion": conclusion,
            "options_breakdown": options_breakdown,
            "market_type": market_type,
            "raw_decision_text": raw_text,
        }

    def _is_valid_result(self, result: Dict[str, Any]) -> bool:
        probability = str(result.get("probability", "")).strip()
        confidence = str(result.get("confidence", "")).strip()
        if not probability or probability == "N/A":
            return False
        if not confidence:
            return False
        reasoning = str(result.get("reasoning", "")).strip()
        conclusion = str(result.get("conclusion", "")).strip()
        if not reasoning and not conclusion:
            return False
        return True

    def _market_aligned_fallback(
        self,
        question: str,
        category: str,
        market_probability: str,
        market_prob_value: float,
        market_leader: str,
        options: List[str],
        market_type: str,
        days_to_event: Optional[int],
        market_balance: str,
        news_summary: str,
        trend_summary: str,
        lang: str = "ru",
    ) -> Dict[str, Any]:

        if market_type == "multiple_choice" and market_leader not in ("Yes", "No", "Unknown"):
            probability = f"{market_leader} — {market_prob_value:.1f}%"
        else:
            probability = f"{market_leader} — {market_prob_value:.1f}%"

        if market_balance in ("balanced", "slight_lean"):
            confidence = "Низкая"
        elif market_prob_value >= 80:
            confidence = "Высокая"
        elif market_prob_value >= 60:
            confidence = "Средняя"
        else:
            confidence = "Низкая"

        days_str = f"{days_to_event} дней" if days_to_event is not None else ""
        time_ctx = ""
        if days_to_event is not None:
            if days_to_event <= 7:
                time_ctx = f"До события {days_str} — рынок близок к разрешению."
            elif days_to_event <= 30:
                time_ctx = f"До события {days_str}."

        if market_balance in ("balanced", "slight_lean"):
            reasoning = (
                f"Рынок в состоянии неопределённости — ни один исход не доминирует. "
                f"Вероятность {market_prob_value:.1f}% указывает на сбалансированность. "
                f"Исход зависит от появления нового катализатора."
            )
            main_scenario = (
                f"Сценарий '{market_leader}' реализуется при сохранении текущего баланса. "
                f"Любое значимое событие может сдвинуть вероятность в любую сторону."
            )
            alt_scenario = (
                f"Противоположный исход ({100 - market_prob_value:.1f}%) равновероятен. "
                f"Триггером может стать новая статистика, заявление или внешний шок."
            )
            conclusion = (
                f"Сбалансированный рынок — окончательный вывод преждевременен. "
                f"Небольшой перевес: {probability}."
            )
        elif market_balance == "strong_consensus":
            reasoning = (
                f"Консенсус {market_prob_value:.1f}% отражает высокую уверенность участников. "
                f"{time_ctx} Рынок учёл основную часть доступной информации."
            )
            main_scenario = (
                f"Исход '{market_leader}' реализуется при сохранении текущих условий. "
                f"Серьёзных угроз не выявлено."
            )
            alt_scenario = (
                f"Альтернативный исход ({100 - market_prob_value:.1f}%) возможен только "
                f"при резком изменении политики или масштабном внешнем шоке. "
                f"Рынок практически исключает этот сценарий."
            )
            conclusion = f"Высокий консенсус подтверждает: {probability}. {time_ctx}".strip()
        else:
            reasoning = (
                f"Умеренный консенсус {market_prob_value:.1f}% указывает на перевес. "
                f"{time_ctx} Ситуация ещё не окончательная."
            )
            main_scenario = (
                f"Наиболее вероятный исход: '{market_leader}' — {market_prob_value:.1f}%. "
                f"Требуется сохранение текущей динамики."
            )
            alt_scenario = (
                f"Альтернативный исход ({100 - market_prob_value:.1f}%) при изменении "
                f"ключевых макроэкономических или политических факторов."
            )
            conclusion = f"Следуем рыночной оценке: {probability}. {time_ctx}".strip()

        return {
            "question": question,
            "category": category,
            "market_probability": market_probability,
            "probability": probability,
            "confidence": confidence,
            "reasoning": reasoning,
            "main_scenario": main_scenario,
            "alt_scenario": alt_scenario,
            "conclusion": conclusion,
            "options_breakdown": "",
            "market_type": market_type,
            "raw_decision_text": "",
        }
