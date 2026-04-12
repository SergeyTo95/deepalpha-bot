
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
Отвечай ТОЛЬКО на русском. СТРОГО одна строка на каждый пункт — не больше.

Вопрос: {question}
Категория: {category}
Ставки рынка: {market_probability}
Прогноз AI: {probability}
Уверенность: {confidence}
Логика: {reasoning[:200]}

Напиши РОВНО три строки — каждый пункт с новой строки:

Основной сценарий: [одно короткое предложение]
Альтернативный сценарий: [одно короткое предложение]
Вывод: [одно короткое предложение]""".strip()
        else:
            return f"""You are a prediction market analyst.
Respond in English. STRICTLY one line per field — no more.

Market: {question}
Category: {category}
Trader odds: {market_probability}
AI Forecast: {probability}
Confidence: {confidence}
Reasoning: {reasoning[:200]}

Write EXACTLY three lines:

Main Scenario: [one short sentence]
Alternative Scenario: [one short sentence]
Conclusion: [one short sentence]""".strip()

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

        all_prefixes = [f"{k}:" for k in russian_map.keys()]

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            matched = False
            for key, field in russian_map.items():
                prefix = f"{key}:"
                if stripped.startswith(prefix):
                    value = stripped[len(prefix):].strip()
                    # Берём только первое вхождение
                    if not result[field]:
                        result[field] = value
                    matched = True
                    break

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
