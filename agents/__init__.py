"""DeepAlpha agent package runtime wiring."""

# Install a guarded DecisionAgent implementation before ChiefAgent performs its
# dynamic import. This keeps legacy import paths stable while preventing a
# provider-format or optional-summary exception from degrading the user result
# to probability=N/A.
from agents import decision_agent as _decision_agent_module
from agents.safe_decision_agent import SafeDecisionAgent

_decision_agent_module.DecisionAgent = SafeDecisionAgent

# Preserve an upstream DecisionAgent/Kimi probability when TradingPlanAgent
# builds the forecast card. Without this wiring, valid AI probabilities are
# discarded whenever the news-evidence layer is empty.
from agents import trading_plan_agent as _trading_plan_agent_module
from agents.forecast_aware_trading_plan_agent import ForecastAwareTradingPlanAgent

_trading_plan_agent_module.TradingPlanAgent = ForecastAwareTradingPlanAgent

__all__ = ["SafeDecisionAgent", "ForecastAwareTradingPlanAgent"]
