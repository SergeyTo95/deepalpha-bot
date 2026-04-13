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

        # Парсим рыночную вероятность — выбираем ЛИДИРУЮЩИЙ исход
        market_prob_value, market_leader = self._parse_market_probability(
            market_probability, options, market_type
        )
        print(f"DecisionAgent.run: market_prob={market_prob_value}, leader={market_leader}")

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
            days_to_event=days_to_event,
            news_summary=news_summary,
            trend_summary=trend_summary,
            lang=lang,
        )

    def _parse_market_probability(
        self,
        market_probability: str,
        options: List[str],
        market_type: str,
    ) -> Tuple[float, str]:
        """
        Парсит рыночную вероятность и возвращает ЛИДИРУЮЩИЙ исход.
        Всегда выбирает исход с МАКСИМАЛЬНОЙ вероятностью.
        """
        try:
            if market_type == "binary" or not options:
                # Ищем Yes и No вероятности
                yes_match = re.search(r'Yes:\s*([\d.]+)%', str(market_probability))
                no_match = re.search(r'No:\s*([\d.]+)%', str(market_probability))

                yes_prob = float(yes_match.group(1)) if yes_match else None
                no_prob = float(no_match.group(1)) if no_match else None

                if yes_prob is not None and no_prob is not None:
                    # Выбираем лидера по максимальной вероятности
                    if no_prob > yes_prob:
                        print(f"DecisionAgent: No leads ({no_prob}% > {yes_prob}%)")
                        return no_prob, "No"
                    else:
                        return yes_prob, "Yes"
                elif yes_prob is not None:
                    no_prob = 100 - yes_prob
                    if no_prob > yes_prob:
                        return no_prob, "No"
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
        try:
            from datetime import datetime
            if not end_date or end_date == "Unknown":
                return None
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
        time_factor = 1.0
        if days_to_event is not None:
            if days_to_event <= 3:
                time_factor = 0.3
            elif days_to_event <= 7:
                time_factor = 0.5
            elif days_to_event <= 14:
                time_factor = 0.7

        if market_prob_value >= 95:
            return 5 * time_factor, "market>95%: max deviation 5%"
        elif market_prob_value >= 90:
            return 10 * time_factor, "market>90%: max deviation 10%"
        elif market_prob_value >= 75:
            return 20 * time_factor, "market>75%: max deviation 20%"
        elif market_prob_value >= 50:
            return 30 * time_factor, "market>50%: max deviation 30%"
        else:
            return 40 * time_factor, "market<50%: max deviation 40%"

    def _build_context_description(
        self,
        market_prob_value: float,
        market_leader: str,
        days_to_event: Optional[int],
        trend_summary: str,
        news_summary: str,
        category: str,
    ) -> str:
        parts = []

        if days_to_event is not None:
            if days_to_event <= 7:
                parts.append(f"До события {days_to_event} дней — рынок близок к разрешению, эффективность максимальна.")
            elif days_to_event <= 30:
                parts.append(f"До события {days_to_event} дней — рынок активно формирует консенсус.")
            else:
                parts.append(f"До события более {days_to_event} дней — возможна высокая неопределённость.")

        if market_prob_value >= 95:
            parts.append(f"Рыночный консенсус {market_prob_value:.1f}% в пользу '{market_leader}' — почти полное единодушие участников.")
        elif market_prob_value >= 80:
            parts.append(f"Сильный рыночный консенсус {market_prob_value:.1f}% в пользу '{market_leader}'.")
        elif market_prob_value >= 60:
            parts.append(f"Умеренный перевес {market_prob_value:.1f}% в пользу '{market_leader}'.")
        else:
            parts.append(f"Высокая неопределённость — вероятность {market_prob_value:.1f}%.")

        return " ".join(parts)

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

        news_block = news_summary[:500] if news_summary else "Нет данных"
        days_str = f"{days_to_event} дней" if days_to_event is not None else "неизвестно"
        max_dev, rule = self._get_divergence_rules(market_prob_value, days_to_event)
        context = self._build_context_description(
            market_prob_value, market_leader, days_to_event,
            trend_summary, news_summary, category
        )

        # Для binary явно указываем лидера
        leader_note = ""
        if market_type == "binary":
            leader_note = f"\nЛИДЕР РЫНКА: {market_leader} ({market_prob_value:.1f}%) — это исход с НАИБОЛЬШЕЙ вероятностью."

        if market_type == "multiple_choice" and options:
            options_block = "\n".join([f"- {opt}" for opt in options])

            if lang == "ru":
                return f"""Ты старший аналитик предсказательных рынков DeepAlpha.
Твоя задача — ГЛУБОКИЙ причинно-следственный анализ.

ВОПРОС: {question}
КАТЕГОРИЯ: {category}
ДО СОБЫТИЯ: {days_str}
СТАВКИ РЫНКА: {market_probability}{leader_note}

ВАРИАНТЫ:
{options_block}

КОНТЕКСТ: {context}
ТРЕНД: {trend_summary[:200]}
НОВОСТИ: {news_block}
НАСТРОЕНИЕ: {sentiment}

ПРАВИЛА:
1. Выбери вариант с НАИБОЛЬШЕЙ вероятностью как победителя
2. НЕ выбирай Yes/No — выбери конкретный вариант из списка
3. Максимальное отклонение от рынка: {max_dev:.0f}%
4. Объясни ПРИЧИНЫ победы именно этого варианта

Заполни ВСЕ пункты (2-3 предложения каждый):

Вероятность системы: [вариант с наибольшей вероятностью и его %]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [конкретные причины лидерства]
Расклад по вариантам: [все варианты с %]
Основной сценарий: [механизм реализации]
Альтернативный сценарий: [конкретные риски]
Вывод: [итоговая оценка]""".strip()

        else:
            if lang == "ru":
                return f"""Ты старший аналитик предсказательных рынков DeepAlpha.
Твоя задача — ГЛУБОКИЙ причинно-следственный анализ.

ВОПРОС: {question}
КАТЕГОРИЯ: {category}
ДО СОБЫТИЯ: {days_str}
СТАВКИ РЫНКА: {market_probability}{leader_note}

КОНТЕКСТ: {context}
ТРЕНД: {trend_summary[:200]}
НОВОСТИ: {news_block}
НАСТРОЕНИЕ: {sentiment}

ПРАВИЛА:
1. Лидирующий исход: {market_leader} ({market_prob_value:.1f}%) — начни с него
2. Максимальное отклонение от рынка: {max_dev:.0f}% ({rule})
3. При вероятности >90% объясни ПОЧЕМУ рынок прав
4. Используй конкретные факты из новостей и тренда

ВАЖНО: Если No лидирует — отвечай "No — X%" и объясняй почему событие НЕ произойдёт.

Заполни ВСЕ пункты (2-3 предложения каждый):

Вероятность системы: [Yes или No и процент лидера, например "No — 99.5%"]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [2-3 предложения с конкретным объяснением]
Основной сценарий: [2-3 предложения о механизме реализации]
Альтернативный сценарий: [2-3 предложения с конкретными рисками]
Вывод: [итоговая оценка]""".strip()

            else:
                return f"""You are a senior analyst at DeepAlpha prediction markets.

QUESTION: {question}
CATEGORY: {category}
DAYS TO EVENT: {days_to_event or 'unknown'}
MARKET ODDS: {market_probability}{leader_note}

CONTEXT: {context}
TREND: {trend_summary[:200]}
NEWS: {news_block}
SENTIMENT: {sentiment}

RULES:
1. Leading outcome: {market_leader} ({market_prob_value:.1f}%) — start from this
2. Max deviation: {max_dev:.0f}% ({rule})
3. If probability >90% explain WHY market is right
4. Use concrete facts from news and trend

IMPORTANT: If No leads — answer "No — X%" and explain why event will NOT happen.

Fill ALL fields with 2-3 sentences each:

System Probability: [Yes or No and leader's percentage, e.g. "No — 99.5%"]
Confidence: [High/Medium/Low]
Reasoning: [2-3 sentences with specific explanation]
Main Scenario: [2-3 sentences on mechanism]
Alternative Scenario: [2-3 sentences with specific risks]
Conclusion: [final assessment]""".strip()

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
        max_dev, rule = self._get_divergence_rules(market_prob_value, days_to_event)
        model_prob = self._extract_prob_value(prob_str)
        if model_prob is None:
            return prob_str, False

        if market_type == "binary":
            # Проверяем что выбран правильный исход (лидер рынка)
            prob_outcome = "yes" if "yes" in prob_str.lower() else "no"
            expected_outcome = market_leader.lower()

            if prob_outcome != expected_outcome and market_prob_value >= 80:
                print(f"DecisionAgent: Wrong outcome selected, correcting to {market_leader}")
                return f"{market_leader} — {market_prob_value:.1f}%", True

            delta = abs(model_prob - market_prob_value)
            if delta > max_dev:
                print(f"DecisionAgent: DIVERGENCE TOO HIGH: {delta:.1f}% > {max_dev:.1f}% — adjusting")
                return f"{market_leader} — {market_prob_value:.1f}%", True

        return prob_str, False

    def _calibrate_confidence(
        self,
        confidence: str,
        probability: str,
        market_prob_value: float,
        market_type: str,
        days_to_event: Optional[int],
    ) -> str:
        conf_lower = confidence.lower()
        if "high" in conf_lower or "высок" in conf_lower:
            base_score = 3
        elif "medium" in conf_lower or "средн" in conf_lower:
            base_score = 2
        else:
            base_score = 1

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
                "Альтернативный сценарий требует существенного изменения условий."
                if lang == "ru"
                else "Alternative scenario requires significant change of conditions."
            )

        # Multiple choice — убираем Yes/No
        if market_type == "multiple_choice":
            if probability.lower().startswith("yes") or probability.lower().startswith("no"):
                if options_breakdown:
                    first = options_breakdown.split(",")[0].strip()
                    if first:
                        probability = first
                elif market_leader not in ("Yes", "No"):
                    probability = f"{market_leader} — {market_prob_value:.1f}%"

        # Binary — валидируем выбор лидера
        if market_type == "binary":
            probability, was_adjusted = self._validate_and_adjust_probability(
                probability, market_prob_value, market_leader, market_type, days_to_event
            )
            if was_adjusted:
                reasoning = f"Прогноз скорректирован к рыночному лидеру. {reasoning}" if lang == "ru" else f"Forecast adjusted to market leader. {reasoning}"

        confidence = self._calibrate_confidence(
            confidence, probability, market_prob_value, market_type, days_to_event
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
        news_summary: str,
        trend_summary: str,
        lang: str = "ru",
    ) -> Dict[str, Any]:

        if market_type == "multiple_choice" and market_leader not in ("Yes", "No", "Unknown"):
            probability = f"{market_leader} — {market_prob_value:.1f}%"
        else:
            # Используем лидера — Yes или No
            probability = f"{market_leader} — {market_prob_value:.1f}%"

        if market_prob_value >= 80:
            confidence = "Высокая" if lang == "ru" else "High"
        elif market_prob_value >= 60:
            confidence = "Средняя" if lang == "ru" else "Medium"
        else:
            confidence = "Низкая" if lang == "ru" else "Low"

        days_str = f"{days_to_event} дней" if days_to_event is not None else ""
        time_context = ""
        if days_to_event is not None:
            if days_to_event <= 7:
                time_context = f"До события {days_str} — рынок близок к разрешению."
            elif days_to_event <= 30:
                time_context = f"До события {days_str}."

        if lang == "ru":
            if market_prob_value >= 90:
                reasoning = (
                    f"Рыночный консенсус {market_prob_value:.1f}% в пользу '{market_leader}' отражает "
                    f"высокую уверенность участников. {time_context} "
                    f"При такой вероятности рынок уже учёл большую часть доступной информации."
                ).strip()
                main_scenario = (
                    f"Исход '{market_leader}' с высокой вероятностью реализуется. "
                    f"Текущее положение устойчиво."
                )
                alt_scenario = (
                    f"Альтернативный исход ({100 - market_prob_value:.1f}%): "
                    f"возможен только при резком изменении фундаментальных условий или внешнем шоке."
                )
            else:
                reasoning = (
                    f"Рынок оценивает исход '{market_leader}' в {market_prob_value:.1f}%. "
                    f"{time_context}"
                ).strip()
                main_scenario = f"Наиболее вероятный исход: '{market_leader}' — {market_prob_value:.1f}%."
                alt_scenario = f"Альтернативный исход ({100 - market_prob_value:.1f}%) возможен при изменении ключевых факторов."

            conclusion = f"Следуем рыночной оценке: {probability}. {time_context}".strip()
        else:
            reasoning = f"Market consensus {market_prob_value:.1f}% favors '{market_leader}'. {time_context}".strip()
            main_scenario = f"Most likely outcome: '{market_leader}' — {market_prob_value:.1f}%."
            alt_scenario = f"Alternative ({100 - market_prob_value:.1f}%) requires significant factor change."
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
