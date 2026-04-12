from typing import Any, Dict, List

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

        news_summary = news_data.get("news_summary", "")
        sentiment = news_data.get("sentiment", "Unknown")
        news_confidence = news_data.get("confidence", "Unknown")
        sources = news_data.get("sources", [])

        prompt = self._build_prompt(
            question=question,
            category=category,
            market_probability=market_probability,
            options=options,
            related_markets=related_markets,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            news_summary=news_summary,
            sentiment=sentiment,
            news_confidence=news_confidence,
            sources=sources,
            market_type=market_type,
            lang=lang,
        )

        print(f"DecisionAgent.run: calling LLM, prompt length={len(prompt)}")
        raw_response = generate_decision_text(prompt)
        print(f"DecisionAgent.run: LLM response length={len(raw_response)}")
        print(f"DecisionAgent.run: LLM response text={raw_response[:300]}")

        if raw_response:
            parsed = self._parse_llm_output(raw_response)
            print(f"DecisionAgent.run: parsed={parsed}")
            wrapped = self._wrap_llm_result(
                question=question,
                category=category,
                market_probability=market_probability,
                parsed=parsed,
                raw_text=raw_response,
            )
            if self._is_valid_result(wrapped):
                print(f"DecisionAgent.run: valid result, probability={wrapped.get('probability')}")
                return wrapped
            else:
                print(f"DecisionAgent.run: invalid result, using partial")

        print(f"DecisionAgent.run: using fallback")
        return self._fallback_decision(
            question=question,
            category=category,
            market_probability=market_probability,
            options=options,
            related_markets=related_markets,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            news_summary=news_summary,
            sentiment=sentiment,
            news_confidence=news_confidence,
        )

    def _build_prompt(
        self,
        question: str,
        category: str,
        market_probability: Any,
        options: List[str],
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        news_summary: str,
        sentiment: str,
        news_confidence: str,
        sources: List[Dict[str, str]] = None,
        market_type: str = "binary",
        lang: str = "en",
    ) -> str:

        options_text = ", ".join(options) if options else "Yes, No"

        if market_type == "binary":
            market_note = "Binary Yes/No market. Give independent % for Yes."
        else:
            market_note = f"Multiple choice: {options_text}. Pick most likely winner."

        news_block = ""
        if news_summary:
            news_block = news_summary[:400]

        if lang == "ru":
            return f"""Ты эксперт по предсказательным рынкам DeepAlpha.
Отвечай ТОЛЬКО на русском языке. Будь краток — одна строка на каждый пункт.

Вопрос: {question}
Категория: {category}
Ставки трейдеров: {market_probability}
Тип: {market_note}
Тренд: {trend_summary[:150]}
Новости: {news_block}
Настроение: {sentiment}

Дай НЕЗАВИСИМУЮ оценку одной строкой на каждый пункт:

Вероятность системы: [например "Yes — 65%"]
Уверенность: [Высокая/Средняя/Низкая]
Логика: [одно предложение]
Основной сценарий: [одно предложение]
Альтернативный сценарий: [одно предложение]
Вывод: [одно предложение]""".strip()
        else:
            return f"""You are DeepAlpha prediction market expert.
Respond in English. Be brief — one line per field.

Market: {question}
Category: {category}
Trader odds: {market_probability}
Type: {market_note}
Trend: {trend_summary[:150]}
News: {news_block}
Sentiment: {sentiment}

Give INDEPENDENT estimate, one line per field:

System Probability: [e.g. "Yes — 65%"]
Confidence: [High/Medium/Low]
Reasoning: [one sentence]
Main Scenario: [one sentence]
Alternative Scenario: [one sentence]
Conclusion: [one sentence]""".strip()

    def _parse_llm_output(self, text: str) -> Dict[str, str]:
        fields = {
            "System Probability": "",
            "Confidence": "",
            "Reasoning": "",
            "Main Scenario": "",
            "Alternative Scenario": "",
            "Conclusion": "",
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

    def _wrap_llm_result(
        self,
        question: str,
        category: str,
        market_probability: Any,
        parsed: Dict[str, str],
        raw_text: str,
    ) -> Dict[str, Any]:
        probability = parsed.get("System Probability", "").strip() or "N/A"
        confidence = parsed.get("Confidence", "").strip() or "Medium"
        reasoning = parsed.get("Reasoning", "").strip() or ""
        main_scenario = parsed.get("Main Scenario", "").strip() or ""
        alt_scenario = parsed.get("Alternative Scenario", "").strip() or ""
        conclusion = parsed.get("Conclusion", "").strip() or ""

        # Если reasoning есть но conclusion нет — используем reasoning как conclusion
        if reasoning and not conclusion:
            conclusion = reasoning
        if not main_scenario and reasoning:
            main_scenario = reasoning
        if not alt_scenario:
            alt_scenario = "Альтернативный сценарий зависит от изменения новостного фона." if "%" in probability else "Alternative scenario depends on news flow changes."

        return {
            "question": question,
            "category": category,
            "market_probability": market_probability,
            "probability": probability,
            "confidence": confidence,
            "reasoning": reasoning or conclusion,
            "main_scenario": main_scenario,
            "alt_scenario": alt_scenario,
            "conclusion": conclusion,
            "raw_decision_text": raw_text,
        }

    def _is_valid_result(self, result: Dict[str, Any]) -> bool:
        # Достаточно probability и confidence
        probability = str(result.get("probability", "")).strip()
        confidence = str(result.get("confidence", "")).strip()

        if not probability or probability == "N/A":
            return False
        if not confidence:
            return False

        # Должен быть хоть какой-то текст
        reasoning = str(result.get("reasoning", "")).strip()
        conclusion = str(result.get("conclusion", "")).strip()
        if not reasoning and not conclusion:
            return False

        return True

    def _fallback_decision(
        self,
        question: str,
        category: str,
        market_probability: Any,
        options: List[str],
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        news_summary: str,
        sentiment: str,
        news_confidence: str,
    ) -> Dict[str, Any]:

        probability_value = self._estimate_probability_value(
            related_markets=related_markets,
            trend_summary=trend_summary,
            crowd_behavior=crowd_behavior,
            sentiment=sentiment,
            news_confidence=news_confidence,
            category=category,
        )

        main_option = self._choose_main_option(options)
        confidence = self._estimate_confidence(
            related_markets=related_markets,
            trend_summary=trend_summary,
            news_confidence=news_confidence,
        )

        reasoning = (
            f"Резервный анализ для категории {category}. "
            f"Ставки рынка: {market_probability}. "
            f"Настроение: {sentiment}."
        )

        main_scenario = f"Базовый сценарий в пользу '{main_option}' на основе доступных данных."
        alt_scenario = "Альтернативный сценарий возможен при изменении тренда или новостного фона."
        conclusion = f"Резервная оценка: '{main_option}' ~{probability_value} ({confidence.lower()} уверенность)."

        return {
            "question": question,
            "category": category,
            "market_probability": market_probability,
            "probability": f"{main_option} — {probability_value}",
            "confidence": confidence,
            "reasoning": reasoning,
            "main_scenario": main_scenario,
            "alt_scenario": alt_scenario,
            "conclusion": conclusion,
            "raw_decision_text": "",
        }

    def _estimate_probability_value(
        self,
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        crowd_behavior: str,
        sentiment: str,
        news_confidence: str,
        category: str,
    ) -> str:
        score = 50

        if category in {"Politics", "Crypto", "Economy"}:
            score += 5
        if related_markets:
            score += min(len(related_markets) * 4, 16)

        t = str(trend_summary).lower()
        c = str(crowd_behavior).lower()
        s = str(sentiment).lower()
        nc = str(news_confidence).lower()

        if "accelerated" in t: score += 6
        if "24h move" in t: score += 4
        if "strengthened sharply" in c: score += 7
        elif "moderately" in c: score += 4
        elif "reversed sharply" in c: score -= 7
        elif "softened" in c: score -= 4
        if "positive" in s: score += 6
        elif "negative" in s: score -= 6
        if "high" in nc: score += 5
        elif "medium" in nc: score += 2

        score = max(5, min(95, score))
        return f"{score}%"

    def _estimate_confidence(
        self,
        related_markets: List[Dict[str, Any]],
        trend_summary: str,
        news_confidence: str,
    ) -> str:
        score = 0
        if related_markets: score += 1
        if trend_summary and "no price history" not in str(trend_summary).lower(): score += 1
        if str(news_confidence).lower() == "high": score += 2
        elif str(news_confidence).lower() == "medium": score += 1

        if score >= 4: return "High"
        if score >= 2: return "Medium"
        return "Low"

    def _choose_main_option(self, options: List[str]) -> str:
        if not options:
            return "Наиболее вероятный исход"
        cleaned = [str(x).strip() for x in options if str(x).strip()]
        if "Yes" in cleaned: return "Yes"
        if "No" in cleaned: return "No"
        return cleaned[0] if cleaned else "Наиболее вероятный исход"
