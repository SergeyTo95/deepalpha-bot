from typing import Any

from agents.decision_agent import DecisionAgent as _BaseDecisionAgent
from agents.safe_decision_agent import SafeDecisionAgent


def independent_probability_rules(lang: str = "ru") -> str:
    if lang == "ru":
        return (
            "\n\nДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА ОЦЕНКИ ВЕРОЯТНОСТИ:\n"
            "— Обязательно дай ОДНУ точечную оценку вероятности выбранного исхода в формате Yes/No — X.X%.\n"
            "— Это должна быть оценка DeepAlpha, а не механическое копирование market odds.\n"
            "— Даже если свежих источников нет, используй структуру события, базовую частоту, ширину диапазона, "
            "время до дедлайна и доступный контекст; при этом поставь Низкую уверенность.\n"
            "— Не выдумывай текущие счётчики, публикации, факты или источники, которых нет во входных данных.\n"
            "— Если после независимого рассуждения оценка совпала с рынком, это допустимо, но прямо напиши в Логике, "
            "что оценка совпадает с рыночным консенсусом и отдельной альфы нет.\n"
            "— Вероятность системы — это вероятность исхода, а не цена контракта и не диапазон.\n"
            "— Всегда указывай число с одной цифрой после запятой.\n"
        )
    return (
        "\n\nADDITIONAL PROBABILITY RULES:\n"
        "- Always provide ONE point probability for the selected outcome in the format Yes/No — X.X%.\n"
        "- This must be DeepAlpha's estimate, not a mechanical copy of market odds.\n"
        "- When fresh sources are missing, use event structure, base rates, range width, time to deadline, "
        "and available context, while setting confidence to Low.\n"
        "- Never invent current counters, posts, facts, or sources absent from the input.\n"
        "- If independent reasoning genuinely matches the market, say explicitly that it is market-aligned and no alpha exists.\n"
        "- System Probability is an outcome probability, not a contract price or a range.\n"
        "- Always use one decimal place.\n"
    )


def safe_decision_build_prompt(self: SafeDecisionAgent, *args: Any, **kwargs: Any) -> str:
    """Prompt hook installed on SafeDecisionAgent without changing class identity."""
    prompt = _BaseDecisionAgent._build_prompt(self, *args, **kwargs)
    lang = str(kwargs.get("lang") or "ru")
    return prompt + independent_probability_rules(lang)


class IndependentForecastDecisionAgent(SafeDecisionAgent):
    """Optional explicit subclass for direct use and focused tests."""

    independent_probability_rules = staticmethod(independent_probability_rules)
    _build_prompt = safe_decision_build_prompt
