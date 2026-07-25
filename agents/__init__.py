"""DeepAlpha agent package runtime wiring."""

# Install a guarded, independent DecisionAgent implementation before ChiefAgent
# performs its dynamic import. This keeps legacy import paths stable, prevents
# provider-format failures from degrading to probability=N/A, and explicitly
# requires a numeric DeepAlpha point estimate even when source confidence is low.
from agents import decision_agent as _decision_agent_module
from agents.independent_forecast_decision_agent import IndependentForecastDecisionAgent

_decision_agent_module.DecisionAgent = IndependentForecastDecisionAgent

# Preserve an upstream DecisionAgent/Kimi probability when TradingPlanAgent
# builds the forecast card, while keeping market-aligned fallbacks non-independent.
from agents import trading_plan_agent as _trading_plan_agent_module
from agents.forecast_aware_trading_plan_agent import ForecastAwareTradingPlanAgent

_trading_plan_agent_module.TradingPlanAgent = ForecastAwareTradingPlanAgent

# Add market-specific research queries before ChiefAgent imports NewsAgent.
from agents import news_agent as _news_agent_module
from agents.special_market_news_queries import wrap_targeted_news_queries

_news_agent_module.build_targeted_news_queries = wrap_targeted_news_queries(
    _news_agent_module.build_targeted_news_queries
)

__all__ = [
    "IndependentForecastDecisionAgent",
    "ForecastAwareTradingPlanAgent",
]
