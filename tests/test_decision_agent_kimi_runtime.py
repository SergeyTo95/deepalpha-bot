import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _market():
    return {
        "question": "Will Donald Trump post 120-139 Truth Social posts from July 28 to August 4, 2026?",
        "category": "Politics",
        "market_probability": "Yes: 22.5% | No: 77.5%",
        "market_type": "binary",
        "options": ["Yes", "No"],
        "date_context": "2026-08-04",
        "trend_summary": "Stable",
        "crowd_behavior": "NO leads",
        "market_microstructure": None,
        "market_structure": [],
    }


def _news():
    return {
        "news_summary": "No directly relevant fresh evidence was found.",
        "sentiment": "Unclear",
        "source_summary": {
            "tier1": "not-a-number",
            "tier2": None,
            "tier3": "2",
            "fresh": "0",
            "usable": "0.0",
            "stale": "1",
        },
        "key_signals": None,
        "sources": None,
    }


def test_agents_package_installs_safe_decision_agent():
    from agents.decision_agent import DecisionAgent
    from agents.safe_decision_agent import SafeDecisionAgent

    assert DecisionAgent is SafeDecisionAgent


def test_markdown_kimi_response_is_parsed_and_keeps_independent_probability(monkeypatch):
    import agents.decision_agent as decision_module
    from agents.decision_agent import DecisionAgent

    response = """
### DeepAlpha analysis

- **Вероятность системы:** No — 70.0%
- **Уверенность:** Низкая
- **Логика:** Прямых данных о темпе публикаций мало, поэтому оценка остаётся осторожной.
- **Основной сценарий:** Недельный темп публикаций останется ниже указанного диапазона.
- **Альтернативный сценарий:** Резкий всплеск активности может привести к попаданию в диапазон.
- **Trigger Watch:** текущий счётчик | средний дневной темп
- **Trigger High:** фактический счётчик постов
- **Trigger Medium:** публичные выступления
- **Trigger Low:** нерелевантные заголовки
- **Mispricing:** нет подтверждённого расхождения
- **Market Psychology:** рынок склоняется к NO
- **Alpha Note:** Альфа отсутствует. Рынок эффективен.
- **Trade Insight:** вход без данных о темпе не подтверждён
- **Trade Strategy:** ждать
- **Trade Entry:** после появления текущего счётчика
- **Trade Risk:** неожиданный всплеск публикаций
- **Вывод:** Ждать измеримого темпа публикаций перед входом.
"""
    monkeypatch.setattr(decision_module, "generate_decision_text", lambda *a, **k: response)

    result = DecisionAgent().run(_market(), _news(), lang="ru")

    assert result["probability"] == "No — 70.0%"
    assert result["probability"] != "N/A"
    assert result["decision_runtime_guard"] == "ok"
    assert result["main_scenario"]
    assert result["conclusion"]


def test_json_kimi_response_is_parsed(monkeypatch):
    import agents.decision_agent as decision_module
    from agents.decision_agent import DecisionAgent

    response = """{
      "system_probability": "No — 69.0%",
      "confidence": "Low",
      "reasoning": "The exact weekly posting rate is not available.",
      "main_scenario": "Posting activity remains below the target interval.",
      "alternative_scenario": "A burst of posting reaches the interval.",
      "conclusion": "Wait for observed posting-rate data."
    }"""
    monkeypatch.setattr(decision_module, "generate_decision_text", lambda *a, **k: response)

    result = DecisionAgent().run(_market(), _news(), lang="en")

    assert result["probability"] == "No — 69.0%"
    assert result["decision_runtime_guard"] == "ok"


def test_internal_exception_returns_market_aligned_probability_not_na(monkeypatch):
    from agents.safe_decision_agent import SafeDecisionAgent, _BaseDecisionAgent

    def explode(self, *args, **kwargs):
        raise TypeError("unexpected provider metadata")

    monkeypatch.setattr(_BaseDecisionAgent, "run", explode)
    result = SafeDecisionAgent().run(_market(), _news(), lang="ru")

    assert result["probability"] == "No — 77.5%"
    assert result["probability"] != "N/A"
    assert result["decision_runtime_guard"] == "market_aligned_fallback"
    assert result["decision_fallback_reason"] == "exception:TypeError"
    assert result["reasoning"]
    assert result["main_scenario"]
    assert result["conclusion"]


def test_unusable_parent_result_returns_market_aligned_probability(monkeypatch):
    from agents.safe_decision_agent import SafeDecisionAgent, _BaseDecisionAgent

    monkeypatch.setattr(
        _BaseDecisionAgent,
        "run",
        lambda self, *a, **k: {
            "probability": "N/A",
            "confidence": "Low",
            "reasoning": "",
            "conclusion": "",
        },
    )
    result = SafeDecisionAgent().run(_market(), _news(), lang="ru")

    assert result["probability"] == "No — 77.5%"
    assert result["decision_runtime_guard"] == "market_aligned_fallback"
    assert result["decision_fallback_reason"] == "unusable_result"


def test_probability_can_be_recovered_from_unlabelled_kimi_text():
    from agents.decision_agent import DecisionAgent

    parsed = DecisionAgent()._parse_llm_output(
        "Итоговая оценка после анализа: No — 71.5%. Данных пока мало.",
        market_type="binary",
    )

    assert parsed["System Probability"] == "No — 71.5%"
