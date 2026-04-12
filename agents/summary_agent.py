from typing import Any, Dict

from services.llm_service import generate_text


class SummaryAgent:
    def __init__(self) -> None:
        pass

    def run(
        self,
        question: str,
        category: str,
        market_probability: str,
        probability: str,
        confidence: str,
        reasoning: str,
        lang: str = "ru",
    ) -> Dict[str, Any]:
        """Дополняет частичный анализ — генерирует сценарии и вывод."""

        if not reasoning:
            reasoning = f"Вероятность системы: {probability}. Уверенность: {confidence}."

        prompt = self._build_prompt(
            question=question,
            category=category,
            market_probability=market_probability,
            probability=probability,
            confidence=confidence,
            reasoning=reasoning,
            lang=lang,
        )

        raw_response = generate_text(prompt)
        print(f"SummaryAgent: response length={len(raw_response)}, lines={len(raw_response.splitlines())}")

        if raw_response:
            parsed = self._parse(raw_response)
            if parsed.get("main_scenario") or parsed.get("conclusion"):
                return parsed

        return self._fallback(reasoning, lang)

    def _build_prompt(
        self,
        question: str,
        category: str,
        market_probability: str,
        probability: str,
        confidence: str,
        reasoning: str,
        lang: str,
    ) -> str:
        if lang == "ru":
            return f"""Ты аналитик предсказательных рынков.
Отвечай ТОЛЬКО на русском. Одна строка на каждый пункт.

Вопрос: {question}
Категория: {category}
Ставки рынка: {market_probability}
Прогноз AI: {probability}
Уверенность: {confidence}
Логика: {reasoning}

Напиши три пункта:

Основной сценарий: [одно предложение — что скорее всего произойдёт]
Альтернативный сценарий: [одно предложение — что могло бы изменить исход]
Вывод: [одно предложение — итог анализа]""".strip()
        else:
            return f"""You are a prediction market analyst.
Respond in English. One line per field.

Market: {question}
Category: {category}
Trader odds: {market_probability}
AI Forecast: {probability}
Confidence: {confidence}
Reasoning: {reasoning}

Write three fields:

Main Scenario: [one sentence — most likely outcome]
Alternative Scenario: [one sentence — what could change the outcome]
Conclusion: [one sentence — summary of analysis]""".strip()

    def _parse(self, text: str) -> Dict[str, Any]:
        result = {
            "main_scenario": "",
            "alt_scenario": "",
            "conclusion": "",
        }

        russian_map = {
            "Основной сценарий": "main_scenario",
            "Альтернативный сценарий": "alt_scenario",
            "Вывод": "conclusion",
            "Заключение": "conclusion",
            "Main Scenario": "main_scenario",
            "Alternative Scenario": "alt_scenario",
            "Conclusion": "conclusion",
        }

        current_key = None
        for line in text.splitlines():
            stripped = line.strip()
            matched = False
            for key, field in russian_map.items():
                prefix = f"{key}:"
                if stripped.startswith(prefix):
                    value = stripped[len(prefix):].strip()
                    result[field] = value
                    current_key = field
                    matched = True
                    break
            if not matched and current_key and stripped:
                if result[current_key]:
                    result[current_key] += " " + stripped
                else:
                    result[current_key] = stripped

        return result

    def _fallback(self, reasoning: str, lang: str) -> Dict[str, Any]:
        if lang == "ru":
            return {
                "main_scenario": reasoning or "Основной сценарий определяется текущими рыночными данными.",
                "alt_scenario": "Альтернативный сценарий возможен при изменении внешних факторов.",
                "conclusion": reasoning or "Анализ завершён на основе доступных данных.",
            }
        else:
            return {
                "main_scenario": reasoning or "Main scenario determined by current market data.",
                "alt_scenario": "Alternative scenario depends on external factor changes.",
                "conclusion": reasoning or "Analysis complete based on available data.",
            }

