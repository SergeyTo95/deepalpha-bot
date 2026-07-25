"""DeepAlpha agent package runtime wiring."""

# Preserve the established SafeDecisionAgent class identity while extending its
# prompt contract with an explicit independent numeric forecast requirement.
from agents import decision_agent as _decision_agent_module
from agents.safe_decision_agent import SafeDecisionAgent
from agents.independent_forecast_decision_agent import (
    IndependentForecastDecisionAgent,
    safe_decision_build_prompt,
)
from agents.runtime_safety_patches import (
    install_llm_provider_diagnostics,
    install_news_agent_runtime_safety,
)

install_llm_provider_diagnostics()

SafeDecisionAgent._build_prompt = safe_decision_build_prompt
_decision_agent_module.DecisionAgent = SafeDecisionAgent

# Preserve upstream DecisionAgent/Kimi probability, keep fallbacks non-independent,
# and add the actionable decision-first product summary to every forecast card.
from agents import trading_plan_agent as _trading_plan_agent_module
from agents.forecast_aware_trading_plan_agent import ForecastAwareTradingPlanAgent
from agents.decision_first_trading_plan_agent import DecisionFirstTradingPlanAgent

_trading_plan_agent_module.TradingPlanAgent = DecisionFirstTradingPlanAgent

# Add market-specific research queries before ChiefAgent imports NewsAgent and
# protect the legacy NewsAgent runtime from missing context globals / null drivers.
from agents import news_agent as _news_agent_module
from agents.special_market_news_queries import wrap_targeted_news_queries

install_news_agent_runtime_safety(_news_agent_module)
_news_agent_module.build_targeted_news_queries = wrap_targeted_news_queries(
    _news_agent_module.build_targeted_news_queries
)

__all__ = [
    "SafeDecisionAgent",
    "IndependentForecastDecisionAgent",
    "ForecastAwareTradingPlanAgent",
    "DecisionFirstTradingPlanAgent",
]
