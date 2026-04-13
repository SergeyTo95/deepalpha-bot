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
        related_markets = market_data.get("related_markets", [])
        trend_summary = market_data.get("trend_summary", "Unknown")
        crowd_behavior = market_data.get("crowd_behavior", "Unknown")
        market_type = market_data.get("market_type", "binary")
        end_date = market_data.get("date_context", "Unknown")

        news_summary = news_data.get("news_summary", "")
        sentiment = news_data.get("sentiment", "Unknown")
        news_confidence = news_data.get("confidence", "Unknown")
        sources = news_data.get("sources", [])

        # Парсим рыночную вероятность
        market_prob_value, market_leader = self._parse_market_probability(
            market_probability, options, market_type
        )
        print(f"DecisionAgent.run: market_prob={market_prob_value}, leader={market_leader}")

        # Считаем дни до события
        days_to_event = self._days_to_event(end_date)
        print(f"DecisionAgent.run: days_to_event={days_to_event}")

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
            news_confidence=news_confidence,
            market_type=market_type,
            days_to_event=days_to_event,
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
            )

            if not wrapped.get("main_scenario") or not wrapped.get("conclusion"):
                print(f"DecisionAgent.run: calling SummaryAgent")
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
                print(f"DecisionAgent.run: valid result, probability={wrapped.get('probability')}")
                return wrapped

        print(f"DecisionAgent.run: using market-aligned fallback")
        return self._market_aligned_fallback(
            question=question,
            category=category,
            market_probability=market_probability,
            market_prob_value=market_prob_value,
            market_leader=market_leader,
            options=options,
            market_type=market_type,
            lang=lang,
        )

    def _parse_market_probability(
        self,
        market_probability: str,
        options: List[str],
        market_type: str,
    ) -> Tuple[float, str]:
        """Парсит рыночную вероятность и находит лидирующий вариант."""
        try:
            if market_type == "binary" or not options:
                # Формат: "Yes: 98.35% | No: 1.65%"
                match = re.search(r'Yes:\s*([\d.]+)%', str(market_probability))
                if match:
                    return float(match.group(1)), "Yes"
                match = re.search(r'([\d.]+)%', str(market_probability))
                if match:
                    return float(match.group(1)), "Yes"
                return 50.0, "Yes"
            else:
                # Multiple choice — находим лидера
                best_prob = 0.0
                best_option = options[0] if options else "Unknown"
                parts = str(market_probability).split("|")
                for part in parts:
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
        """Считает дни до события."""
        try:
            from datetime import datetime, timezone
            if not end_date or end_date == "Unknown":
                return None
            # Пробуем разные форматы
            for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]:
                try:
                    dt = datetime.strptime(end_date[:19], fmt[:len(end_date[:19])])
                    now = datetime.utcnow()
                    delta = (dt - now).days
                    return max(0, delta)
                except Exception:
                    continue
            return None
        except Exception:
            return None

    def _get_divergence_rules(
        self,
        market_prob_value: float,
        days_to_event: Optional[int],
    ) -> Tuple[float, str]:
        """Возвращает максимально допустимое отклонение и объяснение."""
        # Чем ближе событие — тем меньше отклонение
        time_factor = 1.0
        if days_to_event is not None:
            if days_to_event <= 3:
                time_factor = 0.3
            elif days_to_event <= 7:
                time_factor = 0.5
            elif days_to_event <= 14:
                time_factor = 0.7

        if market_prob_value >= 95:
            max_dev = 5 * time_factor
            rule = "market>95%: max deviation 5%"
        elif market_prob_value >= 90:
            max_dev = 10 * time_factor
            rule = "market>90%: max deviation 10%"
        elif market_prob_value >= 75:
            max_dev = 20 * time_factor
            rule = "market>75%: max deviation 20%"
        elif market_prob_value >= 50:
            max_dev = 30 * time_factor
            rule = "market>50%: max deviation 30%"
        else:
            max_dev = 40 * time_factor
            rule = "market<50%: max deviation 40%"

        return max_dev, rule

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
        news_confidence: str,
        market_type: str,
        days_to_event: Optional[int],
        lang: str = "en",
    ) -> str:

        news_block = news_summary[:400] if news_summary else ""
        days_str = f"{days_to_event} дней" if days_to_event is not None else "неизвестно"

        # Правила отклонения
        max_dev, rule = self._get_divergence_rules(market_prob_value, days_to_event)

        if market_type == "multiple_choice" and options:
            options_block = "\n".join([f"- {opt}" for opt in options])

            if lang == "ru":
                return f"""Ты старший квантовый аналитик предсказательных рынков DeepAlpha.

Вопрос: {question}
Категория: {category}
До события: {days_str}
Текущие ставки рынка: {market_probability}
Лидер рынка: {market_leader} ({market_prob_value:.1f}%)

Варианты ответа:
{options_block}

Тренд: {trend_summary[:150]}
Новости: {news_block}
Настроение: {sentiment}

ПРАВИЛА:
1. Рыночная вероятность — это главный сигнал (prior)
2. Максимальное отклонение от рынка: {max_dev:.0f}% ({rule})
3. Отклоняйся ТОЛЬКО если есть конкретный catalyst с доказательствами
4. Выбери КОНКРЕТНЫЙ вариант из списка — НЕ Yes/No!
5. Чем ближе дата события — тем больше доверяй рынку

Заполни ВСЕ пункты одной строкой:

Вероятность системы: [конкретный вариант и вероятность, например "Anthropic — 72%"]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [конкретные факты, почему именно этот вариант]
Расклад по вариантам: [все варианты с процентами через запятую]
Основной сценарий: [одно предложение]
Альтернативный сценарий: [одно предложение]
Вывод: [итог с учётом рыночных данных]""".strip()
            else:
                return f"""You are a senior quantitative analyst at DeepAlpha prediction markets.

Market: {question}
Category: {category}
Days to event: {days_to_event or 'unknown'}
Current market odds: {market_probability}
Market leader: {market_leader} ({market_prob_value:.1f}%)

Options:
{options_block}

Trend: {trend_summary[:150]}
News: {news_block}
Sentiment: {sentiment}

RULES:
1. Market probability is the primary signal (prior)
2. Maximum deviation from market: {max_dev:.0f}% ({rule})
3. Deviate ONLY if there is a specific catalyst with evidence
4. Choose ONE SPECIFIC option from the list — NOT Yes/No!
5. Closer to event date = trust market more

Fill ALL fields one line each:

System Probability: [specific option and probability, e.g. "Anthropic — 72%"]
Confidence: [High/Medium/Low]
Reasoning: [specific facts why this option wins]
Options Breakdown: [all options with percentages, comma separated]
Main Scenario: [one sentence]
Alternative Scenario: [one sentence]
Conclusion: [summary considering market data]""".strip()

        else:
            # Binary Yes/No
            if lang == "ru":
                return f"""Ты старший квантовый аналитик предсказательных рынков DeepAlpha.

Вопрос: {question}
Категория: {category}
До события: {days_str}
Текущие ставки рынка: {market_probability}
Рыночная вероятность Yes: {market_prob_value:.1f}%

Тренд: {trend_summary[:150]}
Новости: {news_block}
Настроение: {sentiment}

ПРАВИЛА:
1. Рыночная вероятность — это главный сигнал (prior)
2. Максимальное отклонение: {max_dev:.0f}% ({rule})
3. Отклоняйся ТОЛЬКО если есть конкретный catalyst с доказательствами
4. Если нет сильных доказательств — следуй рынку
5. Чем ближе дата события — тем больше доверяй рынку

Заполни ВСЕ пункты одной строкой:

Вероятность системы: [Yes или No и процент, например "Yes — 96%" или "No — 78%"]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [конкретные факты, почему именно такая вероятность]
Основной сценарий: [одно предложение]
Альтернативный сценарий: [одно предложение]
Вывод: [итог с учётом рыночных данных]""".strip()
            else:
                return f"""You are a senior quantitative analyst at DeepAlpha prediction markets.

Market: {question}
Category: {category}
Days to event: {days_to_event or 'unknown'}
Current market odds: {market_probability}
Market probability Yes: {market_prob_value:.1f}%

Trend: {trend_summary[:150]}
News: {news_block}
Sentiment: {sentiment}

RULES:
1. Market probability is the primary signal (prior)
2. Maximum deviation: {max_dev:.0f}% ({rule})
3. Deviate ONLY if there is a specific catalyst with evidence
4. If no strong evidence — follow the market
5. Closer to event date = trust market more

Fill ALL fields one line each:

System Probability: [Yes or No and percentage, e.g. "Yes — 96%" or "No — 78%"]
Confidence: [High/Medium/Low]
Reasoning: [specific facts why this probability]
Main Scenario: [one sentence]
Alternative Scenario: [one sentence]
Conclusion: [summary considering market data]""".strip()

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
        lines = text.splitlines()

        for line in lines:
            stripped = line.strip()
            matched = False

            all_keys = list(fields.keys()) + list(russian_map.keys())
            for key in all_keys:
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
        """Извлекает числовое значение вероятности из строки."""
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
        options_breakdown: str,
    ) -> Tuple[str, bool]:
        """
        Проверяет что вероятность не отклоняется от рынка больше допустимого.
        Возвращает (скорректированная_вероятность, была_ли_коррекция).
        """
        max_dev, rule = self._get_divergence_rules(market_prob_value, days_to_event)

        model_prob = self._extract_prob_value(prob_str)
        if model_prob is None:
            return prob_str, False

        # Для binary рынков проверяем отклонение
        if market_type == "binary":
            delta = abs(model_prob - market_prob_value)
            if delta > max_dev:
                print(f"DecisionAgent: DIVERGENCE TOO HIGH: model={model_prob}% market={market_prob_value}% delta={delta:.1f}% max={max_dev:.1f}% — adjusting to market")
                # Корректируем к рыночной вероятности
                adjusted = market_prob_value
                # Определяем Yes/No
                if "yes" in prob_str.lower() or model_prob >= 50:
                    return f"Yes — {adjusted:.1f}%", True
                else:
                    return f"No — {100 - adjusted:.1f}%", True

        return prob_str, False

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
            alt_scenario = (
                "Альтернативный сценарий возможен при изменении внешних факторов."
                if lang == "ru"
                else "Alternative scenario depends on external factor changes."
            )

        # Для multiple choice убираем случайные Yes/No
        if market_type == "multiple_choice":
            if probability.lower().startswith("yes — ") or probability.lower().startswith("no — "):
                if options_breakdown:
                    first = options_breakdown.split(",")[0].strip()
                    if first:
                        probability = first
                elif market_leader and market_leader not in ("Yes", "No"):
                    probability = f"{market_leader} — {market_prob_value:.1f}%"

        # Валидируем и корректируем вероятность для binary
        if market_type == "binary":
            probability, was_adjusted = self._validate_and_adjust_probability(
                probability, market_prob_value, market_leader,
                market_type, days_to_event, options_breakdown
            )
            if was_adjusted:
                if lang == "ru":
                    reasoning = f"Прогноз скорректирован в соответствии с рыночными данными. {reasoning}"
                else:
                    reasoning = f"Forecast adjusted to align with market data. {reasoning}"

        # Калибруем уверенность
        confidence = self._calibrate_confidence(
            confidence=confidence,
            probability=probability,
            market_prob_value=market_prob_value,
            market_type=market_type,
            days_to_event=days_to_event,
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

    def _calibrate_confidence(
        self,
        confidence: str,
        probability: str,
        market_prob_value: float,
        market_type: str,
        days_to_event: Optional[int],
    ) -> str:
        """Калибрует уверенность на основе согласия с рынком и времени."""
        conf_lower = confidence.lower()

        # Базовая уверенность из LLM
        if "high" in conf_lower or "высок" in conf_lower:
            base_score = 3
        elif "medium" in conf_lower or "средн" in conf_lower:
            base_score = 2
        else:
            base_score = 1

        # Проверяем согласие с рынком
        model_prob = self._extract_prob_value(probability)
        if model_prob is not None and market_type == "binary":
            delta = abs(model_prob - market_prob_value)
            if delta > 20:
                base_score -= 1  # Снижаем если сильно расходимся
            elif delta <= 10:
                base_score += 0  # Нейтрально если близко

        # Близость к событию повышает уверенность
        if days_to_event is not None and days_to_event <= 7:
            base_score = min(3, base_score + 1)

        if base_score >= 3:
            return "Высокая" if "высок" in confidence.lower() or "high" not in confidence.lower() else "High"
        elif base_score >= 2:
            return "Средняя" if "средн" in confidence.lower() or "medium" not in confidence.lower() else "Medium"
        else:
            return "Низкая" if "низк" in confidence.lower() or "low" not in confidence.lower() else "Low"

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
        lang: str = "ru",
    ) -> Dict[str, Any]:
        """Fallback который следует рыночной вероятности."""

        if market_type == "multiple_choice" and market_leader not in ("Yes", "No", "Unknown"):
            probability = f"{market_leader} — {market_prob_value:.1f}%"
        else:
            if market_prob_value >= 50:
                probability = f"Yes — {market_prob_value:.1f}%"
            else:
                probability = f"No — {100 - market_prob_value:.1f}%"

        if market_prob_value >= 80:
            confidence = "Высокая" if lang == "ru" else "High"
        elif market_prob_value >= 60:
            confidence = "Средняя" if lang == "ru" else "Medium"
        else:
            confidence = "Низкая" if lang == "ru" else "Low"

        if lang == "ru":
            reasoning = f"Прогноз основан на рыночных данных. Рынок оценивает вероятность в {market_prob_value:.1f}%."
            main_scenario = f"Рыночный консенсус указывает на '{market_leader}' с вероятностью {market_prob_value:.1f}%."
            alt_scenario = "Альтернативный сценарий возможен при появлении новых данных."
            conclusion = f"Следуем рыночной оценке: {probability}."
        else:
            reasoning = f"Forecast based on market data. Market estimates probability at {market_prob_value:.1f}%."
            main_scenario = f"Market consensus indicates '{market_leader}' with {market_prob_value:.1f}% probability."
            alt_scenario = "Alternative scenario possible if new data emerges."
            conclusion = f"Following market estimate: {probability}."

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
