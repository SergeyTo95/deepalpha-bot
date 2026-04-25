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
                wrapped["main_scenario"] = (
                    summary.get("main_scenario") or wrapped.get("main_scenario", "")
                )
                wrapped["alt_scenario"] = (
                    summary.get("alt_scenario") or wrapped.get("alt_scenario", "")
                )
                wrapped["conclusion"] = (
                    summary.get("conclusion") or wrapped.get("conclusion", "")
                )

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

    # ═══════════════════════════════════════════
    # CLASSIFICATION
    # ═══════════════════════════════════════════

    def _classify_semantic_type(self, question: str, market_type: str) -> str:
        if market_type == "multiple_choice":
            return "multi_outcome"
        q = question.lower()
        threshold_keywords = [
            "exceed", "surpass", "above", "below",
            "more than", "less than", "over", "under", "cross",
        ]
        if any(k in q for k in threshold_keywords):
            return "binary_threshold"
        entity_keywords = [
            "who will", "which company", "which team",
            "which country", "largest", "biggest",
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

    # ═══════════════════════════════════════════
    # PARSE MARKET PROBABILITY
    # ═══════════════════════════════════════════

    def _parse_market_probability(
        self,
        market_probability: str,
        options: List[str],
        market_type: str,
    ) -> Tuple[float, str]:
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

    # ═══════════════════════════════════════════
    # BUILD PROMPT
    # ═══════════════════════════════════════════

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
        news_block = news_summary[:800] if news_summary else (
            "Нет данных" if lang == "ru" else "No data"
        )
        max_dev, rule = self._get_divergence_rules(market_prob_value, days_to_event)

        yes_prob = 50.0
        no_prob = 50.0
        yes_m = re.search(r'Yes:\s*([\d.]+)%', market_probability)
        no_m = re.search(r'No:\s*([\d.]+)%', market_probability)
        if yes_m:
            yes_prob = float(yes_m.group(1))
        if no_m:
            no_prob = float(no_m.group(1))

        if market_type == "multiple_choice" and options:
            market_block = "\n".join(f"- {opt}" for opt in options[:5])
        else:
            market_block = f"Yes: {yes_prob}% | No: {no_prob}%"

        model_prob_note = f"{market_prob_value:.1f}% (лидер: {market_leader})"

        days_str = (
            f"{days_to_event} дней" if days_to_event is not None
            else ("неизвестно" if lang == "ru" else "unknown")
        )

        if lang == "ru":
            return f"""Ты — DeepAlpha AI, профессиональный аналитик prediction markets (Polymarket).

Твоя задача — не пересказать рынок, а дать ПОЛЕЗНУЮ аналитику и практическую ценность.

ВАЖНО:
— Не повторяй просто рынок
— Давай интерпретацию
— Давай стратегию
— Давай где альфа, а где её нет
— Максимальное отклонение от рынка: {max_dev:.0f}% ({rule})
— Пиши ТОЛЬКО на русском языке

──────────────────────────────
INPUT:

Question: {question}
Category: {category}
Market: {market_block}
Model Probability: {model_prob_note}
Days to event: {days_str}
Trend: {trend_summary[:200]}
Crowd: {crowd_behavior[:150]}
Sentiment: {sentiment}

News:
{news_block}

──────────────────────────────

Сгенерируй ответ СТРОГО в следующем формате. Заполни каждый блок.

Вероятность системы: [Yes или No — X.X%]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [1-2 предложения — ЧТО означает состояние рынка, без повторения цифр]
Основной сценарий: [что должно произойти чтобы рынок оказался прав]
Альтернативный сценарий: [когда рынок может ошибаться]
Trigger Watch: [событие1 | событие2 | событие3 | событие4]
Trigger High: [события с высоким влиянием через запятую]
Trigger Medium: [события со средним влиянием через запятую]
Trigger Low: [шум и медиа через запятую]
Mispricing: [есть/нет + Δ и описание если есть]
Market Psychology: [рынок уверен или нет, страх/ожидание/неопределённость]
Alpha Note: [если альфы нет — написать "Альфа отсутствует. Рынок эффективен." Если есть — где именно и при каких условиях]
Trade Insight: [имеет ли смысл вход сейчас, почему]
Trade Strategy: [ждать / входить / игнорировать]
Trade Entry: [условия входа если есть]
Trade Risk: [что может сломать сценарий]
Вывод: [1 сильное предложение — что делать]

──────────────────────────────

КРИТИЧЕСКИЕ ПРАВИЛА:
1. НЕ ПОВТОРЯЙ цифры без смысла
2. НЕ ПИШИ воду и общие фразы
3. КАЖДЫЙ блок должен давать ценность
4. Если нет альфы — прямо скажи
5. Пиши как аналитик фонда, не как блогер
6. Все тексты строго на русском языке""".strip()

        else:
            return f"""You are DeepAlpha AI, a professional prediction markets analyst (Polymarket).

Your task: provide USEFUL analytics and practical value — not just restate the market.

IMPORTANT:
— Do NOT just repeat market numbers
— Give interpretation
— Give strategy
— State clearly where alpha exists or doesn't
— Max deviation from market: {max_dev:.0f}% ({rule})
— All text in English only

──────────────────────────────
INPUT:

Question: {question}
Category: {category}
Market: {market_block}
Model Probability: {model_prob_note}
Days to event: {days_str}
Trend: {trend_summary[:200]}
Crowd: {crowd_behavior[:150]}
Sentiment: {sentiment}

News:
{news_block}

──────────────────────────────

Generate response STRICTLY in this format:

System Probability: [Yes or No — X.X%]
Confidence: [High/Medium/Low]
Reasoning: [1-2 sentences — WHAT the market state means, no number repetition]
Main Scenario: [what must happen for market to be right]
Alternative Scenario: [when market could be wrong]
Trigger Watch: [event1 | event2 | event3 | event4]
Trigger High: [high impact events comma separated]
Trigger Medium: [medium impact events comma separated]
Trigger Low: [noise and media comma separated]
Mispricing: [yes/no + delta and description if exists]
Market Psychology: [confident or not, fear/expectation/uncertainty]
Alpha Note: [if no alpha — write "No alpha. Market is efficient." If exists — where exactly and under what conditions]
Trade Insight: [does entry make sense now, why]
Trade Strategy: [wait / enter / ignore]
Trade Entry: [entry conditions if any]
Trade Risk: [what could break the scenario]
Conclusion: [1 strong sentence — what to do]

──────────────────────────────

CRITICAL RULES:
1. Do NOT repeat numbers without meaning
2. Do NOT write filler or generic phrases
3. EVERY block must give value
4. If no alpha — say it directly
5. Write like a fund analyst, not a blogger
6. All text in English only""".strip()

    # ═══════════════════════════════════════════
    # PARSE LLM OUTPUT
    # ═══════════════════════════════════════════

    def _parse_llm_output(self, text: str, market_type: str = "binary") -> Dict[str, str]:
        fields = {
            "System Probability": "",
            "Confidence": "",
            "Reasoning": "",
            "Main Scenario": "",
            "Alternative Scenario": "",
            "Conclusion": "",
            "Options Breakdown": "",
            "Trigger Watch": "",
            "Trigger High": "",
            "Trigger Medium": "",
            "Trigger Low": "",
            "Mispricing": "",
            "Market Psychology": "",
            "Alpha Note": "",
            "Trade Insight": "",
            "Trade Strategy": "",
            "Trade Entry": "",
            "Trade Risk": "",
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
            "Trigger Watch": "Trigger Watch",
            "Trigger High": "Trigger High",
            "Trigger Medium": "Trigger Medium",
            "Trigger Low": "Trigger Low",
            "Mispricing": "Mispricing",
            "Расхождение": "Mispricing",
            "Market Psychology": "Market Psychology",
            "Психология рынка": "Market Psychology",
            "Alpha Note": "Alpha Note",
            "Альфа": "Alpha Note",
            "Trade Insight": "Trade Insight",
            "Анализ входа": "Trade Insight",
            "Trade Strategy": "Trade Strategy",
            "Стратегия": "Trade Strategy",
            "Trade Entry": "Trade Entry",
            "Условия входа": "Trade Entry",
            "Trade Risk": "Trade Risk",
            "Риск": "Trade Risk",
        }

        current_key = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            matched = False

            all_keys = list(fields.keys()) + list(russian_map.keys())
            for key in all_keys:
                prefix = f"{key}:"
                if stripped.startswith(prefix):
                    actual_key = russian_map.get(key, key)
                    if actual_key in fields:
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

        # Чистим System Probability от мусора
        prob = fields.get("System Probability", "")
        if prob:
            prob_lower = prob.lower()
            bad_phrases = [
                "не повторяем", "не повторяй", "объясняй", "причины",
                "повторяй цифры", "значение", "don't repeat", "do not repeat",
                "explain", "reasons", "mechanism",
            ]
            if any(p in prob_lower for p in bad_phrases):
                fields["System Probability"] = ""
            elif "%" not in prob and "yes" not in prob_lower and "no" not in prob_lower:
                num_match = re.search(r'([\d.]+)', prob)
                if num_match:
                    fields["System Probability"] = f"{num_match.group(1)}%"
                else:
                    fields["System Probability"] = ""

        return fields

    def _extract_prob_value(self, prob_str: str) -> Optional[float]:
        try:
            match = re.search(r'([\d.]+)%', str(prob_str))
            if match:
                return float(match.group(1))
        except Exception:
            pass
        return None

    # ═══════════════════════════════════════════
    # VALIDATE AND ADJUST
    # ═══════════════════════════════════════════

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
        lang: str = "ru",
    ) -> str:
        conf_lower = confidence.lower()
        if "high" in conf_lower or "высок" in conf_lower:
            base_score = 3
        elif "medium" in conf_lower or "средн" in conf_lower:
            base_score = 2
        else:
            base_score = 1

        if market_balance in ("balanced", "slight_lean"):
            base_score = min(base_score, 2)

        model_prob = self._extract_prob_value(probability)
        if model_prob is not None and market_type == "binary":
            delta = abs(model_prob - market_prob_value)
            if delta > 20:
                base_score -= 1

        if days_to_event is not None and days_to_event <= 7:
            base_score = min(3, base_score + 1)

        if lang == "ru":
            if base_score >= 3:
                return "Высокая"
            elif base_score >= 2:
                return "Средняя"
            else:
                return "Низкая"
        else:
            if base_score >= 3:
                return "High"
            elif base_score >= 2:
                return "Medium"
            else:
                return "Low"

    # ═══════════════════════════════════════════
    # WRAP LLM RESULT
    # ═══════════════════════════════════════════

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
            alt_scenario = (
                "Альтернативный сценарий требует изменения ключевых факторов."
                if lang == "ru"
                else "Alternative scenario requires key factor changes."
            )

        if market_type == "multiple_choice":
            if probability.lower().startswith("yes") or probability.lower().startswith("no"):
                if options_breakdown:
                    first = options_breakdown.split(",")[0].strip()
                    if first:
                        probability = first
                elif market_leader not in ("Yes", "No"):
                    probability = f"{market_leader} — {market_prob_value:.1f}%"

        if market_type == "binary":
            probability, was_adjusted = self._validate_and_adjust_probability(
                probability, market_prob_value, market_leader, market_type, days_to_event
            )
            if was_adjusted:
                reasoning = (
                    f"Прогноз скорректирован к рыночному лидеру. {reasoning}"
                    if lang == "ru"
                    else f"Forecast adjusted to market leader. {reasoning}"
                )

        confidence = self._calibrate_confidence(
            confidence, probability, market_prob_value,
            market_type, days_to_event, market_balance, lang
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
            "trigger_watch_raw": parsed.get("Trigger Watch", ""),
            "trigger_high": parsed.get("Trigger High", ""),
            "trigger_medium": parsed.get("Trigger Medium", ""),
            "trigger_low": parsed.get("Trigger Low", ""),
            "mispricing_raw": parsed.get("Mispricing", ""),
            "market_psychology_raw": parsed.get("Market Psychology", ""),
            "alpha_note_raw": parsed.get("Alpha Note", ""),
            "trade_insight": parsed.get("Trade Insight", ""),
            "trade_strategy": parsed.get("Trade Strategy", ""),
            "trade_entry": parsed.get("Trade Entry", ""),
            "trade_risk": parsed.get("Trade Risk", ""),
        }

    # ═══════════════════════════════════════════
    # IS VALID RESULT
    # ═══════════════════════════════════════════

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

    # ═══════════════════════════════════════════
    # MARKET ALIGNED FALLBACK
    # ═══════════════════════════════════════════

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
            confidence = "Низкая" if lang == "ru" else "Low"
        elif market_prob_value >= 80:
            confidence = "Высокая" if lang == "ru" else "High"
        elif market_prob_value >= 60:
            confidence = "Средняя" if lang == "ru" else "Medium"
        else:
            confidence = "Низкая" if lang == "ru" else "Low"

        days_str = (
            (f"{days_to_event} " + ("дней" if lang == "ru" else "days"))
            if days_to_event is not None else ""
        )
        time_ctx = ""
        if days_to_event is not None:
            if days_to_event <= 7:
                time_ctx = (
                    f"До события {days_str} — рынок близок к разрешению."
                    if lang == "ru"
                    else f"Event in {days_str} — market near resolution."
                )
            elif days_to_event <= 30:
                time_ctx = (
                    f"До события {days_str}."
                    if lang == "ru"
                    else f"Event in {days_str}."
                )

        news_excerpt = (news_summary[:150].strip() + "...") if news_summary else ""

        if lang == "ru":
            if market_balance in ("balanced", "slight_lean"):
                reasoning = (
                    f"Рынок в состоянии неопределённости — ни один исход не доминирует. "
                    f"Исход зависит от появления нового катализатора."
                )
                if news_excerpt:
                    reasoning += f" Контекст: {news_excerpt}"
                main_scenario = (
                    f"Сценарий реализуется при сохранении текущего баланса. "
                    f"Любое значимое событие может сдвинуть вероятность."
                )
                alt_scenario = (
                    f"Противоположный исход ({100 - market_prob_value:.1f}%) равновероятен. "
                    f"Триггером может стать новая статистика или внешний шок."
                )
                conclusion = f"Сбалансированный рынок. Небольшой перевес: {probability}."
            elif market_balance == "strong_consensus":
                reasoning = (
                    f"Консенсус {market_prob_value:.1f}% отражает высокую уверенность участников. "
                    f"{time_ctx} Рынок учёл основную часть доступной информации."
                )
                main_scenario = (
                    f"Исход реализуется при сохранении текущих условий. "
                    f"Серьёзных угроз не выявлено."
                )
                alt_scenario = (
                    f"Альтернативный исход ({100 - market_prob_value:.1f}%) возможен только "
                    f"при резком изменении политики или масштабном внешнем шоке."
                )
                conclusion = f"Высокий консенсус подтверждает: {probability}. {time_ctx}".strip()
            else:
                reasoning = (
                    f"Умеренный консенсус {market_prob_value:.1f}% указывает на перевес. "
                    f"{time_ctx}"
                )
                if news_excerpt:
                    reasoning += f" Контекст: {news_excerpt}"
                main_scenario = (
                    f"Наиболее вероятный исход: {probability}. "
                    f"Требуется сохранение текущей динамики."
                )
                alt_scenario = (
                    f"Альтернативный исход ({100 - market_prob_value:.1f}%) "
                    f"при изменении ключевых факторов."
                )
                conclusion = f"Следуем рыночной оценке: {probability}. {time_ctx}".strip()
        else:
            if market_balance in ("balanced", "slight_lean"):
                reasoning = (
                    f"Market is uncertain — no outcome dominates. "
                    f"Result depends on emergence of new catalyst."
                )
                main_scenario = (
                    f"Scenario plays out if current balance holds. "
                    f"Any significant event could shift probability."
                )
                alt_scenario = (
                    f"Opposite outcome ({100 - market_prob_value:.1f}%) nearly equally likely. "
                    f"New data or external shock could be the trigger."
                )
                conclusion = f"Balanced market. Slight edge: {probability}."
            elif market_balance == "strong_consensus":
                reasoning = (
                    f"Consensus {market_prob_value:.1f}% reflects high participant confidence. "
                    f"{time_ctx} Market has priced in most available information."
                )
                main_scenario = (
                    f"Outcome plays out if current conditions hold. "
                    f"No major threats identified."
                )
                alt_scenario = (
                    f"Alternative outcome ({100 - market_prob_value:.1f}%) only possible "
                    f"with sharp policy change or major external shock."
                )
                conclusion = f"High consensus confirms: {probability}. {time_ctx}".strip()
            else:
                reasoning = (
                    f"Moderate consensus {market_prob_value:.1f}% points to an edge. {time_ctx}"
                )
                main_scenario = (
                    f"Most likely outcome: {probability}. "
                    f"Requires stable current dynamics."
                )
                alt_scenario = (
                    f"Alternative ({100 - market_prob_value:.1f}%) if key factors shift."
                )
                conclusion = f"Following market estimate: {probability}. {time_ctx}".strip()

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
            "trigger_watch_raw": "",
            "trigger_high": "",
            "trigger_medium": "",
            "trigger_low": "",
            "mispricing_raw": "",
            "market_psychology_raw": "",
            "alpha_note_raw": "",
            "trade_insight": "",
            "trade_strategy": "",
            "trade_entry": "",
            "trade_risk": "",
        }
